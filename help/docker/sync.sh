#!/usr/bin/env bash
# +----------------------------------------------------------------------
# | crmeb-ai-mall —— 把本机 crmeb/ 源码同步进 Docker 容器（Windows 开发专用）
# +----------------------------------------------------------------------
# | 为什么需要这个脚本？
# |   上游 help/docker/docker-compose.yml 直接把 Windows 目录绑定挂载到容器 /var/www。
# |   在 Docker Desktop（WSL2 后端）下这是 9p 网络文件系统，单次文件元数据操作约 200ms，
# |   而 ThinkPHP 一次请求要 stat/open 上千个文件，实测后台接口 60s+ 直接 504。
# |   本仓库改为「命名卷 crmeb_app（WSL2 ext4）+ 本脚本同步」：
# |     - 读取：NTFS 本地顺序读 + tar 打包，很快
# |     - 写入：docker exec 的 stdin 流进容器，在 ext4 里解包，同样很快
# |     - 全程不产生 9p 随机元数据访问
# |
# | 用法：
# |   bash help/docker/sync.sh              # 全量同步并重启 php 容器
# |   bash help/docker/sync.sh --no-restart # 只同步，不重启（配合手动 reload 时使用）
# |   bash help/docker/sync.sh --status     # 只对比容器内外代码是否一致
# +----------------------------------------------------------------------
set -euo pipefail

# Git Bash(MSYS2) 会把 "/var/www" 这类参数改写成本机路径（实测变成 D:/AIcode/Git/var/www），
# 导致 docker exec 在容器里收到错误路径。以下两个变量关闭这条转换规则。
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC_DIR="$REPO_ROOT/crmeb"
PHP_CONTAINER="${CRMEB_PHP_CONTAINER:-crmeb_php}"
SITE_URL="${CRMEB_SITE_URL:-http://localhost:8011}"
NO_RESTART=0
STATUS_ONLY=0

for arg in "$@"; do
  case "$arg" in
    --no-restart) NO_RESTART=1 ;;
    --status) STATUS_ONLY=1 ;;
    *) echo "未知参数：$arg" >&2; exit 2 ;;
  esac
done

[ -d "$SRC_DIR" ] || { echo "找不到源码目录：$SRC_DIR" >&2; exit 1; }
docker inspect "$PHP_CONTAINER" >/dev/null 2>&1 || { echo "容器 $PHP_CONTAINER 未运行，先执行 docker compose up -d" >&2; exit 1; }

# ---- 只对比，不写入：用 git 记录本机相对基线的改动文件 ----
if [ "$STATUS_ONLY" = "1" ]; then
  echo "== 本机相对 HEAD 的改动文件（这些会在下次 sync 时进入容器）=="
  (cd "$REPO_ROOT" && git status --porcelain -- crmeb | sed 's/^/  /') || true
  echo "== 容器内代码指纹 =="
  docker exec "$PHP_CONTAINER" sh -c "cd /var/www && pwd && ls -1 | head -5 && cat .version 2>/dev/null" || true
  exit 0
fi

echo "[1/4] 打包并流式写入 $PHP_CONTAINER:/var/www ..."
START=$(date +%s)
# runtime/* 与 public/install.lock 之外全部同步；runtime 里是容器自己产生的缓存与日志，不清空
tar -C "$SRC_DIR" \
  --exclude='./runtime/*' \
  --exclude='.git' \
  -cf - . \
  | docker exec -i "$PHP_CONTAINER" tar -xf - -C /var/www
echo "      完成，用时 $(( $(date +%s) - START ))s"

echo "[2/4] 修正属主与可写目录 ..."
docker exec "$PHP_CONTAINER" sh -c '
  mkdir -p /var/www/runtime /var/www/temp /var/www/public/uploads /var/www/public/install
  chmod -R 777 /var/www/runtime /var/www/temp
  chmod -R 777 /var/www/public/uploads 2>/dev/null || true
  chown -R www-data:www-data /var/www/runtime /var/www/temp 2>/dev/null || true
  # 安装向导要求 public/install/.env 模板存在，install.lock 用于阻止重复安装
  [ -f /var/www/public/install.lock ] && echo "      install.lock 已存在" || echo "      提示：尚未安装（public/install.lock 缺失）"
'

echo "[3/4] 同步 .env（数据库/Redis 连接参数，不入库，仅本地）..."
if [ -f "$SRC_DIR/.env" ]; then
  docker exec -i "$PHP_CONTAINER" sh -c 'cat > /var/www/.env' < "$SRC_DIR/.env"
  echo "      已同步 crmeb/.env"
else
  echo "      本机无 crmeb/.env，跳过（容器内保留现有 .env）"
fi

if [ "$NO_RESTART" = "1" ]; then
  echo "[4/4] --no-restart：跳过重启。手动生效请执行 docker restart $PHP_CONTAINER"
  exit 0
fi

echo "[4/4] 重启 php 容器（重建 php-fpm / workerman / queue / timer，并清空 OPcache）..."
docker restart "$PHP_CONTAINER" >/dev/null

echo "      等待服务就绪 ..."
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$SITE_URL/" || true)
  if [ "$code" = "200" ] || [ "$code" = "302" ]; then
    echo "      站点已响应（HTTP $code），等待 $((i * 2))s"
    break
  fi
  sleep 2
done

echo "完成。代码已生效。"
