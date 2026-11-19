#!/bin/bash
# 停掉并移除常驻服务。数据（data/、reviews/ 等）和提醒事项里的列表都不动。
LABEL=com.todohub.daemon
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && echo "服务已停" || echo "服务本来就没在跑"
pkill -x TodoHubBar 2>/dev/null && echo "菜单栏小程序已退出" || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist" "$HOME/.local/bin/hub"
echo "已移除自启配置和 hub 软链。"
