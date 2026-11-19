"""Tests for scripts/audit_public.py: builds throwaway git repos with planted leaks. Run: python3 tests/test_audit.py"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
ok = 0


def check(cond, msg):
    global ok
    assert cond, "FAIL: " + msg
    ok += 1


def sh(cwd, *args, email="1+dev@users.noreply.github.com"):
    env = dict(os.environ, GIT_AUTHOR_NAME="dev", GIT_COMMITTER_NAME="dev", GIT_AUTHOR_EMAIL=email,
               GIT_COMMITTER_EMAIL=email)
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)


def repo():
    d = Path(tempfile.mkdtemp(prefix="audit-"))
    sh(d, "git", "init", "-q")
    (d / "scripts").mkdir()
    (d / "config").mkdir()
    shutil.copy(SRC / "scripts" / "audit_public.py", d / "scripts" / "audit_public.py")
    (d / ".gitignore").write_text("*.local.txt\n")
    (d / "config" / "private-words.local.txt").write_text("# comment\nSecretCo\n秘密项目\n")
    sh(d, "git", "add", "-A")
    sh(d, "git", "commit", "-q", "-m", "init")
    return d


def audit(d, *flags):
    r = sh(d, sys.executable, "scripts/audit_public.py", *flags)
    return r.returncode, r.stdout


# 1. clean repo passes, including history
d = repo()
rc, out = audit(d, "--tracked", "--history")
check(rc == 0 and "AUDIT_OK" in out, "clean repo passes")

# 2. content leaks in tracked files
(d / "notes.md").write_text("call SecretCo tomorrow\n秘密项目进展\n")
(d / "cfg.py").write_text('TOKEN = "ghp_' + "a" * 30 + '"\nPATH = "/Users/' + 'alice/code"\nMAIL = "alice@' + 'corp.io"\n')
sh(d, "git", "add", "-A")
rc, out = audit(d)
check(rc == 1, "staged leaks fail")
for needle in ("SecretCo", "秘密项目", "GitHub 令牌", "本机用户目录", "邮箱地址"):
    check(needle in out, f"reports {needle}")
check("deliberate" not in out, "no stray output")

# 3. ASCII words match whole words only
(d / "notes.md").write_text("SecretCorp is a different word\n")
(d / "cfg.py").write_text("x = 1\n")
sh(d, "git", "add", "-A")
rc, out = audit(d)
check(rc == 0, f"whole-word matching for ASCII terms: {out}")

# 4. forbidden paths
(d / "data").mkdir()
(d / "data" / "e.json").write_text("{}")
(d / "hub.local.json").write_text("{}")
sh(d, "git", "add", "-f", "data/e.json", "hub.local.json")
rc, out = audit(d)
check(rc == 1 and "data/e.json" in out and "hub.local.json" in out, "personal data paths are blocked")
shutil.rmtree(d)

# 5. history: leaks that were deleted, bad emails, commit messages
d = repo()
(d / "old.md").write_text("SecretCo numbers\n")
sh(d, "git", "add", "-A")
sh(d, "git", "commit", "-q", "-m", "add notes", email="someone@" + "gmail.com")
sh(d, "git", "rm", "-q", "old.md")
sh(d, "git", "commit", "-q", "-m", "remove notes about SecretCo")
rc, out = audit(d, "--tracked")
check(rc == 0, "current files alone look clean")
rc, out = audit(d, "--tracked", "--history")
check(rc == 1, "history audit fails")
check("someone@" + "gmail.com" in out, "reports non-noreply author email")
check("old.md" in out and "说明" in out, "reports deleted file content and commit message")
shutil.rmtree(d)

# 6. git@host remotes and allowed addresses are not reported as emails
d = repo()
(d / "x.sh").write_text('git remote add origin "git@github.com:owner/repo.git"\n# noreply@anthropic.com\n'
                       '# Signed-off-by: dependabot[bot] <support@github.com>\n')
sh(d, "git", "add", "-A")
rc, out = audit(d)
check(rc == 0, f"git@ remotes and noreply addresses allowed: {out}")
shutil.rmtree(d)

print(f"all passed: {ok} checks")
