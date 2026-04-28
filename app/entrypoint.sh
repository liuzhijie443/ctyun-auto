#!/bin/bash
set -e

DEVICECODE_FILE="/app/data/.devicecode_${APP_USER}"

if [ -z "$DEVICECODE" ]; then
    if [ -f "$DEVICECODE_FILE" ]; then
        export DEVICECODE=$(cat "$DEVICECODE_FILE")
        echo "[*] 读取到已保存的 DEVICECODE: $DEVICECODE"
    else
        export DEVICECODE="web_$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | fold -w 32 | head -n 1)"
        echo "$DEVICECODE" > "$DEVICECODE_FILE"
        echo "[*] 首次启动，已生成并持久化 DEVICECODE: $DEVICECODE"
    fi
else
    echo "[*] 检测到手动传入的 DEVICECODE: $DEVICECODE"
fi

env >> /etc/environment

service cron start
echo "[*] Cron 定时服务已启动。"

set +e

echo "[*] 启动进程守护模式..."

# 开启无限循环，接管程序的生命周期
while true; do
    echo "======================================================"
    echo "[*] 启动 CtYun.dll..."
    timeout --foreground 2m dotnet CtYun.dll
    sleep 10
    timeout --foreground 24h dotnet CtYun.dll

    # 获取上方进程退出时的状态码
    EXIT_CODE=$?

    if [ $EXIT_CODE -eq 124 ]; then
        echo "[!] 触发定时机制：程序已连续运行 24 小时，执行强制重启。"
    else
        echo "[!] CtYun.dll 进程已退出 (退出码: $EXIT_CODE)。可能正在等待开机或发生了异常。"
    fi

    echo "[*] 容器挂起中，将在 2 分钟 (120秒) 后重新启动程序，请等待..."
    sleep 120

done
