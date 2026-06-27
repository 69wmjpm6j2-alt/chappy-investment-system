from cis_common import OUT, today_jst
# Step4でTradingView取得を実装する。現時点ではratings_master.csvの器を固定する。
(OUT/f"cis_ratings_weekly_{today_jst()}.md").write_text(
"【CIS-W04｜TradingViewレーティング更新】\nStep3では器のみ。Step4でTradingView取得を実装。\n", encoding="utf-8")
