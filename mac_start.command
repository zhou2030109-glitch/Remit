#!/bin/bash
# Remit macOS 双击启动入口（对应 Windows 的 win_start.bat）。
# Finder 双击本文件即可在终端中启动 FastAPI + Vue 两个服务。
cd "$(dirname "$0")" || exit 1

bash tools/start_services.sh "$@"
code=$?
if [ "$code" -ne 0 ]; then
	echo ""
	echo "[ERROR] 启动失败，请查看上方提示。"
	read -n 1 -s -r -p "按任意键关闭窗口..."
fi
exit "$code"
