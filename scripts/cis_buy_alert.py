
import csv
import pandas as pd
import yfinance as yf
from cis_common import DATA, OUT, today_jst, active_watchlist, append_health

SCALE_HARD_HIGH_RATIO = 10.0
SCALE_SOFT_HIGH_RATIO = 4.0
SCALE_HARD_LOW_RATIO = 0.25
NEAR_TOLERANCE_PCT = 1.0

MICROCAP_SIZE_LIMIT = {"KITT", "OPTX", "POET", "AXTI", "RGTI"}
LOW_PRIORITY_PROBE = {"8410.T", "7821.T"}
SCALE_SENSITIVE_TICKERS = {"NOW", "RGTI", "MSTR", "POET", "KITT", "AXTI", "OPTX", "SPCX"}

SPECIAL_STATUS_DISPLAY = {
    "scale_rebase_pending_v1_3_1": "価格スケール確認待ち",
    "conditional_no_price_signal_v1_3_1": "条件確認待ち",
    "watch_only": "監視のみ",
}

CONDITIONAL_NOTES = {
    "MSTR": "MSTRは価格だけで判定しない。BTCトレンド、BTCの200日線、mNAV/プレミアム、希薄化、転換社債を確認。",
}


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


def distance_pct(price, target):
    p = safe_float(price)
    t = safe_float(target)
    if p is None or t is None or p == 0:
        return None
    return (p - t) / p * 100


def distance_label(price, target, target_name):
    pct = distance_pct(price, target)
    if pct is None:
        return f"{target_name}まで：—"
    if pct > 0:
        return f"{target_name}まで：あと{pct:.2f}%下落"
    if pct < 0:
        return f"{target_name}到達済み：{abs(pct):.2f}%下回り"
    return f"{target_name}到達済み：0.00%"


def get_price_snapshot(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="7d", interval="1d", auto_adjust=False)
        if hist is None or hist.empty:
            return None

        hist = hist.reset_index()
        hist["Date"] = pd.to_datetime(hist["Date"]).dt.date
        hist = hist.dropna(subset=["Close"]).sort_values("Date")
        if hist.empty:
            return None

        latest = hist.iloc[-1]
        price = float(latest["Close"])
        price_date = str(latest["Date"])

        daily_diff = None
        daily_pct = None
        if len(hist) >= 2:
            prev = hist.iloc[-2]
            prev_close = float(prev["Close"])
            if prev_close != 0:
                daily_diff = price - prev_close
                daily_pct = (price - prev_close) / prev_close * 100

        return {
            "price": price,
            "price_date": price_date,
            "daily_diff": daily_diff,
            "daily_pct": daily_pct,
            "source": "yfinance",
        }
    except Exception:
        return None


def load_ratings():
    p = DATA / "ratings_master.csv"
    if not p.exists():
        return pd.DataFrame(columns=["ticker"])
    try:
        return pd.read_csv(p)
    except Exception:
        return pd.DataFrame(columns=["ticker"])


def rating_record(ratings, ticker):
    if ratings.empty or "ticker" not in ratings.columns:
        return None
    rr = ratings[ratings["ticker"].astype(str).eq(str(ticker))]
    if rr.empty:
        return None
    return rr.iloc[0].to_dict()


def rating_freshness_suffix(r):
    if r is None:
        return ""
    status = str(r.get("status", ""))
    source = str(r.get("source_used", ""))
    rating_date = r.get("rating_date", "")
    stale_days = r.get("stale_days", "")
    freshness = str(r.get("freshness", ""))

    if status in {"not_applicable_non_us", "not_applicable_etf", "watch_only"}:
        return ""

    if freshness == "fresh":
        return f" / 更新 {rating_date}" if not is_blank(rating_date) else ""

    if freshness == "manual":
        return f" / 手動補完 {rating_date}" if not is_blank(rating_date) else " / 手動補完"

    if freshness in {"cache_ok", "cache_stale"} or "previous_cache" in source:
        days = "—" if is_blank(stale_days) else str(int(float(stale_days)))
        label = "前回値" if freshness == "cache_ok" else "古い前回値"
        return f" / {label} {rating_date or '日付不明'}（{days}日経過）"

    return ""


def rating_line(ratings, ticker, market, asset_type):
    if market == "JP":
        return "レーティング：国内株は任意補完待ち"
    if asset_type == "etf":
        return "レーティング：対象外（ETF）"

    r = rating_record(ratings, ticker)
    if r is None:
        return "レーティング：未取得（ratings_masterに行なし）"

    source = r.get("source_used", "未取得")
    analyst_count = r.get("analyst_count", "")
    consensus = r.get("consensus", "")
    avg_target = r.get("avg_target", "")
    upside = r.get("upside_pct", "")
    status = str(r.get("status", "")).strip()
    note = str(r.get("note", "")).strip()

    if all(is_blank(v) for v in [analyst_count, consensus, avg_target, upside]):
        if status in {"not_applicable_non_us", "not_applicable_etf", "watch_only"}:
            return "レーティング：対象外"
        suffix = rating_freshness_suffix(r)
        return f"レーティング：未取得 / status={status}{suffix}"

    ac = "—" if is_blank(analyst_count) else str(analyst_count)
    con = "—" if is_blank(consensus) else str(consensus)
    tgt = fmt_num(avg_target)
    up = fmt_pct(upside)
    suffix = rating_freshness_suffix(r)
    return f"レーティング：{source} / {ac}人 / {con} / 平均目標 {tgt} / 乖離 {up}{suffix}"


def rating_brief(ratings, ticker, market, asset_type):
    if market == "JP":
        return "評：国内任意"
    if asset_type == "etf":
        return "評：ETF対象外"

    r = rating_record(ratings, ticker)
    if r is None:
        return "評：未取得"

    status = str(r.get("status", ""))
    source = str(r.get("source_used", ""))
    analyst_count = r.get("analyst_count", "")
    consensus = r.get("consensus", "")
    avg_target = r.get("avg_target", "")
    up = r.get("upside_pct", "")
    freshness = str(r.get("freshness", ""))
    stale_days = r.get("stale_days", "")

    if all(is_blank(v) for v in [analyst_count, consensus, avg_target, up]):
        if status in {"not_applicable_non_us", "not_applicable_etf", "watch_only"}:
            return "評：対象外"
        return f"評：未取得({status})"

    if source.startswith("TradingView"):
        src = "TV"
    elif "Yahoo" in source:
        src = "YF"
    elif "previous_cache" in source:
        src = "前回"
    else:
        src = source.replace(" fallback", "")[:8]

    ac = "—" if is_blank(analyst_count) else str(analyst_count)
    con = "—" if is_blank(consensus) else str(consensus)
    age = ""
    if freshness in {"cache_ok", "cache_stale"} or "previous_cache" in source:
        days = "—" if is_blank(stale_days) else str(int(float(stale_days)))
        age = f" {days}日古"
        if freshness == "cache_stale":
            age = f" 古い{days}日"

    return f"評：{src}{age} {ac}人 {con} 目標{fmt_num(avg_target)} 乖離{fmt_pct(up)}"


def basis_display(status):
    s = str(status).strip()
    if s == "reviewed_v1_3_1":
        return "v1.3.1採用"
    if s == "scale_rebase_pending_v1_3_1":
        return "v1.3.1：価格スケール確認待ち"
    if s == "conditional_no_price_signal_v1_3_1":
        return "v1.3.1：条件確認待ち"
    if s == "locked_v1_2":
        return "v1.2採用（価格スケール修正）"
    if s == "proposed_v1_1":
        return "v1.2採用（Step4C修正）"
    if s in {"locked_v1", "locked_seed"}:
        return "v1.2採用"
    if s == "watch_only":
        return "監視のみ"
    if is_blank(s):
        return "—"
    return s


def base_decision(price, probe, core, strong):
    if price is None:
        return "価格未取得"
    if core is None:
        return "基準未設定"

    if strong is not None and price <= strong:
        dist_strong = distance_pct(price, strong)
        if dist_strong is not None and -NEAR_TOLERANCE_PCT <= dist_strong <= 0:
            return "強く買いたい近接"
        return "強く買いたい"

    if price <= core:
        dist_core = distance_pct(price, core)
        if dist_core is not None and -NEAR_TOLERANCE_PCT <= dist_core <= 0:
            return "本命近接"
        return "本命買い"

    if probe is not None and price <= probe:
        dist_probe = distance_pct(price, probe)
        if dist_probe is not None and -NEAR_TOLERANCE_PCT <= dist_probe <= 0:
            return "打診近接"
        return "打診買い"

    return "見送り"


def price_gap_review(ticker, price, core):
    if price is None or core is None or core == 0:
        return None, None, None

    ratio = price / core

    if ratio < SCALE_HARD_LOW_RATIO:
        return f"価格スケール要確認（現在値/本命={ratio:.2f}倍）", None, ratio

    if ratio > SCALE_HARD_HIGH_RATIO:
        return f"価格スケール要確認（現在値/本命={ratio:.2f}倍）", None, ratio

    if ratio > SCALE_SOFT_HIGH_RATIO:
        if ticker in SCALE_SENSITIVE_TICKERS:
            return f"価格スケール要確認（現在値/本命={ratio:.2f}倍）", None, ratio
        return None, f"本命価格の{ratio:.2f}倍。価格異常とは断定せず、高値圏/基準再点検候補として扱う。", ratio

    return None, None, ratio


def card_sort_key(card):
    order = {
        "強く買いたい": 0,
        "本命買い": 1,
        "打診買い": 2,
        "強く買いたい近接": 3,
        "本命近接": 4,
        "打診近接": 5,
        "条件確認待ち": 6,
        "価格スケール確認待ち": 7,
        "価格スケール要確認": 8,
        "基準未設定": 9,
        "価格未取得": 10,
        "見送り": 11,
        "監視のみ": 12,
    }
    decision = str(card["decision"])
    key = "価格スケール要確認" if decision.startswith("価格スケール要確認") else decision
    dist = safe_float(card.get("distance"))
    return (order.get(key, 99), 999999 if dist is None else dist, card["ticker"])


def short_line(card):
    return (
        f"{card['ticker']}｜{card['name']}｜判定：{card['decision']}｜"
        f"現在値：{fmt_num(card['price'])}｜本命：{fmt_num(card['core'])}｜"
        f"{distance_label(card['price'], card['core'], '本命')}｜{card['rating_brief']}"
    )


def add_quick_section(lines, title, cards):
    lines.append(title)
    if not cards:
        lines.append("該当なし")
    else:
        for c in cards:
            lines.append(f"- {short_line(c)}")
    lines.append("")


def main():
    wl = active_watchlist()
    bz = pd.read_csv(DATA / "buy_zone_master.csv")
    ratings = load_ratings()

    health = []
    cards = []
    csv_rows = []

    for _, meta in wl.iterrows():
        ticker = str(meta["ticker"])
        market = str(meta.get("market", ""))
        asset_type = str(meta.get("asset_type", ""))

        b = bz[bz["ticker"].astype(str).eq(ticker)]
        if b.empty:
            probe = core = strong = None
            raw_basis_status = "missing"
            health.append([ticker, market, "ERROR", "基準未設定", "buy_zone_master", "missing row"])
        else:
            b = b.iloc[0]
            probe = safe_float(b.get("probe_price", ""))
            core = safe_float(b.get("core_price", ""))
            strong = safe_float(b.get("strong_price", ""))
            raw_basis_status = str(b.get("status", ""))

        snap = get_price_snapshot(ticker) if asset_type != "watch_only" else None
        if snap is None:
            price = None
            price_date = ""
            daily_pct = daily_diff = None
            if asset_type != "watch_only":
                health.append([ticker, market, "ERROR", "価格未取得", "yfinance", "current price not available"])
        else:
            price = snap["price"]
            price_date = snap["price_date"]
            daily_pct = snap["daily_pct"]
            daily_diff = snap["daily_diff"]

        note_parts = []

        if raw_basis_status in SPECIAL_STATUS_DISPLAY:
            decision = SPECIAL_STATUS_DISPLAY[raw_basis_status]
            if raw_basis_status == "scale_rebase_pending_v1_3_1":
                note_parts.append("通常買い場判定から一時除外。価格スケール/分割/逆分割/取得ソース確認待ち。")
                health.append([ticker, market, "INFO", "価格スケール確認待ち", "buy_zone_master", "basis status"])
            elif raw_basis_status == "conditional_no_price_signal_v1_3_1":
                note_parts.append("通常買い場判定から除外。条件確認後のみ判断。")
                health.append([ticker, market, "INFO", "条件確認待ち", "buy_zone_master", "basis status"])
            elif raw_basis_status == "watch_only":
                note_parts.append("取引可能ティッカー未確認。価格判定しない。")
        else:
            decision = base_decision(price, probe, core, strong)

            hard_warning, soft_note, ratio = price_gap_review(ticker, price, core)
            if hard_warning is not None:
                decision = hard_warning
                health.append([ticker, market, "WARN", "価格スケール要確認", "buy_zone_master/yfinance", hard_warning])
                note_parts.append("買い場判定停止。価格桁・分割・逆分割・基準価格を要確認。")
            elif soft_note is not None:
                note_parts.append(soft_note)
                health.append([ticker, market, "INFO", "高値圏/基準再点検候補", "buy_zone_master/yfinance", soft_note])

        if ticker in CONDITIONAL_NOTES:
            note_parts.append(CONDITIONAL_NOTES[ticker])

        if ticker in MICROCAP_SIZE_LIMIT:
            note_parts.append("超小型/高ボラ枠。判定が出ても投資サイズ制限。")

        if ticker in LOW_PRIORITY_PROBE and decision == "打診買い":
            note_parts.append("低優先打診。毎朝の最優先候補からは分離。")

        if ticker in SCALE_SENSITIVE_TICKERS and not str(decision).startswith("価格スケール要確認") and decision != "価格スケール確認待ち":
            note_parts.append("日次価格桁チェック対象。")

        dist = distance_pct(price, core)
        ratio = None if price is None or core is None or core == 0 else price / core
        rline = rating_line(ratings, ticker, market, asset_type)
        rbrief = rating_brief(ratings, ticker, market, asset_type)
        note = " / ".join([n for n in note_parts if n])

        card = {
            "ticker": ticker,
            "market": market,
            "name": meta.get("name", ""),
            "theme": meta.get("theme", ""),
            "asset_type": asset_type,
            "price": price,
            "price_date": price_date,
            "daily_pct": daily_pct,
            "daily_diff": daily_diff,
            "probe": probe,
            "core": core,
            "strong": strong,
            "distance": dist,
            "gap_ratio": ratio,
            "decision": decision,
            "rating": rline,
            "rating_brief": rbrief,
            "basis_status": raw_basis_status,
            "basis_display": basis_display(raw_basis_status),
            "note": note,
        }
        cards.append(card)
        csv_rows.append(card)

    append_health("D03_buy_alert", health)

    cards = sorted(cards, key=card_sort_key)

    main_buy_cards = [c for c in cards if c["decision"] in {"強く買いたい", "本命買い"} and c["ticker"] not in MICROCAP_SIZE_LIMIT]
    probe_cards_all = [c for c in cards if c["decision"] == "打診買い" and c["ticker"] not in MICROCAP_SIZE_LIMIT]
    low_priority_cards = [c for c in probe_cards_all if c["ticker"] in LOW_PRIORITY_PROBE]
    probe_cards = [c for c in probe_cards_all if c["ticker"] not in LOW_PRIORITY_PROBE]
    microcap_cards = [c for c in cards if c["decision"] in {"強く買いたい", "本命買い", "打診買い", "強く買いたい近接", "本命近接", "打診近接"} and c["ticker"] in MICROCAP_SIZE_LIMIT]
    near_cards = [c for c in cards if c["decision"] in {"強く買いたい近接", "本命近接", "打診近接"} and c["ticker"] not in MICROCAP_SIZE_LIMIT]
    condition_cards = [c for c in cards if c["decision"] in {"条件確認待ち", "価格スケール確認待ち"}]
    warning_cards = [c for c in cards if str(c["decision"]).startswith("価格スケール要確認")]
    far_cards = [
        c for c in cards
        if c not in main_buy_cards
        and c not in probe_cards
        and c not in low_priority_cards
        and c not in microcap_cards
        and c not in near_cards
        and c not in condition_cards
        and c not in warning_cards
        and safe_float(c.get("gap_ratio")) is not None
        and safe_float(c.get("gap_ratio")) > SCALE_SOFT_HIGH_RATIO
    ]
    other_cards = [
        c for c in cards
        if c not in main_buy_cards
        and c not in probe_cards
        and c not in low_priority_cards
        and c not in microcap_cards
        and c not in near_cards
        and c not in condition_cards
        and c not in warning_cards
        and c not in far_cards
    ]

    today = today_jst()
    report_path = OUT / f"cis_buy_alert_{today}.md"
    csv_path = OUT / f"cis_buy_alert_{today}.csv"

    lines = [
        "🆕【今日の更新はここから】",
        "【CIS-D03｜買い場アラート】",
        f"実行日：{today} JST",
        "対象：監視リスト全銘柄",
        "ルール：buy_zone_master.csvを読むだけ。毎日ゴールをずらさない。",
        "v4.2対応：まず見る候補を全件表示し、本命買い/打診買い/近接/低優先/超小型を分離。候補行にもレーティングを表示。",
        "",
        "## 今日のサマリー",
        f"本命買い以上：{len(main_buy_cards)}件",
        f"打診買い：{len(probe_cards)}件",
        f"低優先打診：{len(low_priority_cards)}件",
        f"超小型・サイズ制限：{len(microcap_cards)}件",
        f"近接：{len(near_cards)}件",
        f"条件/価格スケール確認待ち：{len(condition_cards)}件",
        f"価格スケール要確認：{len(warning_cards)}件",
        f"本命から大きく遠い銘柄：{len(far_cards)}件",
        f"見送り・その他：{len(other_cards)}件",
        "",
        "## まず見る候補・全件",
    ]

    add_quick_section(lines, "### 最優先：本命買い以上", main_buy_cards)
    add_quick_section(lines, "### 次点：打診買い", probe_cards)
    add_quick_section(lines, "### 低優先打診", low_priority_cards)
    add_quick_section(lines, "### 超小型・サイズ制限", microcap_cards)
    add_quick_section(lines, "### 近接", near_cards)
    add_quick_section(lines, "### 条件確認待ち・価格スケール確認待ち", condition_cards)

    def add_detail_section(title, section_cards):
        lines.append(title)
        if not section_cards:
            lines.append("該当なし")
            lines.append("")
            return

        for c in section_cards:
            lines.extend([
                f"【{c['ticker']}｜{c['name']}｜{c['theme']}】",
                f"現在値：{fmt_num(c['price'])}（{c['price_date'] or '—'}）",
                f"前日比：{fmt_pct(c['daily_pct'])} / {fmt_num(c['daily_diff'])}",
                "",
                f"打診：{fmt_num(c['probe'])}",
                f"本命：{fmt_num(c['core'])}",
                f"強く買いたい：{fmt_num(c['strong'])}",
                distance_label(c["price"], c["probe"], "打診"),
                distance_label(c["price"], c["core"], "本命"),
                "",
                f"判定：{c['decision']}",
                c["rating"],
                f"基準状態：{c['basis_display']}",
            ])
            if c["note"]:
                lines.append(f"注意：{c['note']}")
            lines.append("")

    add_detail_section("## 詳細：最優先・本命買い以上", main_buy_cards)
    add_detail_section("## 詳細：打診買い", probe_cards)
    add_detail_section("## 詳細：低優先打診", low_priority_cards)
    add_detail_section("## 詳細：超小型・サイズ制限", microcap_cards)
    add_detail_section("## 詳細：近接", near_cards)
    add_detail_section("## 詳細：条件確認待ち・価格スケール確認待ち", condition_cards)
    add_detail_section("## 詳細：価格スケール要確認", warning_cards)
    add_detail_section("## 詳細：本命から大きく遠い銘柄", far_cards)
    add_detail_section("## 見送り・その他", other_cards)

    report_path.write_text("\n".join(lines), encoding="utf-8")

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "ticker", "market", "name", "theme", "asset_type", "price", "price_date", "daily_pct", "daily_diff",
            "probe", "core", "strong", "distance", "gap_ratio", "decision", "rating", "rating_brief",
            "basis_status", "basis_display", "note"
        ])
        writer.writeheader()
        writer.writerows(csv_rows)

    print(f"created {report_path}")
    print(f"created {csv_path}")


if __name__ == "__main__":
    main()
