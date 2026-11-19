"""共用小工具：路径、时间、配置、JSON 读写。只依赖标准库，兼容 Python 3.9+。"""
from __future__ import annotations

import datetime as dt
import json
import os
import secrets
import time
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

ROOT = Path(os.environ.get("HUB_ROOT") or Path(__file__).resolve().parent.parent)

DEFAULT_CFG = {
    "timezone": "UTC",
    "owner_name": "用户",
    "actor": "me",
    "port": 8791,
    "my_github_owners": [],
    "reminders": {"enabled": True, "list": "Todo Hub", "import_untracked": True},
    "sources": {"codex_sessions": "~/.codex/sessions", "claude_code_projects": "~/.claude/projects"},
    "scan_roots": [{"path": "~", "depth": 2}],
    "scan_exclude": ["Library", "node_modules", ".Trash", ".cache", ".npm", ".bun", "Pictures", "Music", "Movies",
                     "venv", ".venv", "site-packages", "__pycache__", "dist", "build", ".git"],
    "dispatch": {"mode": "auto", "max_runs_per_day": 20, "timeout_min": 60},
    "intervals_min": {"collect": 30, "reminders": 10, "screener": 360, "runner": 1},
    "stale_days": 14,
}

_cfg_cache = None


def local_or(name: str) -> Path:
    """config/<名>.local.json 存在就用它（个人配置，不进仓库），否则用仓库里的示例 config/<名>.json。"""
    base = ROOT / "config"
    loc = base / name.replace(".json", ".local.json")
    return loc if loc.exists() else base / name


def _merge(base: dict, over: dict) -> dict:
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = _merge(dict(base[k]), v)
        else:
            base[k] = v
    return base


def cfg() -> dict:
    """默认值 ← config/hub.json（仓库里的示例）← config/hub.local.json（你自己的，不进仓库）。"""
    global _cfg_cache
    if _cfg_cache is None:
        merged = json.loads(json.dumps(DEFAULT_CFG))
        _merge(merged, read_json(ROOT / "config" / "hub.json", {}))
        _merge(merged, read_json(ROOT / "config" / "hub.local.json", {}))
        _cfg_cache = merged
    return _cfg_cache


def agents_cfg() -> dict:
    """config/agents.json ← config/agents.local.json（同名 agent 整条覆盖，可新增）。每次现读。"""
    a = read_json(ROOT / "config" / "agents.json", {})
    a.update(read_json(ROOT / "config" / "agents.local.json", {}))
    return a


def owner() -> str:
    return cfg().get("owner_name") or "用户"


def tz():
    name = cfg().get("timezone", "UTC")
    if ZoneInfo:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return dt.timezone.utc


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(d: dt.datetime) -> str:
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s) -> dt.datetime | None:
    """接受 ISO 字符串（带 Z / 偏移 / 仅日期）或 epoch 秒/毫秒。失败返回 None。"""
    if s is None or s == "":
        return None
    if isinstance(s, (int, float)):
        return dt.datetime.fromtimestamp(s / 1000 if s > 1e11 else s, dt.timezone.utc)
    s = str(s).strip()
    try:
        if len(s) == 10:
            return dt.datetime.fromisoformat(s).replace(tzinfo=tz())
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        # Python 3.9/3.10 的 fromisoformat 不接受 1~5 位小数秒，统一截到秒
        if "." in s:
            head, rest = s.split(".", 1)
            off = ""
            for sep in ("+", "-"):
                if sep in rest:
                    off = sep + rest.split(sep, 1)[1]
                    break
            s = head + off
        d = dt.datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


def local_date(ts) -> dt.date | None:
    d = ts if isinstance(ts, dt.datetime) else parse_ts(ts)
    return d.astimezone(tz()).date() if d else None


def today() -> dt.date:
    return now().astimezone(tz()).date()


def week_of(d: dt.date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_range(week: str):
    """返回 (周一, 周日) 的本地日期。"""
    y, w = week.split("-W")
    mon = dt.date.fromisocalendar(int(y), int(w), 1)
    return mon, mon + dt.timedelta(days=6)


def short_id(prefix: str) -> str:
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    return prefix + "-" + "".join(secrets.choice(alphabet) for _ in range(5))


def _path_map():
    """HUB_PATH_MAP='from=to;from2=to2'：在非本机环境（如只挂载了部分文件夹的沙箱）里把路径还原成本机写法。"""
    out = []
    for pair in os.environ.get("HUB_PATH_MAP", "").split(";"):
        if "=" in pair:
            a, b = pair.split("=", 1)
            out.append((os.path.expanduser(a.strip()), b.strip()))
    return out


def expand(p: str) -> str:
    """把配置/事件里的 ~ 路径变成当前环境可访问的真实路径。"""
    p = str(p)
    for real, shown in _path_map():
        if p == shown or p.startswith(shown.rstrip("/") + "/"):
            return real + p[len(shown):]
    return os.path.expanduser(p)


def tilde(p: str) -> str:
    """把真实路径写回成以 ~ 开头的稳定写法，方便跨环境比较。"""
    p = str(p)
    for real, shown in _path_map():
        if p == real or p.startswith(real.rstrip("/") + "/"):
            return shown + p[len(real):]
    home = os.path.expanduser("~")
    if p == home or p.startswith(home + "/"):
        return "~" + p[len(home):]
    if p.startswith("/Users/"):
        parts = p.split("/", 3)
        return "~" + ("/" + parts[3] if len(parts) > 3 else "")
    return p


def read_json(path, default=None):
    path = Path(path)
    for attempt in range(3):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return default
        except json.JSONDecodeError:
            time.sleep(0.05)  # 可能正好有人在写，稍等重试
    return default


def write_json(path, obj) -> None:
    """原地覆盖写。不用「临时文件 + rename」，因为部分挂载环境不允许替换已存在的文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def trim(s, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def log(msg: str) -> None:
    line = f"{now().astimezone(tz()).strftime('%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
