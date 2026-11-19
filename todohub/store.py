"""事件存储。

所有写入都是「往 data/events/ 里新增一个小 JSON 文件」，从不修改旧文件。
当前状态 = 把全部事件按时间顺序重放（fold）一遍。这样做的原因：
  1. 多个 agent（Claude、Codex、Grok Bot、本机守护进程）可以同时写，互不覆盖；
  2. 每条进展、每次打勾都有时间戳和署名，周复盘直接从事件里取数。
"""
from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

from . import util

EVENT_DIR = util.ROOT / "data" / "events"

CATEGORIES = ["research", "invest", "bd", "coding", "personal"]
PROJECT_STATUS = ["active", "backlog", "paused", "done", "archived"]
TODO_STATUS = ["open", "done", "dropped"]
TASK_STATUS = ["queued", "running", "done", "failed", "blocked", "cancelled"]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")

_cache: dict[str, dict] = {}
_last_us = 0  # 进程内单调递增，保证同一批事件的先后顺序稳定


class HubError(ValueError):
    pass


def default_actor() -> str:
    return os.environ.get("HUB_ACTOR") or util.cfg().get("actor") or "me"


def load_events() -> list[dict]:
    """读取全部事件，按文件名（= 时间）排序。已读过的文件走内存缓存。"""
    out = []
    if not EVENT_DIR.exists():
        return out
    for month in sorted(p for p in EVENT_DIR.iterdir() if p.is_dir()):
        for f in sorted(month.glob("*.json")):
            key = f.name
            ev = _cache.get(key)
            if ev is None:
                ev = util.read_json(f)
                if not isinstance(ev, dict) or "type" not in ev:
                    continue
                ev.setdefault("id", f.stem)
                _cache[key] = ev
            out.append(ev)
    out.sort(key=lambda e: (e.get("ts", ""), e.get("id", "")))
    return out


def _new_project(pid: str, ts: str) -> dict:
    return {"id": pid, "name": pid, "category": "research", "status": "active", "summary": "", "aliases": [],
            "paths": [], "goal": "", "state": "", "next": "", "created": ts, "updated": ts, "progress": [],
            "last_activity": ""}


def lines() -> list[dict]:
    """一级分类（主线）。来自 config/taxonomy.local.json（你自己的）或 config/taxonomy.json（示例）。"""
    t = util.read_json(util.local_or("taxonomy.json"), None) or {}
    return t.get("lines") or [{"id": c, "name": c, "color": c} for c in CATEGORIES]


def fold(events: list[dict] | None = None) -> dict:
    events = load_events() if events is None else events
    st = {"projects": {}, "todos": {}, "tasks": {}, "repo_notes": {}, "reviews": {}, "session_map": {}, "meta": {},
          "redirect": {}, "event_count": len(events), "last_event": events[-1]["ts"] if events else None}
    P, T, K = st["projects"], st["todos"], st["tasks"]

    # 项目合并关系先扫一遍：旧项目 id → 并入的项目 id。之后所有指向旧 id 的事件都改投到新项目，
    # 并自动带上「子线」标签（默认用旧项目的名字），这样合并不丢历史，也不用改旧事件。
    redirect, merged_at, label = {}, {}, {}
    for ev in events:
        if ev["type"] == "project.merge":
            for old in ev["data"]["from"]:
                redirect[old], merged_at[old] = ev["data"]["into"], ev.get("ts", "")
                if (ev["data"].get("threads") or {}).get(old):
                    label[old] = ev["data"]["threads"][old]

    def res(pid):
        seen = set()
        while pid in redirect and pid not in seen:
            seen.add(pid)
            pid = redirect[pid]
        return pid

    def touch(pid, ts):
        if pid in P and ts > P[pid]["last_activity"]:
            P[pid]["last_activity"] = ts

    # 分轮重放：先建项目，再做合并，再建 todo / 任务，最后才是进展和状态变化。
    # 这样「回填」一条日期早于项目创建时间的进展也不会丢。
    phase = {"project.upsert": 0, "project.merge": 1, "todo.add": 2, "task.create": 2}
    for ev in sorted(events, key=lambda e: phase.get(e["type"], 3)):
        typ, d, ts, actor = ev["type"], ev.get("data", {}), ev.get("ts", ""), ev.get("actor", "?")
        if typ == "project.upsert":
            pid = d["id"]
            if pid in redirect and ts > merged_at[pid]:
                pid = res(pid)
            p = P.setdefault(pid, _new_project(pid, ts))
            for k in ("name", "category", "status", "summary", "aliases", "paths", "goal", "state", "next"):
                if k in d:
                    p[k] = d[k]
            for extra in d.get("add_paths", []):
                if extra not in p["paths"]:
                    p["paths"].append(extra)
            p["updated"] = max(p["updated"], ts)
        elif typ == "project.merge":
            into = res(d["into"])
            target = P.get(into)
            for old in d["from"]:
                src = P.pop(old, None) if old != into else None
                if not src or not target:
                    continue
                label.setdefault(old, src["name"])
                for x in src["paths"]:
                    if x not in target["paths"]:
                        target["paths"].append(x)
                if src["name"] not in target["aliases"]:
                    target["aliases"].append(src["name"])
        elif typ == "project.progress":
            pid = res(d["project"])
            p = P.get(pid)
            if p:
                p["progress"].append({"ts": ts, "date": d.get("date") or str(util.local_date(ts)), "text": d["text"],
                                      "source": d.get("source", ""), "links": d.get("links", []), "actor": actor,
                                      "thread": d.get("thread") or label.get(d["project"], ""), "event": ev["id"]})
                touch(pid, ts)
        elif typ == "todo.add":
            T[d["id"]] = {"id": d["id"], "project": res(d["project"]) if d.get("project") else None,
                          "thread": d.get("thread") or label.get(d.get("project"), ""), "title": d["title"],
                          "status": "open", "due": d.get("due"), "priority": d.get("priority", 2),
                          "notes": d.get("notes", ""), "source": d.get("source", ""), "rid": d.get("rid"),
                          "rlist": d.get("rlist"), "created": ts, "updated": ts, "done_at": None, "actor": actor}
        elif typ in ("todo.update", "todo.done", "todo.reopen", "todo.drop"):
            t = T.get(d["id"])
            if not t:
                continue
            if typ == "todo.update":
                for k in ("title", "due", "priority", "notes", "thread"):
                    if k in d:
                        t[k] = d[k]
                if "project" in d:
                    t["project"] = res(d["project"]) if d["project"] else None
            elif typ == "todo.done":
                t["status"], t["done_at"], t["done_by"] = "done", ts, actor
            elif typ == "todo.reopen":
                t["status"], t["done_at"] = "open", None
            else:
                t["status"] = "dropped"
            t["updated"] = ts
            if typ == "todo.done":  # 只有真正做完才算项目有活动；加 todo、改日期不算
                touch(t.get("project"), ts)
        elif typ == "task.create":
            K[d["id"]] = {"id": d["id"], "project": res(d["project"]) if d.get("project") else None,
                          "agent": d["agent"], "title": d["title"],
                          "prompt": d["prompt"], "cwd": d.get("cwd"), "acceptance": d.get("acceptance", ""),
                          "todo": d.get("todo"), "status": "queued", "created": ts, "updated": ts,
                          "started": None, "finished": None, "summary": "", "run_dir": None, "by": actor}
        elif typ == "task.status":
            k = K.get(d["id"])
            if not k:
                continue
            k["status"], k["updated"] = d["status"], ts
            if d["status"] == "running":
                k["started"], k["runner"] = ts, actor
            if d["status"] in ("done", "failed", "cancelled", "blocked"):
                k["finished"] = ts
            for f in ("summary", "run_dir", "exit_code"):
                if f in d:
                    k[f] = d[f]
            if d["status"] == "done":
                touch(k.get("project"), ts)
        elif typ == "repo.note":
            n = st["repo_notes"].setdefault(d["key"], {"key": d["key"], "tags": [], "created": ts})
            for f in ("status", "note", "url", "name", "audit"):
                if f in d:
                    n[f] = d[f]
            for tag in d.get("tags", []):
                if tag not in n["tags"]:
                    n["tags"].append(tag)
            n["updated"] = ts
        elif typ == "review.published":
            st["reviews"][d["week"]] = {"week": d["week"], "path": d.get("path"), "ts": ts}
        elif typ == "session.assign":
            st["session_map"][d["key"]] = res(d["project"])
        elif typ == "meta.set":
            st["meta"][d["key"]] = d["value"]
    for p in P.values():
        p["progress"].sort(key=lambda g: g["ts"])
    st["redirect"] = {k: res(k) for k in redirect}
    return st


REQUIRED = {
    "project.upsert": ["id"], "project.progress": ["project", "text"], "project.merge": ["into", "from"],
    "todo.add": ["title"], "todo.update": ["id"], "todo.done": ["id"], "todo.reopen": ["id"], "todo.drop": ["id"],
    "task.create": ["agent", "title", "prompt"], "task.status": ["id", "status"],
    "repo.note": ["key"], "review.published": ["week"], "session.assign": ["key", "project"],
    "meta.set": ["key", "value"],
}


def validate(typ: str, d: dict, st: dict) -> dict:
    """检查一条待写入事件；必要时补全 id。出错抛 HubError（消息是给人看的）。"""
    if typ not in REQUIRED:
        raise HubError(f"未知事件类型 {typ}；可用：{', '.join(sorted(REQUIRED))}")
    if not isinstance(d, dict):
        raise HubError("data 必须是对象")
    d = dict(d)
    if typ == "todo.add":
        d.setdefault("id", util.short_id("T"))
    if typ == "task.create":
        d.setdefault("id", util.short_id("K"))
    for f in REQUIRED[typ]:
        if d.get(f) in (None, ""):
            raise HubError(f"{typ} 缺少字段 {f}")

    def need_project(pid, allow_none=True):
        if pid is None and allow_none:
            return
        if pid not in st["projects"] and pid not in st.get("redirect", {}):
            raise HubError(f"项目 {pid} 不存在；先用 project.upsert 建项目。现有：{', '.join(sorted(st['projects'])) or '（空）'}")

    if typ == "project.upsert":
        if d["id"] not in st["projects"] and d["id"] not in st.get("redirect", {}) and not SLUG_RE.match(d["id"]):
            raise HubError("项目 id 只能用小写字母、数字、连字符，例如 my-research")
        ids = [l["id"] for l in lines()]
        if "category" in d and d["category"] not in ids:
            raise HubError(f"category（主线）只能是 {ids}，定义在 config/taxonomy(.local).json")
        if "status" in d and d["status"] not in PROJECT_STATUS:
            raise HubError(f"status 只能是 {PROJECT_STATUS}")
        for f in ("aliases", "paths", "add_paths"):
            if f in d and not isinstance(d[f], list):
                raise HubError(f"{f} 必须是数组")
    elif typ == "project.merge":
        need_project(d["into"], allow_none=False)
        if not isinstance(d["from"], list) or not d["from"] or d["into"] in d["from"]:
            raise HubError("from 必须是非空数组，且不能包含 into 自己")
        for old in d["from"]:
            need_project(old, allow_none=False)
    elif typ == "project.progress":
        need_project(d["project"], allow_none=False)
        if d.get("date") and not DATE_RE.match(d["date"]):
            raise HubError("date 格式应为 YYYY-MM-DD")
    elif typ in ("todo.add", "todo.update"):
        if typ == "todo.add" and d["id"] in st["todos"]:
            raise HubError(f"todo {d['id']} 已存在")
        if typ == "todo.update" and d["id"] not in st["todos"]:
            raise HubError(f"todo {d['id']} 不存在")
        if "project" in d:
            need_project(d["project"])
        if d.get("due") and not DATE_RE.match(str(d["due"])):
            raise HubError("due 格式应为 YYYY-MM-DD")
        if "priority" in d and d["priority"] not in (1, 2, 3):
            raise HubError("priority 只能是 1（高）/2/3（低）")
    elif typ in ("todo.done", "todo.reopen", "todo.drop"):
        if d["id"] not in st["todos"]:
            raise HubError(f"todo {d['id']} 不存在")
    elif typ == "task.create":
        if d["id"] in st["tasks"]:
            raise HubError(f"task {d['id']} 已存在")
        need_project(d.get("project"))
        agents = util.agents_cfg()
        if agents and d["agent"] not in agents:
            raise HubError(f"agent {d['agent']} 没有在 config/agents.json 里登记；可用：{', '.join(agents)}")
        if d.get("todo") and d["todo"] not in st["todos"]:
            raise HubError(f"关联的 todo {d['todo']} 不存在")
    elif typ == "task.status":
        if d["id"] not in st["tasks"]:
            raise HubError(f"task {d['id']} 不存在")
        if d["status"] not in TASK_STATUS:
            raise HubError(f"status 只能是 {TASK_STATUS}")
    elif typ == "session.assign":
        need_project(d["project"], allow_none=False)
    return d


def append(typ: str, data: dict, actor: str | None = None, ts: str | None = None, state: dict | None = None) -> dict:
    """校验并写入一条事件，返回写入的事件。"""
    st = state if state is not None else fold()
    data = validate(typ, data, st)
    actor = re.sub(r"[^a-zA-Z0-9_-]", "", actor or default_actor()) or "unknown"
    when = util.parse_ts(ts) if ts else util.now()
    if when is None:
        raise HubError(f"无法解析时间 {ts}")
    stamp = when.astimezone(util.dt.timezone.utc)
    global _last_us
    _last_us = us = min(999999, max(util.now().microsecond, _last_us + 1))
    name = f"{stamp.strftime('%Y%m%dT%H%M%S')}{us:06d}Z-{actor}-{secrets.token_hex(2)}"
    ev = {"id": name, "ts": util.iso(stamp), "actor": actor, "type": typ, "data": data}
    folder = EVENT_DIR / stamp.strftime("%Y-%m")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name + ".json")
    with open(path, "x", encoding="utf-8") as f:  # "x"：只允许新建，绝不覆盖
        util.json.dump(ev, f, ensure_ascii=False, indent=1)
    _cache[path.name] = ev
    return ev


def apply_batch(items: list[dict], actor: str | None = None) -> list[dict]:
    """按顺序写入一批事件；先整体校验（前面的事件对后面的可见），全部通过才落盘。"""
    events = load_events()
    st = fold(events)
    staged = []
    for i, it in enumerate(items):
        try:
            data = validate(it.get("type"), it.get("data", {}), st)
        except HubError as e:
            raise HubError(f"第 {i + 1} 条（{it.get('type')}）：{e}")
        fake = {"id": f"staged-{i}", "ts": it.get("ts") and util.iso(util.parse_ts(it["ts"])) or util.iso(util.now()),
                "actor": actor or default_actor(), "type": it["type"], "data": data}
        events.append(fake)
        events.sort(key=lambda e: (e.get("ts", ""), e.get("id", "")))
        st = fold(events)
        staged.append((it["type"], data, it.get("ts")))
    return [append(t, d, actor=actor, ts=ts, state=fold()) for t, d, ts in staged]
