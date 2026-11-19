#!/bin/bash
# 提交并推送到 GitHub。可重复运行：第一次建仓库，之后只提交并推送新改动。
# 每次提交前都跑隐私检查（scripts/audit_public.py：文件内容 + 全部提交历史 + 提交邮箱），不过就不提交、不推送。
# 用本机已有的 git / gh 登录；不会 force push，不会改仓库的公开 / 私有设置。
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${DIR}"
REPO_NAME="${REPO_NAME:-todohub}"
command -v git >/dev/null || { echo "NEED_GIT: 本机没有 git"; exit 3; }
[ -d .git ] || git init -q -b main

# 提交身份用 GitHub 匿名邮箱，真实邮箱不会写进公开的提交记录
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  LOGIN="$(gh api user --jq .login)"; GID="$(gh api user --jq .id)"
  git config user.name "${LOGIN}"
  git config user.email "${GID}+${LOGIN}@users.noreply.github.com"
fi

MSG="更新"
if [ -s COMMIT_MSG ]; then MSG="$(cat COMMIT_MSG)"; fi

git add -A
if ! python3 scripts/audit_public.py --email --history; then
  git reset -q
  echo "ABORT_AUDIT: 隐私检查没通过，什么都没提交。按上面列出的位置改掉，或者把误报从 config/private-words.local.txt 里去掉。"
  exit 5
fi
if git diff --cached --quiet; then
  echo "NOTHING_TO_COMMIT"
else
  # 可选：提交说明末尾的固定署名行，写在 config/commit-trailer.local.txt（不进仓库）
  if [ -s config/commit-trailer.local.txt ]; then
    git commit -q -m "${MSG}" -m "$(cat config/commit-trailer.local.txt)"
  else
    git commit -q -m "${MSG}"
  fi
  rm -f COMMIT_MSG
  echo "COMMITTED $(git rev-parse --short HEAD) files=$(git ls-files | wc -l | tr -d ' ')"
fi

if git remote get-url origin >/dev/null 2>&1; then
  git push -q -u origin main
  echo "PUSHED $(git remote get-url origin)"
elif command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  gh repo create "${REPO_NAME}" --private --source=. --remote=origin --push \
    --description "Local-first personal todo / project dashboard with a Claude-orchestrated agent swarm and a macOS menu-bar widget" >/dev/null
  echo "CREATED_PRIVATE_AND_PUSHED $(git remote get-url origin)"
else
  echo "NEED_GH: 没有远端仓库，本机也没有登录的 gh。装好 gh 并 gh auth login 后重跑。已在本地提交。"
  exit 4
fi
