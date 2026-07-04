#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CIS Weekly Performance Module
土曜用：監視リスト全銘柄を週間騰落率順に並べる。米国株のみ日次騰落側のTradingViewレーティング・アナリスト情報を参照し、日本株は週間騰落率だけをシンプルに出力する。

設計方針：
- TradingViewはこの週次処理では原則再取得しない。
- 米国株は日次騰落レポート/スナップショットに保存済みの最新TV情報を読む。
- 日次側はTradingViewスナップショット保存を必須にする。scripts/cis_validate_daily_tv_snapshot.py で検証する。
- 日本株はTradingView評価を扱わず、表示は週間騰落率のみにする。
- 価格はyfinance優先。取れない場合は日次スナップショットの直近値でフォールバック。
- 取得不可があっても全体を落とさず、銘柄カード内に「未取得」を残す。

出力：
- output/weekly_performance_latest.json
- output/weekly_performance_latest.md
- docs/latest/weekly_performance_latest.json
- docs/latest/weekly_performance_latest.md
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import shutil
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import yfinance as yf  # type: ignore
except Exception:  # pragma: no cover
    yf = None

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
DOCS_LATEST_DIR = ROOT / "docs" / "latest"

WATCHLIST_CANDIDATES = [
    DATA_DIR / "watchlist_master.csv",
    DATA_DIR / "watchlist.csv",
    ROOT / "watchlist_master.csv",
]

# 日次騰落側の出力候補。既存CISのファイル名ゆれに耐える。
DAILY_SNAPSHOT_CANDIDATES = [
    OUTPUT_DIR / "daily_performance_latest.json",
    OUTPUT_DIR / "us_daily_performance_latest.json",
    OUTPUT_DIR / "us_watchlist_daily_latest.json",
    OUTPUT_DIR / "watchlist_daily_latest.json",
    DOCS_LATEST_DIR / "daily_performance_latest.json",
    DOCS_LATEST_DIR / "us_daily_performance_latest.json",
    DOCS_LATEST_DIR / "us_watchlist_daily_latest.json",
    DOCS_LATEST_DIR / "watchlist_daily_latest.json",
]

US_MARKET_HINTS = {"US", "USA", "NASDAQ", "NYSE", "AMEX", "ETF_US", "US_ETF"}
JP_MARKET_HINTS = {"JP", "JPN", "TSE", "TYO", "東証", "日本"}

MIN_PRICE_SUCCESS_RATIO = 0.70  # 週間騰落レポートとして成立させる最低価格取得率
MAX_US_RATING_SOURCE_AGE_DAYS = 7  # 米国株TV参照元の日次JSONとして許容する最大経過日数

DESCRIPTION_FALLBACK = {
    "AVAV": "軍用ドローン・無人機",
    "AXON": "警察向けテーザー・ボディカメラ",
    "AUR": "自動運転トラック技術",
    "TMDX": "臓器移植用保存・輸送システム",
    "NOW": "大企業向け業務クラウド",
    "RDW": "宇宙インフラ・衛星部品",
    "TRMB": "測位・建設農業テック",
    "VEEV": "製薬向けクラウドSaaS",
    "PYPL": "オンライン決済",
    "TEM": "AI医療・精密医療",
    "FICO": "信用スコア・金融分析",
    "AAOI": "光通信部品",
    "OUST": "LiDARセンサー",
    "APH": "電子コネクタ",
    "ASPI": "核燃料・同位体濃縮",
    "NBIS": "AIクラウド・GPUインフラ",
    "EWY": "韓国株ETF",
    "ISRG": "手術支援ロボット",
    "SPGI": "金融指数・格付け",
    "MELI": "中南米EC・決済",
    "TMO": "ライフサイエンス機器",
    "META": "SNS・AI広告",
    "IONQ": "量子コンピューター",
    "RGTI": "量子コンピューター",
    "SDGR": "AI創薬ソフト",
    "RXRX": "AI創薬",
    "QCOM": "半導体・通信SoC",
    "HSAI": "LiDARセンサー",
    "PL": "衛星画像データ",
    "V": "カード決済ネットワーク",
    "VRTX": "バイオ医薬品",
    "DIS": "メディア・テーマパーク",
    "KVYO": "ECマーケSaaS",
    "DKNG": "オンライン賭博・スポーツベット",
    "CRSP": "遺伝子編集医療",
    "BEAM": "遺伝子編集医療",
    "ETN": "電力管理・産業機器",
    "MSTR": "BTC保有・BIソフト",
    "POET": "光半導体・CPO",
    "KITT": "海洋ロボティクス",
    "AXTI": "化合物半導体基板",
    "COHR": "光通信・レーザー部品",
    "ANET": "DCネットワーク機器",
    "DDOG": "クラウド監視SaaS",
    "SNOW": "データクラウド",
    "OPTX": "精密光学・フォトニクス",
    "AEM": "金鉱株",
}

@dataclass
class WatchItem:
    ticker: str
    name: str = ""
    description: str = ""
    market: str = ""
    enabled: bool = True

@dataclass
class WeeklyRow:
    ticker: str
    label: str
    description: str
    market: str
    display_fields: List[str]
    hidden_fields_by_policy: List[str]
    current_price: Optional[float]
    week_ago_price: Optional[float]
    weekly_change: Optional[float]
    weekly_change_pct: Optional[float]
    tv_rating: str
    analyst_count: Optional[int]
    avg_target_price: Optional[float]
    upside_to_avg_target_pct: Optional[float]
    data_status: str
    rating_source: str
    rating_snapshot_generated_at: str


def norm_key(s: str) -> str:
    return s.strip().lower().replace(" ", "_").replace("-", "_")


def normalize_ticker_id(raw: Any) -> str:
    """CIS内の表記ゆれを吸収した照合用ティッカー。

    例：NASDAQ:PYPL / PYPL.US / 6758.T / 東証:6758 を同じ銘柄として探しやすくする。
    表示用のticker自体は変更しない。
    """
    s = str(raw or "").strip().upper()
    if not s:
        return ""
    s = s.replace(" ", "")
    if ":" in s:
        s = s.split(":")[-1]
    for suffix in [".US", ".NASDAQ", ".NYSE", ".AMEX"]:
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    if s.endswith(".T") and s[:-2].isdigit():
        return s[:-2]
    return s


def ticker_lookup_keys(raw: Any) -> List[str]:
    base = normalize_ticker_id(raw)
    if not base:
        return []
    keys = [base]
    if base.isdigit():
        keys.append(f"{base}.T")
    return list(dict.fromkeys(keys))


def pick(row: Dict[str, str], *keys: str, default: str = "") -> str:
    nrow = {norm_key(k): v for k, v in row.items()}
    for key in keys:
        val = nrow.get(norm_key(key), "")
        if val is not None and str(val).strip() != "":
            return str(val).strip()
    return default


def is_truthy_disabled(v: str) -> bool:
    return str(v).strip().lower() in {"0", "false", "no", "n", "disabled", "除外", "停止"}


def find_watchlist() -> Path:
    for p in WATCHLIST_CANDIDATES:
        if p.exists():
            return p
    raise FileNotFoundError("watchlist_master.csv が見つかりません。data/watchlist_master.csv を確認してください。")


def load_watchlist() -> List[WatchItem]:
    path = find_watchlist()
    items: List[WatchItem] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = pick(row, "ticker", "symbol", "code")
            if not ticker:
                continue
            enabled_raw = pick(row, "enabled", "active", "use", "include", default="1")
            if is_truthy_disabled(enabled_raw):
                continue
            market = pick(row, "market", "exchange", "country", "region")
            name = pick(row, "name", "company", "company_name", "銘柄名")
            desc = pick(row, "description", "short_description", "business", "業態", "説明")
            t = normalize_ticker_id(ticker)
            if not desc:
                desc = DESCRIPTION_FALLBACK.get(t, "事業説明未設定")
            items.append(WatchItem(ticker=t, name=name, description=desc, market=market, enabled=True))
    return dedupe_items(items)


def dedupe_items(items: Iterable[WatchItem]) -> List[WatchItem]:
    seen = set()
    out = []
    for item in items:
        key = normalize_ticker_id(item.ticker)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def detect_market(item: WatchItem) -> str:
    m = item.market.upper().strip()
    if m in US_MARKET_HINTS:
        return "US"
    if m in JP_MARKET_HINTS:
        return "JP"
    if item.ticker.isdigit() or item.ticker.endswith(".T"):
        return "JP"
    return "US"


def yf_symbol(item: WatchItem) -> str:
    market = detect_market(item)
    if market == "JP" and item.ticker.isdigit():
        return f"{item.ticker}.T"
    return normalize_ticker_id(item.ticker)


def to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if math.isnan(float(v)):
            return None
        return float(v)
    s = str(v).strip().replace(",", "").replace("$", "").replace("¥", "").replace("円", "").replace("人", "")
    if s in {"", "-", "—", "未取得", "None", "null"}:
        return None
    if s.endswith("%"):
        s = s[:-1]
    try:
        return float(s)
    except Exception:
        return None


def to_int(v: Any) -> Optional[int]:
    f = to_float(v)
    return None if f is None else int(round(f))


def load_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def rel_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except Exception:
        return str(path)


GENERATED_AT_KEYS = [
    "generated_at_jst", "generated_at", "data_time", "updated_at",
    "snapshot_generated_at", "snapshot_at", "as_of",
    "取得時刻", "生成時刻", "実行時刻", "基準時刻"
]


def extract_generated_at_from_mapping(obj: Any) -> str:
    """JSONルートまたは銘柄行から生成時刻を抽出する。

    既存CISの日次JSONは、生成時刻がルート直下ではなく各row内にある可能性がある。
    GitHubのmtimeではなく、JSON本文内の時刻だけを鮮度情報として採用する。
    """
    if isinstance(obj, dict):
        flat = flatten_dict_for_lookup(obj) if "flatten_dict_for_lookup" in globals() else {norm_key(k): v for k, v in obj.items()}
        for key in GENERATED_AT_KEYS:
            val = flat.get(norm_key(key))
            if val not in (None, ""):
                return str(val)
    return "unknown"


def extract_root_generated_at(obj: Any, path: Path) -> str:
    return extract_generated_at_from_mapping(obj)


def flatten_records(obj: Any) -> List[Dict[str, Any]]:
    """JSON構造の違いを吸収して銘柄レコード配列を取り出す。

    rows直下だけでなく、us.rows / jp.items / data.stocks のような入れ子も拾う。
    """
    records: List[Dict[str, Any]] = []
    if isinstance(obj, list):
        for x in obj:
            if isinstance(x, dict):
                records.append(x)
        return records
    if isinstance(obj, dict):
        preferred_keys = ["rows", "items", "data", "records", "stocks", "watchlist", "results", "us", "jp", "japan", "usa"]
        for key in preferred_keys:
            if key in obj:
                records.extend(flatten_records(obj.get(key)))
        if records:
            return records
        # dict keyed by ticker
        for k, v in obj.items():
            if isinstance(v, dict):
                rec = dict(v)
                rec.setdefault("ticker", k)
                records.append(rec)
        return records
    return records


def flatten_dict_for_lookup(obj: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    """日次JSONの項目名ゆれ・入れ子構造を吸収するための平坦化。

    例：
    {"tradingview": {"rating": "Buy"}} も rating 候補として拾えるようにする。
    """
    out: Dict[str, Any] = {}
    for k, v in obj.items():
        key = str(k)
        joined = f"{prefix}.{key}" if prefix else key
        out[norm_key(joined)] = v
        out[norm_key(key)] = v
        if isinstance(v, dict):
            out.update(flatten_dict_for_lookup(v, joined))
    return out


def parse_datetime_like(value: Any) -> Optional[datetime]:
    """JSON内の生成時刻を可能な範囲でdatetime化する。

    GitHub Actionsのcheckout後mtimeはデータ鮮度を表さないため、
    日次JSONの優先順位は原則としてファイル中の generated_at 系メタデータで判断する。
    """
    if value in (None, ""):
        return None
    s = str(value).strip()
    if not s or s.lower() in {"unknown", "none", "null"}:
        return None
    s = s.replace("Z", "+00:00")
    # よくある日本語表記をISO風に寄せる。
    # 例：2026/07/04（土）JST / 2026年7月4日 18:30 JST
    s = re.sub(r"[（(][月火水木金土日][）)]", "", s)
    s = s.replace("JST", "").replace("jst", "").strip()
    # 「2026/07/04 18:30（JST）」のJST除去後に残る空括弧も消す。
    s = s.replace("（）", "").replace("()", "").strip()
    s = s.replace("年", "/").replace("月", "/").replace("日", " ").strip()
    candidates = [s]
    if "/" in s and "T" not in s:
        candidates.append(s.replace("/", "-"))
    for c in candidates:
        try:
            dt = datetime.fromisoformat(c)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            return dt.astimezone(JST)
        except Exception:
            pass
    for fmt in ["%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d", "%Y-%m-%d"]:
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=JST)
            return dt
        except Exception:
            pass
    return None


def file_embedded_generated_at(path: Path) -> Optional[datetime]:
    try:
        obj = load_json_file(path)
    except Exception:
        return None
    dt = parse_datetime_like(extract_generated_at_from_mapping(obj))
    if dt is not None:
        return dt
    # ルートに生成時刻がない形式でも、各rowに時刻が入っていればそれを使う。
    row_times = []
    for rec in flatten_records(obj):
        rdt = parse_datetime_like(extract_generated_at_from_mapping(rec))
        if rdt is not None:
            row_times.append(rdt)
    return max(row_times) if row_times else None


def file_priority(path: Path) -> Tuple[float, int, str]:
    """新しい日次スナップショットを優先。

    優先順位：
    1. JSON本文の generated_at 系時刻
    2. output/ を docs/latest より優先
    3. パス名で安定ソート

    GitHub Actionsのcheckout後mtimeはデータ鮮度を表さないため、
    優先順位にもフォールバックにも使わない。
    """
    embedded = file_embedded_generated_at(path)
    embedded_ts = embedded.timestamp() if embedded is not None else 0.0
    output_bonus = 1 if str(path).find(f"{os.sep}output{os.sep}") >= 0 else 0
    return (embedded_ts, output_bonus, rel_path(path))


def discover_daily_snapshot_files() -> List[Path]:
    """日次側JSON候補を広めに探す。

    固定名だけだと既存CIS側のファイル名ゆれでTV情報が全滅するため、
    output/ と docs/latest/ のJSONも補助的に見る。週次自身・status系は除外する。
    """
    candidates: List[Path] = []
    candidates.extend([p for p in DAILY_SNAPSHOT_CANDIDATES if p.exists()])
    for base in [OUTPUT_DIR, DOCS_LATEST_DIR]:
        if base.exists():
            candidates.extend(base.glob("*.json"))
    out: List[Path] = []
    seen = set()
    for p in candidates:
        name = p.name.lower()
        if "weekly" in name or "status" in name or "repair" in name:
            continue
        # 既存CIS側の日次ファイル名は固定とは限らない。
        # 例：us_stock_latest.json / japan_stock_latest.json / us_movers_latest.json なども拾う。
        include_tokens = [
            "daily", "watchlist", "performance", "rating", "snapshot", "market",
            "stock", "stocks", "movers", "us_", "jp_", "japan", "tokyo", "nasdaq", "nyse"
        ]
        if not any(k in name for k in include_tokens):
            continue
        rp = str(p.resolve())
        if rp not in seen:
            seen.add(rp)
            out.append(p)
    out.sort(key=file_priority, reverse=True)
    return out




def has_root_global_tv_marker(obj: Dict[str, Any]) -> bool:
    """JSONルートのTradingView明示が「全rowへ継承してよい」ものかを厳格判定する。

    v18では `sources: ["Yahoo", "TradingView"]` のような混合ソースでも
    ルートTV扱いになり、row内の汎用 `rating` / `avg_target_price` を
    TradingView情報として誤採用する余地があった。

    全rowへ継承してよいのは、rootの source/provider/rating_source 等が
    単一のTradingView系ソースを明示している場合に限定する。
    `sources` のような複数ソース一覧は継承根拠にしない。
    """
    if not isinstance(obj, dict):
        return False

    # まず、複数ソース一覧は「TradingViewも含む」だけでは全row継承不可。
    multi_sources = obj.get("sources") or obj.get("Sources") or obj.get("参照元一覧")
    if isinstance(multi_sources, (list, tuple, set)):
        return False

    # root直下/metadata直下などの単一ソース表記だけを採用。
    flat = flatten_dict_for_lookup(obj)
    allowed_source_keys = {
        "source", "provider", "data_source", "rating_source", "tv_source",
        "tradingview_source", "取得元"
    }
    disqualifiers = ["yfinance", "yahoo", "株予報", "minkabu", "ifis", "quick", "morningstar", "zacks", "tipranks", "marketbeat"]
    for k, v in flat.items():
        leaf = k.split(".")[-1]
        if leaf not in allowed_source_keys:
            continue
        if isinstance(v, (list, tuple, set, dict)):
            continue
        sv = str(v).lower().replace(" ", "")
        if "tradingview" not in sv and "trading_view" not in sv:
            continue
        # 混合ソース表記は不可。例：yfinance+TradingView / Yahoo, TradingView
        if any(x in sv for x in disqualifiers):
            continue
        if any(sep in sv for sep in [",", "、", "+", "/", "|"]):
            continue
        return True

    # 重要：root直下に tradingview オブジェクトがあるだけでは、全rowをTV由来とはみなさない。
    # 例：{sources:["Yahoo","TradingView"], tradingview:{...}, rows:[...]} や、
    # 価格はYahoo・評価はTVという混合JSONで、rowの汎用 rating/target を誤採用する事故を防ぐため。
    # 全rowへTV由来を継承するには、上の source/provider/rating_source 等で
    # 単一TradingViewソースが明示されている必要がある。
    return False



def non_empty(v: Any) -> bool:
    return v not in (None, "")


def merge_daily_records_preserving_newer_price(existing: Dict[str, Any], rating_rec: Dict[str, Any]) -> Dict[str, Any]:
    """日次JSON候補を統合する。

    候補ファイルは本文中の生成時刻で新しい順に読むため、先に入ったexistingは
    価格フォールバックとしては新しい可能性が高い。一方で、後続のrating_recにだけ
    TradingView情報がある場合、単純に置き換えるとexisting側の直近価格を失う。

    そこで、価格系フィールドは既存値を優先し、TradingView/メタデータ系は
    rating_recを優先して統合する。
    """
    combined = dict(existing or {})
    price_norms = {norm_key(k) for k in PRICE_FIELD_KEYS}
    for k, v in (rating_rec or {}).items():
        if not non_empty(v):
            continue
        nk = norm_key(str(k))
        if nk in price_norms and non_empty(combined.get(k)):
            continue
        combined[k] = v
    return combined

def load_latest_daily_rating_map() -> Dict[str, Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    existing = discover_daily_snapshot_files()
    for p in existing:
        try:
            obj = load_json_file(p)
            records = flatten_records(obj)
            generated_at = extract_root_generated_at(obj, p)
            root_tv_marker = has_root_global_tv_marker(obj) if isinstance(obj, dict) else False
            root_source_value = get_from_record(obj, "source", "sources", "rating_source", "provider", "data_source", "取得元") if isinstance(obj, dict) else None
        except Exception:
            continue
        for rec in records:
            raw_ticker = (
                get_from_record(rec, "ticker", "symbol", "code", "銘柄コード", "ティッカー")
                or rec.get("ticker") or rec.get("symbol") or rec.get("code") or ""
            )
            keys = ticker_lookup_keys(raw_ticker)
            if not keys:
                continue
            rec = dict(rec)
            # 日次JSONによっては、TradingView由来を示すsource/providerが
            # row単位ではなくJSONルートにだけ入っている。
            # その形式を落とすと、正しいTV情報が全滅するため、rowへ安全に継承する。
            if root_tv_marker and not has_tv_source_marker(rec) and not has_tv_specific_payload(rec):
                rec.setdefault("_root_tradingview_source", True)
                if root_source_value not in (None, ""):
                    rec.setdefault("_root_source", root_source_value)
            row_generated_at = extract_generated_at_from_mapping(rec)
            effective_generated_at = row_generated_at if row_generated_at != "unknown" else generated_at
            rec.setdefault("_daily_snapshot_file", rel_path(p))
            rec.setdefault("_daily_snapshot_generated_at", effective_generated_at)
            # 新しいファイルを優先。ただし、先に拾ったレコードにTV系項目がなく、
            # 後続候補にTV系項目がある場合は後続で補完する。
            # TV情報として扱うには、単なる汎用 rating だけでなく、TV系キーまたはアナリスト/目標株価を伴うことを条件にする。
            for key in keys:
                if key not in merged:
                    merged[key] = rec
                elif (not looks_like_rating_record(merged[key])) and looks_like_rating_record(rec):
                    # 後続候補にだけTV情報がある場合でも、先に拾った新しい価格フォールバックは失わない。
                    merged[key] = merge_daily_records_preserving_newer_price(merged[key], rec)
    return merged


def get_from_record(rec: Dict[str, Any], *keys: str) -> Any:
    nrec = flatten_dict_for_lookup(rec)
    for key in keys:
        nk = norm_key(key)
        if nk in nrec and nrec[nk] not in (None, ""):
            return nrec[nk]
    return None


def has_tv_source_marker(rec: Dict[str, Any]) -> bool:
    """レコード全体がTradingView由来だと明示されているか。

    重要：`tv_rating` / `tv_analyst_count` のようなTV系payloadがあるだけでは、
    row全体の汎用 `rating` / `avg_target_price` までTradingView由来とは限らない。
    そのため、ここでは source/provider/rating_source 等の明示、または
    安全に継承された `_root_tradingview_source` だけを見る。
    """
    nrec = flatten_dict_for_lookup(rec)
    root_marker = nrec.get(norm_key("_root_tradingview_source"))
    if root_marker is True or str(root_marker).lower() in {"true", "1", "yes"}:
        return True

    source_val = get_from_record(rec, "source", "rating_source", "provider", "data_source", "tv_source", "tradingview_source", "取得元")
    if source_val is None:
        return False
    if isinstance(source_val, (list, tuple, set, dict)):
        return False
    sv = str(source_val).lower().replace(" ", "")
    if "tradingview" not in sv and "trading_view" not in sv:
        return False
    # 混合ソース表記はrow全体のTV根拠にしない。
    disqualifiers = ["yfinance", "yahoo", "株予報", "minkabu", "ifis", "quick", "morningstar", "zacks", "tipranks", "marketbeat"]
    if any(x in sv for x in disqualifiers):
        return False
    if any(sep in sv for sep in [",", "、", "+", "/", "|"]):
        return False
    return True


def get_tv_rating_value(rec: Dict[str, Any]) -> Any:
    """TradingViewレーティングだけを取り出す。

    `tv_rating` / `tradingview.rating` のような専用キーは採用する。
    汎用 `rating` / `consensus` は、row全体がTradingView由来だと
    source/provider等で明示されている場合だけ採用する。
    これにより、買い場判定や独自レーティングをTV評価として誤表示する事故を防ぐ。
    """
    specific = get_from_record(
        rec,
        "tv_rating", "tradingview_rating", "trading_view_rating", "TradingViewレーティング",
        "tradingview.rating", "tradingview.consensus", "tv.rating", "tv.consensus",
    )
    if specific not in (None, ""):
        return specific
    if has_tv_source_marker(rec):
        return get_from_record(rec, "rating", "consensus", "analyst_rating", "アナリスト評価", "コンセンサス")
    return None

def has_tv_specific_payload(rec: Dict[str, Any]) -> bool:
    """TradingView系の明示フィールドを含むか。

    `analyst_count` や `avg_target_price` は他ソースでも使われるため、
    それだけではTradingView由来と判断しない。
    """
    nrec = flatten_dict_for_lookup(rec)
    tv_tokens = ("tradingview", "trading_view", "tv_")
    return any(any(token in key for token in tv_tokens) for key in nrec.keys())


def looks_like_rating_record(rec: Dict[str, Any]) -> bool:
    """TV/アナリスト情報を含む日次レコードかを判定する。

    汎用的な `rating` / `analyst_count` / `avg_target_price` だけでは、
    買い場判定・社内評価・Yahoo等の目標株価をTradingViewとして誤採用する危険がある。
    そのため、TradingView由来のsource marker、またはtv_/tradingview系の明示フィールドが
    ある場合だけTV情報として採用する。
    """
    if not rec:
        return False
    return bool(has_tv_source_marker(rec) or has_tv_specific_payload(rec))


def extract_rating(ticker: str, daily_map: Dict[str, Dict[str, Any]]) -> Tuple[str, Optional[int], Optional[float], str, str]:
    rec = next((daily_map.get(k) for k in ticker_lookup_keys(ticker) if daily_map.get(k) and looks_like_rating_record(daily_map.get(k) or {})), {})
    rating = get_tv_rating_value(rec)
    if has_tv_source_marker(rec):
        # row全体がTradingView由来と明示されている場合だけ、汎用名もTV値として許可する。
        analysts = get_from_record(
            rec,
            "analyst_count", "analysts", "tv_analyst_count", "rating_count", "number_of_analysts",
            "アナリスト人数", "アナリスト数", "tradingview.analyst_count", "tradingview.analysts", "tv.analyst_count",
        )
        target = get_from_record(
            rec,
            "avg_target_price", "average_target_price", "target_mean_price", "targetMeanPrice",
            "mean_target", "tv_avg_target", "price_target_average", "average_price_target",
            "平均目標株価", "目標株価平均", "平均ターゲット", "tradingview.avg_target_price",
            "tradingview.average_target_price", "tradingview.targetMeanPrice", "tv.avg_target_price",
        )
    else:
        # `tv_rating` 等の専用payloadだけがある混合rowでは、汎用目標株価をTV平均として扱わない。
        analysts = get_from_record(
            rec,
            "tv_analyst_count", "tradingview_analyst_count", "trading_view_analyst_count",
            "tradingview.analyst_count", "tradingview.analysts", "tv.analyst_count", "tv.analysts",
        )
        target = get_from_record(
            rec,
            "tv_avg_target", "tv_average_target_price", "tradingview_avg_target_price",
            "tradingview_average_target_price", "trading_view_avg_target_price",
            "tradingview.avg_target_price", "tradingview.average_target_price",
            "tradingview.targetMeanPrice", "tv.avg_target_price", "tv.average_target_price",
        )
    rating_text = str(rating).strip() if rating not in (None, "") else "未取得"
    source = str(rec.get("_daily_snapshot_file", "daily_snapshot")) if rec else "not_found"
    generated_at = str(rec.get("_daily_snapshot_generated_at", "unknown")) if rec else "unknown"
    return rating_text, to_int(analysts), to_float(target), source, generated_at

PRICE_FIELD_KEYS = (
    "current_price", "latest_price", "last_price", "close", "adj_close", "price",
    "regular_market_price", "regularMarketPrice", "current", "last",
    "終値", "現在値", "直近終値", "最新価格"
)


def extract_price_value(rec: Dict[str, Any]) -> Optional[float]:
    return to_float(get_from_record(rec, *PRICE_FIELD_KEYS))


def extract_price_from_daily(ticker: str, daily_map: Dict[str, Dict[str, Any]]) -> Optional[float]:
    """日次スナップショット内の直近価格をフォールバックとして読む。

    週次価格差を計算するには1週間前価格が必要なので、これだけでは週間騰落は出せない。
    ただし平均目標株価乖離率の計算や現在価格の記録には使える。

    注意：daily_mapはTV情報入りレコードを優先するため、同じ銘柄の価格だけを持つ
    日次JSONが別ファイルにある場合、価格フィールドが落ちる可能性がある。
    そのため、daily_mapで見つからない場合は日次候補JSON全体を再走査する。
    """
    for k in ticker_lookup_keys(ticker):
        rec = daily_map.get(k)
        if rec:
            price = extract_price_value(rec)
            if price is not None:
                return price

    wanted = set(ticker_lookup_keys(ticker))
    for p in discover_daily_snapshot_files():
        try:
            obj = load_json_file(p)
            records = flatten_records(obj)
        except Exception:
            continue
        for rec in records:
            raw_ticker = (
                get_from_record(rec, "ticker", "symbol", "code", "銘柄コード", "ティッカー")
                or rec.get("ticker") or rec.get("symbol") or rec.get("code") or ""
            )
            if not wanted.intersection(ticker_lookup_keys(raw_ticker)):
                continue
            price = extract_price_value(rec)
            if price is not None:
                return price
    return None


def fetch_week_prices(item: WatchItem, daily_map: Optional[Dict[str, Dict[str, Any]]] = None) -> Tuple[Optional[float], Optional[float], str]:
    if yf is None:
        fallback_current = extract_price_from_daily(item.ticker, daily_map or {})
        return fallback_current, None, "yfinance_not_installed;week_ago_unavailable"
    symbol = yf_symbol(item)
    try:
        hist = yf.Ticker(symbol).history(period="10d", interval="1d", auto_adjust=False)
        if hist is None or hist.empty or "Close" not in hist:
            fallback_current = extract_price_from_daily(item.ticker, daily_map or {})
            return fallback_current, None, "price_not_found;week_ago_unavailable"
        closes = [float(x) for x in hist["Close"].dropna().tolist()]
        if len(closes) < 2:
            fallback_current = extract_price_from_daily(item.ticker, daily_map or {}) or (closes[-1] if closes else None)
            return fallback_current, None, "insufficient_price_history;week_ago_unavailable"
        current = closes[-1]
        # 5営業日前があればそれを使い、なければ取得範囲内の最古値。
        # 10d取得にしているため、祝日を挟んでも多くの場合6本以上残る。
        week_ago = closes[-6] if len(closes) >= 6 else closes[0]
        return current, week_ago, "ok"
    except Exception as e:
        fallback_current = extract_price_from_daily(item.ticker, daily_map or {})
        return fallback_current, None, f"price_error:{type(e).__name__};week_ago_unavailable"


def pct(current: Optional[float], base: Optional[float]) -> Optional[float]:
    if current is None or base is None or base == 0:
        return None
    return (current - base) / base * 100.0


def build_rows() -> List[WeeklyRow]:
    items = load_watchlist()
    if not items:
        raise ValueError("監視リストに有効な銘柄がありません。watchlist_master.csv の ticker/enabled を確認してください。")
    daily_map = load_latest_daily_rating_map()
    rows: List[WeeklyRow] = []
    for item in items:
        market = detect_market(item)
        current, week_ago, price_status = fetch_week_prices(item, daily_map)
        if market == "US":
            tv_rating, analyst_count, avg_target, rating_source, rating_generated_at = extract_rating(item.ticker, daily_map)
        else:
            # 日本株はCIS方針としてTradingViewレーティング不要。
            # 週次ではシンプルに週間騰落率だけを表示し、TV未取得を警告・partial要因にしない。
            tv_rating, analyst_count, avg_target = "対象外", None, None
            rating_source, rating_generated_at = "jp_not_applicable", "not_applicable"
        change = None if current is None or week_ago is None else current - week_ago
        change_pct = pct(current, week_ago)
        upside = pct(avg_target, current) if avg_target is not None and current is not None else None
        status_parts = []
        if price_status != "ok":
            status_parts.append(price_status)
        if market == "US" and rating_source == "not_found":
            status_parts.append("rating_snapshot_not_found")
        if market == "US":
            display_fields = [
                "ticker_description", "weekly_change_pct", "weekly_change",
                "tradingview_rating", "analyst_count", "avg_target_price", "upside_to_avg_target_pct"
            ]
            hidden_fields_by_policy: List[str] = []
        else:
            display_fields = ["ticker_description", "weekly_change_pct"]
            hidden_fields_by_policy = [
                "weekly_change", "tradingview_rating", "analyst_count",
                "avg_target_price", "upside_to_avg_target_pct"
            ]
        rows.append(WeeklyRow(
            ticker=item.ticker,
            label=item.name or item.ticker,
            description=item.description,
            market=market,
            display_fields=display_fields,
            hidden_fields_by_policy=hidden_fields_by_policy,
            current_price=current,
            week_ago_price=week_ago,
            weekly_change=change,
            weekly_change_pct=change_pct,
            tv_rating=tv_rating,
            analyst_count=analyst_count,
            avg_target_price=avg_target,
            upside_to_avg_target_pct=upside,
            data_status="ok" if not status_parts else ";".join(status_parts),
            rating_source=rating_source,
            rating_snapshot_generated_at=rating_generated_at,
        ))
    # Noneは最後。0.00%を falsy 扱いするとマイナス銘柄より下に落ちるため、明示分岐する。
    rows.sort(key=lambda r: (r.weekly_change_pct is None, -r.weekly_change_pct if r.weekly_change_pct is not None else 10**9, r.ticker))
    return rows


def fmt_num(v: Optional[float], digits: int = 2, prefix: str = "", suffix: str = "") -> str:
    if v is None:
        return "未取得"
    sign = "+" if v > 0 and prefix == "" else ""
    return f"{sign}{prefix}{v:,.{digits}f}{suffix}"


def fmt_price(v: Optional[float], market: str) -> str:
    if v is None:
        return "未取得"
    amount = abs(v)
    sign = "-" if v < 0 else ""
    return f"{sign}¥{amount:,.0f}" if market == "JP" else f"{sign}${amount:,.2f}"


def fmt_diff(v: Optional[float], market: str) -> str:
    if v is None:
        return "未取得"
    amount = abs(v)
    sign = "+" if v > 0 else "-" if v < 0 else ""
    return f"{sign}¥{amount:,.0f}" if market == "JP" else f"{sign}${amount:,.2f}"


def render_markdown(rows: List[WeeklyRow]) -> str:
    now = datetime.now(JST)
    lines: List[str] = []
    lines.append("# CIS 週間騰落｜全監視銘柄")
    lines.append("")
    lines.append(f"実行日：{now:%Y/%m/%d}（JST）")
    lines.append("並び順：週間騰落率が高い順")
    lines.append("米国株：週間騰落＋日次保存済みTradingView情報を表示")
    lines.append("日本株：週間騰落率のみ表示（TradingViewは対象外）")
    price_missing_count = sum(1 for r in rows if r.weekly_change_pct is None)
    us_rows = [r for r in rows if r.market == "US"]
    jp_rows = [r for r in rows if r.market == "JP"]
    us_rating_missing_count = sum(1 for r in us_rows if r.tv_rating == "未取得")
    us_complete_tv_count = sum(1 for r in us_rows if r.tv_rating != "未取得" and r.analyst_count is not None and r.avg_target_price is not None)
    us_incomplete_tv_count = len(us_rows) - us_complete_tv_count
    us_rating_unknown_freshness = sorted({
        r.rating_source for r in us_rows
        if r.rating_source not in {"", "not_found", "jp_not_applicable"}
        and parse_datetime_like(r.rating_snapshot_generated_at) is None
    })
    us_rating_stale_sources = []
    for r in us_rows:
        if r.rating_source in {"", "not_found", "jp_not_applicable"}:
            continue
        dt = parse_datetime_like(r.rating_snapshot_generated_at)
        if dt is not None:
            age_days = (now - dt).total_seconds() / 86400
            if age_days > MAX_US_RATING_SOURCE_AGE_DAYS:
                us_rating_stale_sources.append(r.rating_source)
    us_rating_stale_sources = sorted(set(us_rating_stale_sources))
    if price_missing_count or us_rating_missing_count or us_rating_unknown_freshness or us_rating_stale_sources:
        lines.append(
            f"取得状況：週間騰落未取得 {price_missing_count}件 / "
            f"米国株TVレーティング未取得 {us_rating_missing_count}/{len(us_rows)}件 / "
            f"米国株TV 3項目不足 {us_incomplete_tv_count}/{len(us_rows)}件 / "
            f"日本株TV対象外 {len(jp_rows)}件"
        )
        if rows and ((len(rows) - price_missing_count) / len(rows)) < MIN_PRICE_SUCCESS_RATIO:
            lines.append("警告：週間価格の取得成功率が低すぎるため、この回のレポートは要確認です。")
        if us_rows and us_rating_missing_count == len(us_rows):
            lines.append("警告：米国株のTradingViewレーティングが全銘柄未取得です。日次騰落側でTradingViewスナップショット保存が失敗しています。scripts/cis_validate_daily_tv_snapshot.py を日次Actionsに追加してください。")
        if us_rows and us_complete_tv_count / len(us_rows) < 0.70:
            lines.append("警告：米国株のTradingView 3項目（レーティング・アナリスト人数・平均目標株価）の保存率が低すぎます。日次JSONのTV保存仕様を確認してください。")
        if us_rating_unknown_freshness:
            lines.append("警告：米国株TV参照元の日次JSONに生成時刻が無いため、鮮度を確認できません。")
        if us_rating_stale_sources:
            lines.append(f"警告：米国株TV参照元が{MAX_US_RATING_SOURCE_AGE_DAYS}日超古い可能性があります。")
    source_files = sorted({r.rating_source for r in rows if r.rating_source and r.rating_source not in {"not_found", "jp_not_applicable"}})
    if source_files:
        lines.append("TV参照元：" + ", ".join(source_files[:5]) + (" ほか" if len(source_files) > 5 else ""))
    lines.append("")
    lines.append("---")
    lines.append("")
    for i, r in enumerate(rows, start=1):
        lines.append(f"## {i}. {r.ticker}｜{r.description}")
        lines.append(f"- 週間騰落率：{fmt_num(r.weekly_change_pct, 2, suffix='%')}")
        if r.market == "US":
            lines.append(f"- 週間価格差：{fmt_diff(r.weekly_change, r.market)}")
            lines.append(f"- TradingViewレーティング：{r.tv_rating}")
            lines.append(f"- アナリスト人数：{r.analyst_count if r.analyst_count is not None else '未取得'}人" if r.analyst_count is not None else "- アナリスト人数：未取得")
            lines.append(f"- 平均目標株価：{fmt_price(r.avg_target_price, r.market)}")
            lines.append(f"- 現在価格→平均目標株価乖離率：{fmt_num(r.upside_to_avg_target_pct, 2, suffix='%')}")
        # 日本株はここで終了。価格差/TradingView/アナリスト/目標株価は表示しない。
        if r.data_status != "ok":
            lines.append(f"- 取得メモ：{r.data_status}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(rows: List[WeeklyRow]) -> Dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_LATEST_DIR.mkdir(parents=True, exist_ok=True)
    price_missing_count = sum(1 for r in rows if r.weekly_change_pct is None)
    us_rows = [r for r in rows if r.market == "US"]
    jp_rows = [r for r in rows if r.market == "JP"]
    us_rating_missing_count = sum(1 for r in us_rows if r.tv_rating == "未取得")
    us_complete_tv_count = sum(1 for r in us_rows if r.tv_rating != "未取得" and r.analyst_count is not None and r.avg_target_price is not None)
    us_complete_tv_ratio = (us_complete_tv_count / len(us_rows)) if us_rows else 1.0
    us_incomplete_tv_count = len(us_rows) - us_complete_tv_count
    # 日本株はTradingView対象外。全体のrating_missing_countに混ぜると、
    # 日本株が多いだけでpartial/errorに寄るため、米国株だけで数える。
    rating_missing_count = us_rating_missing_count
    rating_sources = sorted({r.rating_source for r in rows if r.rating_source and r.rating_source not in {"not_found", "jp_not_applicable"}})
    rating_source_details = []
    for src in rating_sources:
        generated_values = sorted({r.rating_snapshot_generated_at for r in rows if r.rating_source == src and r.rating_snapshot_generated_at})
        rating_source_details.append({"file": src, "generated_at_candidates": generated_values[:5]})
    now_jst = datetime.now(JST)
    stale_rating_sources = []
    for detail in rating_source_details:
        parsed = [parse_datetime_like(x) for x in detail.get("generated_at_candidates", [])]
        parsed = [x for x in parsed if x is not None]
        if parsed:
            newest = max(parsed)
            age_days = (now_jst - newest).total_seconds() / 86400
            detail["newest_generated_at_jst"] = newest.isoformat()
            detail["age_days"] = round(age_days, 2)
            if age_days > MAX_US_RATING_SOURCE_AGE_DAYS:
                stale_rating_sources.append(detail["file"])
        else:
            detail["newest_generated_at_jst"] = "unknown"
            detail["age_days"] = None
    price_success_count = len(rows) - price_missing_count
    price_success_ratio = (price_success_count / len(rows)) if rows else 0.0

    quality_errors = []
    if len(rows) > 0 and price_success_count == 0:
        quality_errors.append("no_weekly_price_rows")
    elif len(rows) > 0 and price_success_ratio < MIN_PRICE_SUCCESS_RATIO:
        quality_errors.append("too_many_weekly_price_missing")
    # TradingViewレーティングの品質判定は米国株を主対象にする。
    # 日本株はCIS方針上、TradingViewを必須にしない運用があるため、
    # 全銘柄一律で判定すると日本株混在時に誤った fatal/error になりやすい。
    if len(us_rows) > 0 and us_rating_missing_count == len(us_rows):
        quality_errors.append("all_us_tradingview_ratings_missing")
    if len(us_rows) > 0 and us_complete_tv_ratio < 0.70:
        quality_errors.append("too_many_incomplete_us_tradingview_snapshots")
    if len(us_rows) > 0 and stale_rating_sources:
        quality_errors.append("stale_us_tradingview_rating_sources")
    if len(us_rows) > 0 and rating_sources:
        unknown_freshness_sources = [
            d["file"] for d in rating_source_details
            if d.get("newest_generated_at_jst") == "unknown"
        ]
        if unknown_freshness_sources:
            quality_errors.append("unknown_us_tradingview_rating_source_freshness")
    else:
        unknown_freshness_sources = []

    fatal_error = bool(quality_errors)
    if fatal_error:
        status_text = "error"
    elif price_missing_count == 0 and rating_missing_count == 0 and not stale_rating_sources:
        status_text = "ok"
    else:
        status_text = "partial"
    status = {
        "generated_at_jst": now_jst.isoformat(),
        "status": status_text,
        "row_count": len(rows),
        "price_success_count": price_success_count,
        "price_success_ratio": round(price_success_ratio, 4),
        "min_price_success_ratio": MIN_PRICE_SUCCESS_RATIO,
        "price_missing_count": price_missing_count,
        "rating_missing_count": rating_missing_count,
        "us_rating_applicable_count": len(us_rows),
        "us_rating_missing_count": us_rating_missing_count,
        "us_complete_tv_count": us_complete_tv_count,
        "us_complete_tv_ratio": round(us_complete_tv_ratio, 4),
        "us_incomplete_tv_count": us_incomplete_tv_count,
        "jp_rating_not_applicable_count": len(jp_rows),
        "rating_quality_policy": "TradingView is required/evaluated only for US rows. JP rows are intentionally treated as rating_not_applicable and do not affect rating quality.",
        "rating_source_files": rating_sources,
        "rating_source_details": rating_source_details,
        "max_us_rating_source_age_days": MAX_US_RATING_SOURCE_AGE_DAYS,
        "stale_rating_source_files": stale_rating_sources,
        "unknown_freshness_rating_source_files": unknown_freshness_sources,
        "quality_errors": quality_errors,
        "fatal_error": fatal_error,
    }
    payload = {
        **status,
        "sort": "weekly_change_pct_desc",
        "rating_policy": "US: TradingView rating is reused from latest daily snapshot; weekly module does not fetch TradingView by default. Daily workflow must save and validate TradingView snapshots with scripts/cis_validate_daily_tv_snapshot.py. JP: weekly output shows weekly change rate only; TradingView rating is not used.",
        "display_policy": {
            "US": [
                "ticker_description", "weekly_change_pct", "weekly_change",
                "tradingview_rating", "analyst_count", "avg_target_price", "upside_to_avg_target_pct"
            ],
            "JP": ["ticker_description", "weekly_change_pct"],
            "note": "Dashboard/renderers should use display_fields per row. JP rows intentionally hide price difference and all TradingView-related fields even if internal calculation fields exist."
        },
        "rows": [asdict(r) for r in rows],
    }
    json_path = OUTPUT_DIR / "weekly_performance_latest.json"
    md_path = OUTPUT_DIR / "weekly_performance_latest.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(rows), encoding="utf-8")
    status_path = OUTPUT_DIR / "weekly_performance_status.json"
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copy2(json_path, DOCS_LATEST_DIR / json_path.name)
    shutil.copy2(md_path, DOCS_LATEST_DIR / md_path.name)
    shutil.copy2(status_path, DOCS_LATEST_DIR / "weekly_performance_status_latest.json")
    return status


def write_exception_outputs(exc: Exception) -> Dict[str, Any]:
    """例外発生時も latest 本体をエラー表示で上書きする。

    旧版では status だけを更新していたため、GitHub Pages/ダッシュボードが
    `weekly_performance_latest.md/json` だけを読む構成だと、前回の正常レポートが
    そのまま残り「今回も正常に更新された」ように見える危険があった。
    失敗時こそ latest 本体をエラー内容で上書きし、古いレポートの誤表示を防ぐ。
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_LATEST_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(JST)
    err = {
        "generated_at_jst": now.isoformat(),
        "status": "error",
        "fatal_error": True,
        "row_count": 0,
        "error_type": type(exc).__name__,
        "error": str(exc),
        "quality_errors": ["exception_during_weekly_generation"],
        "rows": [],
    }
    md = (
        "# CIS 週間騰落｜生成失敗\n\n"
        f"実行日：{now:%Y/%m/%d %H:%M}（JST）\n\n"
        "この回の週間騰落は生成に失敗しました。前回レポートを最新として表示しないでください。\n\n"
        f"- エラー種別：{type(exc).__name__}\n"
        f"- エラー内容：{str(exc)}\n"
    )
    paths = {
        OUTPUT_DIR / "weekly_performance_status.json": err,
        DOCS_LATEST_DIR / "weekly_performance_status_latest.json": err,
        OUTPUT_DIR / "weekly_performance_latest.json": err,
        DOCS_LATEST_DIR / "weekly_performance_latest.json": err,
    }
    for path, payload in paths.items():
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for path in [OUTPUT_DIR / "weekly_performance_latest.md", DOCS_LATEST_DIR / "weekly_performance_latest.md"]:
        path.write_text(md, encoding="utf-8")
    return err


def main() -> int:
    try:
        rows = build_rows()
        status = write_outputs(rows)
        print(f"weekly performance generated: {len(rows)} rows; status={status.get('status')}")
        if status.get("fatal_error"):
            print("weekly performance fatal: data quality check failed", file=sys.stderr)
            return 1
        return 0
    except Exception as e:
        err = write_exception_outputs(e)
        print(json.dumps(err, ensure_ascii=False), file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
