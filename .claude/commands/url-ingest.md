---
description: chat で渡された URL を inbox/web/<slug>.url に落として取り込む (url-ingest スキル)
argument-hint: "<URL> [追加の指示]"
---

knowledge-kit の `url-ingest` スキル (`.claude/skills/url-ingest/SKILL.md`) を
実行してください。

- 対象 URL / 追加指示: $ARGUMENTS
- プレビュー → canonical → slug/frontmatter 確認 → `.url` 生成 →
  sync に合流 → 関連リンクの選別取り込み、の順で進める
- AskUserQuestion での確認を最低 1 回は必ず行う
