[WEEKLY_PERFORMANCE_README.md](https://github.com/user-attachments/files/29658445/WEEKLY_PERFORMANCE_README.md)
# CIS 週間騰落モジュール

## 目的

土曜日に、CIS監視リスト全銘柄を**週間騰落率順**に並べた一覧を自動生成します。  
レポート本文より先に、まず全銘柄の値動きボードを見られるようにするためのモジュールです。

## 生成されるファイル

- `output/weekly_performance_latest.md`
- `output/weekly_performance_latest.json`
- `output/weekly_performance_status.json`
- `docs/latest/weekly_performance_latest.md`
- `docs/latest/weekly_performance_latest.json`
- `docs/latest/weekly_performance_status_latest.json`

## 表示項目

### 米国株・米国ETF

- 銘柄（短い説明）
- 週間騰落率
- 週間価格差
- TradingViewレーティング
- アナリスト人数
- 平均目標株価
- 現在価格→平均目標株価乖離率

### 日本株

- 銘柄（短い説明）
- 週間騰落率

日本株は週次では週間価格差、TradingViewレーティング、アナリスト人数、平均目標株価、乖離率を表示しません。

## 設計方針

- 週次モジュールはTradingViewを直接取得しません。
- 米国株のTradingViewレーティング、アナリスト人数、平均目標株価は、日次騰落側の保存済みJSONを参照します。
- 日次JSON内でTradingView由来だと分かる `tradingview` / `tv_` 系キー、または `source: TradingView` 等の明示があるレコードだけをTV情報として扱います。`source: TradingView` は各銘柄row内だけでなく、JSONルート直下にある形式も認識します。ただし、ルート直下に `tradingview` オブジェクトがあるだけでは、全rowをTradingView由来とはみなしません。ルートの `sources: ["Yahoo", "TradingView"]` のような複数ソース一覧も、全rowをTradingView由来とみなす根拠にはしません。さらに、`tv_rating` など一部TV専用キーだけがある混合rowでは、汎用 `avg_target_price` / `analyst_count` をTradingView由来とはみなしません。汎用フィールドをTV値として使うのは、row全体またはJSONルートの `source` / `provider` / `rating_source` 等が単一TradingViewソースだと明示されている場合だけです。
- 週次モジュールの責務は、週間価格計算、騰落率順ソート、日次保存済みTradingView情報の再利用です。
- 日本株はTradingViewを使いません。表示も週間騰落率のみです。品質判定・partial/error判定にも日本株のTradingView未取得を入れません。

## 自動実行

`.github/workflows/weekly_performance.yml` により、毎週土曜 18:30 JST に自動実行されます。  
GitHub Actions画面から手動実行もできます。

## 導入手順

1. ZIPを解凍する。
2. `scripts/cis_weekly_performance.py` をCISリポジトリの `scripts/` に入れる。
3. `.github/workflows/weekly_performance.yml` をCISリポジトリの `.github/workflows/` に入れる。
4. GitHubの `Actions` タブに `CIS Weekly Performance` が出ることを確認する。
5. `Run workflow` で手動実行する。
6. 成功後、`docs/latest/weekly_performance_latest.md` を確認する。

## 手動テストで必ず見るところ

まず `docs/latest/weekly_performance_latest.md` を開き、日本株カードに価格差やTradingView項目が出ていないことを確認してください。


- Actionsが成功しているか。
- `docs/latest/weekly_performance_latest.md` が作成されているか。
- 全銘柄が「プラス → ゼロ → マイナス → 未取得」の順に並んでいるか。
- `docs/latest/weekly_performance_status_latest.json` の `status` が `ok` または `partial` か。
- 米国株がある場合、`rating_source_files` に日次騰落側JSONが入っているか。
- `quality_errors` が空か。入っている場合は、価格取得、米国株の日次TradingView保存、または日次JSONの生成時刻メタデータに問題があります。日本株のTV未取得は問題扱いしません。

## statusの見方

- `ok`：週間価格とTradingView情報が十分に取得できています。
- `partial`：一部未取得がありますが、レポートとしては成立しています。
- `error`：価格取得成功率が低すぎる、米国株のTradingView情報が全滅している、米国株TV参照元が古すぎる、または日次JSONの生成時刻が確認できません。

## よくある原因

### TradingViewが未取得になる

週次側ではなく、日次騰落側JSONにTradingView情報が保存されているかを確認してください。  
米国株があるのに `rating_source_files` が空の場合、週次側が参照できる日次JSONを見つけられていないか、日次JSON内にTradingView由来を示す `tradingview` / `tv_` 系キー、または `source: TradingView` 等がありません。`source: TradingView` はルート直下・row内のどちらでも認識します。ただし、ルート直下の `tradingview` オブジェクトや、複数ソース一覧にTradingViewが含まれるだけでは不十分です。`tv_rating` は出るのに平均目標株価やアナリスト人数が未取得になる場合は、日次JSON側で `tv_avg_target` / `tv_analyst_count` のようにTV専用キーへ分けるか、rowの `source` を単一の `TradingView` として明示してください。日本株だけの場合は空でも正常です。

米国株のTV情報を使う日次JSONには、`generated_at_jst` / `generated_at` / `data_time` / `updated_at` などの生成時刻を必ず入れてください。生成時刻はJSONルート直下、または各銘柄row内のどちらでも認識します。GitHub上のファイル更新時刻は信用しないため、JSON本文内に生成時刻が無いTV参照元は品質エラーにします。

### 週間騰落が未取得になる

`yfinance` で価格履歴が取得できていない可能性があります。  
日次JSONに直近価格があれば現在値の補助には使いますが、1週間前価格がない場合は週間騰落率は計算できません。  
`weekly_performance_status_latest.json` の `price_success_ratio` を確認してください。


### 生成処理そのものが失敗する

この版では、例外発生時も `weekly_performance_latest.md/json` をエラー表示で上書きします。  
旧レポートが残って「最新に見える」事故を避けるためです。Actionsが失敗している場合は、`weekly_performance_latest.md` と `weekly_performance_status_latest.json` の両方を確認してください。

### iPhoneで `.github` が見えない

`.github` は隠しフォルダ扱いです。GitHub画面上で直接 `.github/workflows/weekly_performance.yml` というパスを作成してください。

## ダッシュボード連携時の注意

`weekly_performance_latest.json` をダッシュボード側で読む場合は、各行の `display_fields` を必ず参照してください。  
日本株行は内部計算用に価格関連フィールドを持つ場合がありますが、表示対象は `ticker_description` と `weekly_change_pct` のみです。  
`hidden_fields_by_policy` に入っている項目は、表示しないでください。

## 日次JSONの必須メタデータ

米国株TradingView情報を含む日次JSONには、最低限以下を入れてください。

```json
{
  "generated_at_jst": "2026-07-03T18:00:00+09:00",
  "rows": []
}
```

`generated_at_jst` 等の生成時刻はルート直下、または各row内に入れてください。どちらにも無い場合、週次側はGitHubのmtimeで代用しません。古いレーティングを新しいように見せる事故を防ぐためです。

---

## 重要：日次側TradingView保存は必須

週次モジュールはTradingViewを再取得しません。したがって、米国株のレーティング欄を成立させるには、**日次騰落側JSONにTradingViewスナップショットを保存することが必須**です。

最低限、日次JSONには以下のどちらかを入れてください。

### 推奨形式

```json
{
  "generated_at_jst": "2026-07-04T07:15:00+09:00",
  "rows": [
    {
      "ticker": "PYPL",
      "market": "US",
      "tradingview": {
        "rating": "Buy",
        "analyst_count": 38,
        "avg_target_price": 82.1,
        "source": "TradingView"
      }
    }
  ]
}
```

### 許容形式

```json
{
  "generated_at_jst": "2026-07-04T07:15:00+09:00",
  "rows": [
    {
      "ticker": "PYPL",
      "market": "US",
      "tv_rating": "Buy",
      "tv_analyst_count": 38,
      "tv_avg_target_price": 82.1
    }
  ]
}
```

汎用の `rating` / `analyst_count` / `avg_target_price` だけでは、Yahoo・みんかぶ・別ソースの値と混ざる危険があるため、TradingView情報としては原則採用しません。



### v29での重要修正：日次TV品質ゲートの検証対象

`cis_validate_daily_tv_snapshot.py` は、古い日次JSONをすべて合算して判定しません。  
また、`tv_rating` だけが保存されていても週次の7項目は成立しないため、米国株はTradingViewの `rating` / `analyst_count` / `avg_target_price` の3項目が揃っているかも検証します。  
GitHub Actionsではファイル更新時刻が信用できないため、JSON本文内の `generated_at_jst` / `generated_at` / `data_time` 等を使い、**最新の日次米国株スナップショット1件だけ**を選んで検証します。

これにより、以下の事故を防ぎます。

- 古いJSONにTV情報が残っているせいで、最新の日次側のTV保存失敗を見逃す
- 古いJSONの生成時刻欠落・古いTV情報まで合算され、正しい最新JSONがあるのに品質ゲートが失敗する
- `weekly_*` / `status_*` / `guard_*` 系JSONを日次スナップショットと誤認する

品質ゲートの出力では、必ず `selected_file` と `candidate_files` を確認してください。  
`selected_file` が想定の日次米国株JSONでない場合は、ファイル名または日次出力先を修正してください。

## 日次Actionsに追加する品質ゲート

日次騰落レポート生成後に、以下を実行してください。

```yaml
- name: Validate daily TradingView snapshot
  run: python scripts/cis_validate_daily_tv_snapshot.py --min-us-tv-ratio 0.70 --max-age-days 7
```

このチェックが失敗した場合、週次レポートの米国株TradingView欄も壊れる可能性が高いです。週次側で吸収するのではなく、日次側の保存仕様を修正してください。

出力：

- `output/daily_tv_snapshot_status.json`
- `docs/latest/daily_tv_snapshot_status_latest.json`

## 日次TV品質ゲートの入れ方で重要なこと

`scripts/cis_validate_daily_tv_snapshot.py` は品質エラー時だけでなく、検証スクリプト自体が例外で落ちた場合も `output/daily_tv_snapshot_status.json` と `docs/latest/daily_tv_snapshot_status_latest.json` を書き出します。
そのため、日次workflowでは検証stepを `continue-on-error: true` にし、status JSONを `if: always()` でcommitしてから、最後に `steps.validate_daily_tv.outcome == 'failure'` でworkflowを失敗させてください。

単純に `run: python scripts/cis_validate_daily_tv_snapshot.py ...` だけを追加すると、失敗時にworkflowが止まり、原因確認用のstatus JSONがcommitされない可能性があります。

