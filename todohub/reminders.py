"""Todo Hub ↔ macOS「提醒事项」同步。只在 macOS 本机运行。

规则（Hub 是唯一数据源，提醒事项是手机/桌面上的镜像）：
  Hub → 提醒事项：未完成的 todo 建成提醒（标题「项目｜任务」，有截止日就带上）；Hub 里完成/放弃的，提醒打勾。
  提醒事项 → Hub：你在提醒事项里打勾 → Hub 标完成；你在这个列表里手写的新提醒 → 导入成 Hub 的新 todo。
  标题和日期以 Hub 为准，在提醒事项里改这两项会被下次同步覆盖。

每条提醒的备注末尾带一个 ⟦hub:T-XXXXX⟧ 标记，靠它对应，不依赖提醒事项内部 id。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from . import store, util

MARK = re.compile(r"⟦hub:(T-[A-Z0-9]+)⟧")
SCRIPT = Path(__file__).with_name("reminders.jxa.js")


def _osascript(req: dict) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(req, f, ensure_ascii=False)
        req_path = f.name
    r = subprocess.run(["osascript", "-l", "JavaScript", str(SCRIPT), req_path], capture_output=True, text=True,
                       timeout=180)
    Path(req_path).unlink(missing_ok=True)
    if r.returncode != 0:
        hint = ""
        if "-1743" in r.stderr or "Not authorized" in r.stderr:
            hint = "（没有控制「提醒事项」的权限：系统设置 → 隐私与安全性 → 自动化，给运行本程序的终端/Python 勾上「提醒事项」）"
        raise RuntimeError(f"osascript 失败：{r.stderr.strip()[:300]}{hint}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def title_of(todo: dict, state: dict) -> str:
    p = state["projects"].get(todo.get("project") or "")
    return f"{p['name']}｜{todo['title']}" if p else todo["title"]


def body_of(todo: dict) -> str:
    notes = (todo.get("notes") or "").strip()
    return (notes + "\n" if notes else "") + f"⟦hub:{todo['id']}⟧"


def plan(state: dict, items: list[dict]) -> dict:
    """纯函数：根据 Hub 状态和提醒事项现状，算出要做的事。便于测试。

    两类列表：
      镜像列表（reminders.list，默认「Todo Hub」）：Hub 的 todo 在这里各有一条提醒，靠备注里的标记对应。
      接管列表（reminders.adopt_lists）：你原来就在用的列表。里面的提醒原地不动，Hub 的 todo 记下它的 id；
        只同步「完成」这一个状态，不改标题和备注；你在里面新写的提醒会自动导入 Hub。
    """
    rc = util.cfg()["reminders"]
    mirror, adopt = rc["list"], set(rc.get("adopt_lists") or [])
    create, update, events = [], [], []
    by_todo: dict[str, dict] = {}
    by_rid = {it["id"]: it for it in items}
    known_rids = {t["rid"] for t in state["todos"].values() if t.get("rid")}
    names = {p["name"]: pid for pid, p in state["projects"].items()}
    for it in items:
        where = it.get("list") or mirror
        if where in adopt:
            if it["id"] not in known_rids and not it["completed"]:
                data = {"id": util.short_id("T"), "title": it["name"].strip(), "source": "提醒事项/" + where,
                        "rid": it["id"], "rlist": where}
                if (it.get("body") or "").strip():
                    data["notes"] = it["body"].strip()
                if it.get("due"):
                    data["due"] = str(util.local_date(it["due"]))
                events.append(("todo.add", data))
            continue
        m = MARK.search(it.get("body") or "")
        if m:
            by_todo.setdefault(m.group(1), it)
        elif not it["completed"] and rc.get("import_untracked", True):
            # 用户在镜像列表里手写的新提醒：导入 Hub，并回写标记
            title, project = it["name"].strip(), None
            if "｜" in title and title.split("｜", 1)[0] in names:
                head, title = title.split("｜", 1)
                project = names[head]
            tid = util.short_id("T")
            data = {"id": tid, "title": title, "source": "提醒事项"}
            if project:
                data["project"] = project
            if it.get("due"):
                data["due"] = str(util.local_date(it["due"]))
            events.append(("todo.add", data))
            body = (it.get("body") or "").strip()
            update.append({"id": it["id"], "body": (body + "\n" if body else "") + f"⟦hub:{tid}⟧"})
    for tid, t in state["todos"].items():
        if t.get("rid"):  # 接管的提醒：原地对应，只同步完成状态
            it = by_rid.get(t["rid"])
            if it is None:
                continue
            if t["status"] == "open" and it["completed"]:
                events.append(("todo.done", {"id": tid}))
            elif t["status"] != "open" and not it["completed"]:
                update.append({"id": it["id"], "list": t.get("rlist"), "completed": True})
            continue
        it = by_todo.get(tid)
        if it is None:
            if t["status"] == "open":
                create.append({"name": title_of(t, state), "body": body_of(t), "due": t.get("due")})
            continue
        if t["status"] == "open" and it["completed"]:
            events.append(("todo.done", {"id": tid}))
            continue
        u = {}
        if t["status"] != "open" and not it["completed"]:
            u["completed"] = True
        if t["status"] == "open":
            if it["name"] != title_of(t, state):
                u["name"] = title_of(t, state)
            if t.get("due") and str(util.local_date(it.get("due")) or "") != t["due"]:
                u["due"] = t["due"]
            if (it.get("body") or "") != body_of(t):
                u["body"] = body_of(t)
        if u:
            update.append({"id": it["id"], **u})
    return {"create": create, "update": update, "events": events}


def sync(dry_run: bool = False) -> dict:
    if sys.platform != "darwin":
        return {"skipped": "提醒事项同步只能在 macOS 本机运行"}
    rc = util.cfg()["reminders"]
    if not rc.get("enabled", True):
        return {"skipped": "config/hub.json 里 reminders.enabled = false"}
    current = _osascript({"op": "read", "list": rc["list"], "also": rc.get("adopt_lists") or []})
    state = store.fold()
    todo = plan(state, current["items"])
    result = {"list": rc["list"], "adopt": rc.get("adopt_lists") or [], "create": len(todo["create"]),
              "update": len(todo["update"]),
              "to_hub": len(todo["events"]), "errors": []}
    if dry_run:
        result["dry_run"] = todo
        return result
    for typ, data in todo["events"]:
        store.append(typ, data, actor="reminders")
    if todo["create"] or todo["update"]:
        applied = _osascript({"op": "apply", "list": rc["list"], "also": rc.get("adopt_lists") or [],
                              "create": todo["create"], "update": todo["update"]})
        result["errors"] = applied.get("errors", [])
    util.write_json(util.ROOT / "data" / "reminders_last.json", {"at": util.iso(util.now()), **result})
    return result


def dump(days_done: int = 45) -> dict:
    """只读导出全部提醒事项列表到 inbox/reminders/dump.json，供编排者归类导入。"""
    if sys.platform != "darwin":
        return {"skipped": "只能在 macOS 本机运行"}
    since = util.iso(util.now() - util.dt.timedelta(days=days_done))
    data = _osascript({"op": "dump", "list": util.cfg()["reminders"]["list"], "since": since})
    data["dumped_at"], data["done_since"] = util.iso(util.now()), since
    util.write_json(util.ROOT / "inbox" / "reminders" / "dump.json", data)
    return {"lists": [{"name": l["name"], "open": sum(not i["completed"] for i in l["items"]),
                       "done_recent": sum(i["completed"] for i in l["items"]), "done_older": l.get("skipped_done", 0),
                       "error": l.get("error")} for l in data.get("lists", [])]}
