from datetime import timedelta
import math, csv
import pandas as pd
import yfinance as yf
from cis_common import DATA, OUT, now_jst, today_jst, active_watchlist, append_health

def get_hist(ticker, days=20):
    try:
        hist = yf.Ticker(ticker).history(period=f"{days}d", interval="1d", auto_adjust=False)
        if hist is None or hist.empty:
            return pd.DataFrame()
        hist = hist.reset_index()
        hist["Date"] = pd.to_datetime(hist["Date"]).dt.date
        return hist.dropna(subset=["Close"]).sort_values("Date")
    except Exception:
        return pd.DataFrame()

def daily_calc(hist):
    if len(hist) < 2: return None
    latest, prev = hist.iloc[-1], hist.iloc[-2]
    lc, pc = float(latest["Close"]), float(prev["Close"])
    return str(latest["Date"]), lc, pc, lc-pc, (lc-pc)/pc*100

def weekly_calc(hist):
    if len(hist) < 2: return None
    latest = hist.iloc[-1]
    base_rows = hist[hist["Date"] <= latest["Date"] - timedelta(days=7)]
    if base_rows.empty: return None
    base = base_rows.iloc[-1]
    lc, bc = float(latest["Close"]), float(base["Close"])
    return str(latest["Date"]), lc, str(base["Date"]), bc, lc-bc, (lc-bc)/bc*100

def run_daily(market):
    wl = active_watchlist(market)
    rows, health = [], []
    for _, r in wl.iterrows():
        if r["asset_type"] == "watch_only":
            continue
        hist = get_hist(r["ticker"])
        res = daily_calc(hist)
        if res is None:
            rows.append([today_jst(), r["ticker"], market, "", "", "", "", "", "yfinance", "取得不可", ""])
            health.append([r["ticker"], market, "ERROR", "取得不可", "yfinance", "daily price not available"])
        else:
            d, close, prev, diff, pct = res
            rows.append([today_jst(), r["ticker"], market, d, round(close,2), round(prev,2), round(diff,2), round(pct,2), "yfinance", "取得済み", ""])
    with open(DATA/"price_history.csv", "a", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(rows)
    append_health(f"D01_D02_daily_{market}", health)
    df = pd.DataFrame(rows, columns=["run_date","ticker","market","price_date","close","prev_close","daily_diff","daily_pct","source","status","memo"])
    ok = df[df["status"].eq("取得済み")].copy()
    ok["daily_pct"] = pd.to_numeric(ok["daily_pct"], errors="coerce")
    ok = ok.sort_values("daily_pct", ascending=False)
    report = OUT / f"cis_daily_{market}_{today_jst()}.md"
    title = "日本株監視リスト日次騰落" if market=="JP" else "米国株監視リスト日次騰落"
    lines=[f"【CIS-D{'01' if market=='JP' else '02'}｜{title}】", f"実行日：{today_jst()} JST", ""]
    for _, x in ok.iterrows():
        meta = wl[wl["ticker"].eq(x["ticker"])].iloc[0]
        lines.append(f"【{x.ticker}｜{meta['name']}｜{meta['theme']}】\n終値：{x.close}（{x.price_date}）\n前日比：{x.daily_pct}% / {x.daily_diff}\nステータス：{x.status}\n")
    ng = df[~df["status"].eq("取得済み")]
    if not ng.empty:
        lines.append("## 取得不可")
        for _, x in ng.iterrows():
            lines.append(f"{x.ticker}：{x.status}")
    report.write_text("\n".join(lines), encoding="utf-8")

def run_weekly():
    wl = active_watchlist()
    rows, health = [], []
    for _, r in wl.iterrows():
        if r["asset_type"] == "watch_only":
            continue
        hist = get_hist(r["ticker"])
        res = weekly_calc(hist)
        if res is None:
            rows.append([today_jst(), r["ticker"], r["market"], "", "", "", "", "", "", "yfinance", "週間未取得", ""])
            health.append([r["ticker"], r["market"], "ERROR", "週間未取得", "yfinance", "weekly price not available"])
        else:
            ld, lc, bd, bc, diff, pct = res
            rows.append([today_jst(), r["ticker"], r["market"], ld, round(lc,2), bd, round(bc,2), round(diff,2), round(pct,2), "yfinance", "取得済み", ""])
    with open(DATA/"weekly_history.csv", "a", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(rows)
    append_health("W01_weekly", health)
    df = pd.DataFrame(rows, columns=["run_date","ticker","market","latest_date","latest_close","base_date","base_close","weekly_diff","weekly_pct","source","status","memo"])
    report = OUT / f"cis_t04_weekly_report_{today_jst()}.md"
    lines=["🆕【今日の更新はここから】","【タスクT04｜週間騰落まとめ】",
           f"実行日：{today_jst()} JST",
           "対象期間：直近1週間の騰落。米国株は米国市場直近終値ベース、日本株は東証直近終値ベース",
           "ステータス：更新あり" if df["status"].eq("取得済み").all() else "ステータス：一部週間未取得",
           "前回から変わった点：CIS Step3。watchlist_master/weekly_history/data_health_log分離。",
           "――ここから今日分――",""]
    for market, title in [("US","## 米国株・ETF"),("JP","## 日本株")]:
        sub=df[df.market.eq(market)].copy()
        ok=sub[sub.status.eq("取得済み")].copy()
        ok["weekly_pct"] = pd.to_numeric(ok["weekly_pct"], errors="coerce")
        ok=ok.sort_values("weekly_pct", ascending=False)
        lines.append(title)
        for _, x in ok.iterrows():
            meta=wl[wl["ticker"].eq(x["ticker"])].iloc[0]
            lines.append(f"【{x.ticker}｜{meta['name']}｜{meta['theme']}】\n最新終値：{x.latest_close}（{x.latest_date}）\n1週間前基準：{x.base_close}（{x.base_date}）\n週間騰落：{x.weekly_pct}% / {x.weekly_diff}\nステータス：{x.status}\n")
        ng=sub[~sub.status.eq("取得済み")]
        if not ng.empty:
            lines.append(f"### {title.replace('## ','')} 週間未取得")
            for _, x in ng.iterrows(): lines.append(f"{x.ticker}：{x.status}")
    report.write_text("\n".join(lines), encoding="utf-8")

if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv)>1 else "weekly"
    if cmd == "daily_us": run_daily("US")
    elif cmd == "daily_jp": run_daily("JP")
    elif cmd == "weekly": run_weekly()
    else: raise SystemExit(f"unknown cmd {cmd}")
