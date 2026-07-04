#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CIS Daily TradingView Snapshot Validator

日次騰落側の「最新」JSONに、米国株向けTradingViewスナップショットが保存されているかを検証する。
週次モジュールはTradingViewを再取得しない設計なので、日次側でTV情報が保存されないと
週次の米国株レーティング欄が全滅する。これを日次更新時点で検知する品質ゲート。

重要：
- GitHub Actions の mtime は使わない。
- 複数の古い日次JSONを全部合算しない。最新の日次JSON 1つを選んで検証する。
- output と docs/latest に同一内容がある場合は output を優先する。
- weekly/status/guard 系JSONは検証対象から除外する。

終了コード：
  0: OK
  1: 品質エラー
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output"
DOCS_LATEST_DIR = ROOT / "docs" / "latest"

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

TV_RATING_KEYS = ["tv_rating", "tradingview_rating", "trading_view_rating", "TradingViewレーティング"]
TV_ANALYST_KEYS = ["tv_analyst_count", "tradingview_analyst_count", "TradingViewアナリスト人数"]
TV_TARGET_KEYS = ["tv_avg_target_price", "tv_average_target_price", "tradingview_avg_target_price", "tradingview_average_target_price", "TradingView平均目標株価"]
GEN_KEYS = [
    "generated_at_jst", "generated_at", "snapshot_generated_at", "data_generated_at",
    "data_time", "updated_at", "実行日時", "生成日時", "データ取得日時",
]

EXCLUDE_NAME_TOKENS = [
    "weekly", "status", "guard", "validation", "validate", "schema", "config", "backup",
]
INCLUDE_NAME_TOKENS = ["daily", "watchlist", "performance", "snapshot", "stock", "market"]

@dataclass
class Candidate:
    path: Path
    payload: Any
    rows: List[Dict[str, Any]]
    us_rows: List[Dict[str, Any]]
    generated_at: Optional[datetime]
    priority: int


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except Exception:
        return str(path)


def read_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def iter_records(payload: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(payload, list):
        for x in payload:
            if isinstance(x, dict):
                yield x
        return
    if not isinstance(payload, dict):
        return
    for key in ["rows", "items", "data", "records", "watchlist", "stocks", "results"]:
        val = payload.get(key)
        if isinstance(val, list):
            for x in val:
                if isinstance(x, dict):
                    yield x
            return
    if any(k in payload for k in ["ticker", "symbol", "銘柄"]):
        yield payload
        return
    for v in payload.values():
        if isinstance(v, dict) and any(k in v for k in ["ticker", "symbol", "銘柄", "tv_rating", "tradingview"]):
            yield v


def find_value(d: Dict[str, Any], keys: List[str]) -> Optional[Any]:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def parse_datetime_like(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            if value > 10_000_000_000:
                value = value / 1000
            return datetime.fromtimestamp(float(value), tz=timezone.utc).astimezone(JST)
        except Exception:
            return None
    s = str(value).strip()
    if not s:
        return None
    s = re.sub(r"[（(][月火水木金土日][）)]", "", s)
    s = s.replace("JST", "+09:00").replace("Z", "+00:00")
    s = s.replace("年", "/").replace("月", "/").replace("日", " ")
    s = re.sub(r"\s+", " ", s).strip()
    candidates = [s, s.replace("/", "-"), s.replace(" ", "T")]
    fmts = [
        "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    ]
    for c in candidates:
        try:
            dt = datetime.fromisoformat(c)
            return dt.replace(tzinfo=JST) if dt.tzinfo is None else dt.astimezone(JST)
        except Exception:
            pass
    for c in candidates:
        for fmt in fmts:
            try:
                return datetime.strptime(c, fmt).replace(tzinfo=JST)
            except Exception:
                pass
    return None


def generated_at_from_payload_or_row(payload: Any, row: Optional[Dict[str, Any]] = None) -> Optional[datetime]:
    if isinstance(payload, dict):
        dt = parse_datetime_like(find_value(payload, GEN_KEYS))
        if dt:
            return dt
    if row:
        dt = parse_datetime_like(find_value(row, GEN_KEYS))
        if dt:
            return dt
    return None


def candidate_generated_at(payload: Any, rows: List[Dict[str, Any]]) -> Optional[datetime]:
    dt = generated_at_from_payload_or_row(payload)
    if dt:
        return dt
    row_times = [generated_at_from_payload_or_row(payload, r) for r in rows]
    row_times = [x for x in row_times if x]
    return max(row_times) if row_times else None


def market_of(row: Dict[str, Any]) -> str:
    ticker = str(row.get("ticker") or row.get("symbol") or row.get("銘柄") or "").upper()
    market = str(row.get("market") or row.get("exchange") or row.get("市場") or "").upper()
    if ticker.endswith(".T") or ticker.endswith(".JP") or market in JP_MARKET_HINTS:
        return "JP"
    if ticker and re.fullmatch(r"\d{4}", ticker):
        return "JP"
    if market in US_MARKET_HINTS:
        return "US"
    return "US"


def source_is_single_tradingview(obj: Dict[str, Any]) -> bool:
    vals: List[str] = []
    for key in ["source", "provider", "rating_source", "data_source"]:
        v = obj.get(key)
        if isinstance(v, str):
            vals.append(v)
    if not vals:
        return False
    joined = " ".join(vals).lower()
    if "tradingview" not in joined and "trading view" not in joined:
        return False
    mixed_tokens = ["yfinance", "yahoo", "株予報", "minkabu", "みんかぶ", "ifis", "quick", "morningstar", "zacks", "tipranks", "marketbeat", "+", ",", "/"]
    return not any(tok in joined for tok in mixed_tokens)


def root_single_tv_source(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if isinstance(payload.get("sources"), list):
        return False
    if isinstance(payload.get("source"), list):
        return False
    return source_is_single_tradingview(payload)


def row_has_tv_snapshot(row: Dict[str, Any], root_single_tv: bool) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    tv_obj = row.get("tradingview")
    if isinstance(tv_obj, dict):
        if find_value(tv_obj, ["rating", "consensus", "レーティング"]):
            reasons.append("tradingview.rating")
        if find_value(tv_obj, ["analyst_count", "アナリスト人数"]):
            reasons.append("tradingview.analyst_count")
        if find_value(tv_obj, ["avg_target_price", "average_target_price", "平均目標株価"]):
            reasons.append("tradingview.avg_target_price")
    if find_value(row, TV_RATING_KEYS):
        reasons.append("tv_rating")
    if find_value(row, TV_ANALYST_KEYS):
        reasons.append("tv_analyst_count")
    if find_value(row, TV_TARGET_KEYS):
        reasons.append("tv_avg_target_price")
    if root_single_tv or source_is_single_tradingview(row):
        if find_value(row, ["rating", "consensus", "レーティング"]):
            reasons.append("single_tv_source.rating")
        if find_value(row, ["analyst_count", "アナリスト人数"]):
            reasons.append("single_tv_source.analyst_count")
        if find_value(row, ["avg_target_price", "average_target_price", "平均目標株価"]):
            reasons.append("single_tv_source.avg_target_price")
    return bool(reasons), reasons



def row_tv_completeness(row: Dict[str, Any], root_single_tv: bool) -> Dict[str, bool]:
    """TradingViewスナップショットとして、表示に必要な3要素が揃っているか。

    週次の米国株カードは rating / analyst_count / avg_target_price / upside を出す。
    v26までは tv_rating だけでも「TV保存あり」と判定でき、
    Actionsは成功するのにアナリスト人数・平均目標株価が全滅する事故を見逃し得た。
    """
    tv_obj = row.get("tradingview") if isinstance(row.get("tradingview"), dict) else {}
    has_rating = bool(
        find_value(row, TV_RATING_KEYS)
        or find_value(tv_obj, ["rating", "consensus", "レーティング"])
        or ((root_single_tv or source_is_single_tradingview(row)) and find_value(row, ["rating", "consensus", "レーティング"]))
    )
    has_analysts = bool(
        find_value(row, TV_ANALYST_KEYS)
        or find_value(tv_obj, ["analyst_count", "analysts", "アナリスト人数", "アナリスト数"])
        or ((root_single_tv or source_is_single_tradingview(row)) and find_value(row, ["analyst_count", "analysts", "アナリスト人数", "アナリスト数"]))
    )
    has_target = bool(
        find_value(row, TV_TARGET_KEYS)
        or find_value(tv_obj, ["avg_target_price", "average_target_price", "targetMeanPrice", "平均目標株価"])
        or ((root_single_tv or source_is_single_tradingview(row)) and find_value(row, ["avg_target_price", "average_target_price", "targetMeanPrice", "平均目標株価"]))
    )
    return {"rating": has_rating, "analyst_count": has_analysts, "avg_target_price": has_target, "complete": has_rating and has_analysts and has_target}

def discover_files() -> List[Path]:
    out: List[Path] = []
    seen = set()
    for p in DAILY_SNAPSHOT_CANDIDATES:
        if p.exists() and p not in seen:
            out.append(p); seen.add(p)
    for base in [OUTPUT_DIR, DOCS_LATEST_DIR]:
        if not base.exists():
            continue
        for p in sorted(base.glob("*.json"), key=lambda x: str(x)):
            name = p.name.lower()
            if any(tok in name for tok in EXCLUDE_NAME_TOKENS):
                continue
            if any(tok in name for tok in INCLUDE_NAME_TOKENS):
                if p not in seen:
                    out.append(p); seen.add(p)
    return out


def file_priority(path: Path) -> int:
    name = path.name.lower()
    score = 0
    if path.is_relative_to(OUTPUT_DIR):
        score += 100
    if name in {"us_daily_performance_latest.json", "us_watchlist_daily_latest.json"}:
        score += 50
    elif name in {"daily_performance_latest.json", "watchlist_daily_latest.json"}:
        score += 40
    if "us" in name:
        score += 10
    if "latest" in name:
        score += 5
    return score


def build_candidates() -> List[Candidate]:
    candidates: List[Candidate] = []
    for p in discover_files():
        payload = read_json(p)
        if payload is None:
            continue
        rows = list(iter_records(payload))
        us_rows = [r for r in rows if market_of(r) == "US"]
        if not us_rows:
            continue
        candidates.append(Candidate(
            path=p,
            payload=payload,
            rows=rows,
            us_rows=us_rows,
            generated_at=candidate_generated_at(payload, rows),
            priority=file_priority(p),
        ))
    return candidates


def select_latest_candidate(candidates: List[Candidate]) -> Optional[Candidate]:
    if not candidates:
        return None
    dated = [c for c in candidates if c.generated_at]
    if dated:
        # JSON本文の生成時刻が最優先。mtimeは絶対に使わない。
        return sorted(dated, key=lambda c: (c.generated_at, c.priority, rel(c.path)), reverse=True)[0]
    # 生成時刻が全く無い場合でも、検証不能として失敗詳細を出すため1件は選ぶ。
    return sorted(candidates, key=lambda c: (c.priority, rel(c.path)), reverse=True)[0]


def validate(min_us_tv_ratio: float, max_age_days: int) -> Dict[str, Any]:
    now = datetime.now(JST)
    candidates = build_candidates()
    selected = select_latest_candidate(candidates)
    quality_errors: List[str] = []

    if not selected:
        quality_errors.append("no_us_rows_in_daily_snapshot")
        result = {
            "generated_at_jst": now.isoformat(),
            "status": "error",
            "quality_errors": quality_errors,
            "min_us_tv_ratio": min_us_tv_ratio,
            "max_age_days": max_age_days,
            "selected_file": None,
            "selected_file_generated_at_jst": None,
            "us_rows": 0,
            "us_rows_with_tradingview": 0,
            "us_tradingview_ratio": 0.0,
            "candidate_files": [],
            "missing_examples": [],
            "rows_with_tv_detail_sample": [],
            "note": "No daily JSON with US rows was found. weekly/status/guard files are intentionally ignored.",
        }
        return result

    root_single_tv = root_single_tv_source(selected.payload)
    us_total = len(selected.us_rows)
    us_with_tv = 0
    us_with_complete_tv = 0
    incomplete_examples = []
    missing_examples = []
    rows_with_tv_detail = []
    unknown_freshness_rows = []
    stale_rows = []

    for row in selected.us_rows:
        has_tv, reasons = row_has_tv_snapshot(row, root_single_tv)
        ticker = row.get("ticker") or row.get("symbol") or row.get("銘柄")
        completeness = row_tv_completeness(row, root_single_tv)
        if completeness["complete"]:
            us_with_complete_tv += 1
        elif len(incomplete_examples) < 20:
            incomplete_examples.append({"ticker": ticker, "file": rel(selected.path), "fields": completeness})
        if has_tv:
            us_with_tv += 1
            dt = generated_at_from_payload_or_row(selected.payload, row)
            if not dt:
                unknown_freshness_rows.append(str(ticker))
                generated = "unknown"
            else:
                generated = dt.isoformat()
                age = (now - dt).total_seconds() / 86400
                if age > max_age_days:
                    stale_rows.append(str(ticker))
            rows_with_tv_detail.append({
                "ticker": ticker,
                "file": rel(selected.path),
                "detected_by": reasons[:5],
                "generated_at_jst": generated,
            })
        else:
            if len(missing_examples) < 20:
                missing_examples.append({"ticker": ticker, "file": rel(selected.path)})

    ratio = (us_with_tv / us_total) if us_total else 0.0
    complete_ratio = (us_with_complete_tv / us_total) if us_total else 0.0
    if us_total == 0:
        quality_errors.append("no_us_rows_in_daily_snapshot")
    elif us_with_tv == 0:
        quality_errors.append("no_us_tradingview_snapshot_saved")
    elif ratio < min_us_tv_ratio:
        quality_errors.append("too_many_us_tradingview_snapshots_missing")
    if us_total > 0 and complete_ratio < min_us_tv_ratio:
        quality_errors.append("too_many_incomplete_us_tradingview_snapshots")
    if selected.generated_at is None:
        quality_errors.append("daily_snapshot_generated_at_missing")
    if unknown_freshness_rows:
        quality_errors.append("tradingview_snapshot_generated_at_missing")
    if stale_rows:
        quality_errors.append("tradingview_snapshot_stale")

    status = "ok" if not quality_errors else "error"
    return {
        "generated_at_jst": now.isoformat(),
        "status": status,
        "quality_errors": quality_errors,
        "min_us_tv_ratio": min_us_tv_ratio,
        "max_age_days": max_age_days,
        "selected_file": rel(selected.path),
        "selected_file_generated_at_jst": selected.generated_at.isoformat() if selected.generated_at else None,
        "selection_policy": "Validate only the freshest daily US snapshot by JSON generated_at. Do not aggregate old files. Do not use GitHub mtime.",
        "us_rows": us_total,
        "us_rows_with_tradingview": us_with_tv,
        "us_tradingview_ratio": round(ratio, 4),
        "us_rows_with_complete_tradingview": us_with_complete_tv,
        "us_complete_tradingview_ratio": round(complete_ratio, 4),
        "candidate_files": [
            {
                "file": rel(c.path),
                "us_rows": len(c.us_rows),
                "generated_at_jst": c.generated_at.isoformat() if c.generated_at else None,
                "priority": c.priority,
            }
            for c in sorted(candidates, key=lambda c: ((c.generated_at or datetime.min.replace(tzinfo=JST)), c.priority, rel(c.path)), reverse=True)
        ],
        "unknown_freshness_tickers": sorted(set(unknown_freshness_rows)),
        "stale_tickers": sorted(set(stale_rows)),
        "missing_examples": missing_examples,
        "incomplete_examples": incomplete_examples,
        "rows_with_tv_detail_sample": rows_with_tv_detail[:20],
        "required_daily_schema": {
            "generated_at_jst": "2026-07-04T07:15:00+09:00",
            "rows": [
                {
                    "ticker": "PYPL",
                    "market": "US",
                    "tradingview": {
                        "rating": "Buy",
                        "analyst_count": 38,
                        "avg_target_price": 82.1,
                        "source": "TradingView"
                    }
                }
            ]
        }
    }


def write_status_outputs(result: Dict[str, Any], write_status: str = "output/daily_tv_snapshot_status.json") -> None:
    out = ROOT / write_status
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = DOCS_LATEST_DIR / "daily_tv_snapshot_status_latest.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def exception_status(exc: Exception) -> Dict[str, Any]:
    now = datetime.now(JST)
    return {
        "generated_at_jst": now.isoformat(),
        "status": "error",
        "quality_errors": ["exception_during_daily_tv_validation"],
        "error_type": type(exc).__name__,
        "error": str(exc),
        "note": "Daily TV validation itself failed. Status JSON is still written so GitHub Actions can commit the failure reason before failing the workflow.",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-us-tv-ratio", type=float, default=0.70)
    ap.add_argument("--max-age-days", type=int, default=7)
    ap.add_argument("--write-status", default="output/daily_tv_snapshot_status.json")
    args = ap.parse_args()
    try:
        result = validate(args.min_us_tv_ratio, args.max_age_days)
        write_status_outputs(result, args.write_status)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "ok" else 1
    except Exception as exc:
        result = exception_status(exc)
        write_status_outputs(result, args.write_status)
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
