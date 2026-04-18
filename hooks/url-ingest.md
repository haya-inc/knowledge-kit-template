# url-ingest hook — chat で受けた URL を inbox に落とし込む

ユーザーが会話の中で URL を渡してきたとき、それを取り込み対象に
したいかを確認しつつ、`inbox/web/<slug>.url` を生成して
通常の sync に合流させるためのフックです。

`inbox/` は原則ユーザーの領域ですが、URL を `.url` ファイルに
書き起こすこの操作だけはエージェント側で行うことを許しています
(CLAUDE.md §3-2 の例外)。

## このフックの目的

- ユーザーは URL だけを渡す
- エージェントがプレビューを取り、ソース化の可否と frontmatter を
  相談し、`inbox/web/<slug>.url` を作る
- そのまま [`sync.md`](sync.md) の手順に合流し、
  `source/web/<slug>.md` までを一気通貫で仕上げる
- 取り込んだ URL の関連リンクを列挙し、ユーザーに選んでもらって
  まとめて同じフローに流す

inbox に既に `.url` が置かれていて sync だけ走らせたい場合は
sync.md のほうが担当です。このフックは chat 経由の URL 専用です。

## いつ走らせるか

- ユーザーのメッセージに URL が 1 つ以上含まれ、「取り込んで」
  「ソース化して」「入れておいて」などの意図が読めるとき
- 明示がなくても URL 単独のメッセージ (質問なし) が来たときは、
  取り込む意図かを 1 度確認してから入る
- 逆に「この URL の中身を教えて」のような「今回は読むだけ」と
  読める問い合わせは、このフックに入らずその場で答える

## 前提

- Python 3.10+ と PyYAML、`tools/faqkit.py` が動くこと
- Web ソースの取得は `config.web.method_order` に従い fetch → browser
  MCP の順でフォールバック (CLAUDE.md §8)
- 出力先は原則 `inbox/web/<slug>.url` と `source/web/<slug>.md`。
  サブディレクトリで分類したければ `inbox/web/<site>/<slug>.url` の
  ような形でも良いが、slug 規則は保つ

## 手順

### 1. URL をプレビューする

ページを軽く読んで以下を抽出します。

- `<title>` / OG `og:title` (タイトル)
- サイト名 / `og:site_name` / ドメイン
- 著者 / `author` メタ / バイライン
- 公開日 / `article:published_time` / 本文中の日付
- 主言語 (`lang` 属性 / 内容から推定)
- 本文の先頭数段落 (ソース化判断の材料)

取得失敗 (4xx, 5xx, SPA, 認証必須) は `config.web.fallback_on` に
従って browser MCP にフォールバック。すべての手段で失敗した場合
は 9. に進みます。

### 2. canonical URL を決める

取り込む URL を正規化します。

- クエリの `utm_*`, `gclid`, `fbclid`, `ref`, `ref_src` を落とす
- フラグメント (`#...`) は意味を持たない限り落とす
- ページ自身が `<link rel="canonical">` を指していればそれを採用
- 末尾スラッシュは元サイトの表記に合わせる

正規化後の URL を「この資料の同一性」として扱い、重複検知・slug
生成の材料にします。

### 3. slug とファイル名を決める

slug は `<site-or-section>-<title-stem>` のような ASCII kebab-case。
既定のパスは:

- `.url` 本体: `inbox/web/<slug>.url`
- Markdown 出力: `source/web/<slug>.md` (suggest_output の既定)

slug 規則:

- ASCII 英数と `-` のみ
- 小文字化
- 32 文字を目安に短く。長いタイトルは意味を残してカットする
- 同名 slug が既に inbox / state に存在するなら `-2`, `-3` を付ける
- サイト別に整理したいときは `inbox/web/<site>/<slug>.url` にしても
  良い。その場合 MD も `source/web/<site>/<slug>.md` になる

### 4. frontmatter のドラフトを作る

1. で得た情報から以下のテンプレートを埋めます。`.url` 自体は
agent が生成するので、frontmatter は `.url` にそのまま付けます。

```yaml
---
title: "<og:title か <title>>"
url: "<canonical URL>"
site: "<og:site_name かドメイン>"
author: "<著者 / 不明なら空>"
published_at: "<ISO8601 / 不明なら空>"
language: "<ja / en / ...>"
tags: []
note: ""
---
```

`tags` と `note` はユーザーが補足したい場合のフィールドです。空の
ままでも構いません。

### 5. ユーザーに確認する

AskUserQuestion で最低 1 回は聞きます。項目が多いので、複数質問は
まとめて投げて往復回数を減らすこと。

必須の確認:

- ソース化するか (yes / 今回は読むだけ)
- 提案した slug でよいか (yes / 別名を提案)
- 提案した frontmatter に修正したい箇所があるか (tags / note を
  中心に)

分岐:

- `yes` → 6. に進む
- `読むだけ` → 取り込まずに回答モードに入る。ここで終了
- `別名` / `修正あり` → 会話でドラフトを詰めてから 6. に進む

URL / title / site / language は 1. で取得した値をそのまま採用して
構いません。明示の異論があったときだけ変えます。

### 6. `.url` ファイルを書く

`inbox/web/<slug>.url` を作成します。フォーマットは frontmatter +
本文 URL の 2 段構成。

```
---
title: "..."
url: "https://example.com/article"
site: "Example"
author: ""
published_at: ""
language: "ja"
tags: []
note: ""
---
https://example.com/article
```

この時点では `source/web/<slug>.md` はまだ作りません。7. 以降で
sync.md の流れに乗せて作ります。

### 7. sync に合流する

[`sync.md`](sync.md) の 1. scan → 2. 変換 → 3. record を、この 1
ファイル分だけ実行します。

1. `python tools/faqkit.py scan --json` — 先ほど書いた `.url` が
   `added` として現れる
2. 1. でのプレビュー結果を再利用して Markdown 化
   - 先頭に MD 用 frontmatter (`source`, `source_hash`,
     `converted_at`, `converter` + `.url` に載せた項目)
   - 本文は原文尊重 (CLAUDE.md §8)
   - 画像は `source/assets/web/<slug>-<n>.<ext>` に置き、
     `./assets/web/<slug>-<n>.<ext>` で相対参照
3. 成功時:

   ```bash
   python tools/faqkit.py record \
     --source inbox/web/<slug>.url \
     --output source/web/<slug>.md \
     --converter "fetch+readability"
   ```

### 8. 残りの sync を回す

sync.md の 4. 以降と同じです。関連リンクをバッチ処理する場合は
ここを「すべて記録してから 1 度だけ」に束ねます。

```bash
python tools/faqkit.py prune
python tools/faqkit.py verify
python tools/faqkit.py update-readme
python tools/faqkit.py dashboard
```

### 9. 取得失敗時

1. の取得が全手段失敗した場合、または 7-2. の変換で重大なエラーが
起きた場合:

1. `.url` ファイルは残す (frontmatter + URL)
2. `record` を `--status failed --notes "<理由>"` で実行
3. MD は作らない
4. ユーザーに状況を短く報告し、後で再挑戦するかを聞く

次回 scan でこのエントリは `failed_retained` として戻ってきます。
`config.retry.auto` が true なら自動で再挑戦、false なら手動で
再実行します。

### 10. 関連リンクを列挙する

取り込みが成功したら、生成した MD の本文や元 HTML から関連 URL を
抽出します。

抽出対象:

- 本文中の外部リンク (異なるドメイン)
- 本文中の内部リンク (同一ドメインの記事系パス)
- 文末の参考文献 / references / 関連記事
- `article:tag` などに紐づく同サイトのタグページ (必要なら)

除外対象:

- ナビゲーション / フッター / サイドバー (テンプレ的なリンク)
- ログイン / 利用規約 / プライバシーポリシーなどの定型ページ
- 画像 / 動画 / 広告配信ドメイン
- 既に state.yml に取り込み済みの URL (canonical で判定)

整理:

- canonical URL で dedupe
- 同一ドメインから 1 記事、外部は最大 10 件 を目安に上限
- 「同サイト / 引用・参考 / 外部関連」で軽くグルーピング
- 各候補に「なぜ拾ったか (アンカーテキスト / セクション)」を添える

### 11. 関連リンクの取り込みを聞く

10. の候補を提示し、どれを取り込むかをユーザーに多選択で聞きます。
AskUserQuestion で複数項目を同時に問えるならまとめて、無理なら
「全部取り込む / 選んで取り込む / 今回は取り込まない」の 3 択に
しぼってから、「選んで」のときだけリストを提示します。

### 12. 選ばれた URL を再帰的に取り込む

11. で選ばれた URL については、本フックの 1. から繰り返します。
ただし以下は「バッチの最後に 1 度だけ」に束ねる:

- `prune` / `verify` / `update-readme` / `dashboard`

1 件ごとに sync 全体を回すと dashboard が何度も書き換わって嬉しく
ない。バッチでは各 URL の 6. (`.url` 書き込み) と 7. (変換 + record)
まで進め、全 URL が終わってから 8. を 1 度だけ実行します。

### 13. 報告

ユーザーに短くまとめて返します。

- 取り込んだ URL と slug
- 失敗した URL とその理由
- 関連リンクから選ばれた件数と取り込み結果
- 未解決点 (認証必須で落ちた、browser MCP でも取得できなかった等)

## 深さの制御

関連リンクからさらに関連リンクを辿る「再帰探索」はしません。
1 回の実行で再帰は 1 段だけ (今いる URL → 関連リンク)。ユーザーが
「もう一段深く」と明示したときに限り、本フックをもう一度起動して
進めます。

無限に広がらないための既定:

- 1 回の実行で取り込む URL の上限は 10 件
- 同一ドメインから連続で取得するのは 5 件まで
- リクエスト間隔は 1 秒以上 (サーバー側への配慮)

## frontmatter の最小要件

必須は 3 つ、残りは空で良い:

- `title`
- `url` (canonical)
- `language`

その他 (`site`, `author`, `published_at`, `tags`, `note`) は任意。
取得時に分かった情報は可能な限り埋める。

## アンチパターン

- chat で URL をもらっておいて、inbox には書かずに直接
  `source/web/...` を作る → ソースが state に載らないため、scan で
  孤立 MD 扱いになる。必ず `inbox/web/<slug>.url` を先に作る。
- 関連リンクを全自動で再帰的に取り込む → スパイダリングしない。
  毎回ユーザーに選ばせる。
- 関連リンクの列挙でナビ / フッターまで含める → テンプレ部分は
  除外する。
- 取得失敗したのに `.md` を「とりあえず」書いてしまう → 失敗時は
  `.url` だけ残して `--status failed` で記録。
- 5. の確認をスキップして勝手に inbox を書く → AskUserQuestion で
  最低 1 回は相談する。
- バッチ取り込みで URL 1 件ごとに sync 全体 (prune〜dashboard) を
  回す → 8. はバッチの最後に 1 度だけ。

## チェックリスト

- [ ] URL を正規化 (canonical) した
- [ ] プレビューで title / site / language を得た
- [ ] slug と frontmatter ドラフトをユーザーに確認した
- [ ] `inbox/web/<slug>.url` を書いた
- [ ] Markdown を `source/web/<slug>.md` に書いて `record` した
      (失敗時は `--status failed`)
- [ ] 関連リンクを抽出・dedupe・除外ルールで絞った
- [ ] 関連リンクの取り込み可否をユーザーに聞いた
- [ ] バッチ末尾で `prune` / `verify` / `update-readme` / `dashboard`
      を 1 度だけ回した
- [ ] 取り込み結果と未解決点をユーザーに報告した
