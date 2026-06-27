import pandas as pd
import yfinance as yf
from cis_common import DATA, OUT, today_jst, active_watchlist, append_health

def current_price(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=False)
        if hist is None or hist.empty: return None, None
        hist = hist.reset_index()
        hist["Date"] = pd.to_datetime(hist["Date"]).dt.date
        last = hist.dropna(subset=["Close"]).sort_values("Date").iloc[-1]
        return float(last["Close"]), str(last["Date"])
    except Exception:
        return None, None

def judge(price, probe, core, strong):
    if price is None: return "価格未取得"
    if pd.isna(core) or core == "": return "基準未設定"
    probe, core, strong = float(probe), float(core), float(strong)
    if price <= strong: return "強く買いたい"
    if price <= core: return "本命買い"
    if price <= probe: return "打診買い"
    return "見送り"

def dist_to_core(price, core):
    if price is None or pd.isna(core) or core == "": return ""
    return round((float(price)-float(core))/float(price)*100, 2)

def main():
    wl = active_watchlist()
    bz = pd.read_csv(DATA/"buy_zone_master.csv")
    ratings = pd.read_csv(DATA/"ratings_master.csv")
    health, cards = [], []
    for _, r in wl.iterrows():
        if r["asset_type"] == "watch_only":
            continue
        b = bz[bz["ticker"].eq(r["ticker"])]
        if b.empty:
            health.append([r["ticker"], r["market"], "ERROR", "基準未設定", "buy_zone_master", "missing row"])
            continue
        b = b.iloc[0]
        price, pdate = current_price(r["ticker"])
        if price is None:
            health.append([r["ticker"], r["market"], "ERROR", "価格未取得", "yfinance", "current price not available"])
        status = str(b.get("status",""))
        if status == "needs_master_value":
            health.append([r["ticker"], r["market"], "WARN", "基準未設定", "buy_zone_master", "master value required; do not invent"])
        decision = judge(price, b["probe_price"], b["core_price"], b["strong_price"])
        distance = dist_to_core(price, b["core_price"])
        rr = ratings[ratings["ticker"].eq(r["ticker"])]
        rating_line = "レーティング：未更新"
        if not rr.empty:
            rr=rr.iloc[0]
            rating_line = f"レーティング：{rr.get('source_used','')} / {rr.get('analyst_count','')}人 / {rr.get('consensus','')} / 平均目標 {rr.get('avg_target','')} / 乖離 {rr.get('upside_pct','')}%"
        cards.append({
            "ticker":r["ticker"], "name":r["name"], "theme":r["theme"], "price":price, "pdate":pdate,
            "probe":b["probe_price"], "core":b["core_price"], "strong":b["strong_price"],
            "distance":distance, "decision":decision, "rating_line":rating_line, "status":status
        })
    append_health("D03_buy_alert", health)
    order = {"強く買いたい":0, "本命買い":1, "打診買い":2, "見送り":3, "基準未設定":4, "価格未取得":5}
    cards = sorted(cards, key=lambda x: (order.get(x["decision"],9), x["ticker"]))
    lines=["【CIS-D03｜買い場アラート】", f"実行日：{today_jst()} JST",
           "ルール：buy_zone_master.csvを読むだけ。毎日ゴールをずらさない。基準未設定銘柄は推測で作らない。",""]
    for c in cards:
        lines.append(f"【{c['ticker']}｜{c['name']}｜{c['theme']}】\n現在値：{'' if c['price'] is None else round(c['price'],2)}（{c['pdate']}）\n打診：{c['probe']}\n本命：{c['core']}\n強く買いたい：{c['strong']}\n本命まで距離：{c['distance']}%\n判定：{c['decision']}\n{c['rating_line']}\n基準状態：{c['status']}\n")
    (OUT/f"cis_buy_alert_{today_jst()}.md").write_text("\n".join(lines), encoding="utf-8")

if __name__ == "__main__":
    main()
