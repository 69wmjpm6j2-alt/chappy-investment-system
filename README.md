# Chappy Investment System / CIS Step3 Foundation

CIS正式土台パッケージです。

## 入っているもの
- data/watchlist_master.csv
- data/buy_zone_master.csv
- data/ratings_master.csv
- data/price_history.csv
- data/weekly_history.csv
- data/data_health_log.csv
- scripts/cis_prices.py
- scripts/cis_buy_alert.py
- GitHub Actions workflows

## 重要
buy_zone_master.csvは毎日の買い場アラートの基準です。
毎日勝手に変更しません。

## 未完成
TradingView実取得、5ch実取得、ARK実取得は次Step以降です。


## Step4A追加
- data/buy_zone_master_audit.csv
- scripts/cis_buyzone_monthly_review.py
- .github/workflows/monthly_buyzone_review.yml
- docs/CIS_STEP4_BUYZONE_AUDIT.md

Step4Aでは、買い場基準を推測で全銘柄に埋めることはしません。
確認済みの既存基準だけを locked_seed として固定し、未確認は needs_master_value として監査対象にします。
