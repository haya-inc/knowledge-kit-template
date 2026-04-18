# faq-kit

原資料 (PDF / docx / HTML / .url / メモ) を `inbox/` に置くと、エージェント
が `source/` に Markdown 変換して蓄えていき、その Markdown を根拠に
質問に答える、という極小の知識ベース用テンプレートキットです。

## クイックスタート

```bash
npm create faq-kit@latest my-faq
cd my-faq
```

もしくは GitHub の "Use this template" ボタンからこのリポジトリを複製
しても構いません。scaffold 後は [CLAUDE.md](./CLAUDE.md) をエージェントに
読ませ、`inbox/` に資料を投入してください。

## 思想

- **キットは契約だけを定める**: 入力の置き場 (`inbox/`)、出力の保管庫
  (`source/`)、状態ファイル (`.faqkit/state.yml`)、同期スキル
  (`.claude/skills/sync/`) の 4 点で、エージェントの振る舞いを縛ります。
- **エージェントはツールを自由に選ぶ**: PDF→MD に pandoc を使うか
  markitdown を使うか Claude 直読にするかは、エージェントの判断です。
  キットは何を使うべきかは指定しません。
- **決定的な部分はスクリプトで固定する**: 差分検知・状態記録・孤立
  削除は誤差が許されないので、同梱の `tools/faqkit.py` (Python 3.10+,
  PyYAML) に担わせます。
- **回答は会話で返し、`source/` を蓄える**: エージェントは `source/`
  の Markdown を根拠に会話で答えます。二次的な解説ページやまとめ
  ページをキット内に増やしません。

## フォルダ構成

```text
my-faq/
├── README.md              ← このファイル (下部に source-index が自動更新で入る)
├── CLAUDE.md              ← エージェント運用規約 (常に参照)
├── AGENTS.md              ← 他のエージェント向け。CLAUDE.md / スキルへ誘導
├── .claude/
│   ├── skills/
│   │   ├── sync/SKILL.md         ← inbox → source への同期スキル (canonical)
│   │   └── url-ingest/SKILL.md   ← 会話経由の URL を inbox に取り込むスキル (canonical)
│   └── commands/
│       ├── sync.md                ← /sync slash command
│       └── url-ingest.md          ← /url-ingest slash command
├── .agents/
│   └── skills/                   ← Codex CLI 用 (.claude/skills へのシンボリックリンク)
│       ├── sync        -> ../../.claude/skills/sync
│       └── url-ingest  -> ../../.claude/skills/url-ingest
├── templates/
│   └── dashboard.html.tmpl ← ダッシュボード生成用テンプレート
├── inbox/                 ← 原資料を置く場所 (サブフォルダ自由)
│   └── assets/            ← 原資料に付随する画像・添付
├── source/                ← 変換後 Markdown の保管庫 (エージェントの出力先)
│   └── assets/            ← 変換時に切り出した画像・添付
├── .faqkit/
│   ├── config.yml         ← キットの挙動を上書きする設定
│   ├── state.yml          ← 変換履歴のロックファイル (初回 sync 時に生成)
│   └── dashboard.html     ← ダッシュボード (自動生成、gitignore 済)
└── tools/
    └── faqkit.py          ← 差分検知 + state 管理 CLI
```

## `inbox/` と `source/` の読み方

- `inbox/` はユーザーが原資料を置く層。エージェントは chat 経由の
  URL を `inbox/web/<slug>.url` に自動生成する場合があります
  (`url-ingest` スキルを参照)。それ以外でエージェントが inbox を
  書き換えることはありません。
- `source/` は同期フックの結果として Markdown と切り出し画像を書き
  込む層。エージェントが回答を作るときに参照する「資料庫」で、
  手動編集は避けて同期を通してください。

### `source/` — 変換済み Markdown

- inbox の相対パスをほぼ保存します。例:
  - `inbox/handbook/guide.pdf` → `source/handbook/guide.md`
  - `inbox/web/article.url` → `source/web/article.md`
- 1 ファイル 1 資料。複数ドキュメントを 1 つにまとめません。
- 先頭には YAML frontmatter が入ります。

  ```yaml
  ---
  title: "原文のタイトル"
  source: "inbox/handbook/guide.pdf"   # 元ファイルへの相対パス
  source_hash: "sha256:..."             # 変換時のソースハッシュ
  converted_at: "2026-04-18T00:00:00Z"
  converter: "pandoc@3.1"               # 何で変換したかの自己申告
  language: "ja"                        # 原文の主言語
  ---
  ```

- 本文は **原文を尊重**した Markdown です。翻訳しません。見出し階層・
  章節構成・表・コードブロックは可能な限り残します。
- OCR 起因の誤認識や欠落は `[?word]` や `[...]` の印で残し、勝手に
  補完しません。
- 変換時に切り出した画像は `source/assets/` に置き、`![説明](./assets/xxx.png)`
  のような相対参照で読みます。

### `inbox/assets/` と `source/assets/`

- `inbox/assets/` はユーザーが原資料と一緒に入れる画像・添付です。
  `inbox/` 側の MD / docx / PDF から相対参照される想定。
- `source/assets/` は変換時に PDF や docx から切り出した画像の置き場。
  MD 化した資料がここの画像を参照します。
- 命名は `<source-stem>-<page>-<index>.<ext>` のような、元ファイルと
  対応が取れる形を推奨します。`source/assets/<subdir>/` で構造化しても
  構いません。

### 状態ファイル `.faqkit/state.yml`

- 各エントリは `source / source_hash / output / output_hash /
  converted_at / converter / status / notes` を持ちます。
- エージェントは直接編集せず、`tools/faqkit.py record` 経由でのみ
  更新します。状態ファイルはロックファイルとして扱います。

## 使い方

### 1. 原資料を置く

`inbox/` に PDF / docx / HTML / .url / メモなどをそのまま置きます。
サブフォルダは自由に作って構いません。

```text
inbox/
├── handbooks/2026-guide.pdf
├── meetings/2026-04-01-kickoff.docx
└── web/anthropic-hiring.url
```

`.url` はテキストの URL ブックマークです。次のいずれのフォーマットでも
受け付けます (`tools/faqkit.py` は中身を解釈せず、変換はエージェントが
行うため)。

- **1 行 URL** のプレーンテキスト (例: `https://example.com/article`)
- **Windows の `.url` ショートカット** 形式 (`[InternetShortcut]` +
  `URL=...` の INI 形式)
- **frontmatter + URL 本文の 2 段構成**。chat 経由で渡された URL を
  `url-ingest` スキル経由で取り込むと、このフォーマットで生成されます。
  title / site / language などを保持するため `.url` に YAML frontmatter
  が付き、本文に canonical URL が 1 行で入ります。詳細は
  [`.claude/skills/url-ingest/SKILL.md`](.claude/skills/url-ingest/SKILL.md)
  を参照。

ユーザーが手で `.url` を置く場合は 1 行 URL で十分です。エージェント生成
ファイルとユーザー投入ファイルが `inbox/web/` に混在することがあります。

### 2. 同期スキルを走らせてもらう

エージェントに「取り込んで」「同期して」などと依頼すると、以下が
実行されます (slash command なら `/sync`)。詳しい手順は
[`.claude/skills/sync/SKILL.md`](.claude/skills/sync/SKILL.md) を参照。

1. `python tools/faqkit.py scan --json` で差分を把握
2. `added` / `modified` / `output_missing` / `failed_retained` を
   Markdown に変換
3. `python tools/faqkit.py record ...` で結果を state.yml に登録
4. `python tools/faqkit.py prune` で孤立 MD を削除
5. `python tools/faqkit.py verify` で整合性を確認
6. `python tools/faqkit.py update-readme` でこの README の source-index
   ブロックを最新化
7. `python tools/faqkit.py dashboard` で `.faqkit/dashboard.html` を
   生成 (サーバー不要、`file://` で開ける)

URL を chat で渡して取り込みたいときは
[`.claude/skills/url-ingest/SKILL.md`](.claude/skills/url-ingest/SKILL.md)
の手順に乗ります (slash command なら `/url-ingest <URL>`)。
agent が `inbox/web/<slug>.url` を生成したあとは通常の sync に合流
します。

### Codex CLI から使う

OpenAI Codex CLI (`codex --enable skills`) でも同じスキルが読めます。
`.agents/skills/` が `.claude/skills/` へのシンボリックリンクになって
いるので、canonical は 1 箇所に保たれます。

```bash
codex --enable skills                # 起動
# Codex セッション内:
/skills                              # 一覧
$sync                                # sync を明示起動
$url-ingest https://example.com/...  # url-ingest を明示起動
```

description にマッチする発話で暗黙起動もされます (「同期して」「この URL
を取り込んで」など)。Codex には project scope の slash command 機構が
ないため、Claude Code の `/sync` `/url-ingest` に対応するのが `$sync`
`$url-ingest` です。

Windows で git clone した場合は `core.symlinks=true` が必要です。symlink
が効かない環境では `.agents/skills/` を `.claude/skills/` の内容で複製
してください (内容を 2 者で一致させる)。

### 3. 質問する

`source/` を直接指すような依頼をすればそのまま答えが返ります。

- 「2026 年版ハンドブックの休暇ポリシーは？」
- 「4/1 のキックオフで決まったアクションアイテムは？」
- 「あの採用ページで言ってた応募条件を要約して」

根拠として引用した MD のパスが回答に添えられます。詳しくは
[`CLAUDE.md`](CLAUDE.md) を参照してください。

## 設定と CLI

設定は `.faqkit/config.yml` で上書きします (キー階層は組み込み
デフォルトに深くマージされる)。

```bash
python tools/faqkit.py config              # 実効設定を確認
python tools/faqkit.py config --diff       # ユーザーの上書き分だけ
python tools/faqkit.py config --defaults   # デフォルトだけ
python tools/faqkit.py config --strict     # 警告があれば rc=1
```

主要な設定項目:

- `ignore` — inbox / source 走査でスキップする glob
- `retry.auto` / `retry.max_attempts` — failed エントリ再挑戦の方針
- `ocr.languages` / `ocr.dpi` — OCR の既定パラメータ
- `web.method_order` / `web.fetch` / `web.browser_mcp` — `.url` 取得
  の方針 (fetch→browser MCP フォールバック)
- `language.default` — 原文言語の扱い (既定 `auto`、翻訳は既定 off)
- `logs.retention_days` — ログ保持期間
- `readme.auto_update` / `readme.include_failed` — この README の
  source-index ブロック自動更新
- `dashboard.auto_generate` / `dashboard.auto_open` / `dashboard.output`
  — ダッシュボード HTML の自動生成と起動挙動

CLI まとめ:

| コマンド | 役割 |
| --- | --- |
| `scan` | inbox と state の差分を人間向け or JSON で列挙 |
| `record` | 変換結果 (ok / failed) を state.yml に記録 |
| `prune` | 孤立 MD + エントリを削除 (dry-run あり) |
| `verify` | state と filesystem の整合性確認 |
| `reindex` | state を壊したときに filesystem から仮復旧 |
| `config` | 実効設定を表示 |
| `render-index` | source 一覧を Markdown 断片として出力 |
| `update-readme` | この README のマーカーブロックを差し替え |
| `dashboard` | 1 枚 HTML ダッシュボードを生成 (`--open` でブラウザ起動) |

## ダッシュボード

`python tools/faqkit.py dashboard` を実行すると、`.faqkit/dashboard.html`
が生成されます。中身はインライン CSS / JS / データを 1 枚にまとめた静的
HTML で、サーバー不要・追加依存ゼロ・`file://` で即時起動します。

表示内容:

- 最終 sync 日時 / 状態ピル (同期済 / 要 sync / 空)
- 取り込み済件数、失敗件数、inbox 総数、source 合計サイズ
- 差分サマリ (added / modified / output_missing / orphan 等)
- 取り込み済み一覧 (ディレクトリ別、各 MD と原資料へのリンク付き)
- 警告セクション (変換失敗、MD 改変、孤立など)
- 使い方 (主要コマンド) と実効 config

起動:

```bash
python tools/faqkit.py dashboard            # 生成のみ (高速、冪等)
python tools/faqkit.py dashboard --open     # 生成後に既定ブラウザで開く
```

sync フックの一部として毎回生成されるため、ダッシュボードは常に最新
状態と一致します。sync 後に再生成せず既存の HTML をすぐ開きたいときは、
OS の標準ハンドラに直接渡すのが早いです。

```bash
# macOS
open .faqkit/dashboard.html

# Linux
xdg-open .faqkit/dashboard.html

# Windows (PowerShell)
Start-Process .faqkit\dashboard.html
```

あるいはエクスプローラ / Finder でダブルクリックするか、ブラウザの
アドレスバーに `file://...` を直接入力しても構いません (サーバー不要)。

毎回自動で開きたい場合は `.faqkit/config.yml` の `dashboard.auto_open`
を `true` にすると、sync 末尾の dashboard 生成後にブラウザが立ち上がり
ます (タブが増え続けるので既定は `false`)。

## 依存

- Python 3.10 以上
- PyYAML (`pip install pyyaml` もしくは venv 経由)
- 変換に使うツール群 (pandoc / markitdown / OCR など) はエージェント
  の判断で入れる

## ライセンス

MIT License. 詳細は [LICENSE](./LICENSE) を参照。

## 現在の source 一覧

下のブロックは `python tools/faqkit.py update-readme` で自動更新されます。
手で編集しないでください。マーカーを消すとフックがエラーになります。

<!-- BEGIN source-index -->

_まだ原資料は取り込まれていません。`inbox/` にファイルを置いて同期フックを走らせてください。_

<!-- END source-index -->
