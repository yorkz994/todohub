"""hub 命令行 —— 人和所有 agent 共用的唯一写入口。`hub <命令> -h` 看用法。"""
from __future__ import annotations

import argparse
import json
import sys

from . import store, util


def _out(obj, as_json=False):
    if as_json or not isinstance(obj, str):
        print(json.dumps(obj, ensure_ascii=False, indent=1))
    else:
        print(obj)


def _emit(args, typ, data):
    ev = store.append(typ, {k: v for k, v in data.items() if v is not None}, actor=args.actor, ts=getattr(args, "ts", None))
    print(f"ok {typ} {ev['data'].get('id') or ev['data'].get('project') or ev['data'].get('key') or ''}".rstrip())
    return ev


def cmd_status(a):
    from . import views
    s = views.snapshot()
    open_ = [t for t in s["todos"] if t["status"] == "open"]
    print(f"Todo Hub · {s['today']}（{s['timezone']}）· {s['week']} · 事件 {s['event_count']} 条")
    print(f"项目 {len(s['projects'])}（active {sum(p['status'] == 'active' for p in s['projects'])}，"
          f"停滞 {sum(p['stale'] for p in s['projects'])}）")
    print(f"Todo 未完成 {len(open_)}，逾期 {sum(t['overdue'] for t in open_)}")
    print(f"Swarm 排队 {sum(k['status'] == 'queued' for k in s['tasks'])}，运行中 {sum(k['status'] == 'running' for k in s['tasks'])}"
          f"，模式 {s['dispatch_mode']}" + ("，已暂停" if s["meta"].get("swarm_paused") else ""))
    print(f"仓库/工具登记 {len(s['repos'])} 条；未归类会话 {len(s['unmapped_sessions'])} 个")


def cmd_project(a):
    if a.action == "list":
        st = store.fold()
        if a.json:
            return _out(list(st["projects"].values()), True)
        for p in sorted(st["projects"].values(), key=lambda p: (p["category"], p["status"], p["id"])):
            print(f"{p['category']:9}{p['status']:9}{p['id']:26}{p['name']}")
        return
    if a.action == "merge":
        return _emit(a, "project.merge", {"into": a.id, "from": a.src or []})
    data = {"id": a.id, "name": a.name, "category": a.category, "status": a.status, "summary": a.summary,
            "goal": a.goal, "state": a.state, "next": a.next}
    if a.path:
        data["add_paths"] = [util.tilde(p) for p in a.path]
    if a.alias:
        data["aliases"] = a.alias
    _emit(a, "project.upsert", data)


def cmd_progress(a):
    _emit(a, "project.progress", {"project": a.project, "text": a.text, "source": a.source, "date": a.date,
                                  "links": a.link or None, "thread": a.thread})


def cmd_todo(a):
    if a.action == "list":
        st = store.fold()
        rows = [t for t in st["todos"].values() if (a.all or t["status"] == "open") and (not a.project or t["project"] == a.project)]
        rows.sort(key=lambda t: (t["status"] != "open", t.get("due") or "9999", t.get("priority", 2)))
        if a.json:
            return _out(rows, True)
        for t in rows:
            mark = {"open": " ", "done": "x", "dropped": "-"}[t["status"]]
            print(f"[{mark}] {t['id']}  P{t.get('priority', 2)}  {t.get('due') or '          '}  {(t.get('project') or '-'):26} {t['title']}")
        return
    if a.action == "add":
        return _emit(a, "todo.add", {"title": a.title, "project": a.project, "due": a.due, "priority": a.pri,
                                     "notes": a.notes, "source": a.source, "thread": a.thread})
    if a.action == "set":
        return _emit(a, "todo.update", {"id": a.id, "title": a.title, "project": a.project, "due": a.due,
                                        "priority": a.pri, "notes": a.notes, "thread": a.thread})
    _emit(a, {"done": "todo.done", "reopen": "todo.reopen", "drop": "todo.drop"}[a.action], {"id": a.id})


def cmd_task(a):
    from . import runner
    if a.action == "list":
        rows = sorted(store.fold()["tasks"].values(), key=lambda k: k["created"])
        if a.json:
            return _out(rows, True)
        for k in rows:
            print(f"{k['id']}  {k['status']:9} {k['agent']:12} {(k.get('project') or '-'):26} {k['title']}")
    elif a.action == "add":
        prompt = open(a.prompt_file, encoding="utf-8").read() if a.prompt_file else a.prompt
        _emit(a, "task.create", {"agent": a.agent, "title": a.title, "prompt": prompt, "project": a.project,
                                 "cwd": util.tilde(a.cwd) if a.cwd else None, "acceptance": a.accept, "todo": a.todo})
    elif a.action == "next":
        task = runner.next_for(a.agent, a.claim)
        _out(task or {"none": True}, True)
    elif a.action == "result":
        summary = open(a.file, encoding="utf-8").read() if a.file else (a.summary or "")
        _emit(a, "task.status", {"id": a.id, "status": a.status, "summary": summary[-3000:]})
    elif a.action == "cancel":
        _emit(a, "task.status", {"id": a.id, "status": "cancelled"})
    elif a.action == "retry":
        _emit(a, "task.status", {"id": a.id, "status": "queued"})
    elif a.action == "approve":
        _emit(a, "meta.set", {"key": "approved:" + a.id, "value": True})


def cmd_collect(a):
    from .collectors import sessions
    for src in (["codex", "claude-code"] if a.source == "all" else [a.source]) if a.source != "git" else []:
        _out(sessions.collect(src), True)
    if a.source in ("all", "git"):
        g = sessions.git_activity(store.fold())
        print(f"git：{len(g)} 个项目有近期提交")


def cmd_digest(a):
    from . import views
    _out(views.digest(a.days), True)


def cmd_repos(a):
    from . import screener
    if a.action == "scan":
        stats = screener.scan(with_tools=not a.no_tools)
        if a.online:
            stats["online_checked"] = screener.enrich_online()
        _out(stats, True)
    elif a.action == "list":
        rows = screener.view(store.fold())
        if not a.all:
            rows = [r for r in rows if r.get("ai") or r["kind"] == "planned"]
        if a.json:
            return _out(rows, True)
        for r in rows:
            where = (r.get("paths") or [""])[0]
            print(f"{r['state']:10}{r['kind']:8}{r['name'][:30]:31}{(r.get('commit') or r.get('version') or ''):14}{r.get('url') or where}")
    elif a.action == "plan":
        url, key = screener.norm_url(a.target) if "/" in a.target else ("", "planned:" + a.target.lower())
        _emit(a, "repo.note", {"key": key, "status": "planned", "url": url or None, "name": a.name or a.target.rstrip("/").split("/")[-1],
                               "note": a.note})
    elif a.action == "note":
        audit = {"commit": a.audit_commit, "verdict": a.audit_verdict, "ref": a.audit_ref} if a.audit_verdict else None
        _emit(a, "repo.note", {"key": a.key, "status": a.status, "note": a.note, "tags": a.tag or None, "audit": audit})


def cmd_reminders(a):
    from . import reminders
    _out(reminders.dump() if a.action == "dump" else reminders.sync(dry_run=a.dry_run), True)


def cmd_review(a):
    from . import review
    if a.print:
        return print(review.build(a.week)[1])
    print(review.publish(a.week))


def cmd_apply(a):
    raw = sys.stdin.read() if a.file == "-" else open(a.file, encoding="utf-8").read()
    items = json.loads(raw)
    evs = store.apply_batch(items if isinstance(items, list) else items["events"], actor=a.actor)
    print(f"ok 写入 {len(evs)} 条事件")


def cmd_pause(a):
    _emit(a, "meta.set", {"key": "swarm_paused", "value": a.cmd == "pause"})


def cmd_meta(a):
    _emit(a, "meta.set", {"key": a.key, "value": a.value})


def cmd_runonce(a):
    from . import runner
    _out(runner.run_once(a.limit), True)


def cmd_serve(a):
    from . import daemon
    daemon.main(jobs=(a.cmd == "daemon"), port=a.port)


def build_parser():
    ap = argparse.ArgumentParser(prog="hub", description="Todo Hub：个人项目/Todo 看板与 agent swarm 的共用命令行")
    from todohub import __version__
    ap.add_argument("--version", action="version", version=f"todohub {__version__}")
    ap.add_argument("--actor", help="署名（默认取环境变量 HUB_ACTOR，否则取配置里的 actor）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="一屏概览").set_defaults(fn=cmd_status)

    p = sub.add_parser("project", help="项目：add / set / list")
    p.add_argument("action", choices=["add", "set", "list", "merge"])
    p.add_argument("id", nargs="?")
    for f, hp in (("name", None), ("summary", None), ("goal", "这个项目要达成什么"), ("state", "现在到哪了（综合后的一两句）"),
                  ("next", "下一步关键动作")):
        p.add_argument("--" + f, help=hp)
    p.add_argument("--category", help="主线 id，见 config/taxonomy.json")
    p.add_argument("--from", dest="src", action="append", help="merge 时：要并入的旧项目 id，可多次")
    p.add_argument("--status", choices=store.PROJECT_STATUS)
    p.add_argument("--path", action="append", help="项目的本机目录，可多次；会话按它自动归类")
    p.add_argument("--alias", action="append")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_project)

    p = sub.add_parser("progress", help="给项目记一条进展")
    p.add_argument("project"), p.add_argument("text")
    p.add_argument("--source", help="来源，如 claude-chat / codex / grokbot / 手动")
    p.add_argument("--link", action="append"), p.add_argument("--date", help="进展实际发生的日期 YYYY-MM-DD")
    p.add_argument("--thread", help="项目内的子线，如某个标的或某条工作线")
    p.add_argument("--ts", help="把事件时间记成过去某一刻（回填用）")
    p.set_defaults(fn=cmd_progress)

    p = sub.add_parser("todo", help="todo：add / set / done / reopen / drop / list")
    p.add_argument("action", choices=["add", "set", "done", "reopen", "drop", "list"])
    p.add_argument("arg", nargs="?", help="add 时是标题，其余是 todo id")
    p.add_argument("-p", "--project"), p.add_argument("--due"), p.add_argument("--pri", type=int, choices=[1, 2, 3])
    p.add_argument("--notes"), p.add_argument("--source"), p.add_argument("--title"), p.add_argument("--thread")
    p.add_argument("--all", action="store_true"), p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_todo)

    p = sub.add_parser("task", help="swarm 任务：add / list / next / result / cancel / retry / approve")
    p.add_argument("action", choices=["add", "list", "next", "result", "cancel", "retry", "approve"])
    p.add_argument("id", nargs="?")
    p.add_argument("--agent"), p.add_argument("--title"), p.add_argument("--prompt"), p.add_argument("--prompt-file")
    p.add_argument("-p", "--project"), p.add_argument("--cwd"), p.add_argument("--accept", help="验收标准")
    p.add_argument("--todo", help="关联的 todo id"), p.add_argument("--claim", action="store_true")
    p.add_argument("--status", choices=["done", "failed", "blocked"], default="done")
    p.add_argument("--summary"), p.add_argument("--file"), p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_task)

    p = sub.add_parser("collect", help="采集本机 agent 会话与 git 提交")
    p.add_argument("--source", choices=["all", "codex", "claude-code", "git"], default="all")
    p.set_defaults(fn=cmd_collect)

    p = sub.add_parser("digest", help="给编排者的增量简报（JSON）")
    p.add_argument("--days", type=float, default=1.5)
    p.set_defaults(fn=cmd_digest)

    p = sub.add_parser("repos", help="开源仓库 screener：scan / list / plan / note")
    p.add_argument("action", choices=["scan", "list", "plan", "note"])
    p.add_argument("target", nargs="?", help="plan：仓库地址或名字；note：登记表里的 key")
    p.add_argument("--name"), p.add_argument("--note"), p.add_argument("--status"), p.add_argument("--tag", action="append")
    p.add_argument("--audit-verdict", help="代码审计结论，如 pass / 有保留 / 不装")
    p.add_argument("--audit-commit"), p.add_argument("--audit-ref", help="审计记录的链接")
    p.add_argument("--online", action="store_true", help="顺便查 GitHub 星数/是否归档")
    p.add_argument("--no-tools", action="store_true", help="只扫目录，不查包管理器")
    p.add_argument("--all", action="store_true"), p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_repos)

    p = sub.add_parser("reminders", help="和 macOS 提醒事项同步")
    p.add_argument("action", choices=["sync", "dump"]), p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_reminders)

    p = sub.add_parser("review", help="生成周复盘")
    p.add_argument("--week", help="如 2026-W47，默认本周"), p.add_argument("--print", action="store_true")
    p.set_defaults(fn=cmd_review)

    p = sub.add_parser("apply", help="批量写入事件：JSON 数组 [{type, data, ts?}]，文件名或 - 表示标准输入")
    p.add_argument("file")
    p.set_defaults(fn=cmd_apply)

    sub.add_parser("restart", help="让常驻服务重启并加载新代码").set_defaults(
        fn=lambda a: _emit(a, "meta.set", {"key": "daemon_restart", "value": util.iso(util.now())}))
    for name, hp in (("pause", "暂停 swarm 派单"), ("resume", "恢复 swarm 派单")):
        sub.add_parser(name, help=hp).set_defaults(fn=cmd_pause)
    p = sub.add_parser("meta", help="写一个元数据键值")
    p.add_argument("key"), p.add_argument("value")
    p.set_defaults(fn=cmd_meta)
    p = sub.add_parser("run-once", help="立刻执行排队中的 swarm 任务")
    p.add_argument("--limit", type=int, default=1)
    p.set_defaults(fn=cmd_runonce)
    for name, hp in (("serve", "只起看板"), ("daemon", "看板 + 定时采集/同步/派单")):
        p = sub.add_parser(name, help=hp)
        p.add_argument("--port", type=int)
        p.set_defaults(fn=cmd_serve)
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    # todo / repos / task 的位置参数各有含义，这里统一摆正
    if a.cmd == "todo":
        a.title, a.id = (a.arg if a.action == "add" else a.title), (a.arg if a.action != "add" else None)
        if a.action == "add" and not a.title:
            ap.error("todo add 需要标题")
        if a.action not in ("add", "list") and not a.id:
            ap.error(f"todo {a.action} 需要 todo id")
    if a.cmd == "repos":
        a.key = a.target
        if a.action in ("plan", "note") and not a.target:
            ap.error(f"repos {a.action} 需要一个目标")
    if a.cmd == "project" and a.action != "list" and not a.id:
        ap.error("需要项目 id")
    try:
        a.fn(a)
    except store.HubError as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(2)
    except RuntimeError as e:
        print(f"失败：{e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
