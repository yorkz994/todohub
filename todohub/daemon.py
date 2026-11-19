"""本机常驻进程：看板网页 + 定时采集 / 提醒事项同步 / screener / swarm 执行 / 周复盘数字层。

只监听 127.0.0.1。写接口要求自定义请求头 X-Hub，并校验 Host —— 因为 swarm 是自动执行的，
必须防止你浏览的某个网页偷偷往 localhost 发请求来塞任务。
"""
from __future__ import annotations

import json
import socketserver
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import store, util

WEB = Path(__file__).with_name("web")
DASHBOARD_EVENTS = {"project.merge", "todo.add", "todo.update", "todo.done", "todo.reopen", "todo.drop", "project.upsert",
                    "project.progress", "task.create", "task.status", "repo.note", "session.assign", "meta.set"}
STARTED = util.now()
_lock = threading.Lock()
_last_run: dict[str, float] = {}
_job_log: dict[str, dict] = {}


def job(name: str):
    """执行一个后台作业并记录结果；任何异常都只记日志，不让守护进程退出。"""
    from . import reminders, review, runner, screener
    from .collectors import sessions
    started = time.time()
    try:
        with _lock if name != "runner" else threading.Lock():
            if name == "collect":
                res = {s: sessions.collect(s) for s in sessions.SOURCES}
                res["git_projects"] = len(sessions.git_activity(store.fold()))
            elif name == "reminders":
                res = reminders.sync()
            elif name == "screener":
                res = screener.scan()
                res["online_checked"] = screener.enrich_online(limit=20)
            elif name == "runner":
                res = runner.run_once(limit=1)
            elif name == "review":
                res = {"path": review.publish()}
            elif name == "widget":
                res = ensure_widget()
            else:
                res = {"error": "unknown job"}
        _job_log[name] = {"at": util.iso(util.now()), "sec": round(time.time() - started, 1), "result": res}
        if res or name not in ("runner", "widget"):
            util.log(f"job {name}: {json.dumps(res, ensure_ascii=False)[:300]}")
    except Exception as e:
        _job_log[name] = {"at": util.iso(util.now()), "error": str(e)}
        util.log(f"job {name} 失败：{e}\n{traceback.format_exc(limit=3)}")
    return _job_log[name]


def ensure_widget():
    """菜单栏 / 悬浮窗小程序没在跑就把它拉起来（登录后自动出现靠的就是这里）。用户从它的菜单里选了退出则不拉。"""
    app = util.ROOT / "widget" / "build" / "TodoHubBar.app"
    if sys.platform != "darwin" or not util.cfg().get("widget", {}).get("autostart", True) or not app.exists():
        return None
    if store.fold()["meta"].get("widget_off"):
        return None
    if subprocess.run(["pgrep", "-x", "TodoHubBar"], capture_output=True).returncode != 0:
        subprocess.run(["open", "-g", str(app), "--args", f"http://127.0.0.1:{util.cfg()['port']}"])
        return {"started": True}
    return None


def scheduler():
    every = util.cfg()["intervals_min"]
    plan = [("collect", every["collect"]), ("reminders", every["reminders"]), ("screener", every["screener"]),
            ("review", 180), ("widget", 2)]
    if sys.platform != "darwin":
        plan = [p for p in plan if p[0] != "reminders"]
    while True:
        # `hub restart` 会写下一个重启请求；看到比自己启动时间更新的请求就退出，launchd 会立刻拉起新进程（加载新代码）
        asked = util.parse_ts(store.fold()["meta"].get("daemon_restart"))
        if asked and asked > STARTED and not any(k["status"] == "running" and k.get("runner") == "runner"
                                                 for k in store.fold()["tasks"].values()):
            util.log("收到重启请求，退出并由 launchd 重新拉起")
            os._exit(0)
        for name, minutes in plan:
            if time.time() - _last_run.get(name, 0) >= minutes * 60:
                _last_run[name] = time.time()
                job(name)
        time.sleep(20)


def runner_loop():
    while True:
        job("runner")
        time.sleep(max(20, int(util.cfg()["intervals_min"]["runner"] * 60)))


class Handler(BaseHTTPRequestHandler):
    server_version = "todohub"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def do_GET(self):
        from . import views
        if not self._host_ok():
            return self._send(403, {"error": "bad host"})
        path = self.path.split("?")[0]
        query = dict(q.split("=", 1) for q in self.path.split("?")[1].split("&") if "=" in q) if "?" in self.path else {}
        try:
            if path in ("/", "/index.html"):
                return self._send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/widget":  # 菜单栏弹窗和桌面悬浮窗用的紧凑页面
                return self._send(200, (WEB / "widget.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/api/state":
                snap = views.snapshot()
                snap["jobs"] = _job_log
                return self._send(200, snap)
            if path == "/api/timeline":
                return self._send(200, views.timeline(int(query.get("days", 14))))
            if path == "/api/review":
                week = query.get("week", "")
                if not re.fullmatch(r"\d{4}-W\d{2}", week):  # 只接受形如 2026-W47，其余一律当作本周
                    week = util.week_of(util.today())
                f = util.ROOT / "reviews" / f"{week}.md"
                if not f.exists():
                    from . import review
                    return self._send(200, {"week": week, "text": review.build(week)[1], "saved": False})
                return self._send(200, {"week": week, "text": f.read_text(encoding="utf-8"), "saved": True})
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-Hub") != "1":
            return self._send(403, {"error": "forbidden"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path == "/api/event":
                if body.get("type") not in DASHBOARD_EVENTS:
                    return self._send(400, {"error": "不支持的事件类型"})
                ev = store.append(body["type"], body.get("data", {}), actor=util.cfg().get("actor") or "me")
                return self._send(200, {"ok": True, "event": ev})
            if self.path == "/api/job":
                if body.get("name") not in ("collect", "reminders", "screener", "review", "runner"):
                    return self._send(400, {"error": "未知作业"})
                threading.Thread(target=job, args=(body["name"],), daemon=True).start()
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})
        except store.HubError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": str(e)})



class _Server(ThreadingHTTPServer):
    """跳过 HTTPServer 默认的反向 DNS 查询（getfqdn）：在部分 Mac 上它会卡住好几秒。"""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

def main(jobs: bool = True, port: int | None = None):
    port = port or int(util.cfg()["port"])
    httpd = _Server(("127.0.0.1", port), Handler)
    util.log(f"Todo Hub 看板：http://127.0.0.1:{port}  （数据目录 {util.ROOT}）")
    if jobs:
        # 上一个进程退出时还标着「运行中」的任务不会再有结果，这里收尾
        for k in store.fold()["tasks"].values():
            if k["status"] == "running" and k.get("runner") == "runner":
                ok = k["agent"] == "hub-restart"
                store.append("task.status", {"id": k["id"], "status": "done" if ok else "failed",
                                             "summary": "常驻服务已重启" if ok else "常驻服务重启时被中断，可重试"}, actor="daemon")
        threading.Thread(target=scheduler, daemon=True).start()
        threading.Thread(target=runner_loop, daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
