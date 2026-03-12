"""
Live weather + calendar integration.

Provides:
- Current conditions + 24-hour forecast via Open-Meteo API (free, no key)
- Astronomical sun position for light conditions
- NZ public holiday and holiday period detection
"""
import math
import time
import threading
from datetime import datetime, timezone, timedelta, date

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

    # Holiday risk
    holiday = conditions.get("holiday")
    if holiday and holiday.get("is_holiday"):
        period = holiday.get("period_name") or holiday.get("holiday_name") or "Holiday period"
        risks.append(f"{period} — historically elevated crash rates during holiday periods")
    elif holiday and holiday.get("is_long_weekend"):
        risks.append("Long weekend — elevated traffic volumes and crash risk")

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


# ---------------------------------------------------------------------------
# NZ Holiday / Long Weekend Detection
# ---------------------------------------------------------------------------

def _easter_date(year):
    """Compute Easter Sunday using the Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def get_nz_holidays(year):
    """
    Return a dict of NZ public holidays and official holiday periods for a given year.
    Each entry maps a date to a dict with 'name' and 'period' (the CAS-style period name).
    """
    easter = _easter_date(year)

    # Fixed-date holidays
    holidays = {
        date(year, 1, 1): {"name": "New Year's Day", "period": "Christmas New Year"},
        date(year, 1, 2): {"name": "Day after New Year's", "period": "Christmas New Year"},
        date(year, 2, 6): {"name": "Waitangi Day", "period": "Waitangi Day"},
        date(year, 4, 25): {"name": "ANZAC Day", "period": "Easter/Anzac"},
        date(year, 12, 25): {"name": "Christmas Day", "period": "Christmas New Year"},
        date(year, 12, 26): {"name": "Boxing Day", "period": "Christmas New Year"},
    }

    # Mondayisation: if a fixed holiday falls on Sat/Sun, the following Mon (or Tue) is observed
    for d, info in list(holidays.items()):
        if d.weekday() == 5:  # Saturday
            holidays[d + timedelta(days=2)] = {"name": info["name"] + " (observed)", "period": info["period"]}
        elif d.weekday() == 6:  # Sunday
            holidays[d + timedelta(days=1)] = {"name": info["name"] + " (observed)", "period": info["period"]}

    # Easter (moveable)
    holidays[easter + timedelta(days=-2)] = {"name": "Good Friday", "period": "Easter/Anzac"}
    holidays[easter + timedelta(days=-1)] = {"name": "Easter Saturday", "period": "Easter/Anzac"}
    holidays[easter] = {"name": "Easter Sunday", "period": "Easter/Anzac"}
    holidays[easter + timedelta(days=1)] = {"name": "Easter Monday", "period": "Easter/Anzac"}

    # King's Birthday — first Monday in June
    jun1 = date(year, 6, 1)
    kings_bday = jun1 + timedelta(days=(7 - jun1.weekday()) % 7)
    holidays[kings_bday] = {"name": "King's Birthday", "period": "Queens Birthday"}

    # Matariki — varies (gazetted dates)
    matariki_dates = {
        2024: date(2024, 6, 28), 2025: date(2025, 6, 20),
        2026: date(2026, 7, 10), 2027: date(2027, 6, 25),
        2028: date(2028, 7, 14), 2029: date(2029, 7, 6),
        2030: date(2030, 6, 21),
    }
    if year in matariki_dates:
        holidays[matariki_dates[year]] = {"name": "Matariki", "period": "Matariki"}

    # Labour Day — fourth Monday in October
    oct1 = date(year, 10, 1)
    first_mon = oct1 + timedelta(days=(7 - oct1.weekday()) % 7)
    labour = first_mon + timedelta(weeks=3)
    holidays[labour] = {"name": "Labour Day", "period": "Labour Weekend"}

    return holidays


def get_nz_holiday_periods(year):
    """
    Return extended holiday PERIODS (not just single days) matching CAS convention.
    These are the multi-day periods when crash rates are historically elevated.
    """
    easter = _easter_date(year)

    periods = []

    # Christmas/New Year: ~20 Dec to ~5 Jan
    periods.append({
        "name": "Christmas New Year",
        "start": date(year, 12, 20),
        "end": date(year + 1, 1, 5),
    })
    # Also catch the start of the year
    if year > 2000:
        periods.append({
            "name": "Christmas New Year",
            "start": date(year - 1, 12, 20),
            "end": date(year, 1, 5),
        })

    # Easter/ANZAC: Good Friday -1 to Easter Monday +1, plus ANZAC
    periods.append({
        "name": "Easter/Anzac",
        "start": easter + timedelta(days=-3),
        "end": easter + timedelta(days=2),
    })

    # Queen's/King's Birthday weekend (Sat-Mon)
    jun1 = date(year, 6, 1)
    kings_bday = jun1 + timedelta(days=(7 - jun1.weekday()) % 7)
    periods.append({
        "name": "Queens Birthday",
        "start": kings_bday + timedelta(days=-2),
        "end": kings_bday + timedelta(days=1),
    })

    # Labour Weekend (Sat-Mon)
    oct1 = date(year, 10, 1)
    first_mon = oct1 + timedelta(days=(7 - oct1.weekday()) % 7)
    labour = first_mon + timedelta(weeks=3)
    periods.append({
        "name": "Labour Weekend",
        "start": labour + timedelta(days=-2),
        "end": labour + timedelta(days=1),
    })

    return periods


def get_current_holiday_info(dt=None):
    """
    Check if the given date falls within a NZ holiday period.
    Returns dict with:
        is_holiday (bool), holiday_name (str or None),
        period_name (str or None), is_long_weekend (bool),
        next_holiday (dict or None)
    """
    if dt is None:
        dt = datetime.now(timezone(timedelta(hours=13)))  # NZDT
    today = dt.date() if isinstance(dt, datetime) else dt

    year = today.year
    holidays = get_nz_holidays(year)
    periods = get_nz_holiday_periods(year)

    # Check if today is a public holiday
    holiday_info = holidays.get(today)

    # Check if today falls within a holiday period
    current_period = None
    for p in periods:
        if p["start"] <= today <= p["end"]:
            current_period = p["name"]
            break

    # Check for long weekend (Fri-Mon around a public holiday)
    is_long_weekend = False
    dow = today.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun
    if dow in (4, 5, 6, 0):  # Fri-Mon
        for d_offset in range(-1, 4):
            check = today + timedelta(days=d_offset)
            if check in holidays:
                is_long_weekend = True
                break

    # Find next upcoming holiday
    next_holiday = None
    future_dates = sorted(d for d in holidays if d > today)
    if future_dates:
        nd = future_dates[0]
        days_until = (nd - today).days
        next_holiday = {
            "name": holidays[nd]["name"],
            "date": nd.isoformat(),
            "days_until": days_until,
        }

    return {
        "is_holiday": holiday_info is not None or current_period is not None,
        "holiday_name": holiday_info["name"] if holiday_info else None,
        "period_name": current_period,
        "is_long_weekend": is_long_weekend,
        "next_holiday": next_holiday,
    }
