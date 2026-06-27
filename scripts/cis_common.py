from pathlib import Path
from datetime import datetime, timedelta, timezone
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)

JST = timezone(timedelta(hours=9))

def now_jst():
    return datetime.now(JST)

def today_jst():
    return now_jst().strftime("%Y-%m-%d")

def read_csv(name):
    return pd.read_csv(DATA / name)

def active_watchlist(market=None):
    df = read_csv("watchlist_master.csv")
    df = df[df["active"].astype(str).str.lower().eq("true")].copy()
    if market:
        df = df[df["market"].eq(market)]
    return df

def append_health(task, rows):
    path = DATA / "data_health_log.csv"
    if not rows:
        return
    import csv
    exists = path.exists()
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["run_datetime","task","ticker","market","severity","status","source","message"])
        for r in rows:
            w.writerow([now_jst().isoformat(), task] + r)
