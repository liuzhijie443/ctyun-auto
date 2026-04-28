# 1. 使用你指定的 Ctyun 镜像作为基础镜像
FROM su3817807/ctyun:latest

# 2. 切换到 root 用户以安装系统级依赖
USER root

# 3. 设置时区为亚洲/上海，确保 Cron 定时任务按北京时间执行
ENV TZ=Asia/Shanghai
ENV RUNNING_IN_DOCKER=true
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 4. 安装 Python3、包管理器、Chromium、中文字体、Cron 以及图像处理底层库
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    chromium \
    chromium-driver \
    fonts-wqy-zenhei \
    cron \
    libgl1 \
    libglib2.0-0 \
    dos2unix \
    && rm -rf /var/lib/apt/lists/*

# 5. 安装 Python 依赖（Debian 12 默认开启 PEP 668，需要 break-system-packages）
RUN pip3 install --no-cache-dir DrissionPage==4.0.4.19 ddddocr --break-system-packages

# 6. 将 Python 脚本和启动脚本复制到工作目录
COPY login_script.py /app/login_script.py
COPY pc_login.py /app/pc_login.py
COPY entrypoint.sh /app/entrypoint.sh

# 7. 修复 Windows 环境下可能产生的 CRLF 换行符问题，并赋予执行权限
RUN dos2unix /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# 8. 配置 Cron 定时任务
RUN { \
    echo "0 3,20 * * * root /usr/bin/python3 /app/login_script.py > /proc/1/fd/1 2>&1"; \
    echo "0 4,6 * * * root /usr/bin/python3 /app/pc_login.py > /proc/1/fd/1 2>&1"; \
    } > /etc/cron.d/ctyun-cron \
    && chmod 0644 /etc/cron.d/ctyun-cron \
    && crontab /etc/cron.d/ctyun-cron

# 9. 覆盖原镜像的 ENTRYPOINT，使用我们的双进程启动脚本
ENTRYPOINT ["/app/entrypoint.sh"]
