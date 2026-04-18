---
name: sync
description: inbox/ に置かれた原資料を source/ の Markdown に変換し、.knowledgekit/state.yml を最新化するフルフロー同期。scan → 変換 → record → prune → verify → update-readme → dashboard の順で実行する。ユーザーが「同期して」「取り込んで」「scan して」「更新して」と依頼した、あるいは inbox/ にファイルの追加・更新・削除があったときに使う。URL を chat で受け取って取り込む場合は先に url-ingest スキルに入り、その手順の後半で本スキルに合流する。
---

# sync — inbox → source 同期

`inbox/` を走査して `source/` に変換済み Markdown を揃え、`README.md`
の一覧ブロックと `.knowledgekit/dashboard.html` を更新する一連の作業を、
毎回同じ順序・同じ保証で実行するための手順書です。

chat 経由で URL を渡されたときは先に url-ingest スキル
(`.claude/skills/url-ingest/SKILL.md`) の手順に乗ってから、
その中で本スキルの後半 (prune 以降) に合流します。

手順は `tools/knowledgekit.py` の呼び出しと、エージェントによる変換作業で
構成されます。前者は決定的・冪等、後者は自由度があります。

## いつ走らせるか

- ユーザーが inbox にファイルを追加・更新・削除した直後
- ユーザーが「同期して」「取り込んで」「更新して」と依頼したとき
- state の不整合 (verify の警告) をリセットしたいとき
- README の source-index ブロックが古いと気づいたとき

曖昧な指示でも `inbox/` が動いているなら、このスキルを走らせて
よいか 1 度だけ確認してから実行してください。

## 前提

- Python 3.10+ と PyYAML が導入されていること
- `.knowledgekit/` を持つプロジェクト root を作業起点とすること
  (`tools/knowledgekit.py` はそこから呼ぶ)
- `source/` への書き込みはこのスキル経由の変換結果 (Markdown / 画像)
  のみに限ること。既存 MD の直接編集は行わない。
- `inbox/` は原則ユーザーの領域。このスキルで inbox 側を書き換えない
  (URL ソースの `inbox/web/<slug>.url` 生成は url-ingest スキルの担当)。

## 手順

### 1. 差分を知る

```bash
python tools/knowledgekit.py scan --json
```

返ってくる JSON の 5 区分を頭に入れます。

- `added`: inbox にあるが state に未登録
- `modified`: ソースハッシュが変わった既存エントリ
- `orphan`: state にあるが inbox から消えた
- `output_missing`: state では ok なのに source/ に MD がない
- `failed_retained`: 過去に失敗して再挑戦すべきもの

何も無いなら、以降の変換作業はスキップして 5. に進みます。

### 2. 変換する

`added` / `modified` / `output_missing` / (config.retry.auto が true の
ときは) `failed_retained` の各エントリについて、

1. ソースを読む (PDF / HTML / .url / その他)
2. 原文の言語・構造を尊重しつつ Markdown に整える
   (CLAUDE.md §7「原文尊重」の原則)
3. 出力先は `source/<相対パス>.md`。`suggest_output` の既定に
   従う (scan の JSON にも `suggested_output` が入る)
4. 変換で切り出した画像は `source/assets/` に置き、MD から
   `./assets/xxx.png` のような相対参照で読む。ユーザーが
   `inbox/assets/` に置いた画像はそのまま相対参照で扱う

変換手段は問いません。pandoc, pdfminer, OCRmyPDF, Claude 本体の
読解、など適したものを選んでください。Web ソース (`*.url`) は
`config.yml` の `web.method_order` に従い、まず fetch、必要なら
browser MCP にフォールバックします。

### 3. 結果を記録する

成功時:

```bash
python tools/knowledgekit.py record \
  --source inbox/handbook/guide.pdf \
  --output source/handbook/guide.md \
  --converter "pandoc@3.1"
```

失敗時:

```bash
python tools/knowledgekit.py record \
  --source inbox/broken.pdf \
  --converter "pdfminer@20231228" \
  --status failed \
  --notes "暗号化 PDF のためテキスト抽出不可"
```

- `--converter` は「どう変換したか」を後で追跡できる自己申告です
- `--status failed` のエントリは次回 scan で `failed_retained` に
  戻ってきます。リトライ上限は `config.retry.max_attempts`

### 4. 孤立 MD を片付ける

```bash
python tools/knowledgekit.py prune
```

`orphan` に該当する state エントリと、それに紐づく `source/`
の Markdown を削除します。state に載っていない未登録 MD は
`orphan_outputs` として検出されて同様に削除されます。

削除前に確認したいときは `--dry-run` を付けてください。

### 5. 整合性を検証する

```bash
python tools/knowledgekit.py verify
```

warning が出たら内容を確認し、必要なら reindex や手動の record で
修復します。verify が素通りしてから次へ進むのが原則です。

### 6. README の一覧ブロックを更新する

```bash
python tools/knowledgekit.py update-readme
```

`README.md` 内の以下のマーカー間を、現時点の state.yml 由来の
一覧で置き換えます。

```
<!-- BEGIN source-index -->
<!-- END source-index -->
```

- マーカーが見つからないときはエラー終了するので、最初に
  README に自分で挿入してください
- 失敗エントリを載せるかどうかは `config.readme.include_failed`
  に従います。`--include-failed` / `--no-include-failed` で上書き可

`config.readme.auto_update` が false のとき、このステップは
飛ばして構いません (手動実行に任せます)。既定は true です。

### 7. ダッシュボード HTML を更新する

```bash
python tools/knowledgekit.py dashboard
```

`.knowledgekit/dashboard.html` を生成します。状態・inbox 数・取り込み済み
一覧・警告・使い方を 1 枚にまとめた静的 HTML で、サーバー不要で
`file://` から即座に開けます。

- 生成は数十 ms で冪等 (状態に変化がなければ mtime も変えません)
- 既定ではブラウザを開きません。見たいときは `--open` を付けて
  実行するか、config.dashboard.auto_open を true にしてください
- `config.dashboard.auto_generate` が false のときは飛ばして構いません

### 8. 最後に

- 差分や変換結果をユーザーに短く報告する
- `state.yml` を直接編集しない。必ず `record` / `prune` /
  `reindex` を経由する
- エラーが残っているときは完了扱いにせず、何が未解決かを明示する

## チェックリスト

- [ ] scan の 5 区分すべてに目を通した
- [ ] added / modified / output_missing を Markdown 化した
- [ ] 失敗したものは `--status failed` で記録した
- [ ] prune を (dry-run ではなく) 実行した
- [ ] verify が warning 0 で通った
- [ ] update-readme が成功した (もしくは auto_update=false で意図的に飛ばした)
- [ ] dashboard が生成された (もしくは auto_generate=false で飛ばした)
- [ ] 変更点と未解決点をユーザーに報告した
