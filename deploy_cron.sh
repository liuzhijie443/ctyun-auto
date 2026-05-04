#!/bin/bash
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

DEFAULT_LOGIN_CRON='0 3,20 * * *'
DEFAULT_PC_CRON='0 4,6 * * *'
DEFAULT_LOGIN_SCRIPT='/app/login_script.py'
DEFAULT_PC_SCRIPT='/app/pc_login.py'

APP_USER=''
APP_PASSWORD=''
LOGIN_CRON_EXPR=''
PC_CRON_EXPR=''
AUTO_CONFIRM='false'

print_usage() {
    cat <<'EOF'
用法:
  bash deploy_cron.sh [账号] [密码] [挂机cron] [AI对话cron] [选项]

选项:
  -y, --yes                     跳过“按回车启动容器”确认
  -h, --help                    显示帮助

示例:
  bash deploy_cron.sh 181xxxx '***' "0 4,6 * * *" "0 3,20 * * *" -y

参数顺序说明:
  1. 账号(APP_USER)
  2. 密码(APP_PASSWORD)
  3. 挂机一小时任务的 cron 时间
  4. AI对话脚本的 cron 时间
EOF
}

expand_path() {
    local input="$1"
    if [[ "$input" == ~* ]]; then
        echo "${input/#\~/$HOME}"
    else
        echo "$input"
    fi
}

validate_cron_expr() {
    local expr="$1"
    local field_count
    field_count=$(awk '{print NF}' <<< "$expr")
    [ "$field_count" -eq 5 ]
}

configure_cron_in_container() {
    local container_name="$1"
    local login_cron="$2"
    local pc_cron="$3"
    local login_script="$4"
    local pc_script="$5"

    echo -e "${YELLOW}[*] 正在更新容器内 Cron 配置...${NC}"

    docker exec "$container_name" sh -c "test -f '$login_script'"
    docker exec "$container_name" sh -c "test -f '$pc_script'"

    docker exec -i "$container_name" sh -c "cat > /etc/cron.d/ctyun-cron && chmod 0644 /etc/cron.d/ctyun-cron && crontab /etc/cron.d/ctyun-cron" <<EOF
${login_cron} root /usr/bin/python3 ${login_script} > /proc/1/fd/1 2>&1
${pc_cron} root /usr/bin/python3 ${pc_script} > /proc/1/fd/1 2>&1
EOF

    echo -e "${GREEN}[*] Cron 更新成功。${NC}"
    echo -e "    云电脑挂机任务: ${pc_cron}"
    echo -e "    AI对话任务任务: ${login_cron}"
}

run_pc_login_until_warm_then_background() {
    local container_name="$1"
    local pc_script="$2"
    local log_file="/app/data/pc_login_once.log"
    local pid_file="/app/data/pc_login_once.pid"
    local max_wait_seconds=900
    local warmup_seconds=120
    local waited_seconds=0
    local printed_lines=0

    echo -e "${YELLOW}[*] 正在启动云电脑挂机任务...${NC}"

    docker exec "$container_name" sh -c "rm -f '$log_file' '$pid_file'; nohup env PYTHONUNBUFFERED=1 python3 -u '$pc_script' > '$log_file' 2>&1 & echo \$! > '$pid_file'"

    while true; do
        local new_lines
        new_lines=$(docker exec "$container_name" sh -c "if [ -f '$log_file' ]; then sed -n '$((printed_lines + 1)),\$p' '$log_file'; fi")

        if [ -n "$new_lines" ]; then
            while IFS= read -r line; do
                echo "$line"
                printed_lines=$((printed_lines + 1))
            done <<< "$new_lines"
        fi

        if ! docker exec "$container_name" sh -c "[ -f '$pid_file' ] && kill -0 \$(cat '$pid_file') 2>/dev/null"; then
            echo -e "${RED}[!] 任务提前退出。${NC}"
            return 1
        fi

        sleep 2
        waited_seconds=$((waited_seconds + 2))

        if [ "$waited_seconds" -ge "$warmup_seconds" ]; then
            echo -e "${GREEN}[*] 后台任务运行正常。${NC}"
            return 0
        fi

        if [ "$waited_seconds" -ge "$max_wait_seconds" ]; then
            echo -e "${YELLOW}[!] 等待超时，任务将继续在后台运行。${NC}"
            return 0
        fi
    done
}

# -------------------- 参数解析 --------------------
POSITIONAL_ARGS=()
while [ "$#" -gt 0 ]; do
    case "$1" in
        -y|--yes)
            AUTO_CONFIRM='true'
            shift
            ;;
        -h|--help)
            print_usage
            exit 0
            ;;
        -*)
            echo -e "${RED}[!] 未知选项: $1${NC}"
            print_usage
            exit 1
            ;;
        *)
            POSITIONAL_ARGS+=("$1")
            shift
            ;;
    esac
done

# 按约定顺序读取位置参数：账号 密码 pc_login cron login_script cron
if [ "${#POSITIONAL_ARGS[@]}" -gt 0 ]; then
    APP_USER="${POSITIONAL_ARGS[0]:-}"
fi
if [ "${#POSITIONAL_ARGS[@]}" -gt 1 ]; then
    APP_PASSWORD="${POSITIONAL_ARGS[1]:-}"
fi
if [ "${#POSITIONAL_ARGS[@]}" -gt 2 ]; then
    PC_CRON_EXPR="${POSITIONAL_ARGS[2]:-}"
fi
if [ "${#POSITIONAL_ARGS[@]}" -gt 3 ]; then
    LOGIN_CRON_EXPR="${POSITIONAL_ARGS[3]:-}"
fi
if [ "${#POSITIONAL_ARGS[@]}" -gt 4 ]; then
    echo -e "${RED}[!] 位置参数过多，请按“账号 密码 挂机cron AI对话cron${NC}"
    print_usage
    exit 1
fi

echo -e "${GREEN}=== 天翼云电脑自动部署===${NC}\n"

if ! command -v docker >/dev/null 2>&1; then
    echo -e "${RED}[!] 错误：未检测到 Docker，请先安装。${NC}"
    exit 1
fi

if [ ! -f "app/Dockerfile" ]; then
    echo -e "${RED}[!] 错误：未找到 app/Dockerfile，请在项目根目录执行。${NC}"
    exit 1
fi

# 账号
if [ -z "$APP_USER" ]; then
    read -r -e -p "账号（APP_USER）: " APP_USER
fi
if [ -z "$APP_USER" ]; then
    echo -e "${RED}[!] APP_USER 不能为空。${NC}"
    exit 1
fi

# 密码
if [ -z "$APP_PASSWORD" ]; then
    read -r -s -p "密码（APP_PASSWORD）: " APP_PASSWORD
    echo ""
fi
if [ -z "$APP_PASSWORD" ]; then
    echo -e "${RED}[!] APP_PASSWORD 不能为空。${NC}"
    exit 1
fi

# 数据目录固定为默认值
DATA_DIR=$(expand_path "~/data")
mkdir -p "$DATA_DIR"

# login cron
if [ -z "$LOGIN_CRON_EXPR" ]; then
    while true; do
        read -r -e -p "AI对话任务 Cron [默认: ${DEFAULT_LOGIN_CRON}]: " LOGIN_CRON_EXPR
        LOGIN_CRON_EXPR="${LOGIN_CRON_EXPR:-$DEFAULT_LOGIN_CRON}"
        if validate_cron_expr "$LOGIN_CRON_EXPR"; then
            break
        fi
        echo -e "${RED}[!] Cron 表达式无效，请使用 5 段格式，例如：0 3,20 * * *${NC}"
    done
else
    if ! validate_cron_expr "$LOGIN_CRON_EXPR"; then
        echo -e "${RED}[!] AI对话 cron 表达式无效，请使用 5 段格式。${NC}"
        exit 1
    fi
fi

# pc cron
if [ -z "$PC_CRON_EXPR" ]; then
    while true; do
        read -r -e -p "挂机任务 Cron [默认: ${DEFAULT_PC_CRON}]: " PC_CRON_EXPR
        PC_CRON_EXPR="${PC_CRON_EXPR:-$DEFAULT_PC_CRON}"
        if validate_cron_expr "$PC_CRON_EXPR"; then
            break
        fi
        echo -e "${RED}[!] Cron 表达式无效，请使用 5 段格式，例如：0 4,6 * * *${NC}"
    done
else
    if ! validate_cron_expr "$PC_CRON_EXPR"; then
        echo -e "${RED}[!] 挂机 cron 表达式无效，请使用 5 段格式。${NC}"
        exit 1
    fi
fi

LOGIN_SCRIPT="$DEFAULT_LOGIN_SCRIPT"
PC_SCRIPT="$DEFAULT_PC_SCRIPT"

echo -e "${YELLOW}[*] 正在构建镜像...${NC}"
docker build -q -t ctyun-auto-sign:v1 ./app >/dev/null

CONTAINER_NAME="ctyun_sign_${APP_USER}"
if [ "$(docker ps -aq -f name=^${CONTAINER_NAME}$)" ]; then
    docker rm -f "$CONTAINER_NAME" >/dev/null
fi

echo -e "\n${RED}=== 首次运行风控提醒 ===${NC}"
echo -e "1. 如日志要求输入短信验证码，请直接在当前终端输入并回车。"
echo -e "2.  ${GREEN}保活任务启动${NC} 后，请依次按 ${YELLOW}Ctrl+P${NC} 再按 ${YELLOW}Ctrl+Q${NC} 转入后台挂起。"
echo -e "   (如果误按 Ctrl+C 退出，请执行: docker start ${CONTAINER_NAME})"
echo -e "===========================\n"
DOCKER_TYPE="-d"
if [ "$AUTO_CONFIRM" != 'true' ]; then
    read -r -p "按回车启动容器..."
    DOCKER_TYPE="-it"
fi
docker run $DOCKER_TYPE \
  --name "$CONTAINER_NAME" \
  -e APP_USER="$APP_USER" \
  -e APP_PASSWORD="$APP_PASSWORD" \
  -v "$DATA_DIR":/app/data \
  --add-host "deskcdn.ctyun.cn:106.120.187.154" \
  --add-host "deskcdn.ctyun.cn.ctadns.cn:106.120.187.154" \
  --restart unless-stopped \
  ctyun-auto-sign:v1

echo -e "\n${YELLOW}[*] 已退出交互界面，正在检查容器状态...${NC}"
sleep 2
if [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null)" = "true" ]; then
    echo -e "${GREEN}[*] 容器正在后台运行。${NC}"

    configure_cron_in_container "$CONTAINER_NAME" "$LOGIN_CRON_EXPR" "$PC_CRON_EXPR" "$LOGIN_SCRIPT" "$PC_SCRIPT"

    echo -e "------------------------------------------------------"
    echo -e "${YELLOW}[*] 执行AI 对话积分任务...${NC}"
    docker exec -it "$CONTAINER_NAME" python3 "$LOGIN_SCRIPT"

    echo -e "------------------------------------------------------"
    if ! run_pc_login_until_warm_then_background "$CONTAINER_NAME" "$PC_SCRIPT"; then
        echo -e "${YELLOW}[!] 挂机任务未进入稳定运行阶段，请稍后查看日志。${NC}"
    fi
    echo -e "------------------------------------------------------"

    echo -e "${GREEN}[*] 首次任务触发完成，后续由 Cron 按计划执行。${NC}"
else
    echo -e "${RED}[!] 警告：容器当前未运行。${NC}"
    echo -e "可执行恢复命令：docker start ${CONTAINER_NAME}"
    echo -e "然后手动执行："
    echo -e "  docker exec -it ${CONTAINER_NAME} python3 ${LOGIN_SCRIPT}"
    echo -e "  docker exec -it ${CONTAINER_NAME} env PYTHONUNBUFFERED=1 python3 -u ${PC_SCRIPT}"
fi

echo -e "\n${GREEN}[*] 部署完成。${NC}"
echo -e "容器名：${CONTAINER_NAME}"
echo -e "数据目录：${DATA_DIR}"
echo -e "日志查看：docker logs -f ${CONTAINER_NAME}"
echo -e "启停命令：docker start/stop ${CONTAINER_NAME}"
echo -e "查看定时任务：docker exec -it ${CONTAINER_NAME} crontab -l"
echo -e "------------------------------------------------------"
