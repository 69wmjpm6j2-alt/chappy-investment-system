import csv
import pandas as pd
import yfinance as yf
from cis_common import DATA, OUT, today_jst, active_watchlist, append_health

SCALE_HARD_HIGH_RATIO = 10.0
SCALE_SOFT_HIGH_RATIO = 4.0
SCALE_HARD_LOW_RATIO = 0.25

MICROCAP_SIZE_LIMIT = {"KITT", "OPTX", "POET", "AXTI", "RGTI"}
SCALE_SENSITIVE_TICKERS = {"NOW", "RGTI", "MSTR", "POET", "KITT", "AXTI", "OPTX", "SPCX"}

CONDITIONAL_TICKERS = {
    "MSTR": "MSTRは株価だけで判定しない。BTCトレンド、BTCの200日線、mNAV/プレミアム、希薄化、転換社債を確認。"
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
    return s == "" or s in {"nan", "none", "null"}


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


def distance_label(price, target, target_name):
    p = safe_float(price)
    t = safe_float(target)
    if p is None or t is None or p == 0:
        return f"{target_name}まで：—"
    diff_pct = (p - t) / p * 100
    if p > t:
        return f"{target_name}まで：あと{diff_pct:.2f}%下落"
    if p < t:
        return f"{target_name}到達済み：{abs(diff_pct):.2f}%下回り"
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

        return {"price": price, "price_date": price_date, "daily_diff": daily_diff, "daily_pct": daily_pct, "source": "yfinance"}
    except Exception:
        return None


def rating_line(ratings, ticker, market, asset_type):
    if market == "JP":
        return "レーティング：国内株は任意補完待ち"
    if asset_type == "etf":
        return "レーティング：対象外（ETF）"

    if ratings.empty or "ticker" not in ratings.columns:
        return "レーティング：未更新"

    rr = ratings[ratings["ticker"].astype(str).eq(str(ticker))]
    if rr.empty:
        return "レーティング：未更新"

    r = rr.iloc[0]
    analyst_count = r.get("analyst_count", "")
    consensus = r.get("consensus", "")
    avg_target = r.get("avg_target", "")
    upside = r.get("upside_pct", "")
    source_used = r.get("source_used", "")
    status = str(r.get("status", "")).strip()

    values = [source_used, analyst_count, consensus, avg_target, upside]
    if all(is_blank(v) for v in values):
        if status in {"not_applicable", "ETF_no_analyst_required"}:
            return "レーティング：対象外"
        return "レーティング：未更新"

    source = "未更新" if is_blank(source_used) else str(source_used)
    ac = "—" if is_blank(analyst_count) else str(analyst_count)
    con = "—" if is_blank(consensus) else str(consensus)
    tgt = fmt_num(avg_target)
    up = fmt_pct(upside)
    return f"レーティング：{source} / {ac}人 / {con} / 平均目標 {tgt} / 乖離 {up}"


def basis_display(status):
    s = str(status).strip()
    if s in {"proposed_v1_1", "locked_v1", "locked_seed", "locked_v1_2"}:
        if s == "locked_v1_2":
            return "v1.2採用（価格スケール修正）"
        if s == "proposed_v1_1":
            return "v1.2採用（Step4C修正）"
        return "v1.2採用"
    if s == "watch_only":
        return "対象外"
    if s == "" or s.lower() == "nan":
        return "—"
    return s


def base_decision(price, probe, core, strong):
    if price is None:
        return "価格未取得"
    if core is None:
        return "基準未設定"
    if strong is not None and price <= strong:
        return "強く買いたい"
    if price <= core:
        return "本命買い"
    if probe is not None and price <= probe:
        return "打診買い"
    return "見送り"


def distance_to_core(price, core):
    if price is None or core is None or price == 0:
        return None
    return (price - core) / price * 100


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
    order = {"強く買いたい": 0, "本命買い": 1, "打診買い": 2, "条件付き強く買いたい": 3, "条件付き本命買い": 4, "条件付き打診買い": 5, "価格スケール要確認": 6, "基準未設定": 7, "価格未取得": 8, "見送り": 9}
    decision = str(card["decision"])
    key = "価格スケール要確認" if decision.startswith("価格スケール要確認") else decision
    dist = safe_float(card.get("distance"))
    return (order.get(key, 99), 999999 if dist is None else dist, card["ticker"])


def short_line(card):
    return f"{card['ticker']}｜{card['name']}｜判定：{card['decision']}｜現在値：{fmt_num(card['price'])}｜本命：{fmt_num(card['core'])}｜{distance_label(card['price'], card['core'], '本命')}"


def main():
    wl = active_watchlist()
    bz = pd.read_csv(DATA / "buy_zone_master.csv")
    ratings_path = DATA / "ratings_master.csv"
    ratings = pd.read_csv(ratings_path) if ratings_path.exists() else pd.DataFrame(columns=["ticker"])

    health = []
    cards = []
    csv_rows = []

    for _, meta in wl.iterrows():
        ticker = str(meta["ticker"])
        market = str(meta.get("market", ""))
        asset_type = str(meta.get("asset_type", ""))

        if asset_type == "watch_only":
            card = {"ticker": ticker, "market": market, "name": meta.get("name", ""), "theme": meta.get("theme", ""), "price": None, "price_date": "", "daily_pct": None, "daily_diff": None, "probe": None, "core": None, "strong": None, "distance": None, "gap_ratio": None, "decision": "基準未設定", "rating": "レーティング：対象外", "basis_status": "watch_only", "basis_display": "対象外", "note": "取引可能ティッカー未確認。価格判定しない。"}
            cards.append(card)
            csv_rows.append(card)
            continue

        b = bz[bz["ticker"].astype(str).eq(ticker)]
        if b.empty:
            health.append([ticker, market, "ERROR", "基準未設定", "buy_zone_master", "missing row"])
            probe = core = strong = None
            raw_basis_status = "missing"
        else:
            b = b.iloc[0]
            probe = safe_float(b.get("probe_price", ""))
            core = safe_float(b.get("core_price", ""))
            strong = safe_float(b.get("strong_price", ""))
            raw_basis_status = str(b.get("status", ""))

        snap = get_price_snapshot(ticker)
        if snap is None:
            price = None
            price_date = ""
            daily_pct = daily_diff = None
            health.append([ticker, market, "ERROR", "価格未取得", "yfinance", "current price not available"])
        else:
            price = snap["price"]
            price_date = snap["price_date"]
            daily_pct = snap["daily_pct"]
            daily_diff = snap["daily_diff"]

        decision = base_decision(price, probe, core, strong)
        note_parts = []

        hard_warning, soft_note, ratio = price_gap_review(ticker, price, core)
        if hard_warning is not None:
            decision = hard_warning
            health.append([ticker, market, "WARN", "価格スケール要確認", "buy_zone_master/yfinance", hard_warning])
            note_parts.append("買い場判定停止。価格桁・分割・逆分割・基準価格を要確認。")
        elif soft_note is not None:
            note_parts.append(soft_note)
            health.append([ticker, market, "INFO", "高値圏/基準再点検候補", "buy_zone_master/yfinance", soft_note])

        if ticker in CONDITIONAL_TICKERS:
            if decision in {"強く買いたい", "本命買い", "打診買い"}:
                decision = "条件付き" + decision
            note_parts.append(CONDITIONAL_TICKERS[ticker])

        if ticker in MICROCAP_SIZE_LIMIT:
            note_parts.append("超小型/高ボラ枠。判定が出ても投資サイズ制限。")

        if ticker in SCALE_SENSITIVE_TICKERS and not str(decision).startswith("価格スケール要確認"):
            note_parts.append("日次価格桁チェック対象。")

        dist = distance_to_core(price, core)
        rline = rating_line(ratings, ticker, market, asset_type)
        note = " / ".join([n for n in note_parts if n])

        card = {"ticker": ticker, "market": market, "name": meta.get("name", ""), "theme": meta.get("theme", ""), "price": price, "price_date": price_date, "daily_pct": daily_pct, "daily_diff": daily_diff, "probe": probe, "core": core, "strong": strong, "distance": dist, "gap_ratio": ratio, "decision": decision, "rating": rline, "basis_status": raw_basis_status, "basis_display": basis_display(raw_basis_status), "note": note}
        cards.append(card)
        csv_rows.append(card)

    append_health("D03_buy_alert", health)
    cards = sorted(cards, key=card_sort_key)
    today = today_jst()
    report_path = OUT / f"cis_buy_alert_{today}.md"
    csv_path = OUT / f"cis_buy_alert_{today}.csv"

    warning_cards = [c for c in cards if str(c["decision"]).startswith("価格スケール要確認")]
    action_decisions = {"強く買いたい", "本命買い", "打診買い", "条件付き強く買いたい", "条件付き本命買い", "条件付き打診買い"}
    action_cards = [c for c in cards if c["decision"] in action_decisions]
    far_cards = [c for c in cards if c not in warning_cards and c not in action_cards and safe_float(c.get("gap_ratio")) is not None and safe_float(c.get("gap_ratio")) > SCALE_SOFT_HIGH_RATIO]
    other_cards = [c for c in cards if c not in warning_cards and c not in action_cards and c not in far_cards]

    lines = [
        "🆕【今日の更新はここから】",
        "【CIS-D03｜買い場アラート】",
        f"実行日：{today} JST",
        "対象：監視リスト全銘柄",
        "ルール：buy_zone_master.csvを読むだけ。毎日ゴールをずらさない。",
        "価格スケール警戒：下方向0.25倍未満/上方向10倍超は全停止。4倍超は注意銘柄のみ停止し、通常銘柄は高値圏として分離。",
        "",
        "## 今日のサマリー",
        f"買い場接近・買い場判定：{len(action_cards)}件",
        f"価格スケール要確認：{len(warning_cards)}件",
        f"本命から大きく遠い銘柄：{len(far_cards)}件",
        f"見送り・その他：{len(other_cards)}件",
        "",
    ]

    if action_cards:
        lines.append("## まず見る候補")
        for c in action_cards[:10]:
            lines.append(f"- {short_line(c)}")
        lines.append("")

    if warning_cards:
        lines.append("## 要確認アラート")
        for c in warning_cards[:10]:
            lines.append(f"- {short_line(c)}")
        lines.append("")

    def add_section(title, section_cards):
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

    add_section("## 買い場接近・買い場判定", action_cards)
    add_section("## 価格スケール要確認", warning_cards)
    add_section("## 本命から大きく遠い銘柄", far_cards)
    add_section("## 見送り・その他", other_cards)

    report_path.write_text("\n".join(lines), encoding="utf-8")

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["ticker", "market", "name", "theme", "price", "price_date", "daily_pct", "daily_diff", "probe", "core", "strong", "distance", "gap_ratio", "decision", "rating", "basis_status", "basis_display", "note"])
        writer.writeheader()
        writer.writerows(csv_rows)

    print(f"created {report_path}")
    print(f"created {csv_path}")


if __name__ == "__main__":
    main()
