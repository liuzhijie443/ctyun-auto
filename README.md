# 天翼云电脑保活并完成每日任务获取积分

本项目用于在 Docker 容器中保活云电脑使其长期开机，保活不会中断使用，并自动完成积分任务，每天可获取300积分。

## 新版本更新

- **新增挂机任务**
- **优化海外卡顿**
- **定时任务优化**

## 来源说明

本项目中使用的保活程序来自 `CtYun` 项目：

- https://github.com/leleji/CtYun

当前仓库通过基础镜像 `su3817807/ctyun:latest` 使用该程序（容器内运行 `dotnet CtYun.dll`），本仓库主要补充了定时执行积分任务的能力和增加了24小时重启保活程序。

## 项目结构

```text
。
├─ deploy.sh               # 交互式部署脚本（构建镜像、启动容器）
└─ app/
   ├─ Dockerfile           # 运行环境构建与 cron 任务配置
   ├─ entrypoint.sh        # 容器入口：启动 cron + 保活循环运行 CtYun.dll
   ├─ login_script.py      # AI对话积分任务脚本
   └─ pc_login.py          # 云电脑挂机任务脚本
```
## 快速开始

在项目根目录执行：

```bash
git clone https://github.com/liuzhijie443/ctyun-auto.git
cd ctyun-auto/
bash deploy.sh
```

按提示输入：

- `APP_USER`：账号
- `APP_PASSWORD`：密码
- 数据目录：容器挂载目录（默认 `~/data`）

脚本会构建镜像 `ctyun-auto-sign:v1` 并启动容器 `ctyun_sign_<APP_USER>`。

## 首次运行说明

- 如日志提示输入短信验证码，直接在当前终端输入并回车。
- 当日志出现“保活任务启动”后，可按 `Ctrl+P` 再按 `Ctrl+Q` 让容器脱离终端并后台运行。
- 若误按 `Ctrl+C` 导致退出，可执行 `docker start ctyun_sign_<APP_USER>`。

## 常用命令

```bash
# 查看实时日志
docker logs -f ctyun_sign_<APP_USER>

# 停止/启动容器
docker stop ctyun_sign_<APP_USER>
docker start ctyun_sign_<APP_USER>
```


验证码识别api方案来自 https://github.com/sml2h3/ddddocr
