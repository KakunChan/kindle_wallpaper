# Kindle Weather Wall

[中文说明](README.zh-CN.md)

A lightweight weather wall for old Kindle browsers. It is designed to run in Docker on a NAS or a local machine, while the Kindle opens a high-contrast web page as an always-on display.

The app does not render PNGs, run Chromium, or use a frontend framework. It serves plain HTML and CSS from a small Python HTTP server, which keeps it friendly to older Kindle browsers.

## Current Layout

The default layout is `v2.5`:

- Large date, weekday, time, and location
- Current temperature and today's high/low range
- Weather icon, weather condition, and air quality
- Weather snapshots for the next 1, 2, 3, 6, and 12 hours
- In the hourly forecast row, rainy or thunderstorm hours use inverted time labels
- Weather data is cached server-side for 15 minutes, while the page refreshes every 60 seconds

Older layouts are kept for fallback:

- `/v2.5` / `/v25` / `/ver_v2.5`: current default layout
- `/v1` / `/ver_v1`: layout with feels-like, humidity, wind, and precipitation metrics
- `/v0` / `/ver_v0`: early classic layout

## Run Locally

```sh
cp .env.example .env
docker compose up --build
```

Open:

```text
http://localhost:8080/
```

If you run the service from WSL and LAN devices cannot reach it, set up a Windows port proxy from an elevated Administrator PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows-portproxy.ps1 -ListenAddress <windows-lan-ip> -Port 8080
```

Then open this on the Kindle:

```text
http://<windows-lan-ip>:8080/
```

## NAS Deployment

Copy the repository to a directory on the NAS, then create `.env`:

```sh
cp .env.example .env
```

To expose the service on port `18080`, set this in `.env`:

```sh
PORT=18080
```

Start the container:

```sh
docker compose up -d --build
```

Open this on the Kindle:

```text
http://<nas-ip>:18080/
```

## Configuration

Common options from `.env.example`:

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

Notes:

- `PAGE_REFRESH_SECONDS=60`: refreshes the browser page every 60 seconds, mainly to keep the clock fresh.
- `WEATHER_CACHE_SECONDS=900`: refreshes weather data every 15 minutes.
- `KINDLE_HEIGHT=700`: leaves room for the old Kindle browser header. Increase it if the bottom is clipped; decrease it if there is too much blank space.
- Weather and air quality data come from Open-Meteo and do not require an API key.

## Routes

- `/` / `/kindle`: latest layout, currently `v2.5`
- `/v2.5` / `/v25` / `/ver_v2.5`: `v2.5`
- `/v1` / `/ver_v1`: `v1`
- `/v0` / `/ver_v0`: `v0`
- `/classic` / `/kindle/classic`: same as `/v0`
- `/api/weather`: cached raw weather JSON
- `/healthz`: health check
