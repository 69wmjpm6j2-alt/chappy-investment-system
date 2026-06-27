
import csv
import pandas as pd
import yfinance as yf
from cis_common import DATA, OUT, today_jst, active_watchlist, append_health

# 判定ルール
SCALE_HARD_HIGH_RATIO = 10.0
SCALE_SOFT_HIGH_RATIO = 4.0
SCALE_HARD_LOW_RATIO = 0.25
NEAR_TOLERANCE_PCT = 1.0  # 0〜1%程度の微小到達は「本命近接」に逃がす

MICROCAP_SIZE_LIMIT = {"KITT", "OPTX", "POET", "AXTI", "RGTI"}
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

    dist_core = distance_pct(price, core)

    if strong is not None and price <= strong:
        dist_strong = distance_pct(price, strong)
        if dist_strong is not None and -NEAR_TOLERANCE_PCT <= dist_strong <= 0:
            return "強く買いたい近接"
        return "強く買いたい"

    if price <= core:
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
    if decision.startswith("価格スケール要確認"):
        key = "価格スケール要確認"
    else:
        key = decision
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

        b = bz[bz["ticker"].astype(str).eq(ticker)]
        if b.empty:
            probe = core = strong = None
            raw_basis_status = "missing"
            basis_reason = "buy_zone_masterに行がない"
            health.append([ticker, market, "ERROR", "基準未設定", "buy_zone_master", "missing row"])
        else:
            b = b.iloc[0]
            probe = safe_float(b.get("probe_price", ""))
            core = safe_float(b.get("core_price", ""))
            strong = safe_float(b.get("strong_price", ""))
            raw_basis_status = str(b.get("status", ""))
            basis_reason = "" if is_blank(b.get("basis_reason", "")) else str(b.get("basis_reason", ""))

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

        # v1.3.1の特殊ステータスは価格ロジックより優先する
        if raw_basis_status in SPECIAL_STATUS_DISPLAY:
            decision = SPECIAL_STATUS_DISPLAY[raw_basis_status]
            if raw_basis_status == "scale_rebase_pending_v1_3_1":
                note_parts.append("通常買い場判定から一時除外。価格スケール/分割/逆分割/取得ソース確認待ち。")
                health.append([ticker, market, "INFO", "価格スケール確認待ち", "buy_zone_master", basis_reason])
            elif raw_basis_status == "conditional_no_price_signal_v1_3_1":
                note_parts.append("通常買い場判定から除外。条件確認後のみ判断。")
                health.append([ticker, market, "INFO", "条件確認待ち", "buy_zone_master", basis_reason])
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

        if ticker in SCALE_SENSITIVE_TICKERS and not str(decision).startswith("価格スケール要確認") and decision != "価格スケール確認待ち":
            note_parts.append("日次価格桁チェック対象。")

        dist = distance_pct(price, core)
        ratio = None if price is None or core is None or core == 0 else price / core
        rline = rating_line(ratings, ticker, market, asset_type)
        note = " / ".join([n for n in note_parts if n])

        card = {
            "ticker": ticker,
            "market": market,
            "name": meta.get("name", ""),
            "theme": meta.get("theme", ""),
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
            "basis_status": raw_basis_status,
            "basis_display": basis_display(raw_basis_status),
            "note": note,
        }
        cards.append(card)
        csv_rows.append(card)

    append_health("D03_buy_alert", health)

    cards = sorted(cards, key=card_sort_key)

    today = today_jst()
    report_path = OUT / f"cis_buy_alert_{today}.md"
    csv_path = OUT / f"cis_buy_alert_{today}.csv"

    action_decisions = {"強く買いたい", "本命買い", "打診買い"}
    near_decisions = {"強く買いたい近接", "本命近接", "打診近接"}
    action_cards = [c for c in cards if c["decision"] in action_decisions]
    near_cards = [c for c in cards if c["decision"] in near_decisions]
    condition_cards = [c for c in cards if c["decision"] in {"条件確認待ち", "価格スケール確認待ち"}]
    warning_cards = [c for c in cards if str(c["decision"]).startswith("価格スケール要確認")]
    far_cards = [
        c for c in cards
        if c not in action_cards
        and c not in near_cards
        and c not in condition_cards
        and c not in warning_cards
        and safe_float(c.get("gap_ratio")) is not None
        and safe_float(c.get("gap_ratio")) > SCALE_SOFT_HIGH_RATIO
    ]
    other_cards = [
        c for c in cards
        if c not in action_cards
        and c not in near_cards
        and c not in condition_cards
        and c not in warning_cards
        and c not in far_cards
    ]

    lines = [
        "🆕【今日の更新はここから】",
        "【CIS-D03｜買い場アラート】",
        f"実行日：{today} JST",
        "対象：監視リスト全銘柄",
        "ルール：buy_zone_master.csvを読むだけ。毎日ゴールをずらさない。",
        "v1.3.1対応：条件確認待ち/価格スケール確認待ちを通常買い場から分離。",
        "近接ルール：0〜1%程度の微小到達は本命買いではなく近接として分離。",
        "",
        "## 今日のサマリー",
        f"買い場判定：{len(action_cards)}件",
        f"近接：{len(near_cards)}件",
        f"条件/価格スケール確認待ち：{len(condition_cards)}件",
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

    if near_cards:
        lines.append("## 近接候補")
        for c in near_cards[:10]:
            lines.append(f"- {short_line(c)}")
        lines.append("")

    if condition_cards:
        lines.append("## 条件確認待ち・価格スケール確認待ち")
        for c in condition_cards[:10]:
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

    add_section("## 買い場判定", action_cards)
    add_section("## 近接", near_cards)
    add_section("## 条件確認待ち・価格スケール確認待ち", condition_cards)
    add_section("## 価格スケール要確認", warning_cards)
    add_section("## 本命から大きく遠い銘柄", far_cards)
    add_section("## 見送り・その他", other_cards)

    report_path.write_text("\n".join(lines), encoding="utf-8")

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "ticker", "market", "name", "theme", "price", "price_date", "daily_pct", "daily_diff",
            "probe", "core", "strong", "distance", "gap_ratio", "decision", "rating",
            "basis_status", "basis_display", "note"
        ])
        writer.writeheader()
        writer.writerows(csv_rows)

    print(f"created {report_path}")
    print(f"created {csv_path}")


if __name__ == "__main__":
    main()
