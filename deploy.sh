#!/bin/bash
set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

run_pc_login_until_hang_then_background() {
    local container_name="$1"
    local log_file="/app/data/pc_login_once.log"
    local pid_file="/app/data/pc_login_once.pid"
    local max_wait_seconds=900
    local waited_seconds=0
    local printed_lines=0

    echo -e "${YELLOW}[*] 启动云电脑一小时使用积分任务...${NC}"

    docker exec "$container_name" sh -c "rm -f '$log_file' '$pid_file'; nohup env PYTHONUNBUFFERED=1 python3 -u /app/pc_login.py > '$log_file' 2>&1 & echo \$! > '$pid_file'"

    while true; do
        local new_lines
        new_lines=$(docker exec "$container_name" sh -c "if [ -f '$log_file' ]; then sed -n '$((printed_lines + 1)),\$p' '$log_file'; fi")

        if [ -n "$new_lines" ]; then
            while IFS= read -r line; do
                echo "$line"
                printed_lines=$((printed_lines + 1))
            done <<< "$new_lines"
        fi

        if docker exec "$container_name" sh -c "grep -aEq '挂机剩余' '$log_file'"; then
            echo -e "${GREEN}[*] 已检测到挂机阶段，已转后台继续运行。${NC}"
            return 0
        fi

        if ! docker exec "$container_name" sh -c "[ -f '$pid_file' ] && kill -0 \$(cat '$pid_file') 2>/dev/null"; then
            echo -e "${RED}[!] 任务已提前退出${NC}"
            echo -e "${YELLOW}[*] 最近日志如下：${NC}"
            docker exec "$container_name" sh -c "tail -n 30 '$log_file' 2>/dev/null || true"
            return 1
        fi

        sleep 2
        waited_seconds=$((waited_seconds + 2))
        if [ "$waited_seconds" -ge "$max_wait_seconds" ]; then
            echo -e "${YELLOW}[!] 等待超过 $((max_wait_seconds / 60)) 分钟，未检测到挂机提示。继续后台运行。${NC}"
            return 0
        fi
    done
}

echo -e "${GREEN}=== 天翼云电脑保活自动化部署 ===${NC}\n"

# 1. 环境与目录检查
if ! command -v docker &> /dev/null; then
    echo -e "${RED}[!] 错误: 未安装 Docker。${NC}"
    exit 1
fi

if [ ! -f "app/Dockerfile" ]; then
    echo -e "${RED}[!] 错误: 未找到 app/Dockerfile，请确认处于项目根目录。${NC}"
    exit 1
fi

# 2. 读取配置
read -e -p "账号 (APP_USER): " APP_USER
[ -z "$APP_USER" ] && {
    echo -e "${RED}[!] 账号不能为空。${NC}"
    exit 1
}

read -e -p "密码 (APP_PASSWORD): " APP_PASSWORD
echo ""
[ -z "$APP_PASSWORD" ] && {
    echo -e "${RED}[!] 密码不能为空。${NC}"
    exit 1
}

while true; do
    read -e -p "数据目录 [留空默认 ~/data]: " INPUT_DIR
    if [ -z "$INPUT_DIR" ]; then
        DATA_DIR="$HOME/data"
        break
    elif [[ "$INPUT_DIR" == /* ]]; then
        DATA_DIR="$INPUT_DIR"
        break
    elif [[ "$INPUT_DIR" == ~* ]]; then
        DATA_DIR="${INPUT_DIR/#\~/$HOME}"
        break
    else
        echo -e "${RED}[!] 请输入绝对路径（以 / 或 ~ 开头）。${NC}"
    fi
done

mkdir -p "$DATA_DIR"

# 3. 构建镜像并清理同名容器
echo -e "${YELLOW}[*] 正在构建镜像...${NC}"
docker build -q -t ctyun-auto-sign:v1 ./app > /dev/null

CONTAINER_NAME="ctyun_sign_${APP_USER}"
if [ "$(docker ps -aq -f name=^${CONTAINER_NAME}$)" ]; then
    docker rm -f "$CONTAINER_NAME" > /dev/null
fi

# 4. 首次运行提示
echo -e "\n${RED}=== 首次运行风控提醒 ===${NC}"
echo -e "1. 如日志要求输入短信验证码，请直接在当前终端输入并回车。"
echo -e "2.  ${GREEN}保活任务启动${NC} 后，请依次按 ${YELLOW}Ctrl+P${NC} 再按 ${YELLOW}Ctrl+Q${NC} 转入后台挂起。"
echo -e "   (如果误按 Ctrl+C 退出，请执行: docker start ${CONTAINER_NAME})"
echo -e "===========================\n"

read -p "确认后按【回车键】启动容器..."

# 5. 启动容器（该过程会占用终端，直到用户按 Ctrl+P、Ctrl+Q 脱离）
docker run -it \
  --name "$CONTAINER_NAME" \
  -e APP_USER="$APP_USER" \
  -e APP_PASSWORD="$APP_PASSWORD" \
  -v "$DATA_DIR":/app/data \
  --add-host "deskcdn.ctyun.cn:106.120.187.154" \
  --add-host "deskcdn.ctyun.cn.ctadns.cn:106.120.187.154" \
  --restart unless-stopped \
  ctyun-auto-sign:v1

# 6. 脱离后的自动化首次任务
echo -e "\n${YELLOW}[*] 检测到交互界面已退出，正在检查容器运行状态...${NC}"
sleep 2
if [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null)" == "true" ]; then
    echo -e "${GREEN}[*] 容器后台运行正常。${NC}"

    echo -e "------------------------------------------------------"
    echo -e "${YELLOW}[*] 执行AI对话积分任务...${NC}"
    docker exec -it "$CONTAINER_NAME" python3 /app/login_script.py

    echo -e "------------------------------------------------------"
    if ! run_pc_login_until_hang_then_background "$CONTAINER_NAME"; then
        echo -e "${YELLOW}[!] 未进入挂机阶段，请稍后查看容器日志排查。${NC}"
    fi
    echo -e "------------------------------------------------------"

    echo -e "${GREEN}[*] 首次积分任务已触发完成，后续将由 Cron 定时接管。${NC}"
else
    # 防呆：如果用户误按 Ctrl+C 导致容器停止
    echo -e "${RED}[!] 警告：检测到容器已停止。${NC}"
    echo -e "可能原因：您刚才按下了 ${YELLOW}Ctrl+C${NC}，而不是 ${YELLOW}Ctrl+P${NC} 后 ${YELLOW}Ctrl+Q${NC}。"
    echo -e "补救措施：请先执行 ${YELLOW}docker start ${CONTAINER_NAME}${NC} 重新启动容器。"
    echo -e "然后手动执行 ${YELLOW}docker exec -it ${CONTAINER_NAME} python3 /app/login_script.py${NC}。"
    echo -e "如需手动运行：${YELLOW}docker exec -it ${CONTAINER_NAME} env PYTHONUNBUFFERED=1 python3 -u /app/pc_login.py${NC}。"
fi

# 7. 结束信息
echo -e "\n${GREEN}[*] 部署与首次配置完成。${NC}"
echo -e "容器名: ${CONTAINER_NAME}"
echo -e "数据目录: ${DATA_DIR}"
echo -e "日志查询: ${YELLOW}docker logs -f ${CONTAINER_NAME}${NC}"
echo -e "启动/停止: ${YELLOW}docker start/stop ${CONTAINER_NAME}${NC}"
