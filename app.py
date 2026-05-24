from __future__ import annotations

import datetime as dt
import html
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_WEATHER_API_URL = "http://api.open-meteo.com/v1/forecast"
DEFAULT_AIR_QUALITY_API_URL = "http://air-quality-api.open-meteo.com/v1/air-quality"


WEATHER_CODES = {
    0: "晴",
    1: "大部晴朗",
    2: "局部多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "大毛毛雨",
    56: "冻毛毛雨",
    57: "冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "阵雨",
    81: "阵雨",
    82: "强阵雨",
    85: "阵雪",
    86: "阵雪",
    95: "雷雨",
    96: "雷雨",
    99: "雷雨",
}

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class Config:
    def __init__(self) -> None:
        self.host = env_str("HOST", "0.0.0.0")
        self.port = env_int("PORT", 8080)
        self.latitude = env_float("LATITUDE", 30.2741)
        self.longitude = env_float("LONGITUDE", 120.1551)
        self.location_name = env_str("LOCATION_NAME", "杭州市西湖区")
        self.timezone_name = env_str("TIMEZONE", "Asia/Shanghai")
        self.weather_api_url = env_str("WEATHER_API_URL", DEFAULT_WEATHER_API_URL)
        self.air_quality_api_url = env_str(
            "AIR_QUALITY_API_URL",
            DEFAULT_AIR_QUALITY_API_URL,
        )
        self.units = env_choice("UNITS", "metric", {"metric", "imperial"})
        self.page_refresh_seconds = env_int("PAGE_REFRESH_SECONDS", 60)
        self.weather_cache_seconds = env_int("WEATHER_CACHE_SECONDS", 900)
        self.forecast_days = max(1, min(env_int("FORECAST_DAYS", 3), 7))
        self.request_timeout_seconds = env_float("REQUEST_TIMEOUT_SECONDS", 10.0)
        self.kindle_width = env_int("KINDLE_WIDTH", 600)
        self.kindle_height = env_int("KINDLE_HEIGHT", 700)

        try:
            self.timezone = ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError:
            print(
                f"Unknown TIMEZONE={self.timezone_name!r}; falling back to UTC",
                file=sys.stderr,
            )
            self.timezone_name = "UTC"
            self.timezone = ZoneInfo("UTC")

    @property
    def temp_unit(self) -> str:
        return "F" if self.units == "imperial" else "C"

    @property
    def wind_unit(self) -> str:
        return "mph" if self.units == "imperial" else "km/h"

    @property
    def precip_unit(self) -> str:
        return "in" if self.units == "imperial" else "毫米"

    @property
    def coord_label(self) -> str:
        return f"{self.latitude:.4f}, {self.longitude:.4f}"


def env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def env_int(name: str, default: int) -> int:
    value = env_str(name, str(default))
    try:
        return int(value)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer, got {value!r}") from exc


def env_float(name: str, default: float) -> float:
    value = env_str(name, str(default))
    try:
        return float(value)
    except ValueError as exc:
        raise SystemExit(f"{name} must be a number, got {value!r}") from exc


def env_choice(name: str, default: str, choices: set[str]) -> str:
    value = env_str(name, default).lower()
    if value not in choices:
        raise SystemExit(f"{name} must be one of {sorted(choices)}, got {value!r}")
    return value


class WeatherCache:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.lock = threading.Lock()
        self.data: dict[str, Any] | None = None
        self.fetched_at = 0.0
        self.last_error: str | None = None
        self.refreshing = False

    def get(self) -> tuple[dict[str, Any], bool, str | None]:
        now = time.time()
        if self.data and now - self.fetched_at < self.config.weather_cache_seconds:
            return self.data, False, self.last_error

        with self.lock:
            now = time.time()
            if self.data and now - self.fetched_at < self.config.weather_cache_seconds:
                return self.data, False, self.last_error
            if self.data:
                if not self.refreshing:
                    self.refreshing = True
                    thread = threading.Thread(target=self.refresh, daemon=True)
                    thread.start()
                return self.data, True, self.last_error

            try:
                self.data = fetch_weather(self.config)
                self.fetched_at = time.time()
                self.last_error = None
                return self.data, False, None
            except Exception as exc:  # noqa: BLE001 - keep stale data for the wall display.
                self.last_error = str(exc)
                if self.data:
                    return self.data, True, self.last_error
                raise

    def refresh(self) -> None:
        try:
            data = fetch_weather(self.config)
            with self.lock:
                self.data = data
                self.fetched_at = time.time()
                self.last_error = None
        except Exception as exc:  # noqa: BLE001 - retry on the next request.
            with self.lock:
                self.last_error = str(exc)
        finally:
            with self.lock:
                self.refreshing = False


def fetch_weather(config: Config) -> dict[str, Any]:
    params = {
        "latitude": str(config.latitude),
        "longitude": str(config.longitude),
        "timezone": config.timezone_name,
        "forecast_days": str(config.forecast_days),
        "current": ",".join(
            [
                "temperature_2m",
                "apparent_temperature",
                "relative_humidity_2m",
                "precipitation",
                "cloud_cover",
                "pressure_msl",
                "weather_code",
                "wind_speed_10m",
            ]
        ),
        "daily": ",".join(
            [
                "weather_code",
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_probability_max",
                "precipitation_sum",
                "sunrise",
                "sunset",
            ]
        ),
        "hourly": ",".join(
            [
                "temperature_2m",
                "weather_code",
            ]
        ),
    }
    if config.units == "imperial":
        params["temperature_unit"] = "fahrenheit"
        params["wind_speed_unit"] = "mph"
        params["precipitation_unit"] = "inch"

    url = f"{config.weather_api_url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "kindle-weather-wall/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=config.request_timeout_seconds) as res:
            body = res.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"weather fetch failed: {exc}") from exc

    payload = json.loads(body.decode("utf-8"))
    if "error" in payload:
        reason = payload.get("reason") or payload.get("message") or "unknown error"
        raise RuntimeError(f"weather API error: {reason}")
    try:
        payload["air_quality"] = fetch_air_quality(config)
    except Exception as exc:  # noqa: BLE001 - weather should still render without AQI.
        payload["air_quality_error"] = str(exc)
    return payload


def fetch_air_quality(config: Config) -> dict[str, Any]:
    params = {
        "latitude": str(config.latitude),
        "longitude": str(config.longitude),
        "timezone": config.timezone_name,
        "current": "us_aqi,pm2_5",
    }
    url = f"{config.air_quality_api_url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "kindle-weather-wall/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=config.request_timeout_seconds) as res:
            body = res.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"air quality fetch failed: {exc}") from exc

    payload = json.loads(body.decode("utf-8"))
    if "error" in payload:
        reason = payload.get("reason") or payload.get("message") or "unknown error"
        raise RuntimeError(f"air quality API error: {reason}")
    return payload


def weather_label(code: Any) -> str:
    try:
        return WEATHER_CODES[int(code)]
    except (TypeError, ValueError):
        return "未知"


def compact_weather_label(code: Any) -> str:
    label = weather_label(code)
    replacements = {
        "大部晴朗": "晴朗",
        "局部多云": "多云",
        "小毛毛雨": "毛毛雨",
        "大毛毛雨": "毛毛雨",
        "冻毛毛雨": "冻雨",
        "强阵雨": "阵雨",
    }
    return replacements.get(label, label)


def aqi_level(value: Any) -> str:
    try:
        aqi = float(value)
    except (TypeError, ValueError):
        return "--"
    if aqi <= 50:
        return "优"
    if aqi <= 100:
        return "良"
    if aqi <= 150:
        return "轻污"
    if aqi <= 200:
        return "中污"
    if aqi <= 300:
        return "重污"
    return "严重"


def weather_kind(code: Any) -> str:
    try:
        value = int(code)
    except (TypeError, ValueError):
        return "unknown"
    if value in {0, 1}:
        return "clear"
    if value == 2:
        return "partly"
    if value == 3:
        return "cloudy"
    if value in {45, 48}:
        return "fog"
    if value in {71, 73, 75, 77, 85, 86}:
        return "snow"
    if value in {95, 96, 99}:
        return "thunder"
    if value in {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}:
        return "rain"
    return "unknown"


def weather_icon_svg(code: Any, class_name: str) -> str:
    kind = weather_kind(code)
    icons = {
        "clear": """
          <circle cx="32" cy="32" r="10"></circle>
          <path d="M32 7v9M32 48v9M7 32h9M48 32h9M14 14l7 7M43 43l7 7M50 14l-7 7M21 43l-7 7"></path>
        """,
        "partly": """
          <circle cx="24" cy="23" r="9"></circle>
          <path d="M24 7v7M24 32v5M9 22h6M36 22h6M13 11l5 5M35 11l-5 5"></path>
          <path d="M22 45h24c7 0 11-4 11-10s-5-10-11-10c-3-8-16-9-20 0c-7 0-12 4-12 10s4 10 8 10z"></path>
        """,
        "cloudy": """
          <path d="M13 43h35c7 0 12-5 12-12s-6-12-13-11c-4-11-21-12-26 0c-9 0-16 5-16 13c0 6 4 10 8 10z"></path>
          <path d="M14 29c2-8 11-13 20-9"></path>
        """,
        "rain": """
          <path d="M13 34h35c7 0 12-5 12-12s-6-12-13-11c-4-11-21-12-26 0c-9 0-16 5-16 13c0 6 4 10 8 10z"></path>
          <path d="M20 43l-3 8M33 43l-3 8M46 43l-3 8"></path>
        """,
        "snow": """
          <path d="M13 34h35c7 0 12-5 12-12s-6-12-13-11c-4-11-21-12-26 0c-9 0-16 5-16 13c0 6 4 10 8 10z"></path>
          <path d="M22 43v10M17 48h10M18 44l8 8M26 44l-8 8M43 43v10M38 48h10M39 44l8 8M47 44l-8 8"></path>
        """,
        "thunder": """
          <path d="M13 34h35c7 0 12-5 12-12s-6-12-13-11c-4-11-21-12-26 0c-9 0-16 5-16 13c0 6 4 10 8 10z"></path>
          <path d="M34 37l-8 13h9l-5 10l15-17h-9l5-6z"></path>
        """,
        "fog": """
          <path d="M13 31h35c7 0 12-5 12-12s-6-12-13-11c-4-11-21-12-26 0c-9 0-16 5-16 13c0 6 4 10 8 10z"></path>
          <path d="M10 42h44M16 51h34"></path>
        """,
        "unknown": """
          <circle cx="32" cy="32" r="22"></circle>
          <path d="M25 25c1-6 13-7 14 0c1 5-5 7-7 11v3M32 49h.1"></path>
        """,
    }
    return f"""
      <svg class="weather-logo {class_name}" viewBox="0 0 64 64" aria-hidden="true">
        {icons[kind]}
      </svg>
    """


def fmt_number(value: Any, digits: int = 0) -> str:
    if value is None:
        return "--"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "--"
    if digits == 0:
        return str(int(round(number)))
    return f"{number:.{digits}f}"


def fmt_time(value: Any) -> str:
    if not value:
        return "--:--"
    text = str(value)
    if "T" in text:
        return text.split("T", 1)[1][:5]
    return text[:5]


def parse_weather_time(value: Any, config: Config) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=config.timezone)
    return parsed.astimezone(config.timezone)


def pct(value: Any) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def day_label(value: str, config: Config) -> str:
    try:
        date = dt.date.fromisoformat(value)
    except ValueError:
        return html.escape(value)
    today = dt.datetime.now(config.timezone).date()
    if date == today:
        return "今天"
    if date == today + dt.timedelta(days=1):
        return "明天"
    if date == today + dt.timedelta(days=2):
        return "后天"
    return WEEKDAY_NAMES[date.weekday()]


def forecast_rows(data: dict[str, Any], config: Config) -> str:
    daily = data.get("daily") or {}
    dates = daily.get("time") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    rain_probs = daily.get("precipitation_probability_max") or []
    codes = daily.get("weather_code") or []

    numeric_temps = [
        float(value)
        for value in list(highs) + list(lows)
        if isinstance(value, int | float)
    ]
    min_temp = min(numeric_temps) if numeric_temps else 0.0
    max_temp = max(numeric_temps) if numeric_temps else 1.0
    temp_span = max(max_temp - min_temp, 1.0)

    rows: list[str] = []
    for index, date_value in enumerate(dates[: config.forecast_days]):
        high = highs[index] if index < len(highs) else None
        low = lows[index] if index < len(lows) else None
        code = codes[index] if index < len(codes) else None
        rain_prob = rain_probs[index] if index < len(rain_probs) else None

        try:
            low_num = float(low)
            high_num = float(high)
            left = int(((low_num - min_temp) / temp_span) * 100)
            width = max(5, int(((high_num - low_num) / temp_span) * 100))
        except (TypeError, ValueError):
            left = 0
            width = 0

        rows.append(
            f"""
            <tr>
              <td class="day">{day_label(str(date_value), config)}</td>
              <td class="mini-icon">{weather_icon_svg(code, "mini-logo")}</td>
              <td class="forecast-condition">{html.escape(weather_label(code))}</td>
              <td class="temps">{fmt_number(low)}-{fmt_number(high)}{config.temp_unit}</td>
              <td class="tempbar">
                <div class="bar"><div class="tempfill" style="margin-left:{left}%;width:{width}%;"></div></div>
              </td>
              <td class="rain">雨 {fmt_number(rain_prob)}%</td>
              <td class="rainbar">
                <div class="bar"><div class="rainfill" style="width:{pct(rain_prob)}%;"></div></div>
              </td>
            </tr>
            """
        )

    return "\n".join(rows)


def hourly_snapshots(data: dict[str, Any], config: Config) -> str:
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    codes = hourly.get("weather_code") or []
    parsed_times = [parse_weather_time(value, config) for value in times]
    offsets = [1, 2, 3, 6, 12]
    now = dt.datetime.now(config.timezone).replace(minute=0, second=0, microsecond=0)

    cells: list[str] = []
    for offset in offsets:
        target = now + dt.timedelta(hours=offset)
        index = next(
            (
                idx
                for idx, parsed in enumerate(parsed_times)
                if parsed is not None and parsed >= target
            ),
            None,
        )
        if index is None:
            valid = [
                (idx, abs((parsed - target).total_seconds()))
                for idx, parsed in enumerate(parsed_times)
                if parsed is not None
            ]
            index = min(valid, key=lambda item: item[1])[0] if valid else -1

        code = codes[index] if 0 <= index < len(codes) else None
        temp = temps[index] if 0 <= index < len(temps) else None
        rain_time_class = (
            " hour-time-rain" if weather_kind(code) in {"rain", "thunder"} else ""
        )
        hour_text = (
            parsed_times[index].strftime("%H:%M")
            if 0 <= index < len(parsed_times) and parsed_times[index] is not None
            else "--:--"
        )

        cells.append(
            f"""
            <td>
              <div class="hour-time{rain_time_class}">{hour_text}</div>
              {weather_icon_svg(code, "hour-logo")}
              <div class="hour-temp">{fmt_number(temp)}{config.temp_unit}</div>
              <div class="hour-cond">{html.escape(compact_weather_label(code))}</div>
            </td>
            """
        )

    return "\n".join(cells)


def render_ver_v0(
    data: dict[str, Any],
    config: Config,
    *,
    stale: bool = False,
    error: str | None = None,
) -> str:
    now = dt.datetime.now(config.timezone)
    date_text = f"{now.month}.{now.day:02d}"
    current = data.get("current") or {}
    daily = data.get("daily") or {}

    temp = fmt_number(current.get("temperature_2m"))
    feels_like = fmt_number(current.get("apparent_temperature"))
    humidity = fmt_number(current.get("relative_humidity_2m"))
    precipitation = fmt_number(current.get("precipitation"), 1)
    cloud_cover = fmt_number(current.get("cloud_cover"))
    pressure = fmt_number(current.get("pressure_msl"))
    wind = fmt_number(current.get("wind_speed_10m"))
    current_code = current.get("weather_code")
    condition = weather_label(current_code)
    weather_updated = fmt_time(current.get("time"))

    sunrise = fmt_time((daily.get("sunrise") or [None])[0])
    sunset = fmt_time((daily.get("sunset") or [None])[0])
    today_low = fmt_number((daily.get("temperature_2m_min") or [None])[0])
    today_high = fmt_number((daily.get("temperature_2m_max") or [None])[0])

    safe_location = html.escape(config.location_name)
    safe_condition = html.escape(condition)
    safe_error = html.escape(error or "")

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="{config.page_refresh_seconds}">
  <meta name="viewport" content="width={config.kindle_width}, initial-scale=1">
  <title>Kindle 天气</title>
  <script>
    setTimeout(function () {{ window.location.reload(true); }}, {config.page_refresh_seconds * 1000});
  </script>
  <style>
    html, body {{
      margin: 0;
      padding: 0;
      background: #fff;
      color: #000;
      font-family: SimSun, STSong, "Songti SC", "Source Han Serif SC", "Noto Serif CJK SC", serif;
    }}
    .page {{
      width: {config.kindle_width}px;
      height: {config.kindle_height}px;
      padding: 16px 26px;
      box-sizing: border-box;
      overflow: hidden;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    .top td {{
      vertical-align: bottom;
    }}
    .date {{
      font-size: 58px;
      line-height: 1;
      font-weight: bold;
    }}
    .date-line {{
      white-space: nowrap;
    }}
    .weekday {{
      display: inline-block;
      font-size: 38px;
      font-weight: bold;
      margin-left: 14px;
      vertical-align: baseline;
    }}
    .year {{
      font-size: 20px;
      padding-top: 2px;
    }}
    .clock {{
      text-align: right;
      font-size: 52px;
      font-weight: bold;
    }}
    .location {{
      text-align: right;
      font-size: 18px;
      padding-top: 4px;
      font-weight: bold;
    }}
    .rule {{
      border-top: 5px solid #000;
      margin: 8px 0 10px;
    }}
    .current td {{
      vertical-align: middle;
    }}
    .temp {{
      font-size: 118px;
      line-height: .9;
      font-weight: bold;
      width: 47%;
      white-space: nowrap;
    }}
    .unit {{
      font-size: 34px;
      vertical-align: top;
    }}
    .weather-logo {{
      display: inline-block;
      fill: none;
      stroke: #000;
      stroke-width: 4;
      stroke-linecap: round;
      stroke-linejoin: round;
      vertical-align: middle;
    }}
    .main-logo {{
      display: block;
      width: 94px;
      height: 94px;
      margin-left: auto;
      margin-bottom: 2px;
    }}
    .mini-logo {{
      width: 27px;
      height: 27px;
      stroke-width: 5;
    }}
    .condition {{
      font-size: 32px;
      line-height: 1.12;
      text-align: right;
      font-weight: bold;
    }}
    .range {{
      display: inline-block;
      font-size: 27px;
      text-align: left;
      font-weight: bold;
      margin-top: 5px;
      padding-top: 4px;
      border-top: 2px solid #000;
    }}
    .metrics {{
      margin: 12px 0 10px;
      border-top: 2px solid #000;
      border-bottom: 2px solid #000;
    }}
    .metrics td {{
      width: 25%;
      padding: 6px 4px;
      border-right: 2px solid #000;
      text-align: center;
    }}
    .metrics td:last-child {{
      border-right: 0;
    }}
    .metric-label {{
      display: block;
      font-size: 14px;
      letter-spacing: 0;
    }}
    .metric-value {{
      display: block;
      font-size: 20px;
      font-weight: bold;
      margin-top: 2px;
    }}
    h2 {{
      margin: 0 0 5px;
      padding: 0;
      font-size: 22px;
      line-height: 1;
    }}
    .forecast {{
      font-size: 16px;
      border-top: 3px solid #000;
    }}
    .forecast td {{
      padding: 7px 4px;
      border-bottom: 2px solid #000;
      vertical-align: middle;
    }}
    .day {{
      width: 50px;
      font-weight: bold;
    }}
    .mini-icon {{
      width: 34px;
      text-align: center;
    }}
    .forecast-condition {{
      width: 72px;
    }}
    .temps {{
      width: 88px;
      white-space: nowrap;
      text-align: right;
      font-weight: bold;
    }}
    .tempbar {{
      width: 110px;
    }}
    .rain {{
      width: 58px;
      text-align: right;
      font-weight: bold;
    }}
    .rainbar {{
      width: 70px;
    }}
    .bar {{
      height: 11px;
      border: 2px solid #000;
      background: #fff;
      box-sizing: border-box;
      overflow: hidden;
    }}
    .tempfill {{
      height: 100%;
      background: #000;
    }}
    .rainfill {{
      height: 100%;
      background: #000;
    }}
    .footer {{
      margin-top: 8px;
      font-size: 12px;
      text-align: center;
    }}
    .notice {{
      margin-top: 8px;
      font-size: 14px;
      text-align: center;
    }}
  </style>
</head>
<body>
  <div class="page">
    <table class="top">
      <tr>
        <td>
          <div class="date-line"><span class="date">{date_text}</span><span class="weekday">{WEEKDAY_NAMES[now.weekday()]}</span></div>
          <div class="year">{now.strftime("%Y年")}</div>
        </td>
        <td>
          <div class="clock">{now.strftime("%H:%M")}</div>
          <div class="location">{safe_location}</div>
        </td>
      </tr>
    </table>

    <div class="rule"></div>

    <table class="current">
      <tr>
        <td class="temp">{temp}<span class="unit">{config.temp_unit}</span></td>
        <td style="text-align:right;">
          {weather_icon_svg(current_code, "main-logo")}
          <div class="condition">{safe_condition}</div>
          <div class="range">今日 {today_low}-{today_high}{config.temp_unit}</div>
        </td>
      </tr>
    </table>

    <table class="metrics">
      <tr>
        <td><span class="metric-label">体感</span><span class="metric-value">{feels_like}{config.temp_unit}</span></td>
        <td><span class="metric-label">湿度</span><span class="metric-value">{humidity}%</span></td>
        <td><span class="metric-label">风速</span><span class="metric-value">{wind} {config.wind_unit}</span></td>
        <td><span class="metric-label">降水</span><span class="metric-value">{precipitation} 毫米</span></td>
      </tr>
    </table>

    <table class="metrics">
      <tr>
        <td><span class="metric-label">日出</span><span class="metric-value">{sunrise}</span></td>
        <td><span class="metric-label">日落</span><span class="metric-value">{sunset}</span></td>
        <td><span class="metric-label">云量</span><span class="metric-value">{cloud_cover}%</span></td>
        <td><span class="metric-label">气压</span><span class="metric-value">{pressure} hPa</span></td>
      </tr>
    </table>

    <h2>未来几天</h2>
    <table class="forecast">
      {forecast_rows(data, config)}
    </table>

    <div class="footer">页面刷新 {now.strftime("%H:%M")} | 天气更新 {weather_updated}</div>
    {f'<div class="notice">{safe_error}</div>' if safe_error else ''}
  </div>
</body>
</html>
"""


def render_ver_v1(
    data: dict[str, Any],
    config: Config,
    *,
    stale: bool = False,
    error: str | None = None,
    version_label: str = "v1",
) -> str:
    now = dt.datetime.now(config.timezone)
    date_text = f"{now.month}.{now.day:02d}"
    current = data.get("current") or {}
    daily = data.get("daily") or {}

    temp = fmt_number(current.get("temperature_2m"))
    feels_like = fmt_number(current.get("apparent_temperature"))
    humidity = fmt_number(current.get("relative_humidity_2m"))
    precipitation = fmt_number(current.get("precipitation"), 1)
    wind = fmt_number(current.get("wind_speed_10m"))
    current_code = current.get("weather_code")
    condition = weather_label(current_code)
    weather_updated = fmt_time(current.get("time"))
    air_current = (data.get("air_quality") or {}).get("current") or {}
    aqi = fmt_number(air_current.get("us_aqi"))
    aqi_updated = fmt_time(air_current.get("time"))

    today_low = fmt_number((daily.get("temperature_2m_min") or [None])[0])
    today_high = fmt_number((daily.get("temperature_2m_max") or [None])[0])

    safe_location = html.escape(config.location_name)
    safe_condition = html.escape(condition)
    condition_class = "condition condition-long" if len(condition) >= 4 else "condition"
    safe_aqi_level = html.escape(aqi_level(air_current.get("us_aqi")))
    safe_error = html.escape(error or "")
    safe_version = html.escape(version_label)
    is_v25 = version_label == "v2.5"

    font_family = (
        'Arial, Helvetica, "Microsoft YaHei", "Noto Sans CJK SC", sans-serif'
        if is_v25
        else 'SimSun, STSong, "Songti SC", "Source Han Serif SC", "Noto Serif CJK SC", serif'
    )
    temp_font_size = 190 if is_v25 else 174
    temp_width = "39%" if is_v25 else "40%"
    unit_font_size = 58 if is_v25 else 54
    today_stack_width = "22%" if is_v25 else "20%"
    today_stack_padding = "18px" if is_v25 else "30px"
    today_temp_font_size = 68 if is_v25 else 58
    today_slash_font_size = 38 if is_v25 else 34
    today_unit_font_size = 30 if is_v25 else 26
    current_bottom_margin = 14 if is_v25 else 0
    main_logo_size = 112 if is_v25 else 100
    weather_panel_margin = -16 if is_v25 else -12
    condition_font_size = 72 if is_v25 else 62
    condition_long_font_size = 54 if is_v25 else 48
    aqi_font_size = 52 if is_v25 else 44

    metrics_html = ""
    if not is_v25:
        metrics_html = f"""
    <table class="metrics">
      <tr>
        <td><span class="metric-label">体<br>感</span><span class="metric-value">{feels_like}{config.temp_unit}</span></td>
        <td><span class="metric-label">湿<br>度</span><span class="metric-value">{humidity}%</span></td>
        <td><span class="metric-label">风<br>速</span><span class="metric-value">{wind}级</span></td>
        <td><span class="metric-label">降<br>水</span><span class="metric-value">{precipitation}</span></td>
      </tr>
    </table>
"""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="{config.page_refresh_seconds}">
  <meta name="viewport" content="width={config.kindle_width}, initial-scale=1">
  <title>Kindle 天气 {safe_version}</title>
  <script>
    setTimeout(function () {{ window.location.reload(true); }}, {config.page_refresh_seconds * 1000});
  </script>
  <style>
    html, body {{
      margin: 0;
      padding: 0;
      background: #fff;
      color: #000;
      font-family: {font_family};
    }}
    .page {{
      width: {config.kindle_width}px;
      height: {config.kindle_height}px;
      padding: 12px 14px 10px;
      box-sizing: border-box;
      overflow: hidden;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    .top td {{
      vertical-align: bottom;
    }}
    .date {{
      font-size: 88px;
      line-height: .92;
      font-weight: bold;
      white-space: nowrap;
    }}
    .weekday {{
      display: inline-block;
      font-size: 58px;
      font-weight: bold;
      margin-left: 12px;
      vertical-align: baseline;
    }}
    .clock {{
      text-align: right;
      font-size: 76px;
      line-height: .92;
      font-weight: bold;
      white-space: nowrap;
    }}
    .location {{
      text-align: right;
      font-size: 31px;
      line-height: 1.05;
      padding-top: 4px;
      font-weight: bold;
    }}
    .rule {{
      border-top: 6px solid #000;
      margin: 7px 0 7px;
    }}
    .current td {{
      vertical-align: middle;
    }}
    .current {{
      margin-bottom: {current_bottom_margin}px;
    }}
    .temp {{
      font-size: {temp_font_size}px;
      line-height: .81;
      font-weight: bold;
      width: {temp_width};
      white-space: nowrap;
    }}
    .unit {{
      font-size: {unit_font_size}px;
      vertical-align: top;
    }}
    .today-stack {{
      width: {today_stack_width};
      padding-left: 0;
      padding-right: {today_stack_padding};
      text-align: center;
      font-weight: bold;
    }}
    .today-hi, .today-lo {{
      font-size: {today_temp_font_size}px;
      line-height: .9;
      white-space: nowrap;
    }}
    .today-slash {{
      font-size: {today_slash_font_size}px;
      line-height: .62;
      font-weight: bold;
      margin: 2px 0 4px;
    }}
    .today-unit {{
      font-size: {today_unit_font_size}px;
    }}
    .weather-logo {{
      display: inline-block;
      fill: none;
      stroke: #000;
      stroke-width: 4;
      stroke-linecap: round;
      stroke-linejoin: round;
      vertical-align: middle;
    }}
    .main-logo-v1 {{
      display: block;
      width: {main_logo_size}px;
      height: {main_logo_size}px;
      margin-left: auto;
      margin-right: auto;
      margin-bottom: 2px;
    }}
    .weather-panel {{
      margin-top: {weather_panel_margin}px;
    }}
    .condition {{
      font-size: {condition_font_size}px;
      line-height: 1;
      text-align: center;
      font-weight: bold;
      white-space: nowrap;
    }}
    .condition-long {{
      font-size: {condition_long_font_size}px;
    }}
    .aqi {{
      display: block;
      font-size: {aqi_font_size}px;
      line-height: 1;
      text-align: center;
      font-weight: bold;
      margin-top: 3px;
      padding-top: 4px;
      border-top: 3px solid #000;
    }}
    .metrics {{
      margin: 6px 0 6px;
      border-top: 3px solid #000;
      border-bottom: 3px solid #000;
    }}
    .metrics td {{
      width: 25%;
      padding: 6px 2px;
      border-right: 3px solid #000;
      text-align: left;
    }}
    .metrics td:last-child {{
      border-right: 0;
    }}
    .metric-label {{
      display: inline-block;
      width: 30px;
      font-size: 24px;
      line-height: .92;
      letter-spacing: 0;
      vertical-align: middle;
      text-align: center;
      font-weight: bold;
    }}
    .metric-value {{
      display: inline-block;
      width: calc(100% - 34px);
      font-size: 42px;
      line-height: 1;
      font-weight: bold;
      white-space: nowrap;
      vertical-align: middle;
      text-align: center;
    }}
    .hourly {{
      table-layout: fixed;
      border-top: 4px solid #000;
      border-bottom: 4px solid #000;
    }}
    .hourly td {{
      width: 20%;
      padding: 7px 1px 7px;
      border-right: 3px solid #000;
      text-align: center;
      vertical-align: top;
    }}
    .hourly td:last-child {{
      border-right: 0;
    }}
    .hour-time {{
      display: inline-block;
      min-width: 96px;
      padding: 2px 4px 3px;
      font-family: Arial, Helvetica, "Microsoft YaHei", sans-serif;
      font-size: 42px;
      line-height: 1;
      font-weight: 900;
      white-space: nowrap;
      box-sizing: border-box;
    }}
    .hour-time-rain {{
      background: #000;
      color: #fff;
      -webkit-text-stroke: .7px #fff;
      text-shadow: 1px 0 #fff, -1px 0 #fff, 0 1px #fff, 0 -1px #fff;
    }}
    .hour-logo {{
      width: 76px;
      height: 76px;
      margin-top: 2px;
      stroke-width: 4.5;
    }}
    .hour-temp {{
      font-size: 43px;
      line-height: 1;
      font-weight: bold;
      margin-top: 1px;
      white-space: nowrap;
    }}
    .hour-cond {{
      font-size: 38px;
      line-height: .95;
      font-weight: bold;
      margin-top: 2px;
    }}
    .footer {{
      margin-top: 4px;
      font-size: 14px;
      line-height: 1;
      text-align: center;
      font-weight: bold;
    }}
    .notice {{
      margin-top: 6px;
      font-size: 18px;
      line-height: 1.05;
      text-align: center;
    }}
  </style>
</head>
<body>
  <div class="page">
    <table class="top">
      <tr>
        <td>
          <span class="date">{date_text}</span><span class="weekday">{WEEKDAY_NAMES[now.weekday()]}</span>
        </td>
        <td>
          <div class="clock">{now.strftime("%H:%M")}</div>
          <div class="location">{safe_location}</div>
        </td>
      </tr>
    </table>

    <div class="rule"></div>

    <table class="current">
      <tr>
        <td class="temp">{temp}<span class="unit">{config.temp_unit}</span></td>
        <td class="today-stack">
          <div class="today-hi">{today_high}<span class="today-unit">{config.temp_unit}</span></div>
          <div class="today-slash">/</div>
          <div class="today-lo">{today_low}<span class="today-unit">{config.temp_unit}</span></div>
        </td>
        <td style="text-align:right;">
          <div class="weather-panel">
            {weather_icon_svg(current_code, "main-logo-v1")}
            <div class="{condition_class}">{safe_condition}</div>
            <div class="aqi">{safe_aqi_level} {aqi}</div>
          </div>
        </td>
      </tr>
    </table>

{metrics_html}

    <table class="hourly">
      <tr>
        {hourly_snapshots(data, config)}
      </tr>
    </table>
    <div class="footer">刷新 {now.strftime("%H:%M")} | 天气 {weather_updated} | 空气 {aqi_updated}</div>
    {f'<div class="notice">{safe_error}</div>' if safe_error else ''}
  </div>
</body>
</html>
"""


def render_ver_v25(
    data: dict[str, Any],
    config: Config,
    *,
    stale: bool = False,
    error: str | None = None,
) -> str:
    return render_ver_v1(
        data,
        config,
        stale=stale,
        error=error,
        version_label="v2.5",
    )


def render_error(config: Config, message: str) -> str:
    now = dt.datetime.now(config.timezone)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="{config.page_refresh_seconds}">
  <meta name="viewport" content="width={config.kindle_width}, initial-scale=1">
  <title>Kindle 天气</title>
  <script>
    setTimeout(function () {{ window.location.reload(true); }}, {config.page_refresh_seconds * 1000});
  </script>
  <style>
    html, body {{ margin: 0; padding: 0; background: #fff; color: #000; font-family: "Songti SC", "Source Han Serif SC", "Noto Serif CJK SC", STSong, "Microsoft YaHei", SimSun, serif; }}
    .page {{ width: {config.kindle_width}px; height: {config.kindle_height}px; padding: 42px; box-sizing: border-box; }}
    h1 {{ font-size: 48px; margin: 0 0 24px; }}
    p {{ font-size: 24px; line-height: 1.35; }}
    .small {{ font-size: 18px; margin-top: 24px; }}
  </style>
</head>
<body>
  <div class="page">
    <h1>天气暂不可用</h1>
    <p>{html.escape(message)}</p>
    <p class="small">下次重试：{now.strftime("%Y-%m-%d %H:%M")}。页面每 {config.page_refresh_seconds // 60} 分钟刷新。</p>
  </div>
</body>
</html>
"""


class KindleWeatherHandler(BaseHTTPRequestHandler):
    config: Config
    cache: WeatherCache

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path in {
            "/",
            "/kindle",
            "/index.html",
            "/v2.5",
            "/v25",
            "/v2_5",
            "/ver_v2.5",
            "/ver_v25",
            "/ver_v2_5",
        }:
            self.handle_page("v25")
            return
        if path in {
            "/v1",
            "/ver_v1",
        }:
            self.handle_page("v1")
            return
        if path in {
            "/classic",
            "/kindle/classic",
            "/v0",
            "/ver_v0",
        }:
            self.handle_page("v0")
            return
        if path == "/api/weather":
            self.handle_api()
            return
        if path == "/healthz":
            self.write_bytes(b"ok\n", "text/plain; charset=utf-8")
            return
        self.send_error(404)

    def handle_page(self, version: str) -> None:
        try:
            data, stale, error = self.cache.get()
            if version == "v25":
                html_text = render_ver_v25(data, self.config, stale=stale, error=error)
            elif version == "v1":
                html_text = render_ver_v1(data, self.config, stale=stale, error=error)
            else:
                html_text = render_ver_v0(data, self.config, stale=stale, error=error)
            self.write_bytes(html_text.encode("utf-8"), "text/html; charset=utf-8")
        except Exception as exc:  # noqa: BLE001 - show errors on the always-on display.
            html_text = render_error(self.config, str(exc))
            self.write_bytes(
                html_text.encode("utf-8"),
                "text/html; charset=utf-8",
                status=503,
            )

    def handle_api(self) -> None:
        try:
            data, stale, error = self.cache.get()
            payload = {
                "stale": stale,
                "error": error,
                "data": data,
            }
            self.write_bytes(
                json.dumps(payload).encode("utf-8"),
                "application/json; charset=utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            payload = {"error": str(exc)}
            self.write_bytes(
                json.dumps(payload).encode("utf-8"),
                "application/json; charset=utf-8",
                status=503,
            )

    def write_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if content_type.startswith("text/html"):
            self.send_header("Refresh", str(self.config.page_refresh_seconds))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(
            f"{self.address_string()} - {dt.datetime.now().isoformat(timespec='seconds')} - "
            + fmt % args,
            flush=True,
        )


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def main() -> None:
    config = Config()
    cache = WeatherCache(config)
    KindleWeatherHandler.config = config
    KindleWeatherHandler.cache = cache

    server = ThreadingHTTPServer((config.host, config.port), KindleWeatherHandler)
    print(
        f"Kindle weather wall listening on http://{config.host}:{config.port} "
        f"for {config.location_name} ({config.latitude}, {config.longitude})",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
