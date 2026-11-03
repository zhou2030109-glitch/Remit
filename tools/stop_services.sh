#!/usr/bin/env bash
# Remit POSIX 服务停止器（macOS / Linux）。与 tools/stop_services.ps1 保持
# 相同语义：优先按 logs/<name>.pid 记录停止；PID 文件缺失时按端口发现监听
# 进程，并只停止属于本项目的进程链，外部进程仅告警不误杀。

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/logs"

REDIS_PORT=16379
BACKEND_PORT=18000
FRONTEND_PORT=15173

if ! command -v lsof >/dev/null 2>&1; then
	echo "[ERROR] 未找到 lsof，无法安全判断端口进程归属。" >&2
	exit 1
fi

# macOS 自带 BSD tail 用 -r 反转行序，GNU coreutils 环境回退到 tac。
if tail -r </dev/null >/dev/null 2>&1; then
	REVERSER="tail -r"
else
	REVERSER="tac"
fi

port_pids() {
	lsof -nP -t -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | sort -u
}

pid_command() {
	ps -p "$1" -o command= 2>/dev/null | head -n 1
}

pid_alive() {
	ps -p "$1" >/dev/null 2>&1
}

# 进程工作目录（lsof -Fn 输出形如 n/Users/.../Remit/frontend）。
pid_cwd() {
	lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1
}

is_recorded_service_process() {
	local service="$1" pid="$2" cwd cmd expected_cwd
	cwd="$(pid_cwd "$pid")"
	cmd="$(pid_command "$pid")"
	case "$service" in
	redis)
		expected_cwd="$ROOT"
		case "$cmd" in *redis-server*) ;; *) return 1 ;; esac
		;;
	backend)
		expected_cwd="$ROOT/backend"
		case "$cmd" in *uvicorn*app.main:app*) ;; *) return 1 ;; esac
		;;
	frontend)
		expected_cwd="$ROOT/frontend"
		case "$cmd" in *pnpm*run*dev* | *vite*15173*) ;; *) return 1 ;; esac
		;;
	*) return 1 ;;
	esac
	[ "$cwd" = "$expected_cwd" ]
}

# 输出该 PID 及其全部后代（父在前），供自底向上终止。
collect_tree() {
	local pid="$1" child
	echo "$pid"
	for child in $(pgrep -P "$pid" 2>/dev/null); do
		collect_tree "$child"
	done
}

# $1=PID $2=服务名: 先整组 TERM，宽限 5 秒后对残留进程 KILL。
kill_tree() {
	local pid="$1" name="$2" list p
	list="$(collect_tree "$pid" | $REVERSER)"
	[ -n "$list" ] || return 0
	for p in $list; do
		kill -TERM "$p" 2>/dev/null || true
	done
	local waited=0
	while [ "$waited" -lt 50 ]; do
		pid_alive "$pid" || break
		sleep 0.1
		waited=$((waited + 1))
	done
	for p in $list; do
		kill -KILL "$p" 2>/dev/null || true
	done
	echo "[STOPPED] $name (PID $pid)"
}

service_port() {
	case "$1" in
	frontend) echo "$FRONTEND_PORT" ;;
	backend) echo "$BACKEND_PORT" ;;
	redis) echo "$REDIS_PORT" ;;
	esac
}

overall_status=0

# redis 仅用于回收旧版本启动的遗留进程。
for service in frontend backend redis; do
	port="$(service_port "$service")"
	pid_file="$LOG_DIR/$service.pid"

	if [ -f "$pid_file" ]; then
		pid="$(tr -d '[:space:]' <"$pid_file")"
		rm -f "$pid_file"
		if [ -z "$pid" ] || ! pid_alive "$pid"; then
			echo "[OK] $service is already stopped."
			continue
		fi
		# PID 可能已被系统回收复用：确认仍属于本项目才按 PID 终止；
		# 无法确认时继续落到按端口发现，避免误杀也避免漏杀。
		cmd="$(pid_command "$pid")"
		if [ -n "$cmd" ] && is_recorded_service_process "$service" "$pid"; then
			kill_tree "$pid" "$service"
			continue
		fi
	fi

	pids="$(port_pids "$port" || true)"
	if [ -z "$pids" ]; then
		echo "[OK] $service is already stopped."
		continue
	fi

	# 没有可信 PID 记录时绝不按端口或 cwd 猜测归属。宁可提示用户手动
	# 处理残留，也不能误停恰好使用同一端口、命令或目录的外部进程。
	echo "[WARN] $service uses port $port, but it was not started from this project; leaving it running."
	overall_status=1
done

exit "$overall_status"
