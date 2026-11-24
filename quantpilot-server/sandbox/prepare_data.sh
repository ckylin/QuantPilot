#!/usr/bin/env bash
# 准备 qlib 二进制行情数据（A 股日频）。
#
# 背景：qlib 官方 Azure 数据包已下线（HTTP 409），改用社区维护的
#       chenditc/investment_data 每日发布包。该数据源与 qlib 官方格式兼容，
#       由 tushare/akshare 等众包数据合并校验而成。
#
# 用法：
#   bash sandbox/prepare_data.sh [目标目录]
# 默认目标目录：./sandbox/data/cn_data（与本仓库 run_sandbox.sh 的默认挂载点一致）
#
# 注意：数据仅本地持有，不对外分发；合规声明见 docs/contracts.md。

set -euo pipefail

TARGET_DIR="${1:-$(cd "$(dirname "$0")" && pwd)/data/cn_data}"
RELEASE_PATH="chenditc/investment_data/releases/latest/download/qlib_bin.tar.gz"
RELEASE_URL="https://github.com/${RELEASE_PATH}"

# 下载源列表：官方直连优先，国内网络不通时自动回退到 GitHub 镜像。
# 可通过 QP_DATA_URL 覆盖为任意可下载地址（如内网 NAS 上的数据包）。
DOWNLOAD_URLS=(
    "${QP_DATA_URL:-}"
    "$RELEASE_URL"
    "https://ghfast.top/https://github.com/${RELEASE_PATH}"
)

mkdir -p "$TARGET_DIR"

# 支持复用已下载的数据包，避免重复下载（网络受限时尤其有用）。
# 用法：QP_DATA_ARCHIVE=/path/to/qlib_bin.tar.gz bash prepare_data.sh
if [ -n "${QP_DATA_ARCHIVE:-}" ]; then
    if [ ! -f "$QP_DATA_ARCHIVE" ]; then
        echo "错误：QP_DATA_ARCHIVE 指向的文件不存在: $QP_DATA_ARCHIVE" >&2
        exit 1
    fi
    echo "使用已有数据包: $QP_DATA_ARCHIVE"
    TMP_ARCHIVE="$QP_DATA_ARCHIVE"
    REUSE_ARCHIVE=1
else
    # 注意：Windows(Git Bash) 下 mktemp -t 返回 C:\... 路径，tar 会误判为远程主机。
    # 用 TMPDIR 显式指定 Git Bash 路径，保证 tar 能识别。
    TMP_ARCHIVE="$(mktemp -p "${TMPDIR:-/tmp}" qlib_bin.XXXXXX.tar.gz)"
    REUSE_ARCHIVE=0
    trap 'rm -f "$TMP_ARCHIVE"' EXIT
fi

if [ "$REUSE_ARCHIVE" -eq 0 ]; then
    echo "[1/3] 下载 qlib 数据包"
    echo "      目标目录: $TARGET_DIR"
    DOWNLOADED=0
    for url in "${DOWNLOAD_URLS[@]}"; do
        [ -z "$url" ] && continue
        echo "      尝试源: $url"
        if curl -fL --retry 2 --retry-delay 2 --max-time 1800 -o "$TMP_ARCHIVE" "$url"; then
            DOWNLOADED=1
            break
        fi
        echo "      该源失败，尝试下一个..." >&2
    done
    if [ "$DOWNLOADED" -ne 1 ]; then
        echo "错误：所有数据源均下载失败。可设置 QP_DATA_URL 指向自有镜像。" >&2
        exit 1
    fi
else
    echo "[1/3] 跳过下载（使用 QP_DATA_ARCHIVE）"
fi

# --force-local：Windows 上 tar 会把 C:\... 中的冒号误判为远程主机分隔符，
# 强制视为本地路径可规避（Linux 下该参数无害）。
echo "[2/3] 校验数据包"
if ! tar --force-local -tzf "$TMP_ARCHIVE" >/dev/null 2>&1; then
    echo "错误：数据包不是有效的 tar.gz（可能下载不完整）" >&2
    exit 1
fi

echo "[3/3] 解压（strip-components=1）"
tar --force-local -zxf "$TMP_ARCHIVE" -C "$TARGET_DIR" --strip-components=1

# 校验 qlib 数据目录结构
for required in calendars instruments features; do
    if [ ! -e "$TARGET_DIR/$required" ]; then
        echo "错误：解压后缺少必要目录 $TARGET_DIR/$required" >&2
        exit 1
    fi
done

echo "完成。qlib.init(provider_uri=\"$TARGET_DIR\", region='cn') 即可使用。"
echo "提示：如需更新数据，重新运行本脚本；tar 包解压会覆盖同名文件。"
