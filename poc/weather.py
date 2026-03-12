"""
Live weather integration via Open-Meteo API (free, no key required).

Provides current conditions + 24-hour forecast for NZ locations,
plus astronomical sun position calculations for light conditions.
"""
import math
import time
import threading
from datetime import datetime, timezone, timedelta

import requests

# NZ timezone offset (NZST=+12, NZDT=+13) — we let Open-Meteo handle this
NZ_TZ = timezone(timedelta(hours=13))  # NZDT (summer), conservative

# Cache weather data (don't hammer the API)
_weather_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL = 600  # 10 minutes

# Representative points across NZ for weather sampling
# We don't need per-cell weather — NZ is small enough that ~8 points cover it
NZ_WEATHER_POINTS = {
    "auckland":    (-36.85, 174.76),
    "hamilton":    (-37.79, 175.28),
    "tauranga":    (-37.69, 176.17),
    "wellington":  (-41.29, 174.78),
    "christchurch":(-43.53, 172.64),
    "dunedin":     (-45.87, 170.50),
    "napier":      (-39.49, 176.92),
    "nelson":      (-41.27, 173.28),
}


def _sun_altitude(lat, lng, dt=None):
    """
    Calculate solar altitude angle in degrees for a given location and time.
    Positive = above horizon, negative = below.
    Uses simplified astronomical formula (accurate to ~1 degree).
    """
    if dt is None:
        dt = datetime.now(timezone.utc)

    # Day of year
    n = dt.timetuple().tm_yday

    # Solar declination (radians)
    declination = math.radians(-23.44 * math.cos(math.radians(360 / 365 * (n + 10))))

    # Hour angle
    # UTC hour as decimal
    utc_hours = dt.hour + dt.minute / 60 + dt.second / 3600
    # Solar noon offset for longitude (15 degrees per hour)
    solar_time = utc_hours + lng / 15
    hour_angle = math.radians(15 * (solar_time - 12))

    # Solar altitude
    lat_rad = math.radians(lat)
    sin_alt = (math.sin(lat_rad) * math.sin(declination) +
               math.cos(lat_rad) * math.cos(declination) * math.cos(hour_angle))
    altitude = math.degrees(math.asin(max(-1, min(1, sin_alt))))
    return altitude


def get_light_condition(lat, lng, dt=None):
    """
    Determine light condition based on sun position.
    Returns: 'day', 'twilight', or 'dark'
    """
    alt = _sun_altitude(lat, lng, dt)
    if alt > 6:
        return "day"
    elif alt > -6:
        return "twilight"
    else:
        return "dark"


def get_light_for_nz(dt=None):
    """
    Get the predominant light condition across NZ right now.
    Uses Wellington as the reference point (central NZ).
    """
    return get_light_condition(-41.29, 174.78, dt)


def fetch_weather(lat, lng):
    """
    Fetch current weather + 24hr forecast from Open-Meteo for a single point.
    Returns dict with current conditions and hourly forecast.
    """
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lng,
        "current": ",".join([
            "temperature_2m", "relative_humidity_2m",
            "precipitation", "rain", "weather_code",
            "cloud_cover", "wind_speed_10m", "wind_gusts_10m",
            "is_day",
        ]),
        "hourly": ",".join([
            "temperature_2m", "precipitation", "precipitation_probability",
            "weather_code", "cloud_cover", "visibility",
            "wind_speed_10m", "wind_gusts_10m",
        ]),
        "forecast_hours": 24,
        "timezone": "Pacific/Auckland",
    }

    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def fetch_nz_weather():
    """
    Fetch weather for representative NZ points.
    Returns a dict keyed by region name with current + forecast data.
    """
    cache_key = "nz_weather"
    with _cache_lock:
        cached = _weather_cache.get(cache_key)
        if cached and time.time() - cached["fetched_at"] < CACHE_TTL:
            return cached["data"]

    # Batch request — Open-Meteo supports comma-separated coords
    lats = ",".join(str(v[0]) for v in NZ_WEATHER_POINTS.values())
    lngs = ",".join(str(v[1]) for v in NZ_WEATHER_POINTS.values())

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lats,
        "longitude": lngs,
        "current": ",".join([
            "temperature_2m", "relative_humidity_2m",
            "precipitation", "rain", "weather_code",
            "cloud_cover", "wind_speed_10m", "wind_gusts_10m",
            "is_day",
        ]),
        "hourly": ",".join([
            "temperature_2m", "precipitation", "precipitation_probability",
            "weather_code", "cloud_cover", "visibility",
            "wind_speed_10m", "wind_gusts_10m",
        ]),
        "forecast_hours": 24,
        "timezone": "Pacific/Auckland",
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        print(f"Weather fetch failed: {e}", flush=True)
        return None

    # Parse multi-location response (list of objects)
    result = {}
    names = list(NZ_WEATHER_POINTS.keys())
    if isinstance(raw, list):
        for i, name in enumerate(names):
            if i < len(raw):
                result[name] = raw[i]
    else:
        # Single location fallback
        result[names[0]] = raw

    with _cache_lock:
        _weather_cache[cache_key] = {"data": result, "fetched_at": time.time()}

    return result


def get_current_conditions():
    """
    Get current weather conditions summarised for risk scoring.
    Returns dict with:
        is_rain (bool), is_dark (bool), light (str),
        rain_mm (float), temp_c (float), wind_kmh (float),
        wind_gust_kmh (float), visibility_m (float),
        weather_description (str), forecast (list of hourly dicts)
    """
    weather_data = fetch_nz_weather()
    light = get_light_for_nz()

    if weather_data is None:
        # Fallback — can't reach API, use light condition only
        return {
            "is_rain": False,
            "is_dark": light == "dark",
            "light": light,
            "rain_mm": 0,
            "temp_c": None,
            "wind_kmh": None,
            "wind_gust_kmh": None,
            "visibility_m": None,
            "weather_description": "Weather data unavailable",
            "weather_code": None,
            "regions": {},
            "forecast": [],
        }

    # Aggregate across regions — use the worst conditions (most rain, lowest visibility)
    max_rain = 0
    min_temp = 100
    max_wind = 0
    max_gust = 0
    min_visibility = 999999
    worst_code = 0
    regions_summary = {}

    for region, data in weather_data.items():
        current = data.get("current", {})
        rain = current.get("precipitation", 0) or 0
        temp = current.get("temperature_2m", 15)
        wind = current.get("wind_speed_10m", 0) or 0
        gust = current.get("wind_gusts_10m", 0) or 0
        code = current.get("weather_code", 0) or 0

        max_rain = max(max_rain, rain)
        min_temp = min(min_temp, temp)
        max_wind = max(max_wind, wind)
        max_gust = max(max_gust, gust)
        worst_code = max(worst_code, code)

        regions_summary[region] = {
            "rain_mm": rain,
            "temp_c": temp,
            "wind_kmh": wind,
            "wind_gust_kmh": gust,
            "weather_code": code,
            "weather_description": _weather_code_to_text(code),
            "is_day": current.get("is_day", 1),
        }

    # Build 24-hour forecast from the most representative point (Wellington)
    wgtn = weather_data.get("wellington", {})
    hourly = wgtn.get("hourly", {})
    forecast = []
    times = hourly.get("time", [])
    for i, t in enumerate(times):
        forecast.append({
            "time": t,
            "rain_mm": (hourly.get("precipitation", []) or [])[i] if i < len(hourly.get("precipitation", [])) else 0,
            "rain_prob": (hourly.get("precipitation_probability", []) or [])[i] if i < len(hourly.get("precipitation_probability", [])) else 0,
            "temp_c": (hourly.get("temperature_2m", []) or [])[i] if i < len(hourly.get("temperature_2m", [])) else None,
            "wind_kmh": (hourly.get("wind_speed_10m", []) or [])[i] if i < len(hourly.get("wind_speed_10m", [])) else 0,
            "visibility_m": (hourly.get("visibility", []) or [])[i] if i < len(hourly.get("visibility", [])) else None,
            "weather_code": (hourly.get("weather_code", []) or [])[i] if i < len(hourly.get("weather_code", [])) else 0,
        })

    # Get visibility from hourly data (current doesn't include it)
    if forecast:
        min_visibility = min((f.get("visibility_m") or 999999) for f in forecast[:3])

    is_rain = max_rain > 0.1 or worst_code in (51, 53, 55, 61, 63, 65, 80, 81, 82, 95, 96, 99)

    return {
        "is_rain": is_rain,
        "is_dark": light == "dark",
        "light": light,
        "rain_mm": round(max_rain, 1),
        "temp_c": round(min_temp, 1),
        "wind_kmh": round(max_wind, 1),
        "wind_gust_kmh": round(max_gust, 1),
        "visibility_m": round(min_visibility),
        "weather_code": worst_code,
        "weather_description": _weather_code_to_text(worst_code),
        "ice_risk": min_temp <= 3,
        "high_wind": max_gust >= 70,
        "poor_visibility": min_visibility < 1000,
        "regions": regions_summary,
        "forecast": forecast,
    }


def get_risk_description(conditions):
    """
    Generate a plain-language risk summary from current conditions.
    Returns a list of risk strings.
    """
    risks = []
    if conditions.get("is_rain"):
        mm = conditions.get("rain_mm", 0)
        if mm >= 10:
            risks.append(f"Heavy rain ({mm}mm/h) — significantly reduced grip and visibility")
        elif mm >= 2:
            risks.append(f"Moderate rain ({mm}mm/h) — reduced grip and spray")
        else:
            risks.append("Light rain — roads may be slippery")

    if conditions.get("ice_risk"):
        risks.append(f"Temperature {conditions['temp_c']}°C — ice risk on exposed roads")

    if conditions.get("high_wind"):
        risks.append(f"Wind gusts {conditions['wind_gust_kmh']:.0f}km/h — crosswind risk for high-sided vehicles")

    if conditions.get("poor_visibility"):
        vis = conditions.get("visibility_m", 0)
        if vis < 200:
            risks.append(f"Very poor visibility ({vis}m) — fog or heavy precipitation")
        else:
            risks.append(f"Reduced visibility ({vis}m)")

    if conditions.get("is_dark"):
        risks.append("Dark — reduced visibility, higher severity risk for pedestrians/cyclists")
    elif conditions.get("light") == "twilight":
        risks.append("Twilight — transitional lighting, glare risk")

    if not risks:
        risks.append("Conditions are favourable — standard risk levels apply")

    return risks


def _weather_code_to_text(code):
    """Convert WMO weather code to human-readable description."""
    codes = {
        0: "Clear sky",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Fog",
        48: "Depositing rime fog",
        51: "Light drizzle",
        53: "Moderate drizzle",
        55: "Dense drizzle",
        56: "Light freezing drizzle",
        57: "Dense freezing drizzle",
        61: "Slight rain",
        63: "Moderate rain",
        65: "Heavy rain",
        66: "Light freezing rain",
        67: "Heavy freezing rain",
        71: "Slight snowfall",
        73: "Moderate snowfall",
        75: "Heavy snowfall",
        77: "Snow grains",
        80: "Slight rain showers",
        81: "Moderate rain showers",
        82: "Violent rain showers",
        85: "Slight snow showers",
        86: "Heavy snow showers",
        95: "Thunderstorm",
        96: "Thunderstorm with slight hail",
        99: "Thunderstorm with heavy hail",
    }
    return codes.get(code, f"Unknown ({code})")
