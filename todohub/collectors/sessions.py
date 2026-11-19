"""会话采集：把各家 coding agent 落在本机的会话日志，压成每个会话一条摘要。

- Codex：~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl（旧的会被压成 .jsonl.zst）
- Claude Code：~/.claude/projects/<目录名>/<会话>.jsonl

只读源文件，结果写到 inbox/<来源>/index.json。按文件大小和修改时间做增量，没变的文件不重读。
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from .. import util

MAX_PROMPTS = 6
SKIP_PREFIX = ("<environment_context", "# AGENTS.md", "<user_instructions", "<command-name>", "<local-command",
               "Caveat:", "<system-reminder", "<turn_aborted", "<user_action", "<subagent_notification")


def _open_lines(path: Path):
    """逐行读取，自动处理 .zst 压缩。读不了时抛 RuntimeError。"""
    if path.suffix != ".zst":
        return open(path, encoding="utf-8", errors="replace")
    try:
        from compression import zstd  # Python 3.14+
        return io.TextIOWrapper(zstd.open(path, "rb"), encoding="utf-8", errors="replace")
    except ImportError:
        pass
    try:
        import zstandard
        return io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(open(path, "rb")), encoding="utf-8",
                                errors="replace")
    except ImportError:
        pass
    exe = shutil.which("zstd")
    if not exe:
        raise RuntimeError("需要 zstd 才能读压缩过的旧会话：brew install zstd")
    proc = subprocess.Popen([exe, "-dc", str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return io.TextIOWrapper(proc.stdout, encoding="utf-8", errors="replace")


def _texts(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    out = []
    for item in content or []:
        if isinstance(item, dict) and item.get("type") in ("input_text", "output_text", "text") and item.get("text"):
            out.append(item["text"])
    return out


TAG_LEAD = re.compile(r"^<[a-z][a-z0-9_-]*[ >/]")


def _is_human(text: str) -> bool:
    """排除客户端自动注入的内容：它们几乎都以一个 XML 标签开头（<environment_context>、<external_…> 等）。"""
    t = text.lstrip()
    return bool(t) and not t.startswith(SKIP_PREFIX) and not TAG_LEAD.match(t)


def _keep(prompts: list[str], text: str) -> None:
    """只留前 3 条和最后 3 条人话，够判断这个会话在干什么。"""
    prompts.append(util.trim(text, 400))
    if len(prompts) > MAX_PROMPTS:
        del prompts[3]


def parse_codex(path: Path) -> dict | None:
    meta, prompts, last_msg, turns, last_ts, n_user, work_ms = {}, [], "", 0, None, 0, 0
    with _open_lines(path) as f:
        for line in f:
            if not line.startswith("{"):
                continue
            # 大多数行是工具调用和推理，先用子串过滤，省掉大量 JSON 解析
            if not meta or '"task_complete"' in line or '"role":"user"' in line or '"role": "user"' in line:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
            else:
                if line.startswith('{"timestamp":"'):
                    last_ts = line[14:38]
                continue
            last_ts = rec.get("timestamp") or last_ts
            typ, p = rec.get("type"), rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
            if typ == "session_meta" and not meta:
                meta = p
            elif typ == "response_item" and p.get("type") == "message" and p.get("role") == "user":
                kinds = (p.get("internal_chat_message_metadata_passthrough") or {}).get("content_item_kinds") or []
                if any(k.startswith(("agents_md", "environments")) for k in kinds):
                    continue
                for text in _texts(p.get("content")):
                    if _is_human(text):
                        n_user += 1
                        _keep(prompts, text)
            elif typ == "event_msg" and p.get("type") == "task_complete":
                turns += 1
                work_ms += p.get("duration_ms") or 0
                if p.get("last_agent_message"):
                    last_msg = p["last_agent_message"]
    if not meta:
        return None
    src = meta.get("source")
    kind = meta.get("thread_source") or ("subagent" if isinstance(src, dict) and "subagent" in src else "user")
    return {"source": "codex", "id": meta.get("id") or path.stem, "kind": kind,
            "cwd": util.tilde(meta.get("cwd") or ""), "started": meta.get("timestamp"), "ended": last_ts,
            "turns": turns, "user_msgs": n_user, "work_min": round(work_ms / 60000),
            "prompts": prompts, "last_message": util.trim(last_msg, 700),
            "app": meta.get("originator"), "parent": meta.get("parent_thread_id")}


def parse_claude_code(path: Path) -> dict | None:
    cwd, sid, first_ts, last_ts, prompts, last_msg, summary, n_user, turns = "", path.stem, None, None, [], "", "", 0, 0
    with _open_lines(path) as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            typ = rec.get("type")
            if typ == "summary":
                summary = rec.get("summary") or summary
                continue
            if rec.get("isSidechain") or typ not in ("user", "assistant"):
                continue
            ts = rec.get("timestamp")
            first_ts, last_ts = first_ts or ts, ts or last_ts
            cwd, sid = cwd or rec.get("cwd") or "", rec.get("sessionId") or sid
            msg = rec.get("message") or {}
            if typ == "user" and not rec.get("isMeta"):
                for text in _texts(msg.get("content")):
                    if _is_human(text):
                        n_user += 1
                        _keep(prompts, text)
            elif typ == "assistant":
                texts = _texts(msg.get("content"))
                if texts:
                    last_msg, turns = texts[-1], turns + 1
    if not first_ts:
        return None
    return {"source": "claude-code", "id": sid, "kind": "user", "cwd": util.tilde(cwd), "started": first_ts,
            "ended": last_ts, "turns": turns, "user_msgs": n_user, "prompts": prompts,
            "last_message": util.trim(last_msg, 700), "title": util.trim(summary, 120), "app": "Claude Code"}


SOURCES = {
    "codex": ("codex_sessions", ("rollout-*.jsonl", "rollout-*.jsonl.zst"), parse_codex),
    "claude-code": ("claude_code_projects", ("*.jsonl",), parse_claude_code),
}


def collect(source: str) -> dict:
    cfg_key, patterns, parser = SOURCES[source]
    root = Path(util.expand(util.cfg()["sources"].get(cfg_key, "")))
    index_path = util.ROOT / "inbox" / source / "index.json"
    index = util.read_json(index_path, None) or {"files": {}, "sessions": {}}
    stats = {"source": source, "root": util.tilde(str(root)), "seen": 0, "parsed": 0, "errors": 0, "error_sample": ""}
    if not root.is_dir():
        stats["missing"] = True
        return stats
    try:
        files = sorted({p for pat in patterns for p in root.rglob(pat)})
    except PermissionError as e:
        stats["errors"], stats["error_sample"] = 1, str(e)
        return stats
    for path in files:
        stats["seen"] += 1
        try:
            st = path.stat()
        except OSError:
            continue
        rel, sig = str(path.relative_to(root)), [int(st.st_mtime), st.st_size]
        if index["files"].get(rel, {}).get("sig") == sig:
            continue
        try:
            digest = parser(path)
        except Exception as e:  # 单个文件坏了不影响其他
            stats["errors"] += 1
            stats["error_sample"] = stats["error_sample"] or f"{rel}: {e}"
            continue
        index["files"][rel] = {"sig": sig, "id": digest["id"] if digest else None}
        if digest:
            digest["file"] = rel
            index["sessions"][digest["id"]] = digest
            stats["parsed"] += 1
    index["collected_at"] = util.iso(util.now())
    stats["sessions"] = len(index["sessions"])
    util.write_json(index_path, index)
    return stats


def load_sessions(include_internal: bool = False) -> list[dict]:
    out = []
    for source in SOURCES:
        index = util.read_json(util.ROOT / "inbox" / source / "index.json", None) or {}
        for s in (index.get("sessions") or {}).values():
            if include_internal or s.get("kind") == "user":
                out.append(s)
    out.sort(key=lambda s: s.get("ended") or s.get("started") or "")
    return out


def match_project(cwd: str, state: dict, key: str | None = None) -> str | None:
    """按工作目录归到项目：手动指定优先，其次取路径前缀最长的项目。"""
    if key and key in state.get("session_map", {}):
        return state["session_map"][key]
    if cwd in state.get("session_map", {}):
        return state["session_map"][cwd]
    best, best_len = None, -1
    for pid, p in state["projects"].items():
        for base in p.get("paths", []):
            base = base.rstrip("/")
            if (cwd == base or cwd.startswith(base + "/")) and len(base) > best_len:
                best, best_len = pid, len(base)
    return best


def git_activity(state: dict, days: int = 14) -> dict:
    """对登记了路径且是 git 仓库的项目，取最近的提交。"""
    out = {}
    if not shutil.which("git"):
        return out
    for pid, p in state["projects"].items():
        for base in p.get("paths", []):
            real = util.expand(base)
            if not os.path.isdir(os.path.join(real, ".git")):
                continue
            try:
                r = subprocess.run(["git", "-C", real, "log", f"--since={days} days ago", "--pretty=%cI\t%s", "-n", "50"],
                                   capture_output=True, text=True, timeout=20)
            except Exception:
                continue
            commits = [dict(zip(("ts", "subject"), ln.split("\t", 1))) for ln in r.stdout.splitlines() if "\t" in ln]
            if commits:
                out.setdefault(pid, []).extend({**c, "repo": base} for c in commits)
    util.write_json(util.ROOT / "inbox" / "git" / "index.json", {"collected_at": util.iso(util.now()), "days": days,
                                                                "projects": out})
    return out
