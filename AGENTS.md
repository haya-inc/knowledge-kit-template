# faq-kit 運用規約

このディレクトリの運用規約は [CLAUDE.md](./CLAUDE.md) に定義されています。
エージェント (Codex / Claude Code / その他) がこのファイルを読んだ場合は、
まず `CLAUDE.md` を開いて全体像を把握してください。取り込み (ingest) /
質問応答 (query) / 同期 (sync) の振る舞いはすべて `CLAUDE.md` の規約に
従います。

## スキル

本プロジェクトは、inbox → source 同期と URL 取り込みを「スキル」として
配布しています。Claude Code と Codex CLI の双方から同じ定義を参照
できるよう、canonical は `.claude/skills/` に置き、`.agents/skills/` は
そこへのシンボリックリンクとしています (Codex 仕様に準拠)。

- `sync` — `inbox/` の差分を `source/` の Markdown に反映するフルフロー同期。
  実体: [`.claude/skills/sync/SKILL.md`](./.claude/skills/sync/SKILL.md)
  (Codex からは `.agents/skills/sync/` 経由で同じ SKILL.md を参照)
- `url-ingest` — chat で渡された URL を `inbox/web/<slug>.url` に落とし、
  その後 sync に合流させるスキル。
  実体: [`.claude/skills/url-ingest/SKILL.md`](./.claude/skills/url-ingest/SKILL.md)
  (Codex からは `.agents/skills/url-ingest/` 経由)

### 起動方法

- **Codex CLI** (`codex --enable skills` が必要): `/skills` で一覧、
  `$sync` / `$url-ingest` で明示起動。description にマッチする発話でも
  暗黙的に起動する
- **Claude Code**: `/sync` / `/url-ingest` の slash command、または
  description にマッチする発話で起動 (`.claude/commands/` 参照)

### 配置

```
.claude/skills/
  sync/SKILL.md         # canonical
  url-ingest/SKILL.md   # canonical
.agents/skills/
  sync           -> ../../.claude/skills/sync         # Codex 用シンボリックリンク
  url-ingest     -> ../../.claude/skills/url-ingest   # Codex 用シンボリックリンク
```

Windows で git clone した場合は `core.symlinks=true` が必要です。symlink が
使えない環境では `.agents/skills/` 以下を `.claude/skills/` の内容で複製
してください (SKILL.md の内容は 2 者で常に一致させる)。

## その他

- エージェントの具体的なふるまいルールは [CLAUDE.md](./CLAUDE.md)
- slash command (Claude Code) の定義は `.claude/commands/`
- Codex には project scope の slash command 機構が無いため、Codex 側は
  skill 名 (`$sync` / `$url-ingest`) で同等の起動を行います
