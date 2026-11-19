"""把事件状态、会话摘要、仓库登记表拼成给人和给 agent 看的视图。"""
from __future__ import annotations

import datetime as dt

from . import screener, store, util
from .collectors import sessions as S


def _activity(state: dict) -> tuple[dict, list]:
    """会话按项目归类。返回 ({项目: [会话...]}, [未归类会话...])。"""
    by_project, unmapped = {}, []
    for s in S.load_sessions():
        pid = S.match_project(s.get("cwd") or "", state, key=f"{s['source']}:{s['id']}")
        s = dict(s, project=pid)
        (by_project.setdefault(pid, []) if pid else unmapped).append(s)
    return by_project, unmapped


def snapshot() -> dict:
    """看板用的完整快照。"""
    state = store.fold()
    by_project, unmapped = _activity(state)
    git = (util.read_json(util.ROOT / "inbox" / "git" / "index.json", None) or {}).get("projects", {})
    today = util.today()
    stale_days = int(util.cfg().get("stale_days", 14))
    projects = []
    for pid, p in state["projects"].items():
        sess = by_project.get(pid, [])
        last = p["last_activity"]
        for s in sess:
            last = max(last, s.get("ended") or s.get("started") or "")
        for c in git.get(pid, []):
            last = max(last, util.iso(util.parse_ts(c["ts"])) if util.parse_ts(c["ts"]) else "")
        idle = (today - util.local_date(last)).days if util.local_date(last) else None
        open_todos = [t for t in state["todos"].values() if t["project"] == pid and t["status"] == "open"]
        threads = []
        for x in [g.get("thread") for g in p["progress"]] + [t.get("thread") for t in open_todos]:
            if x and x not in threads:
                threads.append(x)
        projects.append({**{k: p.get(k, "") for k in ("id", "name", "category", "status", "summary", "paths", "aliases",
                                                      "goal", "state", "next")}, "threads": threads,
                         "progress": p["progress"][-14:], "progress_count": len(p["progress"]),
                         "last_activity": last, "idle_days": idle,
                         "stale": p["status"] == "active" and idle is not None and idle >= stale_days,
                         "open_todos": len(open_todos), "sessions": sess[-6:], "session_count": len(sess),
                         "commits": git.get(pid, [])[:5]})
    todos = sorted(state["todos"].values(), key=lambda t: (t["status"] != "open", t.get("due") or "9999",
                                                           t.get("priority", 2), t["created"]))
    for t in todos:
        t["overdue"] = bool(t["status"] == "open" and t.get("due") and t["due"] < str(today))
    return {"now": util.iso(util.now()), "today": str(today), "timezone": util.cfg()["timezone"],
            "lines": store.lines(),
            "week": util.week_of(today), "projects": projects, "todos": todos,
            "tasks": sorted(state["tasks"].values(), key=lambda k: k["created"], reverse=True),
            "unmapped_sessions": unmapped[-30:], "repos": screener.view(state),
            "repos_scanned_at": (util.read_json(screener.REGISTRY, None) or {}).get("scanned_at"),
            "reviews": sorted(state["reviews"].values(), key=lambda r: r["week"], reverse=True),
            "meta": state["meta"], "dispatch_mode": util.cfg()["dispatch"].get("mode"),
            "agents": {k: {"kind": v.get("kind"), "enabled": v.get("enabled", True), "label": v.get("label", k),
                           "maintenance": bool(v.get("maintenance"))}
                       for k, v in util.agents_cfg().items()},
            "reminders_last": util.read_json(util.ROOT / "data" / "reminders_last.json", None),
            "event_count": state["event_count"]}


def timeline(days: int = 14) -> list[dict]:
    """最近 N 天的事件 + 会话，按时间倒序，给时间线页用。"""
    since = util.now() - dt.timedelta(days=days)
    state = store.fold()
    rows = []
    label = {"project.progress": "进展", "todo.add": "新增 todo", "todo.done": "完成 todo", "todo.drop": "放弃 todo",
             "task.create": "派单", "task.status": "任务状态", "review.published": "周复盘"}
    for ev in store.load_events():
        ts = util.parse_ts(ev["ts"])
        if not ts or ts < since or ev["type"] not in label:
            continue
        d = ev["data"]
        pid = d.get("project")
        text = d.get("text") or d.get("title") or ""
        if ev["type"].startswith("todo.") and ev["type"] != "todo.add":
            t = state["todos"].get(d["id"], {})
            pid, text = t.get("project"), t.get("title", d["id"])
        if ev["type"] == "task.status":
            k = state["tasks"].get(d["id"], {})
            if d["status"] not in ("done", "failed", "blocked"):
                continue
            pid, text = k.get("project"), f"{k.get('agent')}：{k.get('title', d['id'])} → {d['status']}"
        if ev["type"] == "task.create":
            text = f"{d['agent']}：{d['title']}"
        if ev["type"] == "review.published":
            text = d["week"]
        rows.append({"ts": ev["ts"], "kind": label[ev["type"]], "type": ev["type"], "project": pid, "text": text,
                     "actor": ev["actor"], "source": d.get("source", "")})
    for s in S.load_sessions():
        ts = util.parse_ts(s.get("ended") or s.get("started"))
        if ts and ts >= since:
            rows.append({"ts": util.iso(ts), "kind": "会话", "type": "session",
                         "project": S.match_project(s.get("cwd") or "", state, key=f"{s['source']}:{s['id']}"),
                         "text": (s.get("title") or (s["prompts"][0] if s.get("prompts") else "")),
                         "actor": s["source"], "source": s.get("cwd", "")})
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows


def digest(days: float = 1.5) -> dict:
    """给编排者（Claude）的增量简报：这段时间本机各 agent 干了什么、哪些没归类、todo 和任务有什么变化。"""
    since = util.now() - dt.timedelta(days=days)
    state = store.fold()
    by_project, unmapped = _activity(state)

    def recent(sess):
        out = []
        for s in sess:
            ts = util.parse_ts(s.get("ended") or s.get("started"))
            if ts and ts >= since:
                out.append({k: s.get(k) for k in ("source", "id", "cwd", "started", "ended", "turns", "user_msgs",
                                                  "prompts", "last_message", "title")})
        return out

    sessions = {}
    for pid, s in by_project.items():
        r = recent(s)
        if r:
            sessions[pid] = r
    changed_todos = [t for t in state["todos"].values() if util.parse_ts(t["updated"]) >= since]
    finished = [k for k in state["tasks"].values() if k.get("finished") and util.parse_ts(k["finished"]) >= since]
    git = (util.read_json(util.ROOT / "inbox" / "git" / "index.json", None) or {}).get("projects", {})
    return {"since": util.iso(since), "now": util.iso(util.now()), "today": str(util.today()),
            "last_claude_sync": state["meta"].get("last_claude_sync"),
            "lines": store.lines(),
            "projects": [{"id": p["id"], "name": p["name"], "category": p["category"], "status": p["status"],
                          "goal": p.get("goal", ""), "state": p.get("state", ""), "next": p.get("next", ""),
                          "threads": sorted({g.get("thread") for g in p["progress"] if g.get("thread")}),
                          "paths": p["paths"], "last_progress": p["progress"][-1] if p["progress"] else None}
                         for p in state["projects"].values()],
            "sessions_by_project": sessions, "unmapped_sessions": recent(unmapped),
            "commits": {pid: [c for c in cs if util.parse_ts(c["ts"]) and util.parse_ts(c["ts"]) >= since]
                        for pid, cs in git.items()},
            "open_todos": [t for t in state["todos"].values() if t["status"] == "open"],
            "todos_changed": changed_todos, "tasks_finished": finished,
            "tasks_for_claude": [k for k in state["tasks"].values() if k["agent"] == "claude" and k["status"] == "queued"],
            "swarm_paused": bool(state["meta"].get("swarm_paused"))}
