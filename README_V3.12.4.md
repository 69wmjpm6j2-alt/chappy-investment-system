# CIS V3.12.4 Watchlist修復 最終補強版＋ユーザー目線補強

## まず最初に

通常のCIS日次更新は、これまで通り既存の自動workflowが動きます。  
この `CIS Watchlist修復｜手動実行` は、監視リストが壊れた時だけ押す非常用の修復ボタンです。  
**普段は押さなくて大丈夫です。**

- 毎朝・日次のCIS更新：既存のschedule workflowが自動実行
- Watchlist修復：異常カードが出た時だけ手動実行

## V3.12.4で入れた軽微補強

V3.12.3の処理本体は維持し、ユーザー目線で迷いやすい部分だけ補強しました。

1. README冒頭に「通常更新は自動のまま」「普段は押さなくていい」を明記。
2. iPhoneではGitHub Actionsのworkflow一覧が見つけにくい場合があるため、PC表示推奨の説明を追加。
3. 失敗時のログ確認手順を具体化。
4. 成功後にCIS Pagesで異常カードが出ていないことを確認する手順を追加。
5. dashboardスニペット冒頭コメントを `V3.12.4` 表記に修正。
6. status内の `user_next_action` を初心者向けに具体化。

## V3.12.3から維持している安全対策

1. 前回の成功statusが残っている状態で修復スクリプトが失敗しても、古い成功statusをPagesへコピーしません。
2. 実行前statusのSHAを保存し、今回更新されたstatusか前回残骸かを判定します。
3. 修復スクリプト失敗時は、部分的に書き換わった `data/watchlist_master.csv` を保存しません。
4. `persist_status` は「修復自体の成否」ではなく「修復結果statusの保存成否」として扱います。
5. YAML linterで `on` が真偽値扱いされにくいよう、`"on"` と明示しています。
6. push直前の `git pull --rebase --autostash` が衝突した場合も、workflowが即終了せず、永続化失敗statusを残す処理へ進みます。

## 入れるファイル

以下を既存リポジトリへ上書きしてください。

```text
.github/workflows/watchlist_repair.yml
```

以下は dashboard 統合用スニペットです。まだ統合していない場合だけ使ってください。

```text
snippets/cis_dashboard_watchlist_repair_cards_v3124.py
```

既にV3.12.3のdashboardスニペットを統合済みの場合、必須の再統合ではありません。  
ただし、コメント表記と失敗時文言をそろえるならV3.12.4版へ差し替えてください。

## いつ使うか

普段は使いません。

使うのは、CIS Pages上部に以下のような異常カードが出た時だけです。

```text
Watchlist修復に失敗しています
Watchlist修復結果を保存できていません
```

異常カードが出ていなければ、何もしなくて大丈夫です。

## GitHub上での実行手順

PC表示推奨です。  
iPhoneの場合、GitHubのActions画面でworkflow一覧が畳まれていることがあります。その場合はメニューを開いて `CIS Watchlist修復｜手動実行` を選んでください。

1. GitHubを開く
2. 上部の `Actions` を押す
3. workflow一覧から `CIS Watchlist修復｜手動実行` を選ぶ
4. 右上の `Run workflow` を押す
5. 理由欄は空欄のままでもOK
6. もう一度 `Run workflow` を押して開始
7. 緑のチェックが付けば完了

## 成功後の確認

成功したら、数十秒〜数分後にCIS Pagesを開いてください。

確認することはこの2つです。

```text
1. 監視リスト表示が崩れていない
2. 画面上部にWatchlist修復系の異常カードが出ていない
```

異常カードが出ていなければ完了です。

## 失敗した時のログ確認

赤いバツが付いた場合は、以下を確認してください。

1. GitHub → `Actions` を開く
2. `CIS Watchlist修復｜手動実行` を開く
3. 失敗した実行を開く
4. `Watchlist修復と保存` を開く
5. `Repair watchlist and persist result` を開く
6. 赤くなっている行の周辺を見る

よくある原因は以下です。

```text
・通常更新workflowとのpush競合
・watchlist_master.csvの形式崩れが大きい
・GitHub Actionsの一時的な失敗
・contents: write 権限不足
```

push競合っぽい場合は、少し時間を空けてもう一度 `Run workflow` を押してください。

## 成功時にできるファイル

```text
output/watchlist_repair_status.json
output/watchlist_repair_status.md
docs/latest/watchlist_repair_status_latest.json
docs/latest/watchlist_repair_status_latest.md
docs/latest/watchlist_repair_persist_status_latest.json
docs/latest/watchlist_repair_persist_status_latest.md
```

## 重要な設計判断

修復スクリプトが失敗した場合、`watchlist_master.csv` の変更は保存しません。  
失敗途中のCSVを監視リスト本体としてcommitするのを避けるためです。

その代わり、失敗statusをPages側へ保存し、dashboard上部に「Watchlist修復に失敗しています」を出す想定です。

## 追加メモ

保存系workflow同士の競合をさらに減らすなら、通常更新workflow側にも以下と同じ考え方の `concurrency` を入れると安定します。

```yaml
concurrency:
  group: cis-persist-${{ github.ref }}
  cancel-in-progress: false
```

ただし、これはV3.12.4投入の必須条件ではありません。
