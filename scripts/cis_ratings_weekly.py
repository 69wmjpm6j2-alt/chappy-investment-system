
import csv
import html as html_lib
import json
import os
from pathlib import Path
import re
import time
import urllib.request
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from cis_common import DATA, OUT, active_watchlist, append_health


JST = ZoneInfo("Asia/Tokyo")

TV_SCANNER_ENDPOINT = "https://scanner.tradingview.com/america/scan"
USER_AGENT = "Mozilla/5.0 (compatible; CIS-TradingView-Ratings/0.6.3)"

TV_REFRESH_DAYS = 30
TV_CACHE_OLD_DAYS = 60
TV_STALE_DAYS = 90
PARTIAL_RETRY_DAYS = 7
MISSING_RETRY_DAYS = 7

US_EXCHANGES = ["NASDAQ", "NYSE", "AMEX", "OTC"]

RATINGS_COLUMNS = [
    "ticker",
    "tv_symbol",
    "forecast_url",
    "market",
    "asset_type",
    "name",
    "theme",
    "last_attempt_date",
    "rating_date",
    "next_refresh_due",
    "current_price",
    "tv_avg_target",
    "tv_high_target",
    "tv_low_target",
    "tv_upside_pct",
    "tv_high_upside_pct",
    "tv_low_upside_pct",
    "tv_analyst_count_target",
    "tv_consensus",
    "tv_analyst_count_rating",
    "tv_rating_consensus",
    "tv_rating_total_count",
    "tv_strong_buy_count",
    "tv_buy_count",
    "tv_hold_count",
    "tv_sell_count",
    "tv_strong_sell_count",
    "source_quality",
    "freshness",
    "stale_days",
    "status",
    "note",
]

SYMBOL_MAP_COLUMNS = [
    "ticker",
    "tv_symbol",
    "forecast_url",
    "last_verified",
    "source",
    "status",
    "note",
]

TV_SCANNER_COLUMN_SETS = [
    [
        "name", "description", "close", "exchange",
        "target_price_average", "target_price_high", "target_price_low",
        "target_price_recommendation", "number_of_analysts",
    ],
    [
        "name", "description", "close", "exchange",
        "PriceTarget.Average", "PriceTarget.High", "PriceTarget.Low",
        "AnalystRating", "AnalystRating.count",
    ],
    [
        "name", "description", "close", "exchange",
        "price_target_average", "price_target_high", "price_target_low",
        "recommendation_mark", "analyst_count",
    ],
]


def today_jst() -> str:
    return datetime.now(JST).date().isoformat()


def today_date() -> date:
    return datetime.now(JST).date()


def is_first_saturday_jst() -> bool:
    d = today_date()
    return d.weekday() == 5 and 1 <= d.day <= 7


def mode_from_env() -> str:
    mode = (os.getenv("MODE") or os.getenv("mode") or "auto").strip().lower()
    if mode not in {"auto", "missing", "monthly", "full", "single"}:
        return "auto"
    if mode == "auto":
        return "monthly" if is_first_saturday_jst() else "missing"
    return mode


def env_ticker() -> str:
    return (os.getenv("TICKER") or os.getenv("ticker") or "").strip().upper()


def is_blank(x) -> bool:
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
        s = str(x).replace(",", "").replace("$", "").strip()
        return float(s)
    except Exception:
        return None


def safe_intish(x):
    if is_blank(x):
        return None
    try:
        return int(float(str(x).replace(",", "").strip()))
    except Exception:
        return None


def parse_date(x):
    if is_blank(x):
        return None
    try:
        return pd.to_datetime(str(x)).date()
    except Exception:
        return None


def add_days(iso_date, days):
    d = parse_date(iso_date)
    if d is None:
        d = today_date()
    return (d + timedelta(days=days)).isoformat()


def days_since(iso_date):
    d = parse_date(iso_date)
    if d is None:
        return None
    return (today_date() - d).days


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


def valid_target_pack(current, avg, high=None, low=None):
    c = safe_float(current)
    a = safe_float(avg)
    h = safe_float(high)
    l = safe_float(low)

    if a is None:
        return True, ""

    if a <= 0:
        return False, "avg target <= 0"

    if c is not None and c > 0:
        ratio = a / c
        if ratio > 10:
            return False, f"avg target/current too high: {ratio:.2f}x"
        if ratio < 0.1:
            return False, f"avg target/current too low: {ratio:.2f}x"

    if h is not None and l is not None and h < l:
        return False, "high target < low target"

    return True, ""


def request_text(url, payload=None, timeout=18):
    if payload is None:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/json,text/plain,*/*",
                "Accept-Language": "en-US,en;q=0.9",
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
                "User-Agent": USER_AGENT,
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "en-US,en;q=0.9",
            },
            method="POST",
        )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def request_json(url, payload=None, timeout=18):
    return json.loads(request_text(url, payload=payload, timeout=timeout))



def rating_breakdown_total(bd):
    vals = [
        safe_intish(bd.get("tv_strong_buy_count")),
        safe_intish(bd.get("tv_buy_count")),
        safe_intish(bd.get("tv_hold_count")),
        safe_intish(bd.get("tv_sell_count")),
        safe_intish(bd.get("tv_strong_sell_count")),
    ]
    if all(v is None for v in vals):
        return None
    return sum(v or 0 for v in vals)


def consensus_from_breakdown(bd):
    labels = [
        ("Strong Buy", safe_intish(bd.get("tv_strong_buy_count")) or 0),
        ("Buy", safe_intish(bd.get("tv_buy_count")) or 0),
        ("Neutral", safe_intish(bd.get("tv_hold_count")) or 0),
        ("Sell", safe_intish(bd.get("tv_sell_count")) or 0),
        ("Strong Sell", safe_intish(bd.get("tv_strong_sell_count")) or 0),
    ]
    total = sum(v for _, v in labels)
    if total <= 0:
        return None
    max_v = max(v for _, v in labels)
    winners = [label for label, v in labels if v == max_v]
    return winners[0] if winners else None


def extract_count_near_label(text, label_patterns):
    """
    TradingViewのHTML/本文から、ラベル近傍の人数を拾う。
    例: Strong Buy 16 / 強い買い 16 / 16 Strong Buy
    """
    for label in label_patterns:
        patterns = [
            # 誤カウント防止のため、ラベルと数字が近接している場合だけ採用する。
            rf"{label}\s*[:：]?\s*(\d[\d,]*)\b",
            rf"(\d[\d,]*)\s*(?:ratings?|analysts?)?\s*{label}\b",
        ]
        for pat in patterns:
            m = re.search(pat, text, flags=re.I | re.S)
            if m:
                v = safe_intish(m.group(1))
                if v is not None:
                    return v
    return None


def json_rating_count(raw_html, keys):
    joined = "|".join(re.escape(k) for k in keys)
    patterns = [
        rf'"(?:{joined})"\s*:\s*(\d[\d,]*)',
        rf'"(?:{joined})"\s*:\s*\{{[^{{}}]*?"raw"\s*:\s*(\d[\d,]*)',
        rf'"(?:{joined})"\s*:\s*\{{[^{{}}]*?"value"\s*:\s*(\d[\d,]*)',
        rf'"(?:{joined})"\s*:\s*\{{[^{{}}]*?"count"\s*:\s*(\d[\d,]*)',
    ]
    return first_regex_int(raw_html, patterns)


def extract_rating_breakdown(raw_html, clean_text):
    """
    TradingViewのアナリスト評価内訳を抽出。
    取れない場合はNoneを残す。Yahoo等の代替値は使わない。
    """
    # JSON系の候補
    strong_buy = json_rating_count(raw_html, [
        "strong_buy", "strongBuy", "strongBuyCount", "strong_buy_count",
        "StrongBuy", "STRONG_BUY", "recommendationStrongBuy",
    ])
    buy = json_rating_count(raw_html, [
        "buyCount", "buy_count", "recommendationBuy", "ratingBuyCount", "analystBuyCount",
    ])
    hold = json_rating_count(raw_html, [
        "holdCount", "neutralCount", "hold_count", "neutral_count",
        "recommendationHold", "ratingHoldCount", "ratingNeutralCount", "analystHoldCount",
    ])
    sell = json_rating_count(raw_html, [
        "sellCount", "sell_count", "recommendationSell", "ratingSellCount", "analystSellCount",
    ])
    strong_sell = json_rating_count(raw_html, [
        "strong_sell", "strongSell", "strongSellCount", "strong_sell_count",
        "StrongSell", "STRONG_SELL", "recommendationStrongSell",
    ])

    # 本文系の候補。Strong Buyを先に取り、BuyはStrong Buyに引っ張られないように周辺文も見る。
    text = clean_text

    if strong_buy is None:
        strong_buy = extract_count_near_label(text, [
            r"Strong\s*Buy", r"StrongBuy", r"強い買い", r"強気買い"
        ])

    if strong_sell is None:
        strong_sell = extract_count_near_label(text, [
            r"Strong\s*Sell", r"StrongSell", r"強い売り", r"強気売り"
        ])

    # Strong Buy/Sell の断片を消してからBuy/Sellを探す。
    # 日本語UIの「強い買い」「強い売り」も消さないと、Buy/Sell側が強い買い/売りの人数を拾ってしまう。
    text_wo_strong = text
    strong_patterns = [
        r"Strong\s*Buy", r"StrongBuy", r"強い買い", r"強気買い",
        r"Strong\s*Sell", r"StrongSell", r"強い売り", r"強気売り",
    ]
    for sp in strong_patterns:
        text_wo_strong = re.sub(rf"{sp}\s*[:：]?\s*\d[\d,]*", " ", text_wo_strong, flags=re.I)
        text_wo_strong = re.sub(rf"\d[\d,]*\s*(?:ratings?|analysts?)?\s*{sp}", " ", text_wo_strong, flags=re.I)

    if buy is None:
        buy = extract_count_near_label(text_wo_strong, [
            r"\bBuy\b", r"買い"
        ])

    if hold is None:
        hold = extract_count_near_label(text, [
            r"\bNeutral\b", r"\bHold\b", r"中立", r"保有"
        ])

    if sell is None:
        sell = extract_count_near_label(text_wo_strong, [
            r"\bSell\b", r"売り"
        ])

    bd = {
        "tv_strong_buy_count": strong_buy,
        "tv_buy_count": buy,
        "tv_hold_count": hold,
        "tv_sell_count": sell,
        "tv_strong_sell_count": strong_sell,
    }
    total = rating_breakdown_total(bd)
    bd["tv_rating_total_count"] = total
    bd["tv_rating_consensus"] = consensus_from_breakdown(bd)
    return bd



def is_complete_breakdown(bd):
    return all(
        safe_intish(bd.get(k)) is not None
        for k in [
            "tv_strong_buy_count",
            "tv_buy_count",
            "tv_hold_count",
            "tv_sell_count",
            "tv_strong_sell_count",
        ]
    )


def clean_html_text(raw_html: str) -> str:
    s = re.sub(r"<script\b[^>]*>.*?</script>", " ", raw_html, flags=re.I | re.S)
    s = re.sub(r"<style\b[^>]*>.*?</style>", " ", s, flags=re.I | re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html_lib.unescape(s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def first_regex_number(text, patterns):
    for pat in patterns:
        m = re.search(pat, text, flags=re.I | re.S)
        if m:
            for g in m.groups():
                v = safe_float(g)
                if v is not None:
                    return v
    return None


def first_regex_int(text, patterns):
    for pat in patterns:
        m = re.search(pat, text, flags=re.I | re.S)
        if m:
            for g in m.groups():
                v = safe_intish(g)
                if v is not None:
                    return v
    return None


def first_regex_consensus(text):
    patterns = [
        r"Analyst rating.{0,80}\b(Strong\s*Buy|StrongBuy|Buy|Neutral|Hold|Sell|Strong\s*Sell|StrongSell)\b",
        r"\b(Strong\s*Buy|StrongBuy|Buy|Neutral|Hold|Sell|Strong\s*Sell|StrongSell)\b.{0,80}Analyst rating",
        r'"(?:target_price_recommendation|recommendation_mark|AnalystRating|analystRating|recommendationKey)"\s*:\s*"([^"]+)"',
        r'"(?:target_price_recommendation|recommendation_mark|AnalystRating|analystRating|recommendationKey)"\s*:\s*\{"[^"]*"\s*:\s*"([^"]+)"',
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.I | re.S)
        if m:
            val = str(m.group(1)).strip()
            if val:
                return val.replace("StrongBuy", "Strong Buy").replace("StrongSell", "Strong Sell")
    return None


def json_key_number(text, keys):
    joined = "|".join(re.escape(k) for k in keys)
    patterns = [
        rf'"(?:{joined})"\s*:\s*([-+]?\d[\d,]*(?:\.\d+)?)',
        rf'"(?:{joined})"\s*:\s*\{{[^{{}}]*?"raw"\s*:\s*([-+]?\d[\d,]*(?:\.\d+)?)',
        rf'"(?:{joined})"\s*:\s*\{{[^{{}}]*?"value"\s*:\s*([-+]?\d[\d,]*(?:\.\d+)?)',
    ]
    return first_regex_number(text, patterns)


def json_key_int(text, keys):
    joined = "|".join(re.escape(k) for k in keys)
    patterns = [
        rf'"(?:{joined})"\s*:\s*(\d[\d,]*)',
        rf'"(?:{joined})"\s*:\s*\{{[^{{}}]*?"raw"\s*:\s*(\d[\d,]*)',
        rf'"(?:{joined})"\s*:\s*\{{[^{{}}]*?"value"\s*:\s*(\d[\d,]*)',
    ]
    return first_regex_int(text, patterns)


def extract_forecast_from_html(raw_html, current_price=None):
    text = clean_html_text(raw_html)

    avg = json_key_number(raw_html, [
        "target_price_average", "targetPriceAverage", "priceTargetAverage",
        "priceTargetMean", "targetMeanPrice", "averagePriceTarget",
        "avgTargetPrice", "averageTargetPrice", "target_price_avg",
    ])

    high = json_key_number(raw_html, [
        "target_price_high", "targetPriceHigh", "priceTargetHigh",
        "targetHighPrice", "highTargetPrice", "target_price_max",
    ])

    low = json_key_number(raw_html, [
        "target_price_low", "targetPriceLow", "priceTargetLow",
        "targetLowPrice", "lowTargetPrice", "target_price_min",
    ])

    count_target = json_key_int(raw_html, [
        "number_of_analysts", "numberOfAnalysts", "analystCount",
        "analystsCount", "targetPriceAnalysts", "priceTargetAnalystCount",
    ])

    consensus = first_regex_consensus(raw_html) or first_regex_consensus(text)

    # Text fallback. Avoid treating current price as target by validating later.
    if avg is None:
        avg = first_regex_number(text, [
            r"Price target\s+([$]?\d[\d,]*(?:\.\d+)?)",
            r"Average price target(?: is| of)?\s+([$]?\d[\d,]*(?:\.\d+)?)",
            r"average target price(?: is| of)?\s+([$]?\d[\d,]*(?:\.\d+)?)",
            r"mean target(?: price)?(?: is| of)?\s+([$]?\d[\d,]*(?:\.\d+)?)",
        ])

    if high is None:
        high = first_regex_number(text, [
            r"(?:max|maximum|high) estimate(?: of| is)?\s+([$]?\d[\d,]*(?:\.\d+)?)",
            r"high forecast(?: of| is)?\s+([$]?\d[\d,]*(?:\.\d+)?)",
            r"highest price target(?: of| is)?\s+([$]?\d[\d,]*(?:\.\d+)?)",
        ])

    if low is None:
        low = first_regex_number(text, [
            r"(?:min|minimum|low) estimate(?: of| is)?\s+([$]?\d[\d,]*(?:\.\d+)?)",
            r"low forecast(?: of| is)?\s+([$]?\d[\d,]*(?:\.\d+)?)",
            r"lowest price target(?: of| is)?\s+([$]?\d[\d,]*(?:\.\d+)?)",
        ])

    if count_target is None:
        count_target = first_regex_int(text, [
            r"(\d[\d,]*)\s+analysts?\s+offering\s+1[- ]year price forecasts?",
            r"Based on\s+(\d[\d,]*)\s+analysts?",
            r"(\d[\d,]*)\s+analysts?.{0,80}price target",
        ])

    count_rating = first_regex_int(text, [
        r"Analyst rating.{0,120}based on\s+(\d[\d,]*)\s+analysts?",
        r"Based on\s+(\d[\d,]*)\s+analysts?.{0,120}Analyst rating",
        r"(\d[\d,]*)\s+analysts?.{0,120}Analyst rating",
    ])

    breakdown = extract_rating_breakdown(raw_html, text)

    # 内訳が取れた場合は評価人数/コンセンサスを優先補完
    if breakdown.get("tv_rating_total_count") is not None:
        count_rating = breakdown.get("tv_rating_total_count")
    if is_blank(consensus) and breakdown.get("tv_rating_consensus"):
        consensus = breakdown.get("tv_rating_consensus")

    ok, reason = valid_target_pack(current_price, avg, high, low)
    if not ok:
        avg = high = low = None
        note = f"invalid forecast target discarded: {reason}"
    else:
        note = ""

    has_breakdown = is_complete_breakdown(breakdown)
    if not has_breakdown and breakdown.get("tv_rating_total_count") is not None:
        note = (note + " / " if note else "") + "rating breakdown incomplete; not marked PLUS"
    if avg is not None and has_breakdown:
        source_quality = "TV_FULL_PLUS"
    elif avg is not None:
        source_quality = "TV_FULL"
    elif has_breakdown:
        source_quality = "TV_PARTIAL_PLUS"
    elif consensus or count_target or count_rating:
        source_quality = "TV_PARTIAL"
    else:
        source_quality = "TV_MISSING"

    return {
        "source_quality": source_quality,
        "current_price": current_price,
        "tv_avg_target": avg,
        "tv_high_target": high,
        "tv_low_target": low,
        "tv_analyst_count_target": count_target,
        "tv_consensus": consensus,
        "tv_analyst_count_rating": count_rating,
        "tv_rating_consensus": breakdown.get("tv_rating_consensus"),
        "tv_rating_total_count": breakdown.get("tv_rating_total_count"),
        "tv_strong_buy_count": breakdown.get("tv_strong_buy_count"),
        "tv_buy_count": breakdown.get("tv_buy_count"),
        "tv_hold_count": breakdown.get("tv_hold_count"),
        "tv_sell_count": breakdown.get("tv_sell_count"),
        "tv_strong_sell_count": breakdown.get("tv_strong_sell_count"),
        "note": note,
    }


def tv_symbol_to_url(tv_symbol: str, page="forecast") -> str:
    if ":" not in str(tv_symbol):
        return ""
    ex, ticker = tv_symbol.split(":", 1)
    slug = f"{ex}-{ticker}".replace(".", "-").upper()
    if page == "forecast_price_target":
        return f"https://www.tradingview.com/symbols/{slug}/forecast-price-target/"
    return f"https://www.tradingview.com/symbols/{slug}/forecast/"


def scanner_symbol_candidates(ticker):
    t = str(ticker).upper()
    return [f"{ex}:{t}" for ex in US_EXCHANGES]


def scanner_lookup(ticker):
    """
    scannerは主目的ではなく補助。
    tv_symbol特定と、判断だけ取得できる場合のpartial補助に使う。
    """
    ticker_u = str(ticker).upper()
    last_error = ""

    for columns in TV_SCANNER_COLUMN_SETS:
        payloads = [
            {
                "filter": [],
                "options": {"lang": "en"},
                "symbols": {"tickers": scanner_symbol_candidates(ticker_u), "query": {"types": []}},
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
                js = request_json(TV_SCANNER_ENDPOINT, payload=payload, timeout=18)
                data = js.get("data", [])
                if not data:
                    continue

                candidates = []
                for item in data:
                    vals = item.get("d", [])
                    row = {columns[i]: vals[i] if i < len(vals) else None for i in range(len(columns))}
                    sym = str(item.get("s", ""))
                    row["_tv_symbol"] = sym
                    row_name = str(row.get("name", "")).upper()
                    if row_name == ticker_u or sym.upper().endswith(":" + ticker_u):
                        candidates.append(row)

                if not candidates:
                    continue

                def score(row):
                    sym = str(row.get("_tv_symbol", "")).upper()
                    for i, ex in enumerate(US_EXCHANGES):
                        if sym.startswith(ex + ":"):
                            return i
                    return 99

                row = sorted(candidates, key=score)[0]

                def fv(keys):
                    for k in keys:
                        if k in row and not is_blank(row[k]):
                            return row[k]
                    return None

                avg = safe_float(fv(["target_price_average", "PriceTarget.Average", "price_target_average"]))
                high = safe_float(fv(["target_price_high", "PriceTarget.High", "price_target_high"]))
                low = safe_float(fv(["target_price_low", "PriceTarget.Low", "price_target_low"]))
                consensus = fv(["target_price_recommendation", "AnalystRating", "recommendation_mark"])
                count = safe_intish(fv(["number_of_analysts", "AnalystRating.count", "analyst_count"]))
                current = safe_float(fv(["close"]))

                return {
                    "tv_symbol": row.get("_tv_symbol", ""),
                    "current_price": current,
                    "tv_avg_target": avg,
                    "tv_high_target": high,
                    "tv_low_target": low,
                    "tv_consensus": None if is_blank(consensus) else str(consensus),
                    "tv_analyst_count_target": count,
                    "tv_analyst_count_rating": count,
                    "tv_rating_consensus": None,
                    "tv_rating_total_count": None,
                    "tv_strong_buy_count": None,
                    "tv_buy_count": None,
                    "tv_hold_count": None,
                    "tv_sell_count": None,
                    "tv_strong_sell_count": None,
                    "source_quality": "TV_FULL" if avg is not None else ("TV_PARTIAL" if consensus or count else "TV_MISSING"),
                    "status": "scanner_ok",
                    "note": "",
                }

            except Exception as e:
                last_error = str(e)[:220]
            finally:
                time.sleep(0.15)

    return {
        "tv_symbol": "",
        "source_quality": "TV_MISSING",
        "status": "scanner_missing",
        "note": last_error or "scanner returned no matching symbol",
    }


def load_csv(path: Path, columns):
    if path.exists():
        try:
            return pd.read_csv(path)
        except Exception:
            return pd.DataFrame(columns=columns)
    return pd.DataFrame(columns=columns)


def save_symbol_map(df):
    p = DATA / "tv_symbol_map.csv"
    df = df.copy()
    for col in SYMBOL_MAP_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[SYMBOL_MAP_COLUMNS]
    df.to_csv(p, index=False, encoding="utf-8-sig")


def symbol_map_row(symbol_map, ticker):
    if symbol_map.empty or "ticker" not in symbol_map.columns:
        return None
    hit = symbol_map[symbol_map["ticker"].astype(str).eq(str(ticker))]
    if hit.empty:
        return None
    return hit.iloc[0].to_dict()


def upsert_symbol_map(symbol_map, row):
    if symbol_map.empty or "ticker" not in symbol_map.columns:
        symbol_map = pd.DataFrame(columns=SYMBOL_MAP_COLUMNS)

    ticker = str(row.get("ticker", ""))
    rest = symbol_map[~symbol_map["ticker"].astype(str).eq(ticker)].copy()
    new_row = {col: row.get(col, "") for col in SYMBOL_MAP_COLUMNS}
    return pd.concat([rest, pd.DataFrame([new_row])], ignore_index=True)


def previous_row(previous_df, ticker):
    if previous_df.empty or "ticker" not in previous_df.columns:
        return None
    hit = previous_df[previous_df["ticker"].astype(str).eq(str(ticker))]
    if hit.empty:
        return None
    return hit.iloc[0].to_dict()


def normalize_previous(prev):
    """
    v0.5以前のratings_masterがあっても、使えるTV情報だけ拾う。
    """
    if not prev:
        return None

    avg = safe_float(prev.get("tv_avg_target"))
    if avg is None:
        avg = safe_float(prev.get("avg_target"))

    high = safe_float(prev.get("tv_high_target"))
    if high is None:
        high = safe_float(prev.get("high_target"))

    low = safe_float(prev.get("tv_low_target"))
    if low is None:
        low = safe_float(prev.get("low_target"))

    consensus = prev.get("tv_consensus")
    if is_blank(consensus):
        consensus = prev.get("consensus")

    count_target = safe_intish(prev.get("tv_analyst_count_target"))
    if count_target is None:
        count_target = safe_intish(prev.get("analyst_count"))

    count_rating = safe_intish(prev.get("tv_analyst_count_rating"))

    rating_date = prev.get("rating_date") or prev.get("attempt_date") or prev.get("last_attempt_date") or ""

    strong_buy = safe_intish(prev.get("tv_strong_buy_count"))
    buy_count = safe_intish(prev.get("tv_buy_count"))
    hold_count = safe_intish(prev.get("tv_hold_count"))
    sell_count = safe_intish(prev.get("tv_sell_count"))
    strong_sell = safe_intish(prev.get("tv_strong_sell_count"))
    bd = {
        "tv_strong_buy_count": strong_buy,
        "tv_buy_count": buy_count,
        "tv_hold_count": hold_count,
        "tv_sell_count": sell_count,
        "tv_strong_sell_count": strong_sell,
    }
    total = safe_intish(prev.get("tv_rating_total_count"))
    if total is None:
        total = rating_breakdown_total(bd)
    rating_consensus = prev.get("tv_rating_consensus")
    if is_blank(rating_consensus):
        rating_consensus = consensus_from_breakdown(bd)

    complete_bd = is_complete_breakdown(bd)
    if avg is None and is_blank(consensus) and count_target is None and count_rating is None and total is None:
        return None

    q = prev.get("source_quality", "")
    # PLUSは5分類が全部揃った場合だけ。古い/壊れたPLUSは降格する。
    if str(q) in {"TV_FULL_PLUS", "TV_PARTIAL_PLUS"} and not complete_bd:
        q = "TV_FULL" if avg is not None else "TV_PARTIAL"
    if avg is not None and complete_bd and str(q) == "TV_FULL":
        q = "TV_FULL_PLUS"
    if avg is None and complete_bd and str(q) == "TV_PARTIAL":
        q = "TV_PARTIAL_PLUS"

    return {
        "rating_date": rating_date,
        "last_attempt_date": prev.get("last_attempt_date") or prev.get("attempt_date") or "",
        "tv_symbol": prev.get("tv_symbol", ""),
        "forecast_url": prev.get("forecast_url", ""),
        "tv_avg_target": avg,
        "tv_high_target": high,
        "tv_low_target": low,
        "tv_analyst_count_target": count_target,
        "tv_consensus": None if is_blank(consensus) else str(consensus),
        "tv_analyst_count_rating": count_rating,
        "tv_rating_consensus": None if is_blank(rating_consensus) else str(rating_consensus),
        "tv_rating_total_count": total,
        "tv_strong_buy_count": strong_buy,
        "tv_buy_count": buy_count,
        "tv_hold_count": hold_count,
        "tv_sell_count": sell_count,
        "tv_strong_sell_count": strong_sell,
        "source_quality": q,
        "note": prev.get("note", ""),
    }


def has_full_tv(prev_norm):
    return bool(prev_norm and safe_float(prev_norm.get("tv_avg_target")) is not None)


def has_partial_tv(prev_norm):
    return bool(prev_norm and safe_float(prev_norm.get("tv_avg_target")) is None and (
        not is_blank(prev_norm.get("tv_consensus"))
        or prev_norm.get("tv_analyst_count_target") is not None
        or prev_norm.get("tv_analyst_count_rating") is not None
        or prev_norm.get("tv_rating_total_count") is not None
    ))


def derived_quality_from_age(has_full, has_partial, rating_date, previous_quality=""):
    if has_full:
        age = days_since(rating_date)
        if age is None:
            return "TV_CACHE"
        if age <= TV_REFRESH_DAYS:
            return "TV_FULL_PLUS" if str(previous_quality) == "TV_FULL_PLUS" else "TV_FULL"
        if age <= TV_CACHE_OLD_DAYS:
            return "TV_CACHE"
        if age <= TV_STALE_DAYS:
            return "TV_CACHE_OLD"
        return "TV_STALE"
    if has_partial:
        return "TV_PARTIAL_PLUS" if str(previous_quality) == "TV_PARTIAL_PLUS" else "TV_PARTIAL"
    return "TV_MISSING"


def next_due_from_quality(source_quality, rating_date):
    if source_quality in {"TV_FULL", "TV_FULL_PLUS", "TV_CACHE", "TV_CACHE_OLD", "TV_STALE"}:
        return add_days(rating_date or today_jst(), TV_REFRESH_DAYS)
    if source_quality == "TV_PARTIAL":
        return add_days(today_jst(), PARTIAL_RETRY_DAYS)
    if source_quality == "TV_PARTIAL_PLUS":
        return add_days(today_jst(), PARTIAL_RETRY_DAYS)
    if source_quality == "TV_MISSING":
        return add_days(today_jst(), MISSING_RETRY_DAYS)
    return ""


def should_attempt(mode, ticker, meta, prev_norm):
    asset_type = str(meta.get("asset_type", ""))
    market = str(meta.get("market", ""))
    if market != "US" or asset_type in {"etf", "watch_only"}:
        return False

    one = env_ticker()
    if mode == "single":
        return bool(one and ticker.upper() == one)

    if mode in {"monthly", "full"}:
        return True

    # missing retry: TV_MISSING / TV_PARTIAL only. TV_FULL/cacheは触らない。
    if has_full_tv(prev_norm):
        return False
    return True


def resolve_tv_symbol(ticker, symbol_map, prev_norm):
    # 1. symbol_map
    m = symbol_map_row(symbol_map, ticker)
    if m and not is_blank(m.get("tv_symbol")):
        return str(m.get("tv_symbol")), str(m.get("forecast_url") or tv_symbol_to_url(m.get("tv_symbol"))), "symbol_map", symbol_map

    # 2. previous ratings
    if prev_norm and not is_blank(prev_norm.get("tv_symbol")):
        tvs = str(prev_norm.get("tv_symbol"))
        return tvs, str(prev_norm.get("forecast_url") or tv_symbol_to_url(tvs)), "previous", symbol_map

    # 3. scanner
    sc = scanner_lookup(ticker)
    tvs = sc.get("tv_symbol", "")
    if not is_blank(tvs):
        url = tv_symbol_to_url(tvs)
        new_map_row = {
            "ticker": ticker,
            "tv_symbol": tvs,
            "forecast_url": url,
            "last_verified": today_jst(),
            "source": "scanner",
            "status": sc.get("status", ""),
            "note": sc.get("note", ""),
        }
        symbol_map = upsert_symbol_map(symbol_map, new_map_row)
        return tvs, url, "scanner", symbol_map

    # 4. fallback candidate URL is handled by fetch loop
    return "", "", "missing", symbol_map


def forecast_url_candidates(ticker, tv_symbol, forecast_url):
    urls = []
    if forecast_url:
        urls.append(forecast_url)
    if tv_symbol:
        urls.append(tv_symbol_to_url(tv_symbol, "forecast"))
        urls.append(tv_symbol_to_url(tv_symbol, "forecast_price_target"))

    # Last-resort exchange guesses are expensive and can cause workflow timeouts.
    # Use them only when symbol_map/scanner/previous did not resolve a TV symbol.
    if not forecast_url and not tv_symbol:
        t = str(ticker).upper().replace(".", "-")
        for ex in US_EXCHANGES:
            slug = f"{ex}-{t}"
            urls.append(f"https://www.tradingview.com/symbols/{slug}/forecast/")
            urls.append(f"https://www.tradingview.com/symbols/{slug}/forecast-price-target/")

    # Preserve order, remove duplicates.
    seen = set()
    out = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def fetch_tradingview_forecast(ticker, tv_symbol, forecast_url, current_price):
    errors = []
    best_full = None
    best_partial = None
    best_partial_plus = None

    for url in forecast_url_candidates(ticker, tv_symbol, forecast_url):
        try:
            raw = request_text(url, timeout=20)
            parsed = extract_forecast_from_html(raw, current_price=current_price)
            parsed["forecast_url"] = url
            parsed["status"] = "forecast_page_ok"
            parsed["note"] = parsed.get("note", "")

            if parsed["source_quality"] == "TV_FULL_PLUS":
                return parsed

            if parsed["source_quality"] == "TV_FULL":
                if best_full is None:
                    best_full = parsed
                continue

            if parsed["source_quality"] == "TV_PARTIAL_PLUS":
                if best_partial_plus is None:
                    best_partial_plus = parsed
                continue

            if parsed["source_quality"] == "TV_PARTIAL" and best_partial is None:
                best_partial = parsed

        except Exception as e:
            errors.append(f"{url}: {str(e)[:120]}")
        finally:
            time.sleep(0.25)

    if best_full is not None:
        # 別URLで評価内訳だけ取れた場合は、targetとbreakdownを合成する。
        if best_partial_plus is not None:
            for k in [
                "tv_rating_consensus",
                "tv_rating_total_count",
                "tv_strong_buy_count",
                "tv_buy_count",
                "tv_hold_count",
                "tv_sell_count",
                "tv_strong_sell_count",
            ]:
                best_full[k] = best_partial_plus.get(k)
            if is_blank(best_full.get("tv_consensus")):
                best_full["tv_consensus"] = best_partial_plus.get("tv_consensus") or best_partial_plus.get("tv_rating_consensus")
            if is_blank(best_full.get("tv_analyst_count_rating")):
                best_full["tv_analyst_count_rating"] = best_partial_plus.get("tv_analyst_count_rating") or best_partial_plus.get("tv_rating_total_count")
            best_full["source_quality"] = "TV_FULL_PLUS"
            best_full["note"] = (best_full.get("note", "") + " / target and rating breakdown merged from TV pages").strip(" /")
        return best_full

    if best_partial_plus is not None:
        if errors:
            best_partial_plus["note"] = (best_partial_plus.get("note", "") + " / " + " / ".join(errors[:2])).strip(" /")
        return best_partial_plus

    if best_partial is not None:
        if errors:
            best_partial["note"] = (best_partial.get("note", "") + " / " + " / ".join(errors[:2])).strip(" /")
        return best_partial

    return {
        "source_quality": "TV_MISSING",
        "forecast_url": forecast_url or "",
        "status": "forecast_page_missing",
        "note": " / ".join(errors[:3]) if errors else "forecast page returned no usable fields",
        "current_price": current_price,
        "tv_avg_target": None,
        "tv_high_target": None,
        "tv_low_target": None,
        "tv_analyst_count_target": None,
        "tv_consensus": None,
        "tv_analyst_count_rating": None,
        "tv_rating_consensus": None,
        "tv_rating_total_count": None,
        "tv_strong_buy_count": None,
        "tv_buy_count": None,
        "tv_hold_count": None,
        "tv_sell_count": None,
        "tv_strong_sell_count": None,
    }


def merge_with_previous(attempt_result, prev_norm):
    """
    今回取得がTV_FULLなら新規採用。
    TV_PARTIAL/MISSINGなら、過去TV_FULLがある場合は過去値を保持し、品質を年齢で分類。
    過去がpartialだけならpartialを更新/保持。
    """
    current_quality = attempt_result.get("source_quality", "TV_MISSING")

    if current_quality in {"TV_FULL", "TV_FULL_PLUS"}:
        if current_quality == "TV_FULL" and prev_norm and prev_norm.get("source_quality") == "TV_FULL_PLUS" and is_complete_breakdown(prev_norm):
            attempt_result = dict(attempt_result)
            for k in [
                "tv_rating_consensus",
                "tv_rating_total_count",
                "tv_strong_buy_count",
                "tv_buy_count",
                "tv_hold_count",
                "tv_sell_count",
                "tv_strong_sell_count",
            ]:
                attempt_result[k] = prev_norm.get(k)
            if is_blank(attempt_result.get("tv_consensus")):
                attempt_result["tv_consensus"] = prev_norm.get("tv_consensus") or prev_norm.get("tv_rating_consensus")
            if is_blank(attempt_result.get("tv_analyst_count_rating")):
                attempt_result["tv_analyst_count_rating"] = prev_norm.get("tv_analyst_count_rating") or prev_norm.get("tv_rating_total_count")
            attempt_result["source_quality"] = "TV_FULL_PLUS"
            attempt_result["note"] = (str(attempt_result.get("note", "")) + " / rating breakdown kept from previous TV_FULL_PLUS").strip(" /")
        return {
            "use_previous": False,
            "rating_date": today_jst(),
            **attempt_result,
        }

    if has_full_tv(prev_norm):
        rating_date = prev_norm.get("rating_date") or ""
        q = derived_quality_from_age(True, False, rating_date, prev_norm.get("source_quality", ""))
        note = f"今回TV_FULL未取得のため前回TV_FULL値を保持。attempt_quality={current_quality}; {attempt_result.get('note','')}"
        return {
            "use_previous": True,
            "rating_date": rating_date,
            "source_quality": q,
            "status": "kept_previous_tv_full",
            "note": note,
            "forecast_url": prev_norm.get("forecast_url") or attempt_result.get("forecast_url", ""),
            "tv_avg_target": prev_norm.get("tv_avg_target"),
            "tv_high_target": prev_norm.get("tv_high_target"),
            "tv_low_target": prev_norm.get("tv_low_target"),
            "tv_analyst_count_target": prev_norm.get("tv_analyst_count_target"),
            "tv_consensus": prev_norm.get("tv_consensus"),
            "tv_analyst_count_rating": prev_norm.get("tv_analyst_count_rating"),
            "tv_rating_consensus": prev_norm.get("tv_rating_consensus"),
            "tv_rating_total_count": prev_norm.get("tv_rating_total_count"),
            "tv_strong_buy_count": prev_norm.get("tv_strong_buy_count"),
            "tv_buy_count": prev_norm.get("tv_buy_count"),
            "tv_hold_count": prev_norm.get("tv_hold_count"),
            "tv_sell_count": prev_norm.get("tv_sell_count"),
            "tv_strong_sell_count": prev_norm.get("tv_strong_sell_count"),
        }

    if current_quality in {"TV_PARTIAL", "TV_PARTIAL_PLUS"}:
        return {
            "use_previous": False,
            "rating_date": today_jst(),
            **attempt_result,
        }

    if has_partial_tv(prev_norm):
        rating_date = prev_norm.get("rating_date") or ""
        return {
            "use_previous": True,
            "rating_date": rating_date,
            "source_quality": derived_quality_from_age(False, True, rating_date, prev_norm.get("source_quality", "")),
            "status": "kept_previous_tv_partial",
            "note": f"今回未取得のため前回TV_PARTIALを保持。{attempt_result.get('note','')}",
            "forecast_url": prev_norm.get("forecast_url") or attempt_result.get("forecast_url", ""),
            "tv_avg_target": None,
            "tv_high_target": None,
            "tv_low_target": None,
            "tv_analyst_count_target": prev_norm.get("tv_analyst_count_target"),
            "tv_consensus": prev_norm.get("tv_consensus"),
            "tv_analyst_count_rating": prev_norm.get("tv_analyst_count_rating"),
            "tv_rating_consensus": prev_norm.get("tv_rating_consensus"),
            "tv_rating_total_count": prev_norm.get("tv_rating_total_count"),
            "tv_strong_buy_count": prev_norm.get("tv_strong_buy_count"),
            "tv_buy_count": prev_norm.get("tv_buy_count"),
            "tv_hold_count": prev_norm.get("tv_hold_count"),
            "tv_sell_count": prev_norm.get("tv_sell_count"),
            "tv_strong_sell_count": prev_norm.get("tv_strong_sell_count"),
        }

    return {
        "use_previous": False,
        "rating_date": "",
        **attempt_result,
    }


def build_row_from_previous(meta, prev_norm):
    ticker = str(meta["ticker"])
    current = current_price_yf(ticker)
    has_full = has_full_tv(prev_norm)
    has_partial = has_partial_tv(prev_norm)
    rating_date = prev_norm.get("rating_date") if prev_norm else ""
    source_quality = derived_quality_from_age(has_full, has_partial, rating_date, prev_norm.get("source_quality", "") if prev_norm else "")
    stale = days_since(rating_date)

    avg = safe_float(prev_norm.get("tv_avg_target")) if prev_norm else None
    high = safe_float(prev_norm.get("tv_high_target")) if prev_norm else None
    low = safe_float(prev_norm.get("tv_low_target")) if prev_norm else None

    return {
        "ticker": ticker,
        "tv_symbol": prev_norm.get("tv_symbol", "") if prev_norm else "",
        "forecast_url": prev_norm.get("forecast_url", "") if prev_norm else "",
        "market": str(meta.get("market", "")),
        "asset_type": str(meta.get("asset_type", "")),
        "name": str(meta.get("name", "")),
        "theme": str(meta.get("theme", "")),
        "last_attempt_date": prev_norm.get("last_attempt_date", "") if prev_norm else "",
        "rating_date": rating_date or "",
        "next_refresh_due": next_due_from_quality(source_quality, rating_date),
        "current_price": current,
        "tv_avg_target": avg,
        "tv_high_target": high,
        "tv_low_target": low,
        "tv_upside_pct": upside(current, avg),
        "tv_high_upside_pct": upside(current, high),
        "tv_low_upside_pct": upside(current, low),
        "tv_analyst_count_target": prev_norm.get("tv_analyst_count_target") if prev_norm else None,
        "tv_consensus": prev_norm.get("tv_consensus") if prev_norm else None,
        "tv_analyst_count_rating": prev_norm.get("tv_analyst_count_rating") if prev_norm else None,
        "tv_rating_consensus": prev_norm.get("tv_rating_consensus") if prev_norm else None,
        "tv_rating_total_count": prev_norm.get("tv_rating_total_count") if prev_norm else None,
        "tv_strong_buy_count": prev_norm.get("tv_strong_buy_count") if prev_norm else None,
        "tv_buy_count": prev_norm.get("tv_buy_count") if prev_norm else None,
        "tv_hold_count": prev_norm.get("tv_hold_count") if prev_norm else None,
        "tv_sell_count": prev_norm.get("tv_sell_count") if prev_norm else None,
        "tv_strong_sell_count": prev_norm.get("tv_strong_sell_count") if prev_norm else None,
        "source_quality": source_quality,
        "freshness": "skipped_cache",
        "stale_days": stale,
        "status": "skipped_recent_or_not_due",
        "note": "今回は再取得対象外。保存済みTradingView情報を使用。",
    }


def build_not_applicable_row(meta, reason):
    ticker = str(meta["ticker"])
    current = current_price_yf(ticker) if str(meta.get("asset_type", "")) != "watch_only" else None
    return {
        "ticker": ticker,
        "tv_symbol": "",
        "forecast_url": "",
        "market": str(meta.get("market", "")),
        "asset_type": str(meta.get("asset_type", "")),
        "name": str(meta.get("name", "")),
        "theme": str(meta.get("theme", "")),
        "last_attempt_date": "",
        "rating_date": "",
        "next_refresh_due": "",
        "current_price": current,
        "tv_avg_target": None,
        "tv_high_target": None,
        "tv_low_target": None,
        "tv_upside_pct": None,
        "tv_high_upside_pct": None,
        "tv_low_upside_pct": None,
        "tv_analyst_count_target": None,
        "tv_consensus": None,
        "tv_analyst_count_rating": None,
        "tv_rating_consensus": None,
        "tv_rating_total_count": None,
        "tv_strong_buy_count": None,
        "tv_buy_count": None,
        "tv_hold_count": None,
        "tv_sell_count": None,
        "tv_strong_sell_count": None,
        "source_quality": "NOT_APPLICABLE",
        "freshness": "not_applicable",
        "stale_days": None,
        "status": "not_applicable",
        "note": reason,
    }


def build_attempt_row(meta, previous_df, symbol_map):
    ticker = str(meta["ticker"])
    prev_norm = normalize_previous(previous_row(previous_df, ticker))

    current = current_price_yf(ticker)
    tv_symbol, forecast_url, symbol_source, symbol_map = resolve_tv_symbol(ticker, symbol_map, prev_norm)

    attempt = fetch_tradingview_forecast(ticker, tv_symbol, forecast_url, current)

    # Scanner補助：Forecastページがpartial/missingのときだけ、TV判断やtv_symbolを補う。
    if attempt.get("source_quality") not in {"TV_FULL", "TV_FULL_PLUS"}:
        sc = scanner_lookup(ticker)
        if is_blank(tv_symbol) and not is_blank(sc.get("tv_symbol")):
            tv_symbol = sc.get("tv_symbol")
            forecast_url = tv_symbol_to_url(tv_symbol)

        if attempt.get("source_quality") == "TV_MISSING" and sc.get("source_quality") in {"TV_PARTIAL", "TV_PARTIAL_PLUS"}:
            attempt.update({
                "source_quality": sc.get("source_quality", "TV_PARTIAL"),
                "tv_consensus": sc.get("tv_consensus"),
                "tv_analyst_count_target": sc.get("tv_analyst_count_target"),
                "tv_analyst_count_rating": sc.get("tv_analyst_count_rating"),
                "forecast_url": forecast_url or tv_symbol_to_url(tv_symbol),
                "status": "scanner_partial",
                "note": f"Forecastページ未取得。scanner判断のみ。{attempt.get('note','')}",
            })

        # scannerでtargetまで取れる場合はFULLとして採用。ただしForecastページ優先。
        if attempt.get("source_quality") not in {"TV_FULL", "TV_FULL_PLUS"} and sc.get("source_quality") in {"TV_FULL", "TV_FULL_PLUS"}:
            if attempt.get("source_quality") == "TV_PARTIAL_PLUS" and is_complete_breakdown(attempt):
                # Forecastページで評価内訳、scannerで目標株価を取得できた場合は合成する。
                attempt.update({
                    "source_quality": "TV_FULL_PLUS",
                    "current_price": current or sc.get("current_price"),
                    "tv_avg_target": sc.get("tv_avg_target"),
                    "tv_high_target": sc.get("tv_high_target"),
                    "tv_low_target": sc.get("tv_low_target"),
                    "tv_analyst_count_target": sc.get("tv_analyst_count_target"),
                    "forecast_url": forecast_url or tv_symbol_to_url(tv_symbol),
                    "status": "forecast_breakdown_scanner_target_merged",
                    "note": "Forecastページの評価内訳とscannerのTV目標株価を合成。",
                })
                if is_blank(attempt.get("tv_consensus")):
                    attempt["tv_consensus"] = sc.get("tv_consensus") or attempt.get("tv_rating_consensus")
                if is_blank(attempt.get("tv_analyst_count_rating")):
                    attempt["tv_analyst_count_rating"] = attempt.get("tv_rating_total_count")
            else:
                attempt.update({
                    "source_quality": sc.get("source_quality", "TV_FULL"),
                    "current_price": current or sc.get("current_price"),
                    "tv_avg_target": sc.get("tv_avg_target"),
                    "tv_high_target": sc.get("tv_high_target"),
                    "tv_low_target": sc.get("tv_low_target"),
                    "tv_consensus": sc.get("tv_consensus"),
                    "tv_analyst_count_target": sc.get("tv_analyst_count_target"),
                    "tv_analyst_count_rating": sc.get("tv_analyst_count_rating"),
                    "forecast_url": forecast_url or tv_symbol_to_url(tv_symbol),
                    "status": "scanner_full",
                    "note": "ForecastページではなくscannerからTV_FULL取得。",
                })

    merged = merge_with_previous(attempt, prev_norm)
    rating_date = merged.get("rating_date") or ""
    stale = days_since(rating_date)

    avg = safe_float(merged.get("tv_avg_target"))
    high = safe_float(merged.get("tv_high_target"))
    low = safe_float(merged.get("tv_low_target"))

    source_quality = merged.get("source_quality", "TV_MISSING")
    if source_quality in {"TV_FULL", "TV_FULL_PLUS"} and stale is not None:
        source_quality = derived_quality_from_age(True, False, rating_date, source_quality)

    row = {
        "ticker": ticker,
        "tv_symbol": tv_symbol or (prev_norm.get("tv_symbol", "") if prev_norm else ""),
        "forecast_url": merged.get("forecast_url") or forecast_url or "",
        "market": str(meta.get("market", "")),
        "asset_type": str(meta.get("asset_type", "")),
        "name": str(meta.get("name", "")),
        "theme": str(meta.get("theme", "")),
        "last_attempt_date": today_jst(),
        "rating_date": rating_date,
        "next_refresh_due": next_due_from_quality(source_quality, rating_date),
        "current_price": current,
        "tv_avg_target": avg,
        "tv_high_target": high,
        "tv_low_target": low,
        "tv_upside_pct": upside(current, avg),
        "tv_high_upside_pct": upside(current, high),
        "tv_low_upside_pct": upside(current, low),
        "tv_analyst_count_target": merged.get("tv_analyst_count_target"),
        "tv_consensus": merged.get("tv_consensus"),
        "tv_analyst_count_rating": merged.get("tv_analyst_count_rating"),
        "tv_rating_consensus": merged.get("tv_rating_consensus"),
        "tv_rating_total_count": merged.get("tv_rating_total_count"),
        "tv_strong_buy_count": merged.get("tv_strong_buy_count"),
        "tv_buy_count": merged.get("tv_buy_count"),
        "tv_hold_count": merged.get("tv_hold_count"),
        "tv_sell_count": merged.get("tv_sell_count"),
        "tv_strong_sell_count": merged.get("tv_strong_sell_count"),
        "source_quality": source_quality,
        "freshness": "attempted",
        "stale_days": stale,
        "status": merged.get("status", attempt.get("status", "")),
        "note": merged.get("note", ""),
    }

    if row["tv_symbol"]:
        symbol_map = upsert_symbol_map(symbol_map, {
            "ticker": ticker,
            "tv_symbol": row["tv_symbol"],
            "forecast_url": row["forecast_url"] or tv_symbol_to_url(row["tv_symbol"]),
            "last_verified": today_jst(),
            "source": symbol_source,
            "status": row["status"],
            "note": row["note"][:200],
        })

    return row, symbol_map



def complete_breakdown_row(r):
    return all(not is_blank(r.get(k)) for k in [
        "tv_strong_buy_count",
        "tv_buy_count",
        "tv_hold_count",
        "tv_sell_count",
        "tv_strong_sell_count",
    ])


def breakdown_report_text(r):
    if not complete_breakdown_row(r):
        return "内訳 —"
    return (
        f"内訳 強買{fmt_num(r.get('tv_strong_buy_count'))}/"
        f"買{fmt_num(r.get('tv_buy_count'))}/"
        f"中{fmt_num(r.get('tv_hold_count'))}/"
        f"売{fmt_num(r.get('tv_sell_count'))}/"
        f"強売{fmt_num(r.get('tv_strong_sell_count'))}"
    )


def main():
    OUT.mkdir(exist_ok=True)
    (OUT / "latest").mkdir(exist_ok=True)

    mode = mode_from_env()
    one_ticker = env_ticker()

    previous_df = load_csv(DATA / "ratings_master.csv", RATINGS_COLUMNS)
    symbol_map = load_csv(DATA / "tv_symbol_map.csv", SYMBOL_MAP_COLUMNS)

    wl = active_watchlist()

    rows = []
    health = []
    attempted = 0
    skipped = 0

    for _, meta in wl.iterrows():
        ticker = str(meta["ticker"])
        market = str(meta.get("market", ""))
        asset_type = str(meta.get("asset_type", ""))

        if market != "US":
            row = build_not_applicable_row(meta, "米国株以外はTradingView W04対象外")
            rows.append(row)
            continue

        if asset_type in {"etf", "watch_only"}:
            row = build_not_applicable_row(meta, "ETF/watch_onlyはTradingViewアナリスト予測対象外")
            rows.append(row)
            continue

        prev_norm = normalize_previous(previous_row(previous_df, ticker))

        if should_attempt(mode, ticker, meta, prev_norm):
            row, symbol_map = build_attempt_row(meta, previous_df, symbol_map)
            attempted += 1
        else:
            row = build_row_from_previous(meta, prev_norm)
            skipped += 1

        rows.append(row)

        level = "INFO"
        if row["source_quality"] in {"TV_MISSING", "TV_PARTIAL", "TV_PARTIAL_PLUS", "TV_CACHE_OLD", "TV_STALE"}:
            level = "WARN"

        health.append([
            ticker,
            market,
            level,
            "tv_ratings",
            row["source_quality"],
            f"mode={mode}; status={row['status']}; stale_days={row.get('stale_days')}; {row.get('note','')}",
        ])

    append_health("W04_tv_ratings", health)

    df = pd.DataFrame(rows, columns=RATINGS_COLUMNS)
    df.to_csv(DATA / "ratings_master.csv", index=False, encoding="utf-8-sig")
    save_symbol_map(symbol_map)

    us = df[(df["market"] == "US") & (~df["asset_type"].isin(["etf", "watch_only"]))]
    counts = us["source_quality"].value_counts().to_dict()
    target_count = int(us["tv_avg_target"].notna().sum())
    total = len(us)
    target_cov = (target_count / total) if total else 0.0

    report_path = OUT / f"ratings_weekly_{today_jst()}.md"
    latest_path = OUT / "latest" / "ratings_latest.md"

    next_monthly = ""
    d = today_date()
    probe = d
    for _ in range(45):
        if probe.weekday() == 5 and 1 <= probe.day <= 7 and probe > d:
            next_monthly = probe.isoformat()
            break
        probe += timedelta(days=1)

    lines = [
        "【CIS-W04｜TradingViewレーティング更新 v0.6.3】",
        f"実行日：{today_jst()} JST",
        f"実行モード：{mode}",
        f"単独銘柄：{one_ticker or '—'}",
        "",
        "## TradingViewレーティング品質",
        f"TV_FULL_PLUS：{counts.get('TV_FULL_PLUS', 0)}件",
        f"TV_FULL：{counts.get('TV_FULL', 0)}件",
        f"TV_CACHE：{counts.get('TV_CACHE', 0)}件",
        f"TV_CACHE_OLD：{counts.get('TV_CACHE_OLD', 0)}件",
        f"TV_STALE：{counts.get('TV_STALE', 0)}件",
        f"TV_PARTIAL_PLUS：{counts.get('TV_PARTIAL_PLUS', 0)}件",
        f"TV_PARTIAL：{counts.get('TV_PARTIAL', 0)}件",
        f"TV_MISSING：{counts.get('TV_MISSING', 0)}件",
        f"目標株価カバレッジ：{target_count}/{total}件（{target_cov:.1%}）",
        f"評価内訳カバレッジ：{int(us.apply(complete_breakdown_row, axis=1).sum())}/{total}件",
        f"今回取得試行：{attempted}件",
        f"今回スキップ：{skipped}件",
        f"次回月次再確認目安：{next_monthly or '—'}",
        "",
        "## 重要ルール",
        "- D03は毎日TradingViewへアクセスしない。ratings_master.csvを読むだけ。",
        "- W04は週1でTV_MISSING/TV_PARTIAL/TV_PARTIAL_PLUSだけ再挑戦し、月1で全米国株を再確認する。",
        "- 一度TV_FULLで取れた値は、次回失敗しても消さない。",
        "- Yahoo Finance由来のレーティング/目標株価はD03のレーティング欄に出さない。",
        "",
        "## 確認が必要な銘柄",
    ]

    needs = us[us["source_quality"].isin(["TV_PARTIAL", "TV_PARTIAL_PLUS", "TV_MISSING", "TV_CACHE_OLD", "TV_STALE"])].copy()
    if needs.empty:
        lines.append("該当なし")
    else:
        for _, r in needs.iterrows():
            lines.append(
                f"- {r['ticker']}｜{r['name']}｜{r['source_quality']}｜"
                f"rating_date={r['rating_date'] or '—'}｜next={r['next_refresh_due'] or '—'}｜note={r['note']}"
            )

    lines.extend([
        "",
        "## 上昇余地が大きい順（TV目標株価あり）",
    ])

    got = us[us["tv_avg_target"].notna()].copy()
    if got.empty:
        lines.append("該当なし")
    else:
        got = got.sort_values("tv_upside_pct", ascending=False)
        for _, r in got.iterrows():
            age = ""
            if not is_blank(r.get("stale_days")):
                age = f" / {int(float(r['stale_days']))}日古"
            count = r["tv_analyst_count_target"] if not is_blank(r["tv_analyst_count_target"]) else r["tv_analyst_count_rating"]
            lines.append(
                f"- {r['ticker']}｜{r['name']}｜{r['source_quality']}｜"
                f"{count if not is_blank(count) else '—'}人｜"
                f"{r['tv_consensus'] if not is_blank(r['tv_consensus']) else '—'}｜"
                f"{breakdown_report_text(r)}｜"
                f"平均目標 {fmt_num(r['tv_avg_target'])}｜乖離 {fmt_pct(r['tv_upside_pct'])}{age}"
            )

    lines.extend([
        "",
        "## TV_PARTIAL / TV_PARTIAL_PLUS（判断/内訳のみ・平均目標未取得）",
    ])

    partial = us[us["source_quality"].isin(["TV_PARTIAL", "TV_PARTIAL_PLUS"])].copy()
    if partial.empty:
        lines.append("該当なし")
    else:
        for _, r in partial.iterrows():
            count = r["tv_analyst_count_target"] if not is_blank(r["tv_analyst_count_target"]) else r["tv_analyst_count_rating"]
            lines.append(
                f"- {r['ticker']}｜{r['name']}｜"
                f"{count if not is_blank(count) else '—'}人｜"
                f"{r['tv_consensus'] if not is_blank(r['tv_consensus']) else '—'}｜平均目標 未取得"
            )

    content = "\n".join(lines)
    report_path.write_text(content, encoding="utf-8")
    latest_path.write_text(content, encoding="utf-8")

    print(f"created {DATA / 'ratings_master.csv'}")
    print(f"created {DATA / 'tv_symbol_map.csv'}")
    print(f"created {report_path}")
    print(f"created {latest_path}")
    print(f"mode={mode}; attempted={attempted}; skipped={skipped}; target_coverage={target_cov:.1%}")


if __name__ == "__main__":
    main()
