#!/usr/bin/env bash
# QuantPilot · PostgreSQL 本地实例（podman）
#
# 用法：
#   ./scripts/pg.sh up       启动（幂等：已有容器只 start）
#   ./scripts/pg.sh down     停止（保留数据卷）
#   ./scripts/pg.sh status   容器 / 端口 / 表数量
#   ./scripts/pg.sh logs     看日志
#   ./scripts/pg.sh psql     进交互终端
#   ./scripts/pg.sh reset    删容器 + 删数据卷（不可恢复，需 --yes）
#
# 设计取舍：
#   * 用**命名卷**而不是绑 Windows 目录。PG 初始化要对数据目录 chmod 700，
#     而 Windows 文件系统（virtiofs）不认 POSIX 权限，绑过去必然启动失败。
#     命名卷在 podman machine 的 ext4 里，权限语义正常。
#   * locale 固定 C / UTF8。跨机器、跨版本 collation 一致，避免 ORDER BY
#     和索引行为随环境漂移（股票代码/名称用字节序排序完全够用）。
#   * 端口默认 5432，可用 QP_PG_PORT 覆盖。

set -uo pipefail

NAME="${QP_PG_NAME:-quantpilot-pg}"
VOLUME="${QP_PG_VOLUME:-quantpilot-pgdata}"
IMAGE="${QP_PG_IMAGE:-docker.io/library/postgres:16-alpine}"
PORT="${QP_PG_PORT:-5432}"
DB_USER="${QP_PG_USER:-quantpilot}"
DB_PASS="${QP_PG_PASSWORD:-quantpilot_dev}"
DB_NAME="${QP_PG_DB:-quantpilot}"

info() { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m  ok\033[0m %s\n' "$*"; }
warn() { printf '\033[33m  !!\033[0m %s\n' "$*"; }
die()  { printf '\033[31m error\033[0m %s\n' "$*" >&2; exit 1; }

find_podman() {
    if [ -n "${QP_PODMAN_BIN:-}" ]; then
        local p="$QP_PODMAN_BIN"
        # .env 里习惯写 Windows 反斜杠路径，这里换成 bash 认的 /c/... 形式
        case "$p" in
            *\\*) command -v cygpath >/dev/null 2>&1 && p="$(cygpath -u "$p")" ;;
        esac
        [ -x "$p" ] && { printf '%s' "$p"; return 0; }
    fi
    command -v podman >/dev/null 2>&1 && { command -v podman; return 0; }
    local c
    for c in \
        "${LOCALAPPDATA:-$HOME/AppData/Local}/Programs/Podman/podman.exe" \
        "/c/Program Files/RedHat/Podman/podman.exe" \
        "/c/Program Files/Podman/podman.exe" \
        "/usr/bin/podman"; do
        [ -x "$c" ] && { printf '%s' "$c"; return 0; }
    done
    return 1
}

PODMAN="$(find_podman)" || die "找不到 podman。装一个，或在环境里设 QP_PODMAN_BIN 指向 podman 可执行文件。"

# podman 只是客户端：真正跑容器的是背后那台 Linux 虚拟机（Windows 上是 WSL）。
# machine 没起来时所有命令都会报 "cannot connect to Podman socket"。
require_machine() {
    if ! "$PODMAN" info >/dev/null 2>&1; then
        cat >&2 <<EOF
$(printf '\033[31m error\033[0m') 连不上 podman：容器引擎没在跑。

Windows 上的 podman 依赖一台 WSL 虚拟机，先把它拉起来（在**你自己的终端**里执行）：

    podman machine start

如果提示 machine 不存在：

    podman machine init
    podman machine start

起完再回来跑：./scripts/pg.sh up
EOF
        exit 1
    fi
    ok "podman 可用（$("$PODMAN" --version)）"
}

container_exists() { "$PODMAN" container exists "$NAME" 2>/dev/null; }
container_running() { [ "$("$PODMAN" inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" = "true" ]; }

ensure_volume() {
    if "$PODMAN" volume exists "$VOLUME" 2>/dev/null; then
        ok "数据卷 $VOLUME 已存在"
    else
        "$PODMAN" volume create "$VOLUME" >/dev/null || die "创建数据卷失败"
        ok "数据卷 $VOLUME 已创建"
    fi
}

ensure_port_free() {
    if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
        exec 3<&- 2>/dev/null
        die "本机 $PORT 已被占用（可能已经有一个 PG 在跑）。换端口：QP_PG_PORT=5433 ./scripts/pg.sh up"
    fi
}

wait_ready() {
    local i=0
    info "等数据库就绪…"
    while [ $i -lt 60 ]; do
        if "$PODMAN" exec "$NAME" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
            ok "数据库就绪（约 ${i}s）"
            return 0
        fi
        i=$((i + 1))
        sleep 1
    done
    warn "60 秒还没就绪，看日志：./scripts/pg.sh logs"
    return 1
}

print_dsn() {
    cat <<EOF

  连接串（写进 quantpilot-server/.env）：

    QP_DATABASE_URL=postgresql+psycopg://${DB_USER}:${DB_PASS}@127.0.0.1:${PORT}/${DB_NAME}

  建表：

    python -m app.db.cli init-db --seed

  改完 .env 要重启 uvicorn 才生效。

EOF
}

cmd_up() {
    require_machine
    ensure_volume

    if container_exists; then
        if container_running; then
            ok "容器 $NAME 已在运行"
        else
            info "容器存在但已停止，启动它"
            "$PODMAN" start "$NAME" >/dev/null || die "启动失败"
            ok "已启动"
        fi
    else
        ensure_port_free
        info "创建容器 $NAME（镜像 $IMAGE，首次会拉取）"
        "$PODMAN" run -d \
            --name "$NAME" \
            --restart=unless-stopped \
            -e POSTGRES_USER="$DB_USER" \
            -e POSTGRES_PASSWORD="$DB_PASS" \
            -e POSTGRES_DB="$DB_NAME" \
            -e POSTGRES_INITDB_ARGS="--encoding=UTF8 --locale=C" \
            -e TZ=Asia/Shanghai \
            -v "${VOLUME}:/var/lib/postgresql/data:Z" \
            -p "127.0.0.1:${PORT}:5432" \
            --health-cmd="pg_isready -U ${DB_USER} -d ${DB_NAME}" \
            --health-interval=10s \
            --health-timeout=3s \
            --health-retries=6 \
            "$IMAGE" >/dev/null || die "podman run 失败，用 ./scripts/pg.sh logs 或 podman logs $NAME 看原因"
        ok "容器已创建"
    fi

    wait_ready || exit 1
    print_dsn
}

cmd_down() {
    require_machine
    container_exists || { warn "容器 $NAME 不存在"; return 0; }
    "$PODMAN" stop "$NAME" >/dev/null 2>&1 || true
    ok "已停止（数据卷 $VOLUME 保留，下次 up 接着用）"
}

cmd_status() {
    require_machine
    if ! container_exists; then
        warn "容器 $NAME 不存在，先跑 ./scripts/pg.sh up"
        return 0
    fi
    local state health
    state="$("$PODMAN" inspect -f '{{.State.Status}}' "$NAME" 2>/dev/null)"
    health="$("$PODMAN" inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}}' "$NAME" 2>/dev/null)"
    printf '  容器   %s\n  状态   %s\n  健康   %s\n  端口   127.0.0.1:%s\n  镜像   %s\n  数据卷 %s\n' \
        "$NAME" "$state" "$health" "$PORT" "$IMAGE" "$VOLUME"
    if [ "$state" = "running" ]; then
        local tables
        tables="$("$PODMAN" exec "$NAME" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
            "select count(*) from information_schema.tables where table_schema='public'" 2>/dev/null || echo '?')"
        printf '  表数量 %s\n' "$tables"
    fi
}

cmd_logs() {
    require_machine
    "$PODMAN" logs -f --tail 80 "$NAME"
}

cmd_psql() {
    require_machine
    container_running || die "容器没在跑，先 ./scripts/pg.sh up"
    exec "$PODMAN" exec -it "$NAME" psql -U "$DB_USER" -d "$DB_NAME"
}

cmd_reset() {
    require_machine
    if [ "${2:-}" != "--yes" ]; then
        cat >&2 <<EOF
$(printf '\033[33m  !!\033[0m') 这会删掉容器 $NAME 和**整个数据卷 $VOLUME**，库里所有数据不可恢复。

  真要重置：./scripts/pg.sh reset --yes
EOF
        exit 1
    fi
    "$PODMAN" rm -f "$NAME" >/dev/null 2>&1 || true
    "$PODMAN" volume rm -f "$VOLUME" >/dev/null 2>&1 || true
    ok "容器与数据卷已删除"
}

case "${1:-}" in
    up)     cmd_up ;;
    down)   cmd_down ;;
    status) cmd_status ;;
    logs)   cmd_logs ;;
    psql)   cmd_psql ;;
    reset)  cmd_reset "$@" ;;
    *)
        sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
        exit 1
        ;;
esac
