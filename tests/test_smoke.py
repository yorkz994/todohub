"""端到端冒烟测试：在临时目录里造数据，把每个模块跑一遍。运行：python3 tests/test_smoke.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="hubtest-"))
HOME = TMP / "home"
ROOT = TMP / "hub"
(ROOT / "config").mkdir(parents=True)
HOME.mkdir()
os.environ.update(HUB_ROOT=str(ROOT), HOME=str(HOME), HUB_ACTOR="tester")
os.environ.pop("HUB_PATH_MAP", None)  # 测试必须只碰临时目录，不受外部路径映射影响
sys.path.insert(0, str(SRC))

cfg = json.load(open(SRC / "config" / "hub.json"))
cfg["port"] = 18791
cfg["scan_roots"] = [{"path": "~/code", "depth": 3}]
cfg["reminders"]["enabled"] = False  # 测试绝不碰本机真实的提醒事项（macOS 上 sync 会真的读写）
json.dump(cfg, open(ROOT / "config" / "hub.json", "w"))
agents = json.load(open(SRC / "config" / "agents.json"))
fake = TMP / "fakeagent.sh"
fake.write_text('#!/bin/bash\necho "结果：已完成 $HUB_TASK" > "$1"\necho "改动：无" >> "$1"\necho log line\n')
fake.chmod(0o755)
agents["fake"] = {"label": "Fake", "kind": "cli", "enabled": True, "cmd": [str(fake), "{out}", "{prompt}"]}
agents["nobin"] = {"label": "NoBin", "kind": "cli", "enabled": True, "cmd": ["definitely-not-installed-xyz", "{prompt}"]}
json.dump(agents, open(ROOT / "config" / "agents.json", "w"))

# ---- 个人配置覆盖（.local 文件不进仓库）----
json.dump({"owner_name": "测试员", "timezone": "Asia/Seoul", "reminders": {"adopt_lists": ["清单X"]}},
          open(ROOT / "config" / "hub.local.json", "w"), ensure_ascii=False)
json.dump({"lines": [{"id": "alpha", "name": "甲", "color": "invest"}]}, open(ROOT / "config" / "taxonomy.local.json", "w"),
          ensure_ascii=False)
json.dump({"rooty": {"label": "R", "kind": "cli", "enabled": True, "cmd": ["{root}/x.sh"]}},
          open(ROOT / "config" / "agents.local.json", "w"))
from todohub import reminders, review, runner, screener, store, util, views  # noqa: E402
from todohub.collectors import sessions  # noqa: E402

ok = 0


def check(cond, msg):
    global ok
    assert cond, "FAIL: " + msg
    ok += 1


def expect_error(fn, msg):
    try:
        fn()
    except store.HubError:
        return check(True, msg)
    check(False, msg + "（应当报错却没有）")


check(util.owner() == "测试员" and util.cfg()["reminders"]["adopt_lists"] == ["清单X"]
      and util.cfg()["reminders"]["list"] == "Todo Hub", "hub.local.json 逐项覆盖示例配置")
check([l["id"] for l in store.lines()] == ["alpha"], "taxonomy.local.json 替代示例主线")
check("rooty" in util.agents_cfg() and "codex" in util.agents_cfg(), "agents.local.json 合并进 agent 列表")
check("测试员" in runner.CONTRACT.format(owner=util.owner(), agent="a", id="b", project="c", cwd="d", prompt="e",
                                       acceptance="f"), "派单说明里用配置的名字")
(ROOT / "config" / "taxonomy.local.json").unlink()
(ROOT / "config" / "agents.local.json").unlink()
check(store.lines()[0]["id"] != "alpha", "删掉 local 后退回示例主线")


# ---- 事件与校验 ----
store.append("project.upsert", {"id": "garden", "name": "Garden Planner", "category": "coding", "paths": ["~/work/garden"]})
store.append("project.upsert", {"id": "reading", "name": "读书计划", "category": "invest"})
expect_error(lambda: store.append("project.upsert", {"id": "Bad ID"}), "非法项目 id")
expect_error(lambda: store.append("todo.add", {"title": "x", "project": "nope"}), "不存在的项目")
expect_error(lambda: store.append("todo.add", {"title": "x", "due": "11/30"}), "非法日期")
expect_error(lambda: store.append("task.create", {"agent": "ghost", "title": "t", "prompt": "p"}), "未登记的 agent")
expect_error(lambda: store.append("nope.type", {}), "未知事件类型")
t1 = store.append("todo.add", {"title": "画出种植表", "project": "garden", "due": "2020-01-01", "priority": 1})["data"]["id"]
t2 = store.append("todo.add", {"title": "整理书单"})["data"]["id"]
store.append("project.progress", {"project": "garden", "text": "规格冻结", "source": "claude-chat"})
st = store.fold()
check(len(st["projects"]) == 2 and len(st["todos"]) == 2, "fold 基本计数")
check(st["projects"]["garden"]["progress"][0]["text"] == "规格冻结", "进展记录")
check(st["todos"][t1]["actor"] == "tester", "署名来自 HUB_ACTOR")

# 批量：后面的事件能引用前面新建的项目；有一条错则整批不落盘
n_before = len(store.load_events())
expect_error(lambda: store.apply_batch([{"type": "project.upsert", "data": {"id": "newp", "name": "N"}},
                                        {"type": "todo.add", "data": {"title": "a", "project": "missing"}}]), "批量原子性")
check(len(store.load_events()) == n_before, "批量失败时一条都不写")
evs = store.apply_batch([{"type": "project.upsert", "data": {"id": "newp", "name": "N", "category": "bd"}},
                         {"type": "todo.add", "data": {"title": "a", "project": "newp"}},
                         {"type": "project.progress", "data": {"project": "newp", "text": "回填"}, "ts": "2026-11-10T03:00:00Z"}])
check(len(evs) == 3 and store.fold()["projects"]["newp"]["progress"][0]["date"] == "2026-11-10", "批量写入 + 回填日期")
names = sorted(p.name for p in (ROOT / "data" / "events").rglob("*.json"))
check(len(names) == len(set(names)), "事件文件名唯一")

# ---- 会话采集 ----
cs = HOME / ".codex" / "sessions" / "2026" / "11" / "17"
cs.mkdir(parents=True)


def codex_lines(sid, cwd, kind=None, user="帮我把盘口采集器接进仓库"):
    meta = {"id": sid, "cwd": cwd, "timestamp": "2026-11-17T01:00:00.123Z", "originator": "Codex Desktop"}
    if kind:
        meta["thread_source"] = kind
    return "\n".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) for x in [
        {"timestamp": "2026-11-17T01:00:00.123Z", "type": "session_meta", "payload": meta},
        {"timestamp": "2026-11-17T01:00:01.000Z", "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "# AGENTS.md instructions\n..."}], "internal_chat_message_metadata_passthrough": {"content_item_kinds": ["agents_md.instructions"]}}},
        {"timestamp": "2026-11-17T01:00:02.000Z", "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": user}]}},
        {"timestamp": "2026-11-17T01:00:03.000Z", "type": "response_item", "payload": {"type": "reasoning", "x": "y" * 50}},
        {"timestamp": "2026-11-17T02:30:00.000Z", "type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": "采集器已接入，118 项测试通过", "duration_ms": 5400000}},
        {"timestamp": "2026-11-17T02:30:05.000Z", "type": "event_msg", "payload": {"type": "token_count"}},
    ]) + "\n"


(cs / "rollout-2026-11-17T10-00-00-aaa.jsonl").write_text(codex_lines("aaa", str(HOME / "work/garden/sub")))
(cs / "rollout-2026-11-17T10-00-01-ggg.jsonl").write_text(codex_lines("ggg", str(HOME / "work/garden"), kind="guardian_review"))
(cs / "rollout-2026-11-17T11-00-00-bbb.jsonl").write_text(codex_lines("bbb", str(HOME / "elsewhere"), user="随便看看"))
(cs / "rollout-2026-11-17T11-30-00-ccc.jsonl").write_text(codex_lines("ccc", str(HOME / "elsewhere"), user='<external_codex_apps_open_page>{"page_id":null}</external_codex_apps_open_page>'))
if shutil.which("zstd"):
    z = cs / "rollout-2026-11-17T12-00-00-zzz.jsonl"
    z.write_text(codex_lines("zzz", str(HOME / "work/garden"), user="压缩过的旧会话"))
    subprocess.run(["zstd", "-q", "--rm", str(z)], check=True)
cc = HOME / ".claude" / "projects" / "-home-work-garden"
cc.mkdir(parents=True)
(cc / "s1.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in [
    {"type": "summary", "summary": "接入 CoinGlass 数据湖"},
    {"type": "user", "sessionId": "s1", "cwd": str(HOME / "work/garden"), "timestamp": "2026-11-17T05:00:00.000Z", "message": {"role": "user", "content": "审计一下仓库"}},
    {"type": "user", "sessionId": "s1", "timestamp": "2026-11-17T05:00:10.000Z", "message": {"role": "user", "content": [{"type": "tool_result", "content": "x"}]}},
    {"type": "assistant", "sessionId": "s1", "timestamp": "2026-11-17T05:10:00.000Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "审计完成，缺 3 个文件"}]}},
]) + "\n")
r1 = sessions.collect("codex")
check(r1["errors"] == 0 and r1["parsed"] == (5 if shutil.which("zstd") else 4), f"codex 解析 {r1}")
check(sessions.collect("codex")["parsed"] == 0, "增量：没变的文件不重读")
check(sessions.collect("claude-code")["parsed"] == 1, "claude code 解析")
all_s = {s["id"]: s for s in sessions.load_sessions(include_internal=True)}
check(all_s["aaa"]["prompts"] == ["帮我把盘口采集器接进仓库"] and all_s["aaa"]["turns"] == 1, "过滤注入内容，只留人话")
check(all_s["aaa"]["last_message"].startswith("采集器已接入") and all_s["aaa"]["work_min"] == 90, "结论与时长")
check(all_s["aaa"]["ended"].startswith("2026-11-17T02:30:05"), "结束时间取最后一行")
check(all_s["ggg"]["kind"] == "guardian_review", "内部会话被标记")
check(all_s["ccc"]["prompts"] == [] and all_s["ccc"]["user_msgs"] == 0, "标签开头的注入内容不算人话")
check(all_s["s1"]["title"] == "接入 CoinGlass 数据湖" and all_s["s1"]["user_msgs"] == 1, "claude code 摘要")
user_s = sessions.load_sessions()
check("ggg" not in {s["id"] for s in user_s}, "看板默认不含内部会话")
st = store.fold()
check(sessions.match_project(all_s["aaa"]["cwd"], st) == "garden", "按目录前缀归项目")
check(sessions.match_project(all_s["bbb"]["cwd"], st) is None, "未登记目录不归类")
store.append("project.upsert", {"id": "reading", "add_paths": [all_s["bbb"]["cwd"]]})
check(sessions.match_project(all_s["bbb"]["cwd"], store.fold()) == "reading", "补登记目录后归类")

# ---- screener ----
code = HOME / "code"
g = code / "ai-thing" / ".git"
(g / "refs" / "heads").mkdir(parents=True)
(g / "HEAD").write_text("ref: refs/heads/main\n")
(g / "refs" / "heads" / "main").write_text("1111111111111111111111111111111111111111\n")
(g / "config").write_text('[remote "origin"]\n\turl = https://' + 'tok3n' + '@github.com/Someone/AI-Thing.git\n')
(code / "ai-thing" / "README.md").write_text("An LLM agent framework using OpenAI and MCP")
(code / "plain" / ".git").mkdir(parents=True)
(code / "plain" / ".git" / "HEAD").write_text("2222222222222222222222222222222222222222\n")
(code / "tool-main").mkdir()
(code / "tool-main" / "README.md").write_text("langchain rag embedding demo")
check(screener.norm_url("git@github.com:Example-Org/Some_Repo.git") == ("https://github.com/Example-Org/Some_Repo", "github.com/example-org/some_repo"), "ssh 地址规范化")
s1 = screener.scan(with_tools=False)
reg = util.read_json(screener.REGISTRY)["items"]
check(set(s1["new"]) == {"github.com/someone/ai-thing", "local:~/code/plain", "zip:tool"}, f"扫描发现 {s1['new']}")
it = reg["github.com/someone/ai-thing"]
check(it["url"] == "https://github.com/Someone/AI-Thing" and "tok3n" not in json.dumps(reg), "去掉地址里的凭证")
check(it["commit"] == "111111111111" and it["branch"] == "main" and it["ai"], "commit / 分支 / AI 判定")
check(not reg["local:~/code/plain"]["ai"] and reg["zip:tool"]["ai"], "非 AI 仓库与 zip 目录")
(g / "refs" / "heads" / "main").write_text("3333333333333333333333333333333333333333\n")
shutil.rmtree(code / "tool-main")
s2 = screener.scan(with_tools=False)
reg = util.read_json(screener.REGISTRY)["items"]
check(s2["changed"] == ["github.com/someone/ai-thing"] and reg["github.com/someone/ai-thing"]["history"][0]["to"] == "333333333333", "记录 commit 变更")
check(s2["missing"] == ["zip:tool"] and reg["zip:tool"]["first_seen"], "消失的条目标记 missing 且保留首次发现时间")
store.append("repo.note", {"key": "github.com/baarai/artex", "status": "planned", "url": "https://github.com/BaaRai/ARTEX", "name": "ARTEX"})
store.append("repo.note", {"key": "github.com/someone/ai-thing", "audit": {"verdict": "pass", "commit": "1111111"}})
v = {r["key"]: r for r in screener.view(store.fold())}
check(v["github.com/baarai/artex"]["state"] == "planned" and v["github.com/someone/ai-thing"]["note"]["audit"]["verdict"] == "pass", "计划安装与审计备注")

# ---- 提醒事项同步的决策逻辑（纯函数）----
st = store.fold()
items = [
    {"id": "r1", "name": "Garden Planner｜画出种植表", "body": f"⟦hub:{t1}⟧", "completed": True, "due": None},
    {"id": "r2", "name": "读书计划｜手机上记的", "body": "", "completed": False, "due": "2026-11-20T00:00:00.000Z"},
    {"id": "r3", "name": "老提醒", "body": "", "completed": True, "due": None},
]
p = reminders.plan(st, items)
check(("todo.done", {"id": t1}) in p["events"], "提醒事项里打勾 → Hub 完成")
imp = [d for typ, d in p["events"] if typ == "todo.add"]
check(len(imp) == 1 and imp[0]["title"] == "手机上记的" and imp[0]["project"] == "reading" and imp[0]["due"] == "2026-11-20", "手写提醒导入并识别项目前缀")
check(any(u["id"] == "r2" and "⟦hub:" in u["body"] for u in p["update"]), "导入后回写标记")
check({c["name"] for c in p["create"]} == {"整理书单", "N｜a"}, f"未同步的 todo 被创建 {p['create']}")
store.append("todo.done", {"id": t2})
p2 = reminders.plan(store.fold(), [{"id": "r9", "name": "整理书单", "body": f"⟦hub:{t2}⟧", "completed": False, "due": None}])
check({"id": "r9", "completed": True} in p2["update"], "Hub 完成 → 提醒打勾")
check(reminders.sync().get("skipped"), "测试里提醒事项同步被关掉 / 非 macOS 时跳过")

# ---- 接管已有的提醒事项列表 ----
util.cfg()["reminders"]["adopt_lists"] = ["提醒"]
ta = store.append("todo.add", {"title": "【标签A】整理清单", "rid": "x-apple://A1", "rlist": "提醒"})["data"]["id"]
tb = store.append("todo.add", {"title": "【标签B】Finalize", "rid": "x-apple://B2", "rlist": "提醒"})["data"]["id"]
store.append("todo.done", {"id": tb})
p3 = reminders.plan(store.fold(), [
    {"id": "x-apple://A1", "list": "提醒", "name": "【标签A】整理清单", "body": "", "completed": True, "due": None},
    {"id": "x-apple://B2", "list": "提醒", "name": "【标签B】Finalize", "body": "", "completed": False, "due": None},
    {"id": "x-apple://C3", "list": "提醒", "name": "【标签C】新线索", "body": "周主线", "completed": False, "due": None},
    {"id": "x-apple://D4", "list": "提醒", "name": "早就做完的", "body": "", "completed": True, "due": None}])
check(("todo.done", {"id": ta}) in p3["events"], "接管列表里打勾 → Hub 完成")
check({"id": "x-apple://B2", "list": "提醒", "completed": True} in p3["update"], "Hub 完成 → 原提醒打勾（不改标题）")
new3 = [d for typ, d in p3["events"] if typ == "todo.add"]
check(len(new3) == 1 and new3[0]["rid"] == "x-apple://C3" and new3[0]["source"] == "提醒事项/提醒" and new3[0]["notes"] == "周主线", "接管列表里的新提醒自动导入")
check(not any(c["name"].startswith("【") for c in p3["create"]), "接管的 todo 不在镜像列表里再建一份")
util.cfg()["reminders"]["adopt_lists"] = []
store.append("todo.drop", {"id": ta})

# ---- 项目合并与子线 ----
store.append("project.upsert", {"id": "deal-a", "name": "A 公司尽调", "category": "research", "paths": ["~/deals/a"]})
store.append("project.progress", {"project": "deal-a", "text": "访谈完成"})
td = store.append("todo.add", {"title": "要数据室", "project": "deal-a"})["data"]["id"]
store.append("project.upsert", {"id": "pipeline", "name": "项目 pipeline", "category": "research", "goal": "筛出可投标的"})
store.append("project.merge", {"into": "pipeline", "from": ["deal-a"]})
expect_error(lambda: store.append("project.merge", {"into": "pipeline", "from": ["pipeline"]}), "不能自己并自己")
stm = store.fold()
check("deal-a" not in stm["projects"] and stm["redirect"] == {"deal-a": "pipeline"}, "旧项目并入后不再单列")
pp = stm["projects"]["pipeline"]
check(pp["progress"][0]["thread"] == "A 公司尽调" and stm["todos"][td]["project"] == "pipeline" and stm["todos"][td]["thread"] == "A 公司尽调", "历史进展和 todo 跟过去并带上子线")
check("~/deals/a" in pp["paths"] and "A 公司尽调" in pp["aliases"] and pp["goal"] == "筛出可投标的", "目录、别名、目标")
store.append("project.progress", {"project": "deal-a", "text": "旧 id 还能写", "thread": "A 公司尽调"})
check(len(store.fold()["projects"]["pipeline"]["progress"]) == 2, "合并后用旧 id 写入会落到新项目")
check(sessions.match_project(str(HOME / "deals/a/x").replace(str(HOME), "~"), store.fold()) == "pipeline", "旧目录的会话归到新项目")

# ---- swarm 执行器 ----
k1 = store.append("task.create", {"agent": "fake", "title": "跑一下", "prompt": "do it", "project": "garden", "cwd": str(TMP), "todo": t1})["data"]["id"]
k2 = store.append("task.create", {"agent": "nobin", "title": "没装的", "prompt": "x", "cwd": str(TMP)})["data"]["id"]
k3 = store.append("task.create", {"agent": "grokbot", "title": "给 grok", "prompt": "查一下"})["data"]["id"]
store.append("meta.set", {"key": "swarm_paused", "value": True})
check(runner.run_once(5) == [], "暂停时不执行")
store.append("meta.set", {"key": "swarm_paused", "value": False})
res = runner.run_once(5)
st = store.fold()
check(st["tasks"][k1]["status"] == "done" and "已完成 " + k1 in st["tasks"][k1]["summary"], f"cli agent 执行并回收结果 {res}")
check((ROOT / "swarm" / "runs" / k1 / "prompt.md").read_text().count("不要 git push") == 1, "任务契约写入 prompt")
check(st["tasks"][k2]["status"] == "blocked" and "找不到命令" in st["tasks"][k2]["summary"], "命令不存在 → blocked")
check(st["tasks"][k3]["status"] == "queued", "mailbox 任务不由执行器跑")
check(runner.next_for("grokbot", claim=True)["id"] == k3 and store.fold()["tasks"][k3]["status"] == "running", "mailbox 取件并认领")
store.append("task.status", {"id": k3, "status": "done", "summary": "查完了"}, actor="grokbot")
cfg2 = util.cfg()
cfg2["dispatch"]["mode"] = "review"
k4 = store.append("task.create", {"agent": "fake", "title": "待放行", "prompt": "p", "cwd": str(TMP)})["data"]["id"]
check(runner.run_once(1) == [], "review 模式下未放行不执行")
store.append("meta.set", {"key": "approved:" + k4, "value": True})
check(runner.run_once(1)[0]["id"] == k4, "放行后执行")
cfg2["dispatch"]["mode"] = "auto"

# ---- 视图、周复盘 ----
snap = views.snapshot()
garden_p = next(p for p in snap["projects"] if p["id"] == "garden")
check(garden_p["session_count"] >= 2 and garden_p["open_todos"] == 1, f"项目快照 {garden_p['session_count']}")
check([t["id"] for t in snap["todos"] if t["overdue"]] == [t1], "逾期判定")
check("已跑完 " + k1 in next(t for t in snap["todos"] if t["id"] == t1)["notes"], "任务跑完后在关联 todo 上留言")
store.append("todo.done", {"id": t1})
check(not any(t["overdue"] for t in views.snapshot()["todos"]), "已完成的不算逾期")
dg = views.digest(days=3650)
check("garden" in dg["sessions_by_project"] and dg["open_todos"], "编排简报")
check(len(views.timeline(3650)) > 5, "时间线")
week, text = review.build()
check("## 本周数字" in text and "## 按项目" in text, "周复盘数字层")
(ROOT / "reviews").mkdir(exist_ok=True)
(ROOT / "reviews" / f"{week}.narrative.md").write_text("## Claude 的判断\n\n本周重点是 Garden Planner。")
check("Claude 的判断" in Path(review.publish()).read_text() and week in store.fold()["reviews"], "判断层拼接 + 发布")
w17 = review.build("2026-W47")[1]
check("Garden Planner" in w17 and "会话" in w17, "指定周能数出会话")

# ---- HTTP：读写与防跨站 ----
from todohub import daemon  # noqa: E402

threading.Thread(target=daemon.main, kwargs={"jobs": False, "port": 18791}, daemon=True).start()
base = "http://127.0.0.1:18791"
for _ in range(100):  # 等服务起来（CI 的 Mac 上可能要好几秒）
    try:
        urllib.request.urlopen(base + "/api/state", timeout=2).read()
        break
    except Exception:
        time.sleep(0.2)


def http(path, body=None, headers=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


check("<title>Todo Hub</title>" in http("/")[1], "首页")
code_, body = http("/api/state")
check(code_ == 200 and len(json.loads(body)["projects"]) == 4 and json.loads(body)["lines"], "state 接口")
check(http("/api/event", {"type": "todo.add", "data": {"title": "csrf"}})[0] == 403, "没有 X-Hub 头的写请求被拒")
check(http("/api/event", {"type": "todo.add", "data": {"title": "csrf"}}, {"X-Hub": "1", "Host": "evil.example"})[0] == 403, "Host 不对被拒")
check(http("/api/state", headers={"Host": "evil.example"})[0] == 403, "读接口也校验 Host")
check(http("/api/event", {"type": "todo.add", "data": {"title": "from web"}}, {"X-Hub": "1"})[0] == 200, "正常写入")
check(http("/api/event", {"type": "review.published", "data": {"week": "x"}}, {"X-Hub": "1"})[0] == 400, "看板不能写的事件类型")
check(http("/api/event", {"type": "todo.add", "data": {"title": "x", "project": "zzz"}}, {"X-Hub": "1"})[0] == 400, "校验错误返回 400")
check(json.loads(http("/api/review?week=" + week)[1])["saved"] is True, "复盘接口")
check(http("/api/review?week=../../etc/passwd")[0] == 200 and "passwd" not in http("/api/review?week=../../etc/passwd")[1][:50], "week 参数不能穿越路径")

shutil.rmtree(TMP, ignore_errors=True)
print(f"全部通过：{ok} 项检查")
