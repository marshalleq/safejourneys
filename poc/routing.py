"""
Route risk scoring — compute crash probability along a driving route.

Uses OSRM for routing and existing per-cell crash data for risk scoring.
"""
import math
import requests
import h3

OSRM_URL = "https://router.project-osrm.org/route/v1/driving"


def get_route(origin_lng, origin_lat, dest_lng, dest_lat):
    """
    Get driving route from OSRM.
    Returns dict with coordinates, distance_m, duration_s, or None on failure.
    """
    url = f"{OSRM_URL}/{origin_lng},{origin_lat};{dest_lng},{dest_lat}"
    params = {
        "overview": "full",
        "geometries": "geojson",
        "steps": "false",
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"error": f"Routing failed: {e}"}

    if data.get("code") != "Ok" or not data.get("routes"):
        return {"error": data.get("message", "No route found")}

    route = data["routes"][0]
    coords = route["geometry"]["coordinates"]  # [[lng, lat], ...]

    return {
        "coordinates": coords,
        "distance_m": route["distance"],
        "duration_s": route["duration"],
    }


def route_to_h3_cells(coordinates, resolution=9):
    """
    Convert a route polyline to an ordered list of unique H3 cells.
    Samples points along the route to ensure coverage.
    Returns list of (h3_index, lat, lng) tuples in route order.
    """
    cells_seen = set()
    cells_ordered = []

    for i in range(len(coordinates) - 1):
        lng1, lat1 = coordinates[i]
        lng2, lat2 = coordinates[i + 1]

        # Distance between consecutive points (rough, in degrees)
        dist = math.sqrt((lat2 - lat1) ** 2 + (lng2 - lng1) ** 2)

        # Sample intermediate points if segment is longer than ~200m (~0.002 degrees)
        n_samples = max(1, int(dist / 0.002))
        for s in range(n_samples + 1):
            t = s / max(n_samples, 1)
            lat = lat1 + t * (lat2 - lat1)
            lng = lng1 + t * (lng2 - lng1)
            try:
                cell = h3.latlng_to_cell(lat, lng, resolution)
            except Exception:
                continue
            if cell not in cells_seen:
                cells_seen.add(cell)
                cells_ordered.append((cell, lat, lng))

    return cells_ordered


def _estimate_adt(speed_limit, is_urban):
    """
    Estimate ADT for cells without AADT data, based on road characteristics.
    These are rough NZ averages by road type.
    """
    if is_urban:
        if speed_limit <= 50:
            return 8000   # urban local/collector
        elif speed_limit <= 70:
            return 15000  # urban arterial
        else:
            return 25000  # urban motorway
    else:
        if speed_limit <= 60:
            return 1000   # rural local
        elif speed_limit <= 80:
            return 3000   # rural collector
        else:
            return 5000   # rural state highway


def score_route(route_cells, cell_data, cell_multiplier_fn, aadt_data=None):
    """
    Score a route by computing PER-VEHICLE crash probability across all cells.

    The cell's hourly crash rate is the rate for ALL vehicles combined.
    To get individual risk, we divide by the number of vehicles sharing the road:

        per_vehicle_rate = cell_crash_rate / vehicles_per_hour
        my_risk = per_vehicle_rate × hours_in_cell

    AADT data is used where available; otherwise estimated from road type.

    Args:
        route_cells: list of (h3_index, lat, lng) from route_to_h3_cells
        cell_data: dict h3_index -> row dict with hourly_rate, base_dsi_prob, speed_limit etc.
        cell_multiplier_fn: function(lat, lng) -> condition multiplier
        aadt_data: dict h3_index -> {adt, ...} or None

    Returns dict with route risk summary and per-segment details.
    """
    CELL_DIAMETER_KM = 0.6  # H3 resolution 9
    if aadt_data is None:
        aadt_data = {}

    segments = []
    total_no_crash_prob = 1.0
    total_dsi_weighted = 0.0
    total_time_hours = 0.0
    cells_with_data = 0
    cells_without_data = 0
    highest_risk_segment = None
    highest_risk_prob = 0

    for h3id, lat, lng in route_cells:
        cell = cell_data.get(h3id)
        if cell is None:
            cells_without_data += 1
            segments.append({
                "h3": h3id,
                "lat": round(lat, 5),
                "lng": round(lng, 5),
                "has_data": False,
                "crash_prob": 0,
                "dsi_pct": 0,
            })
            continue

        cells_with_data += 1
        mult = cell_multiplier_fn(lat, lng)
        hourly_rate = (cell.get("hourly_rate", 0) or 0) * mult
        speed_limit = cell.get("mean_speed_limit") or cell.get("speed_limit") or 50
        dsi_prob = cell.get("base_dsi_prob", 0) or 0
        is_urban = bool(cell.get("cell_pct_urban", 0) and cell.get("cell_pct_urban", 0) > 0.5)

        # Get traffic volume — AADT if available, otherwise estimate
        aadt_info = aadt_data.get(h3id)
        if aadt_info and aadt_info.get("adt", 0) > 0:
            adt = aadt_info["adt"]
        else:
            adt = _estimate_adt(speed_limit, is_urban)

        vehicles_per_hour = adt / 24  # simplified; real traffic varies by hour

        # Time to traverse this cell
        speed_kmh = max(speed_limit, 10)
        hours_in_cell = CELL_DIAMETER_KM / speed_kmh
        total_time_hours += hours_in_cell

        # Per-VEHICLE crash probability for a single transit through this cell
        # = 1 / (total vehicles between crashes)
        # = hourly_rate / vehicles_per_hour
        # hours_in_cell cancels out: fewer hours exposed, but fewer vehicles present too
        if vehicles_per_hour > 0 and hourly_rate > 0:
            crash_prob = min(hourly_rate / vehicles_per_hour, 0.999)
        else:
            crash_prob = 0

        total_no_crash_prob *= (1 - crash_prob)
        total_dsi_weighted += dsi_prob * crash_prob

        seg = {
            "h3": h3id,
            "lat": round(lat, 5),
            "lng": round(lng, 5),
            "has_data": True,
            "crash_prob": crash_prob,
            "dsi_pct": round(dsi_prob * 100, 1),
            "speed_limit": speed_limit,
            "hourly_rate": round(hourly_rate, 8),
            "multiplier": round(mult, 2),
        }
        segments.append(seg)

        if crash_prob > highest_risk_prob:
            highest_risk_prob = crash_prob
            highest_risk_segment = seg

    # Overall route probability
    route_crash_prob = 1 - total_no_crash_prob
    route_dsi = (total_dsi_weighted / route_crash_prob * 100) if route_crash_prob > 0 else 0

    # Express as "1 in N trips"
    one_in_n = round(1 / route_crash_prob) if route_crash_prob > 0 else 999999

    return {
        "route_crash_probability": round(route_crash_prob * 100, 4),
        "route_crash_pct": f"{route_crash_prob * 100:.3f}%",
        "one_in_n_trips": one_in_n,
        "route_dsi_pct": round(route_dsi, 1),
        "total_cells": len(route_cells),
        "cells_with_data": cells_with_data,
        "cells_without_data": cells_without_data,
        "total_time_hours": round(total_time_hours, 2),
        "highest_risk_segment": highest_risk_segment,
        "segments": segments,
    }
