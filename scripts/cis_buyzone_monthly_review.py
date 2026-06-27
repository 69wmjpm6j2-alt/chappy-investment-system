import pandas as pd
from cis_common import DATA, OUT, today_jst

def main():
    bz = pd.read_csv(DATA / "buy_zone_master.csv")
    fixed = bz[bz["status"].astype(str).str.startswith("locked") | bz["status"].astype(str).str.startswith("proposed")]
    special = bz[bz["asset_type"].astype(str).eq("watch_only")] if "asset_type" in bz.columns else pd.DataFrame()

    lines = [
        "【CIS-M01｜買い場基準マスター月次見直し】",
        f"実行日：{today_jst()} JST",
        "ルール：自動上書きしない。変更候補だけ出す。",
        "",
        f"基準設定済み：{len(fixed)}銘柄",
        f"特殊確認：{len(special)}銘柄",
        "",
    ]

    audit_path = DATA / "buy_zone_scale_audit_v1_2.csv"
    if audit_path.exists():
        audit = pd.read_csv(audit_path)
        warn = audit[audit["severity"].isin(["high","medium"])]
        lines += ["## 価格スケール・桁チェック対象"]
        for _, r in warn.iterrows():
            lines.append(f"{r.ticker}｜{r['name']}｜{r.scale_audit_status}｜{r.action}｜{r.comment}")
        lines.append("")

    lines.append("## 固定済み基準")
    for _, r in bz.iterrows():
        lines.append(f"{r.ticker}｜{r['name']}：打診 {r.probe_price} / 本命 {r.core_price} / 強く {r.strong_price}｜{r.basis_type}｜{r.basis_reason}")

    (OUT / f"cis_buyzone_monthly_review_{today_jst()}.md").write_text("\n".join(lines), encoding="utf-8")

if __name__ == "__main__":
    main()
