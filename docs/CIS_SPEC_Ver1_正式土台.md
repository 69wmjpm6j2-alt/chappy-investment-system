# CIS正式仕様 Ver1.0 / Step3

## 中核
- CIS-D03：買い場アラート
- CIS-W01：週間騰落まとめ
- CIS-W04：TradingViewレーティング週次取得

## 基本方針
- 価格は毎日取得
- 週間騰落は土曜日に最新終値 vs 7日前以前の直近営業日終値
- 買い場基準はbuy_zone_master.csvで固定し、毎日動かさない
- 買い場基準の見直しは月1回だけ。自動上書きせず、変更候補として出す
- TradingViewは週1取得してratings_master.csvに保存。毎日のレポートはキャッシュを使う
- 5chは価格データと分離。失敗してもT01/T03/T04を壊さない

## Step3の完成条件
- watchlist_master.csvが存在する
- buy_zone_master.csvが存在する
- ratings_master.csvが存在する
- price_history.csv / weekly_history.csv / data_health_log.csvが存在する
- GitHub Actionsの各タスク器が存在する

## Step3でまだ完成扱いにしないもの
- TradingView実取得
- 5ch実取得
- ARK実取得
- buy_zone_master全銘柄数値の完全移植

## 注意
buy_zone_master.csvの値が未設定の銘柄は「基準未設定」と出す。
推測で打診/本命/強く買いたい価格を作らない。
