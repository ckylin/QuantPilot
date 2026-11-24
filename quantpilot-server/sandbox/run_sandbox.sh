#!/usr/bin/env bash
# 在 podman 沙箱中执行一段生成的 qlib 代码。
#
# 用法：
#   bash sandbox/run_sandbox.sh <solution.py 路径> [task_id] [timeout_sec]
#
# 安全基线（逐项对应规划文档 §3.4 沙箱要求）：
#   --network=none                 无网络，LLM 生成代码无法外联
#   --read-only                    根文件系统只读
#   --tmpfs /tmp                   仅 /tmp 可写（noexec,nosuid,nodev）
#   -v data:/data/qlib:ro,Z        行情数据只读挂载 + SELinux 重打标
#   -v workdir:/work:ro,Z          生成代码只读挂载
#   --cap-drop=ALL                 丢弃全部 Linux capabilities
#   --security-opt no-new-privileges
#   --userns=keep-id               以当前用户身份运行（rootless podman）
#   --memory / --cpus / --pids-limit / --ulimit nofile
#   --entrypoint 保持镜像内 timeout 强杀
#   --rm                           退出即销毁容器

set -euo pipefail

SOLUTION_FILE="${1:?用法: run_sandbox.sh <solution.py> [task_id] [timeout_sec]}"
TASK_ID="${2:-task}"
TIMEOUT_SEC="${3:-120}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

IMAGE="${QP_SANDBOX_IMAGE:-quantpilot/sandbox:0.1}"
DATA_DIR="${QP_QLIB_DATA_DIR:-$SCRIPT_DIR/data/cn_data}"
WORK_DIR="$(cd "$(dirname "$SOLUTION_FILE")" && pwd)"
SOLUTION_NAME="$(basename "$SOLUTION_FILE")"

# --- 定位 podman 可执行文件 ---
# Windows 刚装完时当前终端 PATH 可能未刷新，故补充常见安装路径探测。
PODMAN_BIN="${QP_PODMAN_BIN:-}"
if [ -z "$PODMAN_BIN" ]; then
    if command -v podman >/dev/null 2>&1; then
        PODMAN_BIN="$(command -v podman)"
    else
        for candidate in \
            "/c/Program Files/RedHat/Podman/podman.exe" \
            "$LOCALAPPDATA/Programs/Podman/podman.exe" \
            "$USERPROFILE/AppData/Local/Programs/Podman/podman.exe"; do
            if [ -x "$candidate" ]; then
                PODMAN_BIN="$candidate"
                break
            fi
        done
    fi
fi
if [ -z "$PODMAN_BIN" ]; then
    echo "错误：未找到 podman，请安装后重开终端，或设置 QP_PODMAN_BIN" >&2
    exit 1
fi

# --- 挂载路径转换 ---
# Windows 上本脚本运行在 Git Bash(MSYS)，路径形如 /c/Users/...；
# podman machine(WSL) 挂载卷需要 Windows 原生路径 C:\Users\...。
# Linux 上原样返回。
to_podman_path() {
    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*)
            if command -v cygpath >/dev/null 2>&1; then
                cygpath -w "$1"
            else
                # cygpath 缺失时的兜底：/c/foo -> C:/foo
                echo "$1" | sed -E 's#^/([a-zA-Z])/#\1:/#'
            fi
            ;;
        *)
            echo "$1"
            ;;
    esac
}
DATA_DIR_PODMAN="$(to_podman_path "$DATA_DIR")"
WORK_DIR_PODMAN="$(to_podman_path "$WORK_DIR")"
if [ ! -d "$DATA_DIR" ] || [ -z "$(ls -A "$DATA_DIR" 2>/dev/null)" ]; then
    echo "错误：qlib 数据目录不存在或为空: $DATA_DIR" >&2
    echo "请先运行: bash sandbox/prepare_data.sh" >&2
    exit 1
fi
if [ ! -f "$SOLUTION_FILE" ]; then
    echo "错误：找不到 solution 文件: $SOLUTION_FILE" >&2
    exit 1
fi

# SELinux 环境下 :Z 重打标需要；非 SELinux 系统 podman 会忽略，安全起见仅在支持时加。
SELINUX_OPT=""
if [ -d /sys/fs/selinux ] && command -v selinuxenabled >/dev/null 2>&1 && selinuxenabled; then
    SELINUX_OPT=",Z"
fi

exec "$PODMAN_BIN" run --rm \
    --name "qp-sandbox-${TASK_ID}" \
    --network=none \
    --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=256m \
    --cap-drop=ALL \
    --security-opt no-new-privileges:true \
    --userns=keep-id \
    --memory="${QP_MEM_LIMIT:-2g}" \
    --memory-swap="${QP_MEM_LIMIT:-2g}" \
    --cpus="${QP_CPU_LIMIT:-2}" \
    --pids-limit="${QP_PIDS_LIMIT:-128}" \
    --ulimit nofile=256:256 \
    --ulimit fsize=104857600:104857600 \
    -e "QP_TIMEOUT_SEC=${TIMEOUT_SEC}" \
    -e "QLIB_PROVIDER_URI=/data/qlib" \
    -v "${DATA_DIR_PODMAN}:/data/qlib:ro${SELINUX_OPT}" \
    -v "${WORK_DIR_PODMAN}:/work:ro${SELINUX_OPT}" \
    -e "SOLUTION_PATH=/work/${SOLUTION_NAME}" \
    "${IMAGE}"
