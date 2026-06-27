
import json
import time
import urllib.request
from datetime import datetime, date
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from cis_common import DATA, OUT, active_watchlist, append_health


JST = ZoneInfo("Asia/Tokyo")

TV_ENDPOINT = "https://scanner.tradingview.com/america/scan"

US_EXCHANGES = ["NASDAQ", "NYSE", "AMEX", "OTC"]
MIN_US_RATING_COVERAGE_WARN = 0.35
CACHE_STALE_DAYS = 14
CACHE_EXPIRE_DAYS = 45

# TradingView scannerは非公式利用のため、列名変更に備えて複数候補を試す。
TV_COLUMN_SETS = [
    [
        "name", "description", "close", "currency", "exchange",
        "target_price_average", "target_price_high", "target_price_low",
        "target_price_recommendation", "number_of_analysts",
    ],
    [
        "name", "description", "close", "currency", "exchange",
        "PriceTarget.Average", "PriceTarget.High", "PriceTarget.Low",
        "AnalystRating", "AnalystRating.count",
    ],
    [
        "name", "description", "close", "currency", "exchange",
        "price_target_average", "price_target_high", "price_target_low",
        "recommendation_mark", "analyst_count",
    ],
]

OUTPUT_COLUMNS = [
    "attempt_date",
    "rating_date",
    "ticker",
    "market",
    "name",
    "theme",
    "asset_type",
    "current_price",
    "analyst_count",
    "consensus",
    "avg_target",
    "high_target",
    "low_target",
    "upside_pct",
    "high_upside_pct",
    "low_upside_pct",
    "source_used",
    "tv_symbol",
    "status",
    "freshness",
    "stale_days",
    "note",
]


def today_jst():
    return datetime.now(JST).date().isoformat()


def parse_date(x):
    if is_blank(x):
        return None
    try:
        return pd.to_datetime(str(x)).date()
    except Exception:
        return None


def days_between(d1, d2):
    a = parse_date(d1)
    b = parse_date(d2)
    if a is None or b is None:
        return None
    return (a - b).days


def is_blank(x):
    if x is None:
        return True
    try:
        if pd.isna(x):
            return True
    except Exception:
        pass
    s = str(x).strip().lower()
    return s == "" or s in {"nan", "none", "null", "na", "n/a", "--", "—"}


def safe_float(x):
    if is_blank(x):
        return None
    try:
        return float(x)
    except Exception:
        return None


def safe_intish(x):
    if is_blank(x):
        return None
    try:
        return int(float(x))
    except Exception:
        return str(x)


def fmt_num(x):
    v = safe_float(x)
    if v is None:
        return "—"
    if abs(v) >= 100:
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    return f"{v:.2f}".rstrip("0").rstrip(".")


def fmt_pct(x):
    v = safe_float(x)
    if v is None:
        return "—"
    return f"{v:.2f}%"


def upside(current, target):
    c = safe_float(current)
    t = safe_float(target)
    if c is None or t is None or c == 0:
        return None
    return (t - c) / c * 100


def valid_target_pack(current, avg, high, low):
    c = safe_float(current)
    a = safe_float(avg)
    h = safe_float(high)
    l = safe_float(low)

    if a is None:
        return True, ""

    if a <= 0:
        return False, "avg_target <= 0"

    if c is not None and c > 0:
        ratio = a / c
        if ratio > 10:
            return False, f"avg_target/current too high: {ratio:.2f}x"
        if ratio < 0.1:
            return False, f"avg_target/current too low: {ratio:.2f}x"

    if h is not None and l is not None and h < l:
        return False, "high_target < low_target"

    return True, ""


def current_price_yf(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="7d", interval="1d", auto_adjust=False)
        if hist is None or hist.empty:
            return None
        hist = hist.dropna(subset=["Close"])
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception:
        return None


def post_json(url, payload=None, timeout=18):
    if payload is None:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 CIS/1.0",
                "Accept": "application/json,text/plain,*/*",
            },
            method="GET",
        )
    else:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 CIS/1.0",
                "Accept": "application/json,text/plain,*/*",
            },
            method="POST",
        )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def first_value(row, keys):
    for k in keys:
        v = row.get(k)
        if not is_blank(v):
            return v
    return None


def candidate_tv_symbols(ticker):
    t = ticker.upper()
    return [f"{ex}:{t}" for ex in US_EXCHANGES]


def extract_tv_row(item, columns):
    vals = item.get("d", [])
    row = {columns[i]: vals[i] if i < len(vals) else None for i in range(len(columns))}
    row["_tv_symbol"] = item.get("s", "")
    return row


def normalize_tv_result(row):
    avg = first_value(row, ["target_price_average", "PriceTarget.Average", "price_target_average"])
    high = first_value(row, ["target_price_high", "PriceTarget.High", "price_target_high"])
    low = first_value(row, ["target_price_low", "PriceTarget.Low", "price_target_low"])
    count = first_value(row, ["number_of_analysts", "AnalystRating.count", "analyst_count"])
    consensus = first_value(row, ["target_price_recommendation", "AnalystRating", "recommendation_mark"])
    close = first_value(row, ["close"])

    return {
        "source": "TradingView",
        "current_price": safe_float(close),
        "analyst_count": safe_intish(count),
        "consensus": None if is_blank(consensus) else str(consensus),
        "avg_target": safe_float(avg),
        "high_target": safe_float(high),
        "low_target": safe_float(low),
        "tv_symbol": row.get("_tv_symbol", ""),
        "status": "tv_ok",
        "note": "",
    }


def has_rating_payload(result):
    return any(
        not is_blank(result.get(k))
        for k in ["analyst_count", "consensus", "avg_target", "high_target", "low_target"]
    )


def tradingview_scan_one(ticker):
    ticker_u = ticker.upper()
    last_error = ""

    for columns in TV_COLUMN_SETS:
        payloads = [
            {
                "filter": [],
                "options": {"lang": "en"},
                "symbols": {"tickers": candidate_tv_symbols(ticker_u), "query": {"types": []}},
                "columns": columns,
                "range": [0, 20],
            },
            {
                "filter": [{"left": "name", "operation": "equal", "right": ticker_u}],
                "options": {"lang": "en"},
                "symbols": {"query": {"types": ["stock", "dr"]}, "tickers": []},
                "columns": columns,
                "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
                "range": [0, 20],
            },
        ]

        for payload in payloads:
            try:
                js = post_json(TV_ENDPOINT, payload)
                data = js.get("data", [])
                if not data:
                    continue

                candidates = []
                for item in data:
                    row = extract_tv_row(item, columns)
                    sym = str(row.get("_tv_symbol", "")).upper()
                    row_name = str(row.get("name", "")).upper()
                    if row_name == ticker_u or sym.endswith(":" + ticker_u):
                        candidates.append(row)

                if not candidates:
                    continue

                def score(row):
                    sym = str(row.get("_tv_symbol", "")).upper()
                    for i, ex in enumerate(["NASDAQ", "NYSE", "AMEX", "OTC"]):
                        if sym.startswith(ex + ":"):
                            return i
                    return 99

                result = normalize_tv_result(sorted(candidates, key=score)[0])

                if not has_rating_payload(result):
                    continue

                if safe_float(result.get("current_price")) is None:
                    result["current_price"] = current_price_yf(ticker)

                ok, reason = valid_target_pack(
                    result.get("current_price"),
                    result.get("avg_target"),
                    result.get("high_target"),
                    result.get("low_target"),
                )
                if not ok:
                    result["source"] = None
                    result["status"] = "tv_invalid_target"
                    result["note"] = reason
                    return result

                return result

            except Exception as e:
                last_error = str(e)[:220]
                continue
            finally:
                time.sleep(0.15)

    return {
        "source": None,
        "status": "tv_unavailable",
        "note": last_error or "TradingView scanner returned no usable analyst forecast fields",
    }


def yahoo_yfinance_fallback(ticker):
    current = current_price_yf(ticker)

    try:
        t = yf.Ticker(ticker)
        info = {}
        try:
            info = t.get_info()
        except Exception:
            try:
                info = t.info
            except Exception:
                info = {}

        current = safe_float(info.get("currentPrice")) or safe_float(info.get("regularMarketPrice")) or current
        avg = safe_float(info.get("targetMeanPrice"))
        high = safe_float(info.get("targetHighPrice"))
        low = safe_float(info.get("targetLowPrice"))
        count = safe_intish(info.get("numberOfAnalystOpinions"))
        rec = info.get("recommendationKey") or info.get("recommendationMean")

        ok, reason = valid_target_pack(current, avg, high, low)
        if not ok:
            return {
                "source": "Yahoo Finance fallback",
                "current_price": current,
                "analyst_count": None,
                "consensus": None,
                "avg_target": None,
                "high_target": None,
                "low_target": None,
                "tv_symbol": "",
                "status": "yf_invalid_target",
                "note": reason,
            }

        if any(not is_blank(v) for v in [avg, high, low, count, rec]):
            return {
                "source": "Yahoo Finance fallback",
                "current_price": current,
                "analyst_count": count,
                "consensus": None if is_blank(rec) else str(rec),
                "avg_target": avg,
                "high_target": high,
                "low_target": low,
                "tv_symbol": "",
                "status": "yf_ok",
                "note": "TradingView未取得のためYahoo fallback",
            }

        return {
            "source": "Yahoo Finance fallback",
            "current_price": current,
            "analyst_count": None,
            "consensus": None,
            "avg_target": None,
            "high_target": None,
            "low_target": None,
            "tv_symbol": "",
            "status": "yf_no_rating",
            "note": "Yahoo yfinance returned no analyst forecast fields",
        }

    except Exception as e:
        return {
            "source": "Yahoo Finance fallback",
            "current_price": current,
            "analyst_count": None,
            "consensus": None,
            "avg_target": None,
            "high_target": None,
            "low_target": None,
            "tv_symbol": "",
            "status": "yf_error",
            "note": str(e)[:220],
        }


def yahoo_direct_fallback(ticker):
    """
    yfinanceが空のときの追加fallback。
    Yahoo quoteSummaryのfinancialDataを直接読む。
    """
    current = current_price_yf(ticker)
    url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}?modules=financialData"
    try:
        js = post_json(url, payload=None, timeout=15)
        result = js.get("quoteSummary", {}).get("result", [])
        if not result:
            return {
                "source": "Yahoo direct fallback",
                "current_price": current,
                "analyst_count": None,
                "consensus": None,
                "avg_target": None,
                "high_target": None,
                "low_target": None,
                "tv_symbol": "",
                "status": "yd_no_result",
                "note": "Yahoo direct returned no result",
            }

        fd = result[0].get("financialData", {})
        def raw_field(name):
            v = fd.get(name)
            if isinstance(v, dict):
                return v.get("raw")
            return v

        avg = safe_float(raw_field("targetMeanPrice"))
        high = safe_float(raw_field("targetHighPrice"))
        low = safe_float(raw_field("targetLowPrice"))
        count = safe_intish(raw_field("numberOfAnalystOpinions"))
        rec = raw_field("recommendationKey") or raw_field("recommendationMean")
        current = safe_float(raw_field("currentPrice")) or current

        ok, reason = valid_target_pack(current, avg, high, low)
        if not ok:
            return {
                "source": "Yahoo direct fallback",
                "current_price": current,
                "analyst_count": None,
                "consensus": None,
                "avg_target": None,
                "high_target": None,
                "low_target": None,
                "tv_symbol": "",
                "status": "yd_invalid_target",
                "note": reason,
            }

        if any(not is_blank(v) for v in [avg, high, low, count, rec]):
            return {
                "source": "Yahoo direct fallback",
                "current_price": current,
                "analyst_count": count,
                "consensus": None if is_blank(rec) else str(rec),
                "avg_target": avg,
                "high_target": high,
                "low_target": low,
                "tv_symbol": "",
                "status": "yd_ok",
                "note": "TradingView/yfinance未取得のためYahoo direct fallback",
            }

        return {
            "source": "Yahoo direct fallback",
            "current_price": current,
            "analyst_count": None,
            "consensus": None,
            "avg_target": None,
            "high_target": None,
            "low_target": None,
            "tv_symbol": "",
            "status": "yd_no_rating",
            "note": "Yahoo direct returned no analyst forecast fields",
        }

    except Exception as e:
        return {
            "source": "Yahoo direct fallback",
            "current_price": current,
            "analyst_count": None,
            "consensus": None,
            "avg_target": None,
            "high_target": None,
            "low_target": None,
            "tv_symbol": "",
            "status": "yd_error",
            "note": str(e)[:220],
        }


def manual_override_row(ticker):
    """
    任意の手動補完ファイル。存在しなければ使わない。
    data/ratings_manual.csv に ticker, rating_date, analyst_count, consensus, avg_target等を置ける。
    """
    path = DATA / "ratings_manual.csv"
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path)
        if "ticker" not in df.columns:
            return None
        hit = df[df["ticker"].astype(str).eq(str(ticker))]
        if hit.empty:
            return None
        r = hit.iloc[0].to_dict()
        if is_blank(r.get("avg_target")) and is_blank(r.get("analyst_count")) and is_blank(r.get("consensus")):
            return None

        current = safe_float(r.get("current_price")) or current_price_yf(ticker)
        avg = safe_float(r.get("avg_target"))
        high = safe_float(r.get("high_target"))
        low = safe_float(r.get("low_target"))
        ok, reason = valid_target_pack(current, avg, high, low)
        if not ok:
            return {
                "source": "Manual override",
                "current_price": current,
                "analyst_count": None,
                "consensus": None,
                "avg_target": None,
                "high_target": None,
                "low_target": None,
                "tv_symbol": "",
                "status": "manual_invalid_target",
                "note": reason,
            }

        return {
            "source": r.get("source_used") or "Manual override",
            "rating_date": r.get("rating_date") or r.get("attempt_date") or today_jst(),
            "current_price": current,
            "analyst_count": safe_intish(r.get("analyst_count")),
            "consensus": r.get("consensus"),
            "avg_target": avg,
            "high_target": high,
            "low_target": low,
            "tv_symbol": r.get("tv_symbol", ""),
            "status": "manual_override",
            "note": r.get("note", "手動補完"),
        }
    except Exception:
        return None


def previous_cache_row(ticker, previous_df):
    if previous_df is None or previous_df.empty or "ticker" not in previous_df.columns:
        return None
    old = previous_df[previous_df["ticker"].astype(str).eq(str(ticker))]
    if old.empty:
        return None
    r = old.iloc[0].to_dict()
    if is_blank(r.get("avg_target")) and is_blank(r.get("analyst_count")) and is_blank(r.get("consensus")):
        return None
    return r


def build_base_row(meta):
    return {
        "attempt_date": today_jst(),
        "rating_date": "",
        "ticker": str(meta["ticker"]),
        "market": str(meta.get("market", "")),
        "name": str(meta.get("name", "")),
        "theme": str(meta.get("theme", "")),
        "asset_type": str(meta.get("asset_type", "")),
        "current_price": None,
        "analyst_count": None,
        "consensus": None,
        "avg_target": None,
        "high_target": None,
        "low_target": None,
        "upside_pct": None,
        "high_upside_pct": None,
        "low_upside_pct": None,
        "source_used": "未取得",
        "tv_symbol": "",
        "status": "unprocessed",
        "freshness": "missing",
        "stale_days": None,
        "note": "",
    }


def fill_row_from_result(row, result, freshness="fresh"):
    current = safe_float(result.get("current_price"))
    avg = safe_float(result.get("avg_target"))
    high = safe_float(result.get("high_target"))
    low = safe_float(result.get("low_target"))
    rating_date = result.get("rating_date") or today_jst()
    stale = days_between(today_jst(), rating_date)

    row.update({
        "rating_date": rating_date,
        "current_price": current,
        "analyst_count": result.get("analyst_count"),
        "consensus": result.get("consensus"),
        "avg_target": avg,
        "high_target": high,
        "low_target": low,
        "upside_pct": upside(current, avg),
        "high_upside_pct": upside(current, high),
        "low_upside_pct": upside(current, low),
        "source_used": result.get("source") or "未取得",
        "tv_symbol": result.get("tv_symbol", ""),
        "status": result.get("status", ""),
        "freshness": freshness,
        "stale_days": stale,
        "note": result.get("note", ""),
    })
    return row


def fill_from_cache(row, old, new_status, new_note):
    rating_date = old.get("rating_date") or old.get("attempt_date") or ""
    stale = days_between(today_jst(), rating_date)

    for key in OUTPUT_COLUMNS:
        if key in old and key not in {"attempt_date", "status", "freshness", "stale_days", "note", "source_used"}:
            row[key] = old[key]

    row["attempt_date"] = today_jst()
    row["rating_date"] = rating_date
    row["source_used"] = f"previous_cache({old.get('source_used', 'unknown')})"
    row["status"] = "cached_previous_stale" if stale is not None and stale > CACHE_STALE_DAYS else "cached_previous"
    row["freshness"] = "cache_stale" if stale is not None and stale > CACHE_STALE_DAYS else "cache_ok"
    row["stale_days"] = stale
    row["note"] = f"今回未取得のため前回値を維持。前回日付={rating_date or '不明'}。new_status={new_status}; {new_note}"
    return row


def build_row(meta, previous_df):
    row = build_base_row(meta)
    ticker = row["ticker"]
    market = row["market"]
    asset_type = row["asset_type"]

    if asset_type == "watch_only":
        row.update({
            "rating_date": "",
            "source_used": "N/A",
            "status": "watch_only",
            "freshness": "not_applicable",
            "note": "取引可能ティッカー未確認",
        })
        return row

    if market != "US":
        row.update({
            "current_price": current_price_yf(ticker),
            "rating_date": "",
            "source_used": "N/A",
            "status": "not_applicable_non_us",
            "freshness": "not_applicable",
            "note": "米国株以外はW04対象外",
        })
        return row

    if asset_type == "etf":
        row.update({
            "current_price": current_price_yf(ticker),
            "rating_date": "",
            "source_used": "N/A",
            "status": "not_applicable_etf",
            "freshness": "not_applicable",
            "note": "ETFはアナリスト目標株価対象外",
        })
        return row

    # 1. TradingView
    tv = tradingview_scan_one(ticker)
    if tv.get("source") == "TradingView":
        return fill_row_from_result(row, tv, freshness="fresh")

    # 2. Yahoo via yfinance
    yf_result = yahoo_yfinance_fallback(ticker)
    if yf_result.get("status") == "yf_ok":
        if tv.get("note"):
            yf_result["note"] = f"{yf_result.get('note','')} / TV: {tv.get('note')}"
        return fill_row_from_result(row, yf_result, freshness="fresh")

    # 3. Yahoo direct
    yd_result = yahoo_direct_fallback(ticker)
    if yd_result.get("status") == "yd_ok":
        notes = []
        if tv.get("note"):
            notes.append(f"TV: {tv.get('note')}")
        if yf_result.get("note"):
            notes.append(f"YF: {yf_result.get('note')}")
        if notes:
            yd_result["note"] = f"{yd_result.get('note','')} / " + " / ".join(notes)
        return fill_row_from_result(row, yd_result, freshness="fresh")

    # 4. Manual override
    manual = manual_override_row(ticker)
    if manual is not None and manual.get("status") == "manual_override":
        return fill_row_from_result(row, manual, freshness="manual")

    # 5. Previous cache
    old = previous_cache_row(ticker, previous_df)
    combined_status = f"tv={tv.get('status')}; yf={yf_result.get('status')}; yd={yd_result.get('status')}"
    combined_note = " / ".join([n for n in [tv.get("note"), yf_result.get("note"), yd_result.get("note")] if n])
    if old is not None:
        return fill_from_cache(row, old, combined_status, combined_note)

    # 6. Missing
    result = {
        "source": "未取得",
        "current_price": current_price_yf(ticker),
        "analyst_count": None,
        "consensus": None,
        "avg_target": None,
        "high_target": None,
        "low_target": None,
        "tv_symbol": "",
        "status": "missing_all_sources",
        "note": combined_note or combined_status,
        "rating_date": "",
    }
    return fill_row_from_result(row, result, freshness="missing")


def main():
    OUT.mkdir(exist_ok=True)
    (OUT / "latest").mkdir(exist_ok=True)

    previous_path = DATA / "ratings_master.csv"
    previous_df = pd.read_csv(previous_path) if previous_path.exists() else pd.DataFrame(columns=OUTPUT_COLUMNS)

    wl = active_watchlist()
    rows = []
    health = []

    for _, meta in wl.iterrows():
        row = build_row(meta, previous_df)
        rows.append(row)

        status = row.get("status", "")
        if status in {
            "tv_ok", "yf_ok", "yd_ok", "manual_override",
            "cached_previous", "cached_previous_stale",
            "not_applicable_non_us", "not_applicable_etf", "watch_only"
        }:
            level = "INFO"
        else:
            level = "WARN"

        health.append([
            row["ticker"],
            row["market"],
            level,
            "ratings_update",
            row["source_used"],
            f"{status}: freshness={row.get('freshness')}; stale_days={row.get('stale_days')}; {row.get('note','')}",
        ])

    df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)

    us_stock = df[(df["market"] == "US") & (~df["asset_type"].isin(["etf", "watch_only"]))]
    covered = us_stock[us_stock["upside_pct"].notna()]
    coverage = (len(covered) / len(us_stock)) if len(us_stock) else 0.0

    if coverage < MIN_US_RATING_COVERAGE_WARN:
        health.append([
            "W04",
            "US",
            "WARN",
            "coverage_low",
            "ratings_master",
            f"US stock rating coverage {coverage:.1%}",
        ])

    append_health("W04_ratings", health)

    data_path = DATA / "ratings_master.csv"
    report_path = OUT / f"ratings_weekly_{today_jst()}.md"
    latest_path = OUT / "latest" / "ratings_latest.md"

    df.to_csv(data_path, index=False, encoding="utf-8-sig")

    tv_ok = int((df["status"] == "tv_ok").sum())
    yf_ok = int((df["status"] == "yf_ok").sum())
    yd_ok = int((df["status"] == "yd_ok").sum())
    cached = int(df["status"].isin(["cached_previous", "cached_previous_stale"]).sum())
    stale = int((df["status"] == "cached_previous_stale").sum())
    missing = int(df[(df["market"] == "US") & (~df["asset_type"].isin(["etf", "watch_only"])) & (df["upside_pct"].isna())].shape[0])

    lines = [
        "【CIS-W04｜TradingViewレーティング週次更新】",
        f"実行日：{today_jst()} JST",
        "",
        "## iPhoneでまず見るところ",
        f"米国株カバレッジ：{len(covered)}/{len(us_stock)}件（{coverage:.1%}）",
        f"TradingView取得：{tv_ok}件",
        f"Yahoo yfinance取得：{yf_ok}件",
        f"Yahoo direct取得：{yd_ok}件",
        f"前回キャッシュ維持：{cached}件（うち14日超：{stale}件）",
        f"未取得：{missing}件",
        "",
        "## 古い/未取得で確認が必要",
    ]

    needs_check = us_stock[
        (us_stock["upside_pct"].isna())
        | (us_stock["status"] == "cached_previous_stale")
    ].copy()

    if needs_check.empty:
        lines.append("該当なし")
    else:
        for _, r in needs_check.iterrows():
            lines.append(
                f"- {r['ticker']}｜{r['name']}｜status={r['status']}｜"
                f"source={r['source_used']}｜rating_date={r['rating_date'] or '—'}｜"
                f"経過={r['stale_days'] if not is_blank(r['stale_days']) else '—'}日｜note={r['note']}"
            )

    lines.extend([
        "",
        "## 上昇余地が大きい順（米国株・取得済み）",
    ])

    got = us_stock[us_stock["upside_pct"].notna()].copy()
    if got.empty:
        lines.append("該当なし")
    else:
        got = got.sort_values("upside_pct", ascending=False)
        for _, r in got.iterrows():
            stale_txt = ""
            if not is_blank(r.get("stale_days")):
                stale_txt = f"｜基準日 {r['rating_date']}（{int(float(r['stale_days']))}日経過）"
            lines.append(
                f"- {r['ticker']}｜{r['name']}｜{r['source_used']}｜"
                f"{r['analyst_count'] if not is_blank(r['analyst_count']) else '—'}人｜"
                f"{r['consensus'] if not is_blank(r['consensus']) else '—'}｜"
                f"現在 {fmt_num(r['current_price'])}｜平均目標 {fmt_num(r['avg_target'])}｜"
                f"乖離 {fmt_pct(r['upside_pct'])}{stale_txt}"
            )

    lines.extend([
        "",
        "## 運用メモ",
        "- 第一候補はTradingView scanner。",
        "- TradingViewが取れない銘柄はYahoo yfinance、Yahoo direct、手動補完、前回キャッシュの順に使う。",
        "- 今回未取得でも前回値があれば previous_cache として保持し、毎回空欄で上書きしない。",
        "- 前回キャッシュはrating_dateを保持する。14日超は古いレーティングとして表示する。",
        "- ETFと日本株はW04対象外。",
        "- W04取得失敗でD03本体は止めない。",
        "",
    ])

    content = "\n".join(lines)
    report_path.write_text(content, encoding="utf-8")
    latest_path.write_text(content, encoding="utf-8")

    print(f"created {data_path}")
    print(f"created {report_path}")
    print(f"created {latest_path}")
    print(f"coverage {coverage:.1%}")


if __name__ == "__main__":
    main()
