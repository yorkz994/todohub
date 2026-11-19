#!/bin/bash
# 编译并启动 Todo Hub 的菜单栏 / 悬浮窗小程序。可重复运行：每次都会重新编译、替换并重启。
# 产物在本目录的 build/TodoHubBar.app，不往系统别处装东西。
#   --no-launch  只编译不启动（CI 用）
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "${DIR}")"
APP="${DIR}/build/TodoHubBar.app"
PORT="$(python3 -c "import json,os;d=os.path.dirname;c=json.load(open('${ROOT}/config/hub.json'));l='${ROOT}/config/hub.local.json';c.update(json.load(open(l)) if os.path.exists(l) else {});print(c.get('port',8791))" 2>/dev/null || echo 8791)"
CC="$(xcrun -f clang 2>/dev/null || command -v clang || true)"
if [ -z "${CC}" ]; then
  echo "NEED_CLT: 没找到 clang。先运行 xcode-select --install 装命令行工具，再重跑。"
  exit 3
fi
echo "clang: $("${CC}" --version 2>&1 | head -1)"
SDK="$(xcrun --sdk macosx --show-sdk-path 2>/dev/null || true)"
SDKFLAG=()
if [ -n "${SDK}" ]; then SDKFLAG=(-isysroot "${SDK}"); fi
mkdir -p "${APP}/Contents/MacOS"
cat > "${APP}/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>com.todohub.bar</string>
  <key>CFBundleName</key><string>Todo Hub</string>
  <key>CFBundleExecutable</key><string>TodoHubBar</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSUIElement</key><true/>
  <key>NSAppTransportSecurity</key><dict>
    <key>NSAllowsLocalNetworking</key><true/>
    <key>NSAllowsArbitraryLoadsInWebContent</key><true/>
  </dict>
</dict></plist>
PLIST
"${CC}" ${SDKFLAG[@]+"${SDKFLAG[@]}"} -fobjc-arc -O2 -Wall -Wno-deprecated-declarations -mmacosx-version-min=12.0 -framework Cocoa -framework WebKit "${DIR}/TodoHubBar.m" -o "${APP}/Contents/MacOS/TodoHubBar.new" 2>&1 | tail -n 40
mv -f "${APP}/Contents/MacOS/TodoHubBar.new" "${APP}/Contents/MacOS/TodoHubBar"
codesign --force --sign - "${APP}" >/dev/null 2>&1 || true
echo "BUILD_OK"
if [ "${1:-}" = "--no-launch" ]; then exit 0; fi
pkill -x TodoHubBar 2>/dev/null || true
sleep 1
open -g "${APP}" --args "http://127.0.0.1:${PORT}"
sleep 3
if pgrep -x TodoHubBar >/dev/null; then echo "WIDGET_RUNNING pid=$(pgrep -x TodoHubBar | head -1)"; else echo "WIDGET_NOT_RUNNING"; exit 4; fi
