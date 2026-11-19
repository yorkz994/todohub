#!/bin/bash
# Todo Hub 本机安装（macOS）。做四件事，可重复运行：
#   1. 注册一个登录后自启的常驻服务（launchd），负责看板、采集、提醒事项同步、swarm 执行
#   2. 把 hub 命令软链到 ~/.local/bin
#   3. 跑第一次采集 / 扫描 / 提醒事项同步（会弹一次「允许控制提醒事项」）
#   4. 打开看板
# 不用 sudo，不改 shell 配置。卸载：bash uninstall.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL=com.todohub.daemon
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$(command -v python3 || true)"
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "需要 Python 3.9 或更高（brew install python）"; exit 1
fi
if [ "$(uname)" != "Darwin" ]; then echo "这个脚本只用于 macOS"; exit 1; fi
PORT="$("$PY" -c "import json,os;d=os.path.dirname;c=json.load(open('$DIR/config/hub.json'));l='$DIR/config/hub.local.json';c.update(json.load(open(l)) if os.path.exists(l) else {});print(c.get('port',8791))")"

# launchd 起的进程拿不到你 shell 里的 PATH，这里把常见位置和 codex / claude 实际所在目录都带上
SVC_PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$HOME/.bun/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# 注意：变量后面紧跟中文时一律写成 ${var}。macOS 自带的 bash 3.2 会把中文字符的第一个字节当成变量名的一部分。
for b in codex claude zstd git; do
  p="$(command -v "${b}" 2>/dev/null || true)"
  if [ -n "${p}" ]; then
    SVC_PATH="$(dirname "${p}"):${SVC_PATH}"
  elif [ "${b}" = zstd ]; then
    echo "提示：没找到 zstd 命令。旧的 Codex 会话是压缩存放的，要读它们需要先 brew install zstd"
  else
    echo "提示：没找到 ${b} 命令，派给它的任务会先卡住，装好后重跑本脚本即可"
  fi
done

mkdir -p "$DIR/logs" "$HOME/Library/LaunchAgents" "$HOME/.local/bin"
chmod +x "$DIR/hub"
ln -sf "$DIR/hub" "$HOME/.local/bin/hub"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$DIR/hub</string><string>daemon</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$SVC_PATH</string>
    <key>HUB_ACTOR</key><string>daemon</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/logs/daemon.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/daemon.log</string>
</dict></plist>
PLIST

echo "== 首次采集与扫描（前台跑一遍，方便看到报错）"
"$PY" "$DIR/hub" collect || true
"$PY" "$DIR/hub" repos scan >/dev/null && echo "仓库扫描完成" || true
echo "== 首次同步提醒事项：系统会问是否允许控制「提醒事项」，点允许"
"$PY" "$DIR/hub" reminders sync || echo "提醒事项同步没成功，稍后在看板左下角点「同步提醒事项」重试"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
UP=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fs --noproxy '*' "http://127.0.0.1:${PORT}/api/state" >/dev/null 2>&1; then UP=1; break; fi
  sleep 1
done
if [ -n "$UP" ]; then
  echo "== 常驻服务已启动：http://127.0.0.1:$PORT （日志 $DIR/logs/daemon.log）"
  open "http://127.0.0.1:$PORT"
else
  echo "服务没有起来，看 $DIR/logs/daemon.log"; exit 1
fi
"$PY" "$DIR/hub" status
echo "== 编译并启动菜单栏 / 悬浮窗小程序"
bash "$DIR/widget/build.sh" || echo "小程序没编译成功（看上面的输出）；看板和其余功能不受影响"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "提示：~/.local/bin 不在 PATH 里，用 $DIR/hub 或自己加一下 PATH";; esac
echo "注意：常驻服务第一次自己同步提醒事项时，系统可能再弹一次授权（这次是给后台的 python），同样点允许。"
