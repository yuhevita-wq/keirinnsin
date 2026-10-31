# 競輪シン：データ蓄積基盤 v0.1

## 何が実装済みか

- SQLiteで競輪のレース・出走選手・レース前スナップショット・確定結果・払戻を保存。
- 1レース×1選手×1事前取得時点という形で特徴量を保存。選手IDと車番を区別。
- レース前の取得時刻を保存し、発走予定時刻**以降**のデータを学習用CSVに混入させない。
- 取込ファイルのハッシュと生JSONを保存、同一データの二重取込を防止。
- DBトランザクションで異常データの一括ロールバック。
- 公表された確定結果の後日訂正（取得時刻が新しいもののみ）。
- 公式開催一覧に相当する**許諾済み提供元**のレース一覧（schedule）を取り込み、未収集レース・結果未収集を検出。
- 許可済みのHTTPS JSONフィードから日付指定で自動取得する入口（エンドポイント設定が必要）。
- Python標準ライブラリのみ。Windows・macOS・LinuxでPython 3.10以降対応。

## 注意：現時点の限界

**実際の公式競輪データはまだ1件も取得していません。** `examples/` の選手・競輪場・レースはすべて架空です。公開サイトの無断スクレイピングは実装していません。

「全レース」を証明するには、該当日付の全開催・全レースを網羅する権限付きの `schedule` 配信が必要です。その上で `report` の `coverage_certified` が `true` となって初めて、その**指定日・その提供元の一覧に対する**収集完了を確認できます。全競輪界の全レースが揃ったことを自動的に保証する機能ではありません。

### 公式サイトの利用条件

- [KEIRIN.JP サイトポリシー](https://keirin.jp/pc/dfw/portal/guest/policy/index.html)は無断複製・転用等を制限しています。
- [netkeirin 利用規約](https://keirin.netkeiba.com/info/kiyaku/)には私的利用を超える複製や営利活動の制限があります。

商用販売を最終目標に含むため、データ事業者から**自動取得・保存・学習利用・商用販売**の各権限を確認する必要があります。設定ファイルの `rights_confirmed` は運用者による確認済み宣言であり、このプログラムが自動的に権利を審査するものではありません。

## 1. インストール

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python -m keirin_shin --db data/keirin.sqlite3 init-db
```

## 2. 架空データで動作確認

```bash
python -m keirin_shin --db data/demo.sqlite3 ingest-dir examples
python -m keirin_shin --db data/demo.sqlite3 report --date 2026-10-31
python -m keirin_shin --db data/demo.sqlite3 export-riders --out data/demo_riders.csv
```

表示例：レース1件、発走前スナップショット1件、確定レース1件、出走選手3件。日時やデータは**架空のサンプル**です。CSVは1選手1行で、発走前の選手特徴量と着順が紐づきます。

## 3. データのフォーマット

トップレベルに `schema_version:1`, `kind`, `source`, `races`（`schedule` の場合は `race_date` も）が必要です。

```json
{
  "schema_version": 1,
  "kind": "pre_race",
  "source": {
    "name": "licensed-provider-name",
    "license_reference": "契約名・許諾番号等（自己申告）",
    "captured_at": "2026-10-31T09:00:00+09:00"
  },
  "races": [{
    "race_id": "provider-specific-stable-id",
    "race_date": "2026-10-31", "venue_code": "99",
    "venue_name": "架空競輪場", "race_no": 1,
    "scheduled_start": "2026-10-31T11:00:00+09:00",
    "race_class": "A級",
    "entries": [{"car_no": 1, "rider_id": "R-001", "rider_name": "架空選手", "race_score": 90.1, "features": {"back_count": 8}}]
  }]
}
```

- `kind=pre_race`：レース・選手属性を記録。**同じレースでも取得時刻が違えば別スナップショット**。
- `kind=results`：`race_id`, `status`（`official`または`cancelled`）、`results`、任意の`payouts`。
- `kind=schedule`：その日の`race_date`と期待するレースID・競輪場コード・レース番号の一覧。全件の一覧として扱い、再取込で入れ替え。
- `features`：追加の選手特徴量をJSONで記録。計測定義や取得元はデータ事業者と合わせて管理する。
- `captured_at`：**実際に当該データを取得した時刻**（タイムゾーン付きISO8601）。バックフィルで歴史上の時刻を創作しない。
- `scheduled_start`：発走予定時刻。実発走時刻は未収録なので、より正確な時点検証には後続改修が必要。
- `rank`：着順。失格・落車・欠場は `finish_status` を指定して `rank:null`。
- `rider_id`：提供元が与える安定ID。名前のみの一致判定は行わない。

具体的な入力サンプルは `examples/01_schedule.json`, `02_pre_race.json`, `03_results.json` を参照。

## 4. 許諾済みデータフィードの自動取込

1. 正式なデータ契約を結び、当サービスでの保存・再利用・商用利用・アクセス頻度を確認する。
2. `examples/provider.config.example.json` を `config/provider.json` にコピーする。
3. 事業者が提供する**正式な** HTTPSエンドポイントを `urls.schedule`, `urls.pre_race`, `urls.results` に設定する。**事業者独自の応答形式は上記JSONフォーマットに変換する小さなアダプターが必要**。
4. 許諾・利用範囲を確認して `rights_confirmed: true` にする。
5. 必要に応じて `KEIRIN_FEED_TOKEN` を環境変数に設定する（キーはGitにコミットしない）。
6. 一日分を取り込む：

```bash
python -m keirin_shin --db data/keirin.sqlite3 sync-date --date 2026-10-31 --config config/provider.json
```

### 日次運用

常時稼働のサーバー・ローカル端末・タスクスケジューラで次を定期実行します。

```bash
python scripts/daily_sync.py --db data/keirin.sqlite3 --config config/provider.json --days-back 2
```

現在日・前日・前々日を再取得するため、発走前の取得と前日確定結果の遅れを拾いやすくしています。**ファイルの継続保存が必要**です。DBファイルはGitにコミットしません。GitHub Actionsで定期処理する場合は、SQLiteを確実に保存できる外部ストレージ・DBを別途設計します。

HTTP 404は「未公開」と表示、通信エラー・スキーマ不一致は失敗として停止します。現時点で提供元が未契約のため日次取得は起動しません。

## 5. 収集漏れの確認

```bash
python -m keirin_shin --db data/keirin.sqlite3 report --date 2026-10-31
```

- `expected_races_from_manifest`：事業者のレース一覧に記載された件数
- `missing_expected_races`：一覧にあるのにまだレース情報がないもの
- `races_missing_results`：出走情報はあるが結果がないもの
- `races_with_before_start_snapshot`：発走予定時刻より前の記録があるレース数
- `coverage_certified`：指定日の一覧、事前情報、確定結果がすべて揃った場合のみtrue

## 6. モデル学習CSVへの出力

```bash
python -m keirin_shin --db data/keirin.sqlite3 export-riders --date 2026-10-31 --out data/riders_20261031.csv
```

CSVは各レースの**発走予定時刻より前に取得した最後のスナップショット**を採用します。発走後のスナップショットはCSVから除外。欠場・確定結果未収録の行は結果を空欄で出力します。学習時には結果が確定していること・各特徴量が予測時点に得られたことをさらに検証してください。

## 7. 次に必要な実務作業

- [ ] 全競輪場・全日付の開催レース一覧を提供するデータ事業者と利用許諾を確保
- [ ] 提供元JSON / CSVとこのスキーマのマッピングアダプターを作成
- [ ] 結果配信遅延に対応し、定期再取込・警告を設定
- [ ] 本番DBのバックアップ、自動障害通知、監視を整備
- [ ] 過去データを取得できる範囲・当時の特徴量スナップショットの正確性を確認
- [ ] ライン、コメント、時系列オッズの専用テーブル追加を検討

データ収集・保管の仕組みと、実際に**全レースの生データを取得できる契約**は別問題です。無許諾のサイト巡回を自動化して代替しない方針です。
