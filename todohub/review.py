"""周复盘。

两层：
  1. 数字层（本文件）：直接从事件和会话摘要里数出来，可重复生成，不经过任何模型。
  2. 判断层：Claude 周日的定时任务读完数字层和当周对话后，写 reviews/<周>.narrative.md，
     生成时自动拼在最前面。
周的口径：ISO 周，周一到周日，按 config 里的时区。
"""
from __future__ import annotations

import datetime as dt

from . import screener, store, util
from .collectors import sessions as S


def build(week: str | None = None) -> tuple[str, str]:
    week = week or util.week_of(util.today())
    mon, sun = util.week_range(week)
    state = store.fold()
    events = store.load_events()

    def in_week(ts) -> bool:
        d = util.local_date(ts)
        return bool(d and mon <= d <= sun)

    P, T, K = state["projects"], state["todos"], state["tasks"]
    name = lambda pid: P[pid]["name"] if pid in P else "未归类"
    done = [t for t in T.values() if t["status"] == "done" and in_week(t["done_at"])]
    added = [t for t in T.values() if in_week(t["created"])]
    open_ = [t for t in T.values() if t["status"] == "open"]
    ref = min(sun, util.today())
    overdue = [t for t in open_ if t.get("due") and t["due"] < str(ref)]
    next_mon = sun + dt.timedelta(days=1)
    due_next = [t for t in open_ if t.get("due") and str(next_mon) <= t["due"] <= str(next_mon + dt.timedelta(days=6))]
    rd = state.get("redirect", {})
    progress = [(rd.get(e["data"]["project"], e["data"]["project"]), e) for e in events
                if e["type"] == "project.progress" and in_week(e["ts"])]
    tasks = [k for k in K.values() if in_week(k["created"]) or (k.get("finished") and in_week(k["finished"]))]
    sess = [s for s in S.load_sessions() if in_week(s.get("ended") or s.get("started"))]
    sess_by = {}
    for s in sess:
        pid = S.match_project(s.get("cwd") or "", state, key=f"{s['source']}:{s['id']}")
        sess_by.setdefault(pid, []).append(s)
    git = (util.read_json(util.ROOT / "inbox" / "git" / "index.json", None) or {}).get("projects", {})
    commits = {pid: [c for c in cs if in_week(c["ts"])] for pid, cs in git.items()}
    repos = screener.view(state)
    new_repos = [r for r in repos if in_week(r.get("first_seen")) and r.get("ai")]
    bumped = [r for r in repos if any(in_week(h["ts"]) for h in r.get("history", []))]

    L = [f"# 周复盘 {week}（{mon:%m-%d} 周一 – {sun:%m-%d} 周日）", ""]
    narrative = util.ROOT / "reviews" / f"{week}.narrative.md"
    if narrative.exists():
        L += [narrative.read_text(encoding="utf-8").strip(), "", "---", ""]
    by_src = {}
    for s in sess:
        by_src[s["source"]] = by_src.get(s["source"], 0) + 1
    L += ["## 本周数字", "",
          f"- Todo：完成 {len(done)}，新增 {len(added)}，当前未完成 {len(open_)}，其中逾期 {len(overdue)}",
          f"- 进展记录：{len(progress)} 条，涉及 {len({p for p, _ in progress})} 个项目",
          f"- 本机 agent 会话：{len(sess)} 个" + ("（" + "，".join(f"{k} {v}" for k, v in sorted(by_src.items())) + "）" if by_src else ""),
          f"- Swarm 派单：{len(tasks)} 个（完成 {sum(k['status'] == 'done' for k in tasks)}，失败 {sum(k['status'] == 'failed' for k in tasks)}，"
          f"卡住 {sum(k['status'] == 'blocked' for k in tasks)}）",
          f"- 新装的 AI 仓库/工具：{len(new_repos)} 个；版本有变化：{len(bumped)} 个", ""]

    active = sorted({p for p, _ in progress} | {t["project"] for t in done + added if t["project"]}
                    | {p for p in sess_by if p} | {p for p, c in commits.items() if c} | {k["project"] for k in tasks if k["project"]})
    L += ["## 按项目", ""]
    if not active:
        L += ["本周没有任何项目有记录。", ""]
    order = {l["id"]: (i, l["name"]) for i, l in enumerate(store.lines())}
    line_of = lambda p: order.get(P.get(p, {}).get("category"), (99, "未归类"))
    current = None
    for pid in sorted(active, key=lambda p: (line_of(p)[0], p)):
        if line_of(pid)[1] != current:
            current = line_of(pid)[1]
            L += [f"### 主线：{current}", ""]
        L += [f"**{name(pid)}**" + (f"　现状：{P[pid]['state']}" if pid in P and P[pid].get("state") else ""), ""]
        for _, e in [x for x in progress if x[0] == pid]:
            src = f"（{e['data']['source']}）" if e["data"].get("source") else ""
            th = f"［{e['data']['thread']}］" if e["data"].get("thread") else ""
            L.append(f"- {util.local_date(e['ts']):%m-%d} {th}{e['data']['text']}{src}")
        for t in [t for t in done if t["project"] == pid]:
            L.append(f"- ✓ 完成：{t['title']}")
        ss = sess_by.get(pid, [])
        if ss:
            mins = sum(s.get("work_min") or 0 for s in ss)
            L.append(f"- 会话 {len(ss)} 个" + (f"（这些会话全程累计运行约 {mins} 分钟）" if mins else "")
                     + (f"；最近一次结论：{util.trim(ss[-1].get('last_message'), 160)}" if ss[-1].get("last_message") else ""))
        if commits.get(pid):
            L.append(f"- 提交 {len(commits[pid])} 次；最近：{util.trim(commits[pid][0]['subject'], 80)}")
        for k in [k for k in tasks if k["project"] == pid]:
            L.append(f"- 派单 {k['id']}（{k['agent']}）{k['title']} → {k['status']}")
        left = [t for t in open_ if t["project"] == pid]
        if left:
            L.append(f"- 还剩 {len(left)} 个未完成：" + "；".join(util.trim(t["title"], 40) for t in left[:4])
                     + ("…" if len(left) > 4 else ""))
        L.append("")

    stale_days = int(util.cfg().get("stale_days", 14))
    stale = []
    for pid, p in P.items():
        if p["status"] != "active" or pid in active:
            continue
        last = p["last_activity"]
        for s in S.load_sessions():
            if S.match_project(s.get("cwd") or "", state) == pid:
                last = max(last, s.get("ended") or "")
        if not util.local_date(last):
            continue  # 从来没有活动记录的项目不算「停滞」，看板上会显示「无记录」
        idle = (ref - util.local_date(last)).days
        if idle >= stale_days:
            stale.append((idle, p["name"]))
    if stale:
        L += [f"## 停滞项目（状态是 active 但 {stale_days} 天以上没有任何记录）", ""]
        L += [f"- {n}：{d} 天" for d, n in sorted(stale, reverse=True)] + [""]

    def todo_lines(ts):
        return [f"- {t.get('due') or '无日期'}　{name(t['project'])}｜{t['title']}" for t in sorted(ts, key=lambda t: t.get("due") or "9")]

    if overdue:
        L += ["## 逾期未完成", ""] + todo_lines(overdue) + [""]
    L += ["## 下周到期", ""] + (todo_lines(due_next) or ["- 没有带日期的 todo 落在下周。"]) + [""]
    nodate = [t for t in open_ if not t.get("due") and t.get("priority") == 1]
    if nodate:
        L += ["## 高优先级但没排日期", ""] + todo_lines(nodate) + [""]
    if unm := sess_by.get(None):
        cwds = sorted({s.get("cwd") or "?" for s in unm})
        L += ["## 没归到项目的会话", "", f"{len(unm)} 个会话的工作目录不属于任何项目：", ""] + [f"- {c}" for c in cwds] + [""]
    if new_repos or bumped:
        L += ["## 仓库与工具变化", ""]
        L += [f"- 新增 [{r['kind']}] {r['name']}" + (f"　{r['url']}" if r.get("url") else "") for r in new_repos]
        L += [f"- 更新 {r['name']}：{r['history'][-1]['from']} → {r['history'][-1]['to']}" for r in bumped] + [""]
    L += ["", f"_数字层生成于 {util.now().astimezone(util.tz()):%Y-%m-%d %H:%M}（{util.cfg()['timezone']}）_", ""]
    return week, "\n".join(L)


def publish(week: str | None = None) -> str:
    week, text = build(week)
    path = util.ROOT / "reviews" / f"{week}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    if week not in store.fold()["reviews"]:
        store.append("review.published", {"week": week, "path": f"reviews/{week}.md"}, actor="daemon")
    return str(path)
