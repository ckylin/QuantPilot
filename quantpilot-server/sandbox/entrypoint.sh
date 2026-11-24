#!/bin/sh
# 容器入口：在超时保护下执行服务端注入的 solution.py。
# 资源限制（CPU/内存/PID）由 podman 在运行时施加，不在此处处理。
set -eu

SOLUTION="${SOLUTION_PATH:-/work/solution.py}"
TIMEOUT="${QP_TIMEOUT_SEC:-120}"

if [ ! -f "$SOLUTION" ]; then
    echo "SANDBOX_ERROR: solution file not found: $SOLUTION" >&2
    exit 2
fi

# qlib 数据目录必须存在且非空，避免空数据导致 qlib.init 行为异常。
if [ ! -d "$QLIB_PROVIDER_URI" ] || [ -z "$(ls -A "$QLIB_PROVIDER_URI" 2>/dev/null)" ]; then
    echo "SANDBOX_ERROR: qlib data dir missing or empty: $QLIB_PROVIDER_URI" >&2
    exit 3
fi

# coreutils timeout 在超时后先发 TERM，再在 5s 后 KILL，防止进程残留。
exec timeout --signal=TERM --kill-after=5s "${TIMEOUT}s" \
    python "$SOLUTION"
