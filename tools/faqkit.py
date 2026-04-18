#!/usr/bin/env python3
"""faq-kit 状態管理ツール。

このスクリプトはキットの「機械的に決定的な部分」だけを担います。

  - inbox/ と source/ を走査してハッシュを計算
  - .faqkit/state.yml と突き合わせて差分を検出
  - 変換結果を state.yml に記録
  - state.yml とファイルシステムの整合性を検証

変換そのもの（PDF→MD 等）はこのスクリプトでは行いません。エージェント
が scan の結果を読み、好みの手段で Markdown 化し、record で結果を
登録する、という分業です。

使い方:

  python tools/faqkit.py scan                 # 差分を人間向けに要約
  python tools/faqkit.py scan --json          # 差分を機械向け JSON で出力
  python tools/faqkit.py record \\
      --source inbox/handbook.pdf \\
      --output source/handbook.md \\
      --converter pandoc@3.1 \\
      [--status ok|failed] [--notes "..."]
  python tools/faqkit.py prune                # 孤立 MD を自動削除し state を整える
  python tools/faqkit.py verify               # state と filesystem の整合性確認
  python tools/faqkit.py reindex              # state を壊したときの復旧下書き
  python tools/faqkit.py config               # 実効設定 (defaults + config.yml) を出力

依存: PyYAML。Python 3.10+。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "PyYAML が必要です。`pip install pyyaml` もしくは apm 経由で導入してください。\n"
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# 定数と設定
# ---------------------------------------------------------------------------

STATE_VERSION = 1
STATE_RELATIVE = Path(".faqkit") / "state.yml"
CONFIG_RELATIVE = Path(".faqkit") / "config.yml"
INBOX_DIRNAME = "inbox"
SOURCE_DIRNAME = "source"

DEFAULT_IGNORE = [
    ".DS_Store",
    "Thumbs.db",
    "*.tmp",
    "*~",
    ".gitkeep",
]


# config.yml の組み込みデフォルト。user の config はこの木に深く
# マージされる。ここが「公式のデフォルト値」の単一真実。
DEFAULT_CONFIG: dict = {
    "version": 1,
    "ignore": [],
    "retry": {
        "auto": True,
        "max_attempts": 3,
    },
    "ocr": {
        "languages": ["jpn", "eng"],
        "dpi": 300,
        "page_range": "all",
        "confidence_mark_below": 0.6,
    },
    "web": {
        "method_order": ["fetch", "browser_mcp"],
        "fetch": {
            "timeout_seconds": 30,
            "user_agent": "faq-kit/0.1",
            "follow_redirects": True,
        },
        "browser_mcp": {
            "timeout_seconds": 60,
        },
        "fallback_on": [
            "http_4xx",
            "http_5xx",
            "empty_body",
            "spa_shell",
            "requires_auth",
            "network_error",
        ],
    },
    "language": {
        "default": "auto",
        "translate": False,
    },
    "logs": {
        "dir": ".faqkit/logs",
        "retention_days": 30,
    },
    "readme": {
        "auto_update": True,
        "include_failed": True,
    },
    "dashboard": {
        "auto_generate": True,
        "auto_open": False,
        "output": ".faqkit/dashboard.html",
    },
}

KNOWN_TOP_LEVEL_KEYS = set(DEFAULT_CONFIG.keys())


def utc_now() -> str:
    """ISO8601 (UTC, 秒精度)。"""
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# ルート解決
# ---------------------------------------------------------------------------

def find_root(start: Path) -> Path:
    """`.faqkit/` を持つ最寄りの祖先を返す。"""
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".faqkit").is_dir():
            return candidate
    sys.stderr.write(
        f"エラー: {start} から `.faqkit/` を持つディレクトリが見つかりません。\n"
        f"kit のルートで実行するか、--root オプションを指定してください。\n"
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# ハッシュと走査
# ---------------------------------------------------------------------------

def sha256_of(path: Path, *, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return f"sha256:{h.hexdigest()}"


def _matches_ignore(rel: Path, patterns: list[str]) -> bool:
    import fnmatch
    posix = rel.as_posix()
    name = rel.name
    for pat in patterns:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(posix, pat):
            return True
    return False


def walk_files(root: Path, subdir: str, ignore: list[str]) -> Iterable[Path]:
    base = root / subdir
    if not base.exists():
        return
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if _matches_ignore(rel, ignore):
            continue
        yield p


# ---------------------------------------------------------------------------
# state.yml I/O
# ---------------------------------------------------------------------------

@dataclass
class Entry:
    source: str
    source_hash: str = ""
    source_mtime: str = ""
    source_size: int = 0
    output: str = ""
    output_hash: str = ""
    converted_at: str = ""
    converter: str = ""
    status: str = "ok"
    notes: str = ""

    def to_dict(self) -> dict:
        d = {
            "source": self.source,
            "source_hash": self.source_hash,
            "source_mtime": self.source_mtime,
            "source_size": self.source_size,
            "output": self.output,
            "output_hash": self.output_hash,
            "converted_at": self.converted_at,
            "converter": self.converter,
            "status": self.status,
        }
        if self.notes:
            d["notes"] = self.notes
        return d


@dataclass
class State:
    version: int = STATE_VERSION
    updated_at: str = ""
    entries: list[Entry] = field(default_factory=list)

    def by_source(self) -> dict[str, Entry]:
        return {e.source: e for e in self.entries}

    def upsert(self, entry: Entry) -> None:
        idx = {e.source: i for i, e in enumerate(self.entries)}
        if entry.source in idx:
            self.entries[idx[entry.source]] = entry
        else:
            self.entries.append(entry)
        self.entries.sort(key=lambda e: e.source)


def load_state(root: Path) -> State:
    path = root / STATE_RELATIVE
    if not path.exists():
        return State(updated_at=utc_now())
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    version = int(data.get("version", STATE_VERSION))
    if version != STATE_VERSION:
        sys.stderr.write(
            f"警告: state.yml の version={version} は未対応です (期待 {STATE_VERSION})。\n"
        )
    entries_raw = data.get("entries") or []
    entries = [Entry(**{**{k: "" for k in Entry.__annotations__}, **e}) for e in entries_raw]
    return State(version=version, updated_at=data.get("updated_at", ""), entries=entries)


def save_state(root: Path, state: State) -> None:
    path = root / STATE_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = utc_now()
    state.entries.sort(key=lambda e: e.source)
    payload = {
        "version": state.version,
        "updated_at": state.updated_at,
        "entries": [e.to_dict() for e in state.entries],
    }
    tmp = path.with_suffix(".yml.tmp")
    header = (
        "# faq-kit state file (managed by tools/faqkit.py)\n"
        "# 手動編集は原則しないでください。壊れた場合は `python tools/faqkit.py reindex` で復旧。\n"
    )
    with tmp.open("w", encoding="utf-8") as f:
        f.write(header)
        yaml.safe_dump(
            payload,
            f,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
    os.replace(tmp, path)


def load_config_raw(root: Path) -> dict:
    """config.yml の生データを読む (存在しなければ空 dict)。"""
    path = root / CONFIG_RELATIVE
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, override: dict) -> dict:
    """override の値で base を再帰的に上書きした新しい dict を返す。

    リストは置換、dict は再帰マージ、スカラは上書き。
    base のキー順を保ちつつ、override 側だけにあるキーを末尾に追加。
    """
    out: dict = {}
    for k, bv in base.items():
        if k in override:
            ov = override[k]
            if isinstance(bv, dict) and isinstance(ov, dict):
                out[k] = _deep_merge(bv, ov)
            else:
                out[k] = ov
        else:
            out[k] = bv
    for k, ov in override.items():
        if k not in base:
            out[k] = ov
    return out


def validate_config(user: dict) -> list[str]:
    """config.yml の警告一覧を返す。致命的なら例外でも良いが現状は警告のみ。"""
    warnings: list[str] = []
    if not isinstance(user, dict):
        return [f"config.yml のトップレベルは mapping である必要があります (got {type(user).__name__})"]

    # 未知の最上位キー
    for k in user.keys():
        if k not in KNOWN_TOP_LEVEL_KEYS:
            warnings.append(f"未知のキー: {k} (typo の可能性。KNOWN={sorted(KNOWN_TOP_LEVEL_KEYS)})")

    # version
    v = user.get("version", 1)
    if not isinstance(v, int):
        warnings.append(f"version は整数である必要があります (got {v!r})")
    elif v != 1:
        warnings.append(f"version={v} は未対応 (現行は 1)")

    # ignore は list[str]
    if "ignore" in user and not (
        isinstance(user["ignore"], list)
        and all(isinstance(x, str) for x in user["ignore"])
    ):
        warnings.append("ignore は文字列のリストである必要があります")

    # retry
    retry = user.get("retry")
    if isinstance(retry, dict):
        if "auto" in retry and not isinstance(retry["auto"], bool):
            warnings.append("retry.auto は真偽値である必要があります")
        if "max_attempts" in retry and not (
            isinstance(retry["max_attempts"], int) and retry["max_attempts"] >= 0
        ):
            warnings.append("retry.max_attempts は 0 以上の整数である必要があります")

    # ocr.languages
    ocr = user.get("ocr")
    if isinstance(ocr, dict):
        langs = ocr.get("languages")
        if langs is not None and not (
            isinstance(langs, list) and all(isinstance(x, str) for x in langs)
        ):
            warnings.append("ocr.languages は文字列のリストである必要があります")

    # dashboard
    dash = user.get("dashboard")
    if isinstance(dash, dict):
        for bk in ("auto_generate", "auto_open"):
            if bk in dash and not isinstance(dash[bk], bool):
                warnings.append(f"dashboard.{bk} は真偽値である必要があります")
        if "output" in dash and not isinstance(dash["output"], str):
            warnings.append("dashboard.output は文字列である必要があります")

    # web.method_order
    web = user.get("web")
    if isinstance(web, dict):
        mo = web.get("method_order")
        if mo is not None:
            if not (isinstance(mo, list) and all(isinstance(x, str) for x in mo)):
                warnings.append("web.method_order は文字列のリストである必要があります")
            else:
                unknown_methods = [m for m in mo if m not in {"fetch", "browser_mcp"}]
                if unknown_methods:
                    warnings.append(
                        f"web.method_order に未知の手段: {unknown_methods} (既知: fetch, browser_mcp)"
                    )

    return warnings


def effective_config(root: Path) -> tuple[dict, list[str]]:
    """デフォルトに user の config を深くマージした dict と警告一覧を返す。"""
    user = load_config_raw(root)
    warnings = validate_config(user) if user else []
    merged = _deep_merge(DEFAULT_CONFIG, user) if user else dict(DEFAULT_CONFIG)
    return merged, warnings


# 後方互換: 既存コードは load_config() が dict を返すことを期待していた
def load_config(root: Path) -> dict:
    merged, warnings = effective_config(root)
    for w in warnings:
        sys.stderr.write(f"warning: config.yml: {w}\n")
    return merged


# ---------------------------------------------------------------------------
# scan: 差分検出
# ---------------------------------------------------------------------------

def suggest_output(rel_source: Path) -> str:
    """inbox/... → source/... への対応を提案する。

    階層は保つ。拡張子は `.md` に置換。
    inbox/pdf/handbook/2026.pdf  →  source/pdf/handbook/2026.md
    """
    if rel_source.parts and rel_source.parts[0] == INBOX_DIRNAME:
        tail = Path(*rel_source.parts[1:])
    else:
        tail = rel_source
    return (Path(SOURCE_DIRNAME) / tail.with_suffix(".md")).as_posix()


def scan(root: Path) -> dict:
    config = load_config(root)  # defaults + user をマージ済み
    ignore = list(DEFAULT_IGNORE) + list(config.get("ignore", []) or [])
    state = load_state(root)
    by_source = state.by_source()

    new: list[dict] = []
    modified: list[dict] = []
    unchanged: list[dict] = []
    failed_retained: list[dict] = []

    for path in walk_files(root, INBOX_DIRNAME, ignore):
        rel = path.relative_to(root).as_posix()
        info = {
            "source": rel,
            "source_hash": sha256_of(path),
            "source_mtime": _dt.datetime.fromtimestamp(
                path.stat().st_mtime, tz=_dt.timezone.utc
            ).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "source_size": path.stat().st_size,
            "suggested_output": suggest_output(Path(rel)),
        }
        prev = by_source.get(rel)
        if prev is None:
            new.append(info)
        elif prev.source_hash != info["source_hash"]:
            info["previous_hash"] = prev.source_hash
            info["output"] = prev.output or info["suggested_output"]
            modified.append(info)
        elif prev.status == "failed":
            info["output"] = prev.output or info["suggested_output"]
            info["notes"] = prev.notes
            failed_retained.append(info)
        else:
            info["output"] = prev.output
            unchanged.append(info)

    # 孤立ソース: state にあるが inbox 実体が消えた
    orphan_sources = []
    for e in state.entries:
        src_path = root / e.source
        if not src_path.exists():
            orphan_sources.append({
                "source": e.source,
                "output": e.output,
                "status": e.status,
            })

    # 孤立 MD: state に載っていない MD が source/ にある
    tracked_outputs = {e.output for e in state.entries if e.output}
    orphan_outputs = []
    for path in walk_files(root, SOURCE_DIRNAME, ignore):
        if path.suffix.lower() != ".md":
            continue
        rel = path.relative_to(root).as_posix()
        if rel not in tracked_outputs:
            orphan_outputs.append({"output": rel})

    # 出力側の改変検知（tampered）
    tampered = []
    for e in state.entries:
        if not e.output or not e.output_hash:
            continue
        out_path = root / e.output
        if not out_path.exists():
            continue
        current = sha256_of(out_path)
        if current != e.output_hash:
            tampered.append({
                "source": e.source,
                "output": e.output,
                "recorded_output_hash": e.output_hash,
                "current_output_hash": current,
            })

    return {
        "root": str(root),
        "summary": {
            "new": len(new),
            "modified": len(modified),
            "unchanged": len(unchanged),
            "failed_retained": len(failed_retained),
            "orphan_sources": len(orphan_sources),
            "orphan_outputs": len(orphan_outputs),
            "tampered_outputs": len(tampered),
        },
        "new": new,
        "modified": modified,
        "unchanged": unchanged,
        "failed_retained": failed_retained,
        "orphan_sources": orphan_sources,
        "orphan_outputs": orphan_outputs,
        "tampered_outputs": tampered,
    }


def print_scan_human(report: dict) -> None:
    s = report["summary"]
    print(f"faq-kit scan ({report['root']})")
    print(f"  新規            : {s['new']}")
    print(f"  更新             : {s['modified']}")
    print(f"  スキップ        : {s['unchanged']}")
    print(f"  失敗残存        : {s['failed_retained']}")
    print(f"  孤立ソース      : {s['orphan_sources']}")
    print(f"  孤立 MD         : {s['orphan_outputs']}")
    print(f"  改変された出力  : {s['tampered_outputs']}")

    def _show(title: str, items: list[dict], keys: list[str]) -> None:
        if not items:
            return
        print(f"\n[{title}] ({len(items)})")
        for it in items:
            parts = []
            for k in keys:
                if k in it and it[k] != "":
                    parts.append(f"{k}={it[k]}")
            print("  - " + " ".join(parts))

    _show("new", report["new"], ["source", "suggested_output"])
    _show("modified", report["modified"], ["source", "output", "previous_hash"])
    _show("failed_retained", report["failed_retained"], ["source", "notes"])
    _show("orphan_sources", report["orphan_sources"], ["source", "output", "status"])
    _show("orphan_outputs", report["orphan_outputs"], ["output"])
    _show("tampered_outputs", report["tampered_outputs"], ["output"])


# ---------------------------------------------------------------------------
# record: 変換結果を state に登録
# ---------------------------------------------------------------------------

def cmd_record(root: Path, args: argparse.Namespace) -> int:
    state = load_state(root)

    source_rel = Path(args.source).as_posix()
    source_path = root / source_rel
    if not source_path.exists():
        sys.stderr.write(f"エラー: source が存在しません: {source_path}\n")
        return 2

    entry = state.by_source().get(source_rel) or Entry(source=source_rel)
    entry.source = source_rel
    entry.source_hash = sha256_of(source_path)
    entry.source_mtime = _dt.datetime.fromtimestamp(
        source_path.stat().st_mtime, tz=_dt.timezone.utc
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    entry.source_size = source_path.stat().st_size

    if args.status == "ok":
        if not args.output:
            sys.stderr.write("エラー: status=ok のとき --output が必須です。\n")
            return 2
        output_rel = Path(args.output).as_posix()
        output_path = root / output_rel
        if not output_path.exists():
            sys.stderr.write(f"エラー: output が存在しません: {output_path}\n")
            return 2
        entry.output = output_rel
        entry.output_hash = sha256_of(output_path)
    elif args.status == "failed":
        entry.output = args.output or entry.output
        entry.output_hash = ""
    else:
        sys.stderr.write("エラー: status は ok か failed のみです。\n")
        return 2

    entry.converted_at = utc_now()
    entry.converter = args.converter or entry.converter or "unknown"
    entry.status = args.status
    entry.notes = args.notes or ""

    state.upsert(entry)
    save_state(root, state)
    print(f"recorded: {entry.source} -> {entry.output or '(no output)'} [{entry.status}]")
    return 0


# ---------------------------------------------------------------------------
# verify: state と filesystem の整合性チェック
# ---------------------------------------------------------------------------

def cmd_prune(root: Path, args: argparse.Namespace) -> int:
    """孤立した source/ の MD と dead state エントリを削除する。

    孤立の定義:
      A. state にエントリがあるが inbox 側のソースが消えている
         → source/ の対応 MD を削除し、state からも外す
      B. source/ に MD があるが state に登録されていない
         → MD を削除 (手動で置いた場合も掃除対象。残したければ
            先に inbox に原資料を置いて convert を走らせる想定)

    --dry-run を付けるとファイル削除と state 書き換えを行わず列挙のみ。
    """
    report = scan(root)
    deleted_md: list[str] = []
    pruned_entries: list[str] = []

    state = load_state(root)

    # A: state にあり source 消失
    orphan_sources = {item["source"]: item for item in report["orphan_sources"]}
    if orphan_sources:
        remaining: list[Entry] = []
        for entry in state.entries:
            if entry.source in orphan_sources:
                # 対応 MD も削除
                if entry.output:
                    out_path = root / entry.output
                    if out_path.exists() and out_path.is_file():
                        if args.dry_run:
                            deleted_md.append(entry.output)
                        else:
                            out_path.unlink()
                            deleted_md.append(entry.output)
                            _prune_empty_dirs(out_path.parent, root / SOURCE_DIRNAME)
                pruned_entries.append(entry.source)
            else:
                remaining.append(entry)
        state.entries = remaining

    # B: source に MD あり state 未登録
    for item in report["orphan_outputs"]:
        out_path = root / item["output"]
        if out_path.exists() and out_path.is_file():
            if args.dry_run:
                deleted_md.append(item["output"])
            else:
                out_path.unlink()
                deleted_md.append(item["output"])
                _prune_empty_dirs(out_path.parent, root / SOURCE_DIRNAME)

    if not args.dry_run:
        save_state(root, state)

    print(f"prune: 削除 MD {len(deleted_md)} 件 / state エントリ削除 {len(pruned_entries)} 件" + (" (dry-run)" if args.dry_run else ""))
    for p in deleted_md:
        print(f"  - {p}")
    for s in pruned_entries:
        print(f"  - state: {s}")
    return 0


def _prune_empty_dirs(start: Path, stop_at: Path) -> None:
    """start から stop_at までの間にある空ディレクトリを上方向に削除する。"""
    stop_at = stop_at.resolve()
    try:
        current = start.resolve()
    except FileNotFoundError:
        return
    while current != stop_at and stop_at in current.parents:
        try:
            if current.is_dir() and not any(current.iterdir()):
                current.rmdir()
                current = current.parent
            else:
                break
        except OSError:
            break


def cmd_verify(root: Path, _args: argparse.Namespace) -> int:
    report = scan(root)
    s = report["summary"]
    drift = (
        s["orphan_sources"]
        + s["orphan_outputs"]
        + s["tampered_outputs"]
        + s["failed_retained"]
    )
    print_scan_human(report)
    if drift > 0:
        print(f"\n不整合 {drift} 件を検出しました。", file=sys.stderr)
        return 1
    if s["new"] > 0 or s["modified"] > 0:
        print(f"\n未処理の差分があります (new={s['new']}, modified={s['modified']})。", file=sys.stderr)
        return 1
    print("\nstate と filesystem は整合しています。")
    return 0


# ---------------------------------------------------------------------------
# reindex: state の下書きを filesystem から再構築
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# render-index / update-readme: source 一覧を README に反映
# ---------------------------------------------------------------------------

README_BEGIN = "<!-- BEGIN source-index -->"
README_END = "<!-- END source-index -->"


def _read_md_title(md_path: Path) -> str | None:
    """frontmatter の title、なければ最初の H1 を返す。"""
    if not md_path.exists():
        return None
    try:
        content = md_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # frontmatter
    if content.startswith("---\n"):
        end = content.find("\n---\n", 4)
        if end > 0:
            fm = content[4:end]
            for line in fm.splitlines():
                if line.lower().startswith("title:"):
                    return line.split(":", 1)[1].strip().strip('"').strip("'") or None
    # H1 fallback
    for line in content.splitlines():
        line_s = line.strip()
        if line_s.startswith("# "):
            return line_s[2:].strip() or None
    return None


def _group_by_toplevel(entries: list[Entry]) -> dict[str, list[Entry]]:
    """source の最上位ディレクトリでグループ化する。

    inbox/handbooks/foo.pdf → "handbooks"
    inbox/memo.txt         → "(root)"
    """
    groups: dict[str, list[Entry]] = {}
    for e in entries:
        parts = Path(e.source).parts
        if len(parts) >= 2 and parts[0] == INBOX_DIRNAME:
            key = parts[1] if len(parts) > 2 else "(root)"
        else:
            key = "(root)"
        groups.setdefault(key, []).append(e)
    return groups


def render_index(root: Path, *, include_failed: bool = True) -> str:
    """source 一覧を Markdown 断片として返す。"""
    state = load_state(root)
    ok_entries = [e for e in state.entries if e.status == "ok"]
    failed_entries = [e for e in state.entries if e.status == "failed"]

    lines: list[str] = []
    lines.append(
        f"最終更新: {utc_now()} / 登録 {len(state.entries)} 件"
        f" (ok {len(ok_entries)} / failed {len(failed_entries)})"
    )
    lines.append("")

    if not ok_entries:
        lines.append("_まだ原資料は取り込まれていません。`inbox/` にファイルを置いて同期フックを走らせてください。_")
    else:
        groups = _group_by_toplevel(ok_entries)
        for key in sorted(groups.keys()):
            lines.append(f"### {key}/")
            lines.append("")
            for e in sorted(groups[key], key=lambda x: x.source):
                title = _read_md_title(root / e.output) or Path(e.source).stem
                date = e.converted_at.split("T")[0] if e.converted_at else "?"
                lines.append(f"- [{title}]({e.output})")
                lines.append(f"  - source: `{e.source}`")
                lines.append(f"  - converted: {date} via `{e.converter or 'unknown'}`")
            lines.append("")

    if include_failed and failed_entries:
        lines.append("### 変換失敗")
        lines.append("")
        for e in sorted(failed_entries, key=lambda x: x.source):
            note = f" — {e.notes}" if e.notes else ""
            lines.append(f"- `{e.source}`{note}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def cmd_render_index(root: Path, args: argparse.Namespace) -> int:
    config, _ = effective_config(root)
    include_failed = bool(
        args.include_failed
        if args.include_failed is not None
        else config.get("readme", {}).get("include_failed", True)
    )
    sys.stdout.write(render_index(root, include_failed=include_failed))
    return 0


def cmd_update_readme(root: Path, args: argparse.Namespace) -> int:
    readme_path = root / (args.path or "README.md")
    if not readme_path.exists():
        sys.stderr.write(f"エラー: README が見つかりません: {readme_path}\n")
        return 2
    content = readme_path.read_text(encoding="utf-8")
    b = content.find(README_BEGIN)
    e = content.find(README_END)
    if b < 0 or e < 0 or b >= e:
        sys.stderr.write(
            f"エラー: README に `{README_BEGIN}` と `{README_END}` のマーカーが見つかりません。\n"
            f"まず手動で以下のブロックを README に挿入してください:\n\n"
            f"{README_BEGIN}\n{README_END}\n"
        )
        return 2

    config, _ = effective_config(root)
    include_failed = bool(
        args.include_failed
        if args.include_failed is not None
        else config.get("readme", {}).get("include_failed", True)
    )
    body = render_index(root, include_failed=include_failed)

    new = (
        content[: b + len(README_BEGIN)]
        + "\n\n"
        + body
        + "\n"
        + content[e:]
    )
    if new == content:
        print(f"update-readme: 変更なし ({readme_path})")
        return 0
    readme_path.write_text(new, encoding="utf-8")
    entries = len(load_state(root).entries)
    print(f"update-readme: 更新しました ({readme_path}, entries={entries})")
    return 0


# ---------------------------------------------------------------------------
# dashboard: state / scan / 設定を 1 枚の HTML にまとめて出力
# ---------------------------------------------------------------------------

TOOL_VERSION = "0.1.0"


def _dir_stats(root: Path, subdir: str, ignore: list[str]) -> tuple[int, int]:
    count = 0
    total = 0
    for p in walk_files(root, subdir, ignore):
        count += 1
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return count, total


def _output_missing_entries(root: Path, state: State) -> list[Entry]:
    missing = []
    for e in state.entries:
        if e.status != "ok" or not e.output:
            continue
        if not (root / e.output).exists():
            missing.append(e)
    return missing


def build_dashboard_data(root: Path) -> dict:
    """dashboard.html に埋め込む JSON。"""
    config, config_warnings = effective_config(root)
    ignore = list(DEFAULT_IGNORE) + list(config.get("ignore", []) or [])
    state = load_state(root)
    report = scan(root)
    s = report["summary"]

    ok_entries = [e for e in state.entries if e.status == "ok"]
    failed_entries = [e for e in state.entries if e.status == "failed"]
    missing_entries = _output_missing_entries(root, state)

    # source/ のファイル統計 (walk_files は MD 以外も拾うが、基本 MD のみ)
    source_count = 0
    source_bytes = 0
    for p in walk_files(root, SOURCE_DIRNAME, ignore):
        if p.suffix.lower() != ".md":
            continue
        source_count += 1
        try:
            source_bytes += p.stat().st_size
        except OSError:
            pass

    inbox_count, inbox_bytes = _dir_stats(root, INBOX_DIRNAME, ignore)

    groups_raw = _group_by_toplevel(ok_entries)
    groups: dict[str, list[dict]] = {}
    for key, items in groups_raw.items():
        arr = []
        for e in sorted(items, key=lambda x: x.source):
            src_path = root / e.source
            try:
                src_size = src_path.stat().st_size if src_path.exists() else 0
            except OSError:
                src_size = 0
            arr.append({
                "source": e.source,
                "output": e.output,
                "title": _read_md_title(root / e.output) if e.output else None,
                "converter": e.converter,
                "converted_at": e.converted_at,
                "source_hash": e.source_hash,
                "source_size": src_size,
            })
        groups[key] = arr

    data = {
        "generated_at": utc_now(),
        "tool_version": TOOL_VERSION,
        "root": str(root),
        "state": {
            "version": state.version,
            "updated_at": state.updated_at,
            "entries_count": len(state.entries),
        },
        "stats": {
            "ok_count": len(ok_entries),
            "failed_count": len(failed_entries),
            "source_files_count": source_count,
            "source_total_bytes": source_bytes,
        },
        "inbox": {
            "files_count": inbox_count,
            "total_bytes": inbox_bytes,
        },
        "scan": {
            "added": s["new"],
            "modified": s["modified"],
            "skipped": s["unchanged"],
            "failed_retained": s["failed_retained"],
            "orphan_source": s["orphan_sources"],
            "orphan_output": s["orphan_outputs"],
            "modified_output": s["tampered_outputs"],
            "output_missing": len(missing_entries),
        },
        "warnings": {
            "failed": [{"source": e.source, "notes": e.notes} for e in failed_entries],
            "output_missing": [e.output for e in missing_entries],
            "orphan_source": [x["source"] for x in report["orphan_sources"]],
            "orphan_output": [x["output"] for x in report["orphan_outputs"]],
            "modified_output": [x["output"] for x in report["tampered_outputs"]],
            "config": config_warnings,
        },
        "groups": groups,
        "config": config,
    }
    return data


def _load_dashboard_template(root: Path) -> str:
    """templates/dashboard.html.tmpl を読む。見つからなければエラー。"""
    candidates = [
        root / "templates" / "dashboard.html.tmpl",
        Path(__file__).resolve().parent.parent / "templates" / "dashboard.html.tmpl",
    ]
    for tpl in candidates:
        if tpl.exists():
            return tpl.read_text(encoding="utf-8")
    raise FileNotFoundError(
        "templates/dashboard.html.tmpl が見つかりません。kit/templates/ に配置してください。"
    )


def _dashboard_signature(data: dict) -> str:
    """生成時刻を除く dashboard データの安定ハッシュ。

    `dashboard` は毎回呼ばれるが、状態に変化がなければ HTML を上書き
    しないために使う。mtime 保存の副次効果もある。
    """
    stripped = {k: v for k, v in data.items() if k != "generated_at"}
    blob = json.dumps(stripped, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def render_dashboard(root: Path) -> tuple[str, str]:
    """dashboard.html 全文と、生成時刻を除いた署名 (16 桁 hex) を返す。"""
    tpl = _load_dashboard_template(root)
    data = build_dashboard_data(root)
    sig = _dashboard_signature(data)
    # `</script>` 混入対策のみ。JSON をそのまま埋め込む。
    payload = json.dumps(data, ensure_ascii=False, indent=2).replace("</script>", "<\\/script>")
    html = tpl.replace("__FAQKIT_DATA__", payload)
    # 署名を HTML の先頭コメントに書き込んで、次回の差分判定に使う。
    html = f"<!-- faqkit-sig: {sig} -->\n" + html
    return html, sig


def cmd_dashboard(root: Path, args: argparse.Namespace) -> int:
    config, _ = effective_config(root)
    dash_cfg = config.get("dashboard", {}) or {}
    out_rel = args.output or dash_cfg.get("output", ".faqkit/dashboard.html")
    out_path = (root / out_rel).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    html, sig = render_dashboard(root)

    # 冪等: 署名が同じなら書き換えない (生成時刻だけのためにファイルを
    # 差し替えて mtime を変えない)
    changed = True
    if out_path.exists():
        prev_head = out_path.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
        if prev_head and f"faqkit-sig: {sig}" in prev_head[0]:
            changed = False
    if changed:
        out_path.write_text(html, encoding="utf-8")

    msg = "更新" if changed else "変更なし"
    print(f"dashboard: {msg} ({out_path}) sig={sig}")

    # open の可否
    should_open = args.open if args.open is not None else bool(dash_cfg.get("auto_open", False))
    if should_open:
        import webbrowser
        url = "file://" + str(out_path)
        ok = webbrowser.open(url)
        print(f"dashboard: ブラウザを起動 ({'ok' if ok else '失敗'}): {url}")
    return 0


def cmd_config(root: Path, args: argparse.Namespace) -> int:
    """実効設定 (defaults + config.yml) を出力する。

    --json で JSON、--defaults で生のデフォルトのみ、--diff で
    ユーザーが上書きした項目だけを表示。
    """
    user = load_config_raw(root)
    warnings = validate_config(user) if user else []

    if args.defaults:
        target = DEFAULT_CONFIG
    elif args.diff:
        target = user
    else:
        target, _ = effective_config(root)

    if args.json:
        json.dump(target, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        yaml.safe_dump(
            target, sys.stdout, allow_unicode=True, sort_keys=False, default_flow_style=False
        )

    if warnings:
        sys.stderr.write("\n")
        for w in warnings:
            sys.stderr.write(f"warning: config.yml: {w}\n")
        return 1 if args.strict else 0
    return 0


def cmd_reindex(root: Path, args: argparse.Namespace) -> int:
    """source/ の frontmatter と inbox/ を突き合わせて state を仮復旧する。

    完全な復旧はできないため、`converter: unknown` / `status: ok` の暫定
    エントリを生成する。次回 scan で差分として扱われないよう hash は
    現時点のものを記録する。人間が内容を確認してから save するために、
    既定では --write を付けないと上書きしない。
    """
    config = load_config(root)
    ignore = list(DEFAULT_IGNORE) + list(config.get("ignore", []) or [])
    state = State()

    # inbox 側から走査
    for path in walk_files(root, INBOX_DIRNAME, ignore):
        rel_source = path.relative_to(root).as_posix()
        suggested = suggest_output(Path(rel_source))
        output_path = root / suggested
        entry = Entry(
            source=rel_source,
            source_hash=sha256_of(path),
            source_mtime=_dt.datetime.fromtimestamp(
                path.stat().st_mtime, tz=_dt.timezone.utc
            ).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            source_size=path.stat().st_size,
            output=suggested if output_path.exists() else "",
            output_hash=sha256_of(output_path) if output_path.exists() else "",
            converted_at="" if not output_path.exists() else utc_now(),
            converter="unknown",
            status="ok" if output_path.exists() else "failed",
            notes="reindex で再構築された暫定エントリ",
        )
        state.upsert(entry)

    if args.write:
        save_state(root, state)
        print(f"reindex: {len(state.entries)} 件を state.yml に書き出しました。")
    else:
        payload = {
            "version": state.version,
            "updated_at": utc_now(),
            "entries": [e.to_dict() for e in state.entries],
        }
        yaml.safe_dump(payload, sys.stdout, allow_unicode=True, sort_keys=False, default_flow_style=False)
        print("\n# ↑ ドライラン。確定するには --write を付けて再実行してください。", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_scan(root: Path, args: argparse.Namespace) -> int:
    report = scan(root)
    if args.json:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    elif args.yaml:
        yaml.safe_dump(report, sys.stdout, allow_unicode=True, sort_keys=False, default_flow_style=False)
    else:
        print_scan_human(report)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="faqkit", description="faq-kit 状態管理ツール")
    p.add_argument("--root", type=Path, default=None, help="kit のルートパス (既定: 自動探索)")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("scan", help="inbox と source を走査して差分を出力")
    ps.add_argument("--json", action="store_true", help="機械可読 JSON で出力")
    ps.add_argument("--yaml", action="store_true", help="YAML で出力")
    ps.set_defaults(func=cmd_scan)

    pr = sub.add_parser("record", help="変換結果を state.yml に記録")
    pr.add_argument("--source", required=True, help="inbox 側ソースのリポジトリ相対パス")
    pr.add_argument("--output", help="source 側出力のリポジトリ相対パス (status=ok のとき必須)")
    pr.add_argument("--converter", required=True, help="変換手段の自己申告 (例: pandoc@3.1)")
    pr.add_argument("--status", default="ok", choices=["ok", "failed"], help="変換結果")
    pr.add_argument("--notes", default="", help="補足 (failed 時の理由など)")
    pr.set_defaults(func=cmd_record)

    pp = sub.add_parser("prune", help="孤立 MD を自動削除し state を整える")
    pp.add_argument("--dry-run", action="store_true", help="削除せず対象のみ列挙する")
    pp.set_defaults(func=cmd_prune)

    pv = sub.add_parser("verify", help="state.yml と filesystem の整合性を検証")
    pv.set_defaults(func=cmd_verify)

    pri = sub.add_parser("reindex", help="state.yml を filesystem から再構築 (暫定)")
    pri.add_argument("--write", action="store_true", help="state.yml を実際に上書きする")
    pri.set_defaults(func=cmd_reindex)

    pc = sub.add_parser("config", help="実効設定を出力 (defaults + config.yml マージ結果)")
    pc.add_argument("--json", action="store_true", help="JSON で出力")
    pc.add_argument("--defaults", action="store_true", help="組み込みデフォルトのみ出力")
    pc.add_argument("--diff", action="store_true", help="config.yml の生内容 (上書き分) のみ出力")
    pc.add_argument("--strict", action="store_true", help="警告が 1 件でもあれば rc=1")
    pc.set_defaults(func=cmd_config)

    pri_idx = sub.add_parser("render-index", help="source 一覧を Markdown 断片として標準出力へ")
    pri_idx.add_argument(
        "--include-failed",
        dest="include_failed",
        action="store_true",
        default=None,
        help="変換失敗のエントリも載せる (既定: config.readme.include_failed)",
    )
    pri_idx.add_argument(
        "--no-include-failed",
        dest="include_failed",
        action="store_false",
        help="変換失敗のエントリを載せない",
    )
    pri_idx.set_defaults(func=cmd_render_index)

    pur = sub.add_parser("update-readme", help="README.md の source-index ブロックを更新")
    pur.add_argument("--path", default=None, help="README のパス (既定: ルート直下の README.md)")
    pur.add_argument(
        "--include-failed",
        dest="include_failed",
        action="store_true",
        default=None,
        help="変換失敗のエントリも載せる",
    )
    pur.add_argument(
        "--no-include-failed",
        dest="include_failed",
        action="store_false",
        help="変換失敗のエントリを載せない",
    )
    pur.set_defaults(func=cmd_update_readme)

    pdash = sub.add_parser("dashboard", help="ダッシュボード HTML を生成 (任意でブラウザ起動)")
    pdash.add_argument("--output", default=None, help="出力先 (既定: config.dashboard.output)")
    pdash.add_argument(
        "--open",
        dest="open",
        action="store_true",
        default=None,
        help="生成後にデフォルトブラウザで開く",
    )
    pdash.add_argument(
        "--no-open",
        dest="open",
        action="store_false",
        help="ブラウザを開かない (既定)",
    )
    pdash.set_defaults(func=cmd_dashboard)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = args.root.resolve() if args.root else find_root(Path.cwd())
    return args.func(root, args)


if __name__ == "__main__":
    raise SystemExit(main())
