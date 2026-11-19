#!/usr/bin/env python3
"""公开前的隐私检查：扫描将要进仓库的每个文件，发现个人数据、密钥或私人词就报错（退出码 1）。

    python3 scripts/audit_public.py            检查 git 暂存区里的文件（没有暂存就检查全部已跟踪文件）
    python3 scripts/audit_public.py --tracked  检查全部已跟踪文件
    python3 scripts/audit_public.py --email    另外要求 git 提交邮箱是 GitHub 匿名邮箱
    python3 scripts/audit_public.py --history  另外检查全部提交历史：提交说明、作者 / 提交者邮箱、每次新增的每一行

私人词清单在 config/private-words.local.txt（一行一个，# 开头是注释），这个文件本身不进仓库。
只用标准库。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORDS_FILE = ROOT / "config" / "private-words.local.txt"

# 任何情况下都不能进仓库的路径
FORBIDDEN_PATH = re.compile(r"^(data|inbox|reviews|logs|swarm|workspace)/|^widget/build/|\.local(\.[a-z]+)?$|"
                            r"(^|/)\.env($|\.)|\.(pem|p12|key)$|(^|/)id_(rsa|ed25519)|COMMIT_MSG$")
# 看起来像密钥、令牌、本机路径的内容
SECRET_PATTERNS = [
    ("GitHub 令牌", re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}")),
    ("OpenAI / Anthropic 密钥", re.compile(r"\bsk-(ant-)?[A-Za-z0-9_-]{20,}")),
    ("AWS 访问密钥", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack 令牌", re.compile(r"\bxox[abpors]-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("私钥", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("带凭证的地址", re.compile(r"[a-z]+://[^/\s:@'\"]+:[^/\s@'\"]+@")),
    ("带令牌的地址", re.compile(r"https://[A-Za-z0-9_]{8,}@github\.com")),
    ("本机用户目录", re.compile(r"/(Users|home)/(?!you\b|me\b|runner\b|user\b|claude\b|<)[A-Za-z0-9._-]+/")),
    ("邮箱地址", re.compile(r"\b(?<!git@)(?!git@)[A-Za-z0-9._%+-]+@(?!users\.noreply\.github\.com|github\.com|anthropic\.com|example\.(com|org))"
                          r"[A-Za-z0-9.-]+\.[a-z]{2,}\b")),
]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def load_words() -> list[tuple[str, re.Pattern]]:
    if not WORDS_FILE.exists():
        return []
    out = []
    for line in WORDS_FILE.read_text(encoding="utf-8").splitlines():
        w = line.strip()
        if not w or w.startswith("#"):
            continue
        if re.fullmatch(r"[\x00-\x7f]+", w):
            pat = re.compile(r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])", re.I)
        else:
            pat = re.compile(re.escape(w), re.I)
        out.append((w, pat))
    return out


OK_EMAIL = re.compile(r"(@users\.noreply\.github\.com|^noreply@anthropic\.com|^noreply@github\.com|"
                      r"^\d+\+[\w.-]+\[bot\]@users\.noreply\.github\.com)$")


def scan_text(where: str, line: str, words) -> list[str]:
    out = []
    for label, pat in SECRET_PATTERNS:
        if pat.search(line):
            out.append(f"{where}: {label}")
    for w, pat in words:
        if pat.search(line):
            out.append(f"{where}: 私人词「{w}」")
    return out


def audit_history(words) -> list[str]:
    """提交说明、作者 / 提交者邮箱、历史上新增过的每一行（含已删除的文件）。"""
    problems = []
    sep = "\x1e"
    log = git("log", "--all", f"--format=%h{sep}%ae{sep}%ce{sep}%B%x1f")
    for rec in log.split("\x1f"):
        rec = rec.strip("\n")
        if not rec:
            continue
        h, ae, ce, msg = (rec.split(sep, 3) + ["", "", ""])[:4]
        for e in {ae, ce}:
            if e and not OK_EMAIL.search(e):
                problems.append(f"提交 {h}: 作者 / 提交者邮箱 {e} 不是匿名邮箱")
        for n, line in enumerate(msg.splitlines(), 1):
            problems += scan_text(f"提交 {h} 的说明第 {n} 行", line, words)
    cur, path = "", ""
    for line in git("log", "--all", "-p", "--no-color", "--format=commit %h", "--unified=0").splitlines():
        if line.startswith("commit "):
            cur = line.split()[1]
        elif line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else line[4:]
            if path != "/dev/null" and FORBIDDEN_PATH.search(path):
                problems.append(f"提交 {cur}: 历史里出现过不该进仓库的文件 {path}")
        elif line.startswith("+") and not line.startswith("+++"):
            problems += scan_text(f"提交 {cur} {path}", line[1:], words)
    return problems


def main() -> int:
    args = set(sys.argv[1:])
    staged = [] if "--tracked" in args else [f for f in git("diff", "--cached", "--name-only", "--diff-filter=ACMR").splitlines() if f]
    files = staged or [f for f in git("ls-files").splitlines() if f]
    words = load_words()
    problems = []
    for f in files:
        if FORBIDDEN_PATH.search(f):
            problems.append(f"{f}: 这个路径不能进仓库")
            continue
        p = ROOT / f
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # 二进制文件跳过
        for n, line in enumerate(text.splitlines(), 1):
            problems += scan_text(f"{f}:{n}", line, words)
    if "--history" in args:
        problems += audit_history(words)
    if "--email" in args:
        email = git("config", "user.email").strip()
        if not email.endswith("@users.noreply.github.com"):
            problems.append(f"git 提交邮箱不是 GitHub 匿名邮箱：{email or '（未设置）'}")
    print(f"AUDIT 检查 {len(files)} 个文件，私人词 {len(words)} 个" + ("" if words else "（没有 config/private-words.local.txt，只做通用检查）"))
    if problems:
        for p in problems:
            print("  ✗ " + p)
        print(f"AUDIT_FAIL {len(problems)} 处")
        return 1
    print("AUDIT_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
