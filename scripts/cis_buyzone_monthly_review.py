
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from cis_common import DATA, OUT, active_watchlist, append_health


JST = ZoneInfo("Asia/Tokyo")

SCALE_HARD_HIGH_RATIO = 10.0
SCALE_HARD_LOW_RATIO = 0.25
SCALE_SOFT_HIGH_RATIO = 4.0

SPECIAL_STATUS = {
    "scale_rebase_pending_v1_3_1": "価格スケール確認待ち",
    "conditional_no_price_signal_v1_3_1": "条件確認待ち",
    "watch_only": "監視のみ",
}

MICROCAP = {"KITT", "OPTX", "POET", "AXTI", "RGTI"}


def now_jst():
    return datetime.now(JST)


def month_key():
    return now_jst().strftime("%Y-%m")


def today_key():
    return now_jst().strftime("%Y-%m-%d")


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


def pct_change(current, past):
    c = safe_float(current)
    p = safe_float(past)
    if c is None or p is None or p == 0:
        return None
    return (c - p) / p * 100


def distance_to_target(price, target):
    p = safe_float(price)
    t = safe_float(target)
    if p is None or t is None or p == 0:
        return None
    return (p - t) / p * 100


def get_price_metrics(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="1y", interval="1d", auto_adjust=False)
        if hist is None or hist.empty:
            return None

        hist = hist.reset_index()
        hist["Date"] = pd.to_datetime(hist["Date"]).dt.date
        hist = hist.dropna(subset=["Close"]).sort_values("Date")
        if hist.empty:
            return None

        latest = hist.iloc[-1]
        close = float(latest["Close"])
        date = str(latest["Date"])
        closes = list(hist["Close"].astype(float))
        high_1y = max(closes)
        low_1y = min(closes)

        def ago(n):
            return closes[-1 - n] if len(closes) > n else None

        return {
            "price": close,
            "price_date": date,
            "pct_1m": pct_change(close, ago(21)),
            "pct_3m": pct_change(close, ago(63)),
            "pct_6m": pct_change(close, ago(126)),
            "drawdown_1y_pct": (close - high_1y) / high_1y * 100 if high_1y else None,
            "up_from_low_1y_pct": (close - low_1y) / low_1y * 100 if low_1y else None,
            "high_1y": high_1y,
            "low_1y": low_1y,
        }
    except Exception:
        return None


def current_decision(price, probe, core, strong):
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


def classify_review(ticker, status, price, probe, core, strong, metrics):
    """
    月次レビュー分類。
    ここでは本番マスターを絶対に変更しない。
    """
    if status in SPECIAL_STATUS:
        return SPECIAL_STATUS[status], "要確認継続", f"{SPECIAL_STATUS[status]}。自動上書きしない。"

    if price is None:
        return "価格未取得", "要確認", "価格未取得。データソース確認。"

    if core is None:
        return "基準未設定", "要確認", "core_priceが空。基準設定が必要。"

    ratio = price / core if core else None
    if ratio is not None and ratio < SCALE_HARD_LOW_RATIO:
        return "価格スケール異常疑い", "要確認", f"現在値/本命={ratio:.2f}倍。価格桁・分割・逆分割・基準価格を確認。"

    if ratio is not None and ratio > SCALE_HARD_HIGH_RATIO:
        return "価格スケール異常疑い", "要確認", f"現在値/本命={ratio:.2f}倍。価格桁・分割・逆分割・基準価格を確認。"

    reasons = []
    category = "維持"
    action = "維持"

    decision = current_decision(price, probe, core, strong)
    if decision in {"強く買いたい", "本命買い", "打診買い"}:
        category = "買い場発火中"
        action = "基準維持/投資判断確認"
        reasons.append(f"現在判定：{decision}")

    p1m = metrics.get("pct_1m") if metrics else None
    p3m = metrics.get("pct_3m") if metrics else None
    dd = metrics.get("drawdown_1y_pct") if metrics else None

    if p1m is not None and p1m <= -20:
        category = "急落確認"
        action = "要確認"
        reasons.append(f"1か月騰落 {p1m:.2f}%。悪材料/決算/需給を確認。")

    if p3m is not None and p3m >= 60:
        category = "急騰後・基準上方修正禁止"
        action = "原則維持"
        reasons.append(f"3か月騰落 +{p3m:.2f}%。現在値追随で基準を上げない。")

    dist_probe = distance_to_target(price, probe)
    if dist_probe is not None and dist_probe >= 50 and ticker not in MICROCAP:
        category = "基準が深すぎる可能性"
        action = "人間レビュー"
        reasons.append(f"打診まであと{dist_probe:.2f}%下落。深すぎる可能性。ただし自動で基準を上げない。")

    if dd is not None and dd <= -40 and decision == "見送り":
        category = "大幅ドローダウン中"
        action = "人間レビュー"
        reasons.append(f"1年高値から{dd:.2f}%。割安化か業績悪化か確認。")

    if ratio is not None and ratio > SCALE_SOFT_HIGH_RATIO:
        reasons.append(f"本命価格の{ratio:.2f}倍。高値圏/基準再点検候補。")

    if ticker in MICROCAP:
        reasons.append("超小型/高ボラ枠。判定が出ても投資サイズ制限。")

    if not reasons:
        reasons.append("大きな異常なし。基準維持。")

    return category, action, " / ".join(reasons)


def main():
    mk = month_key()
    today = today_key()

    wl = active_watchlist()
    bz = pd.read_csv(DATA / "buy_zone_master.csv")

    rows = []
    health = []

    for _, meta in wl.iterrows():
        ticker = str(meta["ticker"])
        market = str(meta.get("market", ""))
        asset_type = str(meta.get("asset_type", ""))
        name = str(meta.get("name", ""))
        theme = str(meta.get("theme", ""))

        b = bz[bz["ticker"].astype(str).eq(ticker)]
        if b.empty:
            probe = core = strong = None
            status = "missing"
            basis_type = ""
            health.append([ticker, market, "ERROR", "基準未設定", "buy_zone_master", "missing row"])
        else:
            b = b.iloc[0]
            probe = safe_float(b.get("probe_price", ""))
            core = safe_float(b.get("core_price", ""))
            strong = safe_float(b.get("strong_price", ""))
            status = str(b.get("status", ""))
            basis_type = str(b.get("basis_type", ""))

        metrics = None if asset_type == "watch_only" else get_price_metrics(ticker)
        if metrics is None and asset_type != "watch_only":
            health.append([ticker, market, "WARN", "月次価格未取得", "yfinance", "1y history unavailable"])

        price = None if metrics is None else metrics["price"]
        price_date = "" if metrics is None else metrics["price_date"]

        category, action, reason = classify_review(ticker, status, price, probe, core, strong, metrics)

        rows.append({
            "month": mk,
            "review_date": today,
            "ticker": ticker,
            "market": market,
            "name": name,
            "theme": theme,
            "asset_type": asset_type,
            "price": price,
            "price_date": price_date,
            "probe_price": probe,
            "core_price": core,
            "strong_price": strong,
            "distance_to_probe_pct": distance_to_target(price, probe),
            "distance_to_core_pct": distance_to_target(price, core),
            "pct_1m": None if metrics is None else metrics["pct_1m"],
            "pct_3m": None if metrics is None else metrics["pct_3m"],
            "pct_6m": None if metrics is None else metrics["pct_6m"],
            "drawdown_1y_pct": None if metrics is None else metrics["drawdown_1y_pct"],
            "up_from_low_1y_pct": None if metrics is None else metrics["up_from_low_1y_pct"],
            "current_decision": current_decision(price, probe, core, strong),
            "basis_status": status,
            "basis_type": basis_type,
            "review_category": category,
            "proposed_action": action,
            "review_reason": reason,
            "suggested_probe_price": probe,
            "suggested_core_price": core,
            "suggested_strong_price": strong,
            "auto_apply": "NO",
        })

    append_health("M01_buyzone_monthly_review", health)

    review_df = pd.DataFrame(rows)
    candidate_df = review_df[~review_df["review_category"].isin(["維持"])].copy()

    OUT.mkdir(exist_ok=True)
    latest_dir = OUT / "latest"
    latest_dir.mkdir(exist_ok=True)

    all_csv = OUT / f"buy_zone_monthly_review_all_{mk}.csv"
    cand_csv = OUT / f"buy_zone_change_candidates_{mk}.csv"
    md_path = OUT / f"buy_zone_monthly_review_{mk}.md"

    latest_md = latest_dir / "buy_zone_monthly_review_latest.md"
    latest_cand_csv = latest_dir / "buy_zone_change_candidates_latest.csv"

    review_df.to_csv(all_csv, index=False, encoding="utf-8-sig")
    candidate_df.to_csv(cand_csv, index=False, encoding="utf-8-sig")
    candidate_df.to_csv(latest_cand_csv, index=False, encoding="utf-8-sig")

    lines = [
        "【CIS-M01｜買い場基準マスター月次レビュー】",
        f"対象月：{mk}",
        f"実行日：{today} JST",
        "",
        "## iPhoneでまず見るところ",
        "このレポートは買い場基準マスターを自動上書きしない。",
        "見るべき順番は、①価格スケール/条件確認、②買い場発火中、③急落/急騰、④深すぎる基準。",
        "現在値から遠いという理由だけで、買い場基準は上げない。",
        "",
        "## サマリー",
        f"対象銘柄：{len(review_df)}件",
        f"見直し候補：{len(candidate_df)}件",
    ]

    counts = candidate_df["review_category"].value_counts().to_dict()
    for k, v in counts.items():
        lines.append(f"- {k}：{v}件")
    lines.append("")

    def add_section(title, category_names):
        sec = candidate_df[candidate_df["review_category"].isin(category_names)].copy()
        lines.append(f"## {title}")
        if sec.empty:
            lines.append("該当なし")
            lines.append("")
            return

        sec = sec.sort_values(["review_category", "distance_to_core_pct", "ticker"], na_position="last")
        for _, r in sec.iterrows():
            lines.extend([
                f"【{r['ticker']}｜{r['name']}｜{r['theme']}】",
                f"現在値：{fmt_num(r['price'])}（{r['price_date'] or '—'}）",
                f"1か月：{fmt_pct(r['pct_1m'])} / 3か月：{fmt_pct(r['pct_3m'])} / 1年高値比：{fmt_pct(r['drawdown_1y_pct'])}",
                f"基準：打診 {fmt_num(r['probe_price'])} / 本命 {fmt_num(r['core_price'])} / 強く買いたい {fmt_num(r['strong_price'])}",
                f"現在判定：{r['current_decision']}",
                f"見直し分類：{r['review_category']}",
                f"提案：{r['proposed_action']}",
                f"理由：{r['review_reason']}",
                "",
            ])

    add_section("最優先確認：価格スケール・条件確認", ["価格スケール確認待ち", "条件確認待ち", "価格スケール異常疑い", "基準未設定", "価格未取得"])
    add_section("買い場発火中", ["買い場発火中"])
    add_section("急落・急騰・ドローダウン", ["急落確認", "急騰後・基準上方修正禁止", "大幅ドローダウン中"])
    add_section("基準が深すぎる可能性", ["基準が深すぎる可能性"])

    lines.extend([
        "## 出力ファイル",
        f"- 全件レビューCSV：`{all_csv.name}`",
        f"- 変更候補CSV：`{cand_csv.name}`",
        "- iPhone用固定リンク：`output/latest/buy_zone_monthly_review_latest.md`",
        "",
        "## 運用ルール",
        "1. このM01は自動上書きしない。",
        "2. 変更候補CSVを確認して、必要なものだけ次版 buy_zone_master に反映する。",
        "3. 反映は別workflowまたは手動確認後に行う。",
        "4. 毎日のD03は、承認済みの buy_zone_master.csv だけを読む。",
        "",
    ])

    content = "\n".join(lines)
    md_path.write_text(content, encoding="utf-8")
    latest_md.write_text(content, encoding="utf-8")

    print(f"created {md_path}")
    print(f"created {latest_md}")
    print(f"created {all_csv}")
    print(f"created {cand_csv}")
    print(f"created {latest_cand_csv}")


if __name__ == "__main__":
    main()
