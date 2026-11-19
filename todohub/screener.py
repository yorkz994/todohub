"""本机 AI 开源项目 screener：记住这台机器上装过/clone 过哪些仓库和工具。

覆盖六种「装」的方式：
  git   —— git clone 下来的仓库（记远端地址、当前 commit、分支）
  zip   —— 从 GitHub 下 zip 解压的目录（xxx-main / xxx-master，没有 .git，无法知道版本）
  tool  —— npm -g / pipx / uv tool / brew / ollama 模型 / docker 镜像
  dotdir—— 家目录下各 agent 工具留下的配置目录（~/.codex 等）
  app   —— /Applications 里的 AI 应用
  planned —— 打算装、还没装的（hub repos plan 登记）

登记表在 data/repos.json。每次扫描只增不删：消失的条目标记 missing，保留首次发现时间和 commit 变更历史。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

from . import util

REGISTRY = util.ROOT / "data" / "repos.json"

AI_WORDS = ["openai", "anthropic", "claude", "codex", "gpt", "llm", "langchain", "langgraph", "llama", "ollama",
            "agent", "mcp", "transformers", "torch", "huggingface", "embedding", "rag", "diffusion", "litellm",
            "autogen", "crewai", "gemini", "deepseek", "mlx", "vllm", "whisper", "copilot", "prompt", "inference",
            "fine-tun", "大模型", "智能体"]
DOTDIRS = {".codex": "OpenAI Codex", ".claude": "Claude Code", ".opencode": "opencode", ".cagent": "Docker cagent",
           ".grokbot": "Grok Bot", ".vibe-kanban": "vibe-kanban", ".claude-mem": "claude-mem",
           ".openbb_platform": "OpenBB Platform", ".workbuddy": "WorkBuddy", ".cursor": "Cursor", ".ollama": "Ollama",
           ".lmstudio": "LM Studio", ".gemini": "Gemini CLI", ".aider": "aider", ".agents": "Agent Skills 目录",
           ".continue": "Continue", ".windsurf": "Windsurf", ".cline": "Cline", ".goose": "goose", ".pi": "Pi"}
APP_WORDS = ["claude", "chatgpt", "codex", "cursor", "grok", "ollama", "lm studio", "doubao", "豆包",
             "workbuddy", "gemini", "perplexity", "windsurf", "kimi", "deepseek", "jan", "msty", "cherry studio",
             "opencode", "trae", "qoder", "comfyui", "diffusionbee"]
MANIFESTS = ["README.md", "README.MD", "readme.md", "README.rst", "README", "README_zh.md", "package.json",
             "pyproject.toml", "requirements.txt", "Cargo.toml", "go.mod"]


def norm_url(url: str) -> tuple[str, str]:
    """返回 (去掉凭证的 https 地址, 规范化 key 如 github.com/owner/repo)。"""
    u = url.strip()
    u = re.sub(r"^(https?://)[^@/]+@", r"\1", u)
    m = re.match(r"^(?:ssh://)?git@([^:/]+)[:/](.+)$", u)
    if m:
        u = f"https://{m.group(1)}/{m.group(2)}"
    u = re.sub(r"\.git/?$", "", u).rstrip("/")
    key = re.sub(r"^https?://(www\.)?", "", u).lower()
    return u, key


def _git_info(repo: Path) -> dict:
    g = repo / ".git"
    info = {"url": "", "commit": "", "branch": "", "head_date": ""}
    try:
        if g.is_file():  # worktree / submodule：.git 是指向真实目录的文件
            m = re.match(r"gitdir:\s*(.+)", g.read_text(errors="replace"))
            if not m:
                return info
            g = (repo / m.group(1).strip()).resolve()
    except OSError:
        return info
    try:
        cfg = (g / "config").read_text(errors="replace")
        m = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', cfg, re.S)
        if m:
            info["url"] = m.group(1)
        head = (g / "HEAD").read_text().strip()
        if head.startswith("ref:"):
            ref = head[4:].strip()
            info["branch"] = ref.replace("refs/heads/", "")
            ref_file = g / ref
            if ref_file.exists():
                info["commit"] = ref_file.read_text().strip()
            elif (g / "packed-refs").exists():
                for ln in (g / "packed-refs").read_text(errors="replace").splitlines():
                    if ln.endswith(" " + ref):
                        info["commit"] = ln.split(" ", 1)[0]
        else:
            info["commit"] = head
        for cand in (g / "logs" / "HEAD", g / "HEAD"):
            if cand.exists():
                info["head_date"] = util.iso(util.parse_ts(cand.stat().st_mtime))
                break
    except OSError:
        pass
    return info


def _ai_score(folder: Path, name: str) -> tuple[int, list[str]]:
    text = name.lower() + "\n"
    for fn in MANIFESTS:
        f = folder / fn
        try:
            if f.is_file():
                text += f.read_text(errors="replace")[:20000].lower() + "\n"
        except OSError:
            pass
    hits = [w for w in AI_WORDS if w in text]
    return len(hits), hits[:8]


def _walk(root: Path, depth: int, exclude: set[str]):
    """广度有限的目录遍历：产出 (目录, 是否 git 仓库)。碰到权限问题就跳过。"""
    stack = [(root, 0)]
    while stack:
        cur, d = stack.pop()
        try:
            names = sorted(os.listdir(cur))
        except OSError:
            continue
        is_git = ".git" in names
        yield cur, is_git, names
        if d >= depth:
            continue
        for n in names:
            if n in exclude or (n.startswith(".") and n != ".config"):
                continue
            child = cur / n
            try:
                ok = child.is_dir() and not child.is_symlink()
            except OSError:  # macOS 上受系统保护的目录
                ok = False
            if ok:
                stack.append((child, d + 1))


def _mtime(p) -> str:
    try:
        return util.iso(util.parse_ts(os.stat(p).st_mtime))
    except OSError:
        return ""


def _run(cmd: list[str], timeout: int = 40) -> str:
    if not shutil.which(cmd[0]):
        return ""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def _tools() -> list[dict]:
    """各包管理器的全局安装。命令不存在就跳过。"""
    out = []

    def add(manager, name, version=""):
        score = sum(1 for w in AI_WORDS if w in name.lower())
        out.append({"key": f"tool:{manager}:{name}".lower(), "kind": "tool", "name": name, "manager": manager,
                    "version": version, "ai_score": score, "ai": manager in ("ollama",) or score > 0})

    try:
        deps = json.loads(_run(["npm", "ls", "-g", "--depth=0", "--json"]) or "{}").get("dependencies", {})
        for n, v in deps.items():
            add("npm", n, (v or {}).get("version", ""))
    except json.JSONDecodeError:
        pass
    for ln in _run(["pipx", "list", "--short"]).splitlines():
        parts = ln.split()
        if parts:
            add("pipx", parts[0], parts[1] if len(parts) > 1 else "")
    for ln in _run(["uv", "tool", "list"]).splitlines():
        if ln and not ln.startswith(("-", " ")):
            parts = ln.split()
            add("uv", parts[0], parts[1] if len(parts) > 1 else "")
    for flag in ("--formula", "--cask"):
        for ln in _run(["brew", "list", flag, "--versions"]).splitlines():
            parts = ln.split()
            if parts:
                add("brew" if flag == "--formula" else "brew-cask", parts[0], parts[-1] if len(parts) > 1 else "")
    for ln in _run(["ollama", "list"]).splitlines()[1:]:
        parts = ln.split()
        if parts:
            add("ollama", parts[0], parts[1] if len(parts) > 1 else "")
    for ln in _run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], timeout=15).splitlines():
        if ln and "<none>" not in ln:
            add("docker", ln)
    return out


def scan(with_tools: bool = True) -> dict:
    cfg = util.cfg()
    exclude = set(cfg["scan_exclude"])
    mine = {o.lower() for o in cfg.get("my_github_owners", [])}
    found: dict[str, dict] = {}
    scope = []
    for root_cfg in cfg["scan_roots"]:
        root = Path(util.expand(root_cfg["path"]))
        if not root.is_dir():
            continue
        scope.append(util.tilde(str(root)))
        for folder, is_git, names in _walk(root, int(root_cfg.get("depth", 3)), exclude):
            name = folder.name
            shown = util.tilde(str(folder))
            if is_git:
                gi = _git_info(folder)
                url, key = norm_url(gi["url"]) if gi["url"] else ("", "local:" + shown.lower())
                owner = key.split("/")[1] if key.count("/") >= 2 else ""
                score, hits = _ai_score(folder, name)
                item = found.setdefault(key, {"key": key, "kind": "git", "name": key.split("/")[-1] or name,
                                              "url": url, "paths": [], "own": owner in mine})
                item["paths"].append(shown)
                item.update(commit=gi["commit"][:12], branch=gi["branch"], head_date=gi["head_date"],
                            ai_score=max(score, item.get("ai_score", 0)), ai_hits=hits, ai=score >= 2)
            elif re.search(r"-(main|master)$", name) and any(n.lower().startswith("readme") for n in names):
                score, hits = _ai_score(folder, name)
                key = "zip:" + re.sub(r"-(main|master)$", "", name).lower()
                found[key] = {"key": key, "kind": "zip", "name": re.sub(r"-(main|master)$", "", name), "url": "",
                              "paths": [shown], "own": False, "commit": "", "branch": "", "ai_score": score,
                              "ai_hits": hits, "ai": score >= 2, "head_date": _mtime(folder)}
    home = Path(os.path.expanduser("~"))
    if with_tools:
        for d, label in DOTDIRS.items():
            p = home / d
            if os.path.lexists(p):
                found["dotdir:" + d] = {"key": "dotdir:" + d, "kind": "dotdir", "name": label, "paths": ["~/" + d],
                                        "ai": True, "ai_score": 1, "head_date": _mtime(p)}
        for apps in (Path("/Applications"), home / "Applications"):
            if apps.is_dir():
                for a in sorted(apps.glob("*.app")):
                    if any(w in a.stem.lower() for w in APP_WORDS + [x.lower() for x in cfg.get("extra_app_words", [])]):
                        found["app:" + a.stem.lower()] = {"key": "app:" + a.stem.lower(), "kind": "app",
                                                          "name": a.stem, "paths": [str(a)], "ai": True, "ai_score": 1,
                                                          "head_date": _mtime(a)}
        for t in _tools():
            found[t["key"]] = t
    return merge(found, scope, with_tools)


def merge(found: dict, scope: list[str], with_tools: bool) -> dict:
    reg = util.read_json(REGISTRY, None) or {"items": {}}
    now = util.iso(util.now())
    items = reg["items"]
    stats = {"new": [], "changed": [], "missing": [], "total": 0}
    for key, it in found.items():
        old = items.get(key)
        if old is None:
            it.update(first_seen=now, last_seen=now, history=[])
            items[key] = it
            stats["new"].append(key)
            continue
        if it.get("commit") and old.get("commit") and it["commit"] != old["commit"]:
            old.setdefault("history", []).append({"ts": now, "from": old["commit"], "to": it["commit"]})
            stats["changed"].append(key)
        if it.get("version") and old.get("version") and it["version"] != old["version"]:
            old.setdefault("history", []).append({"ts": now, "from": old["version"], "to": it["version"]})
            stats["changed"].append(key)
        keep = {k: old[k] for k in ("first_seen", "history", "online") if k in old}
        old.clear()
        old.update(it, **keep, last_seen=now)
        old.pop("missing", None)

    def in_scope(it):
        if it["kind"] in ("git", "zip"):
            return any(p == s or p.startswith(s.rstrip("/") + "/") for p in it.get("paths", []) for s in scope)
        return with_tools

    for key, it in items.items():
        if key not in found and not it.get("missing") and in_scope(it):
            it["missing"] = now
            stats["missing"].append(key)
    reg.update(scanned_at=now, scope=scope, host=os.uname().sysname.lower())
    stats["total"] = len(items)
    util.write_json(REGISTRY, reg)
    return stats


def enrich_online(limit: int = 40) -> int:
    """可选：向 GitHub 公共 API 查星数、是否归档、最近推送。不带 token，每小时 60 次，所以每次只查一部分。"""
    reg = util.read_json(REGISTRY, None) or {"items": {}}
    done = 0
    for key, it in reg["items"].items():
        if done >= limit or not key.startswith("github.com/") or it.get("own"):
            continue
        last = util.parse_ts((it.get("online") or {}).get("checked"))
        if last and (util.now() - last).days < 3:
            continue
        try:
            req = urllib.request.Request("https://api.github.com/repos/" + key.split("/", 1)[1],
                                         headers={"User-Agent": "todohub", "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.load(r)
            it["online"] = {"checked": util.iso(util.now()), "stars": d.get("stargazers_count"),
                            "archived": d.get("archived"), "pushed_at": d.get("pushed_at"),
                            "license": (d.get("license") or {}).get("spdx_id"), "description": d.get("description"),
                            "full_name": d.get("full_name")}
        except Exception as e:
            it["online"] = {"checked": util.iso(util.now()), "error": str(e)[:80]}
        done += 1
    util.write_json(REGISTRY, reg)
    return done


def view(state: dict) -> list[dict]:
    """登记表 + 人工备注（状态、标签、审计结论）合并成给看板用的列表。"""
    reg = util.read_json(REGISTRY, None) or {"items": {}}
    items = {k: dict(v) for k, v in reg["items"].items()}
    for key, note in state.get("repo_notes", {}).items():
        it = items.setdefault(key, {"key": key, "kind": "planned", "name": note.get("name") or key.split("/")[-1],
                                    "url": note.get("url", ""), "paths": [], "ai": True, "first_seen": note["created"]})
        it["note"] = note
        if it["kind"] == "planned" and note.get("url"):
            it["url"] = note["url"]
    for it in items.values():
        planned = (it.get("note") or {}).get("status") == "planned"
        it["state"] = ("planned" if it["kind"] == "planned" else "missing" if it.get("missing") else "installed")
        it["was_planned"] = planned and it["kind"] != "planned"
    return sorted(items.values(), key=lambda i: i.get("first_seen", ""), reverse=True)
