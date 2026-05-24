# Kindle Weather Wall

一个给旧 Kindle 浏览器用的轻量天气墙。目标是放在 Docker/NAS 上跑，让 Kindle 打开一个高对比度网页作为常亮展示。

它不生成 PNG，不跑 Chromium，不依赖前端框架；页面就是服务端拼出的 HTML/CSS，适合老设备。

## 当前布局

默认布局是 `v2.5`：

- 大号日期、星期、时间和地点
- 当前温度、今日最高/最低温
- 当前天气图标、天气文字、空气质量
- 未来 1、2、3、6、12 小时天气
- 未来小时如果是雨/雷雨，时间会反白显示
- 天气数据服务端缓存 15 分钟，页面每 60 秒刷新一次

保留旧布局用于回退：

- `/v2.5` / `/v25` / `/ver_v2.5`：当前默认版
- `/v1` / `/ver_v1`：带体感、湿度、风速、降水指标行
- `/v0` / `/ver_v0`：早期 classic 版

## 本地运行

```sh
cp .env.example .env
docker compose up --build
```

打开：

```text
http://localhost:8080/
```

如果是 WSL，本机浏览器能打开但局域网设备打不开时，可以在 Windows 管理员 PowerShell 里设置端口转发：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows-portproxy.ps1 -ListenAddress <windows-lan-ip> -Port 8080
```

然后 Kindle 打开：

```text
http://<windows-lan-ip>:8080/
```

## NAS 部署

在 NAS 上准备项目目录后，复制本仓库文件过去，创建 `.env`：

```sh
cp .env.example .env
```

如果希望端口是 `18080`，在 `.env` 里设置：

```sh
PORT=18080
```

启动：

```sh
docker compose up -d --build
```

Kindle 打开：

```text
http://<nas-ip>:18080/
```

## 配置

`.env.example` 里的常用项：

```sh
LATITUDE=30.2741
LONGITUDE=120.1551
LOCATION_NAME=杭州市西湖区
TIMEZONE=Asia/Shanghai
WEATHER_API_URL=http://api.open-meteo.com/v1/forecast
AIR_QUALITY_API_URL=http://air-quality-api.open-meteo.com/v1/air-quality
UNITS=metric
PAGE_REFRESH_SECONDS=60
WEATHER_CACHE_SECONDS=900
REQUEST_TIMEOUT_SECONDS=10
KINDLE_WIDTH=600
KINDLE_HEIGHT=700
FORECAST_DAYS=3
```

说明：

- `PAGE_REFRESH_SECONDS=60`：浏览器页面每 60 秒刷新，主要让时间更新。
- `WEATHER_CACHE_SECONDS=900`：天气数据每 15 分钟重新拉取一次。
- `KINDLE_HEIGHT=700`：给旧 Kindle 浏览器顶部栏预留空间；底部被裁就调大，空白太多就调小。
- 天气和空气质量来自 Open-Meteo，不需要 API key。

## 路由

- `/` / `/kindle`：最新版，目前是 `v2.5`
- `/v2.5` / `/v25` / `/ver_v2.5`：`v2.5`
- `/v1` / `/ver_v1`：`v1`
- `/v0` / `/ver_v0`：`v0`
- `/classic` / `/kindle/classic`：同 `/v0`
- `/api/weather`：缓存后的原始天气 JSON
- `/healthz`：健康检查
