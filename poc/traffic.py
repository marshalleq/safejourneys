"""
AADT (Annual Average Daily Traffic) integration from NZTA open data.

Fetches carriageway segment data with ADT counts and maps them to H3 cells.
This enables exposure-adjusted crash rates (crashes per vehicle-km).
"""
import math
import time
import threading

import requests
import h3

# NZTA Carriageway segments — polylines with ADT counts
CARRIAGEWAY_URL = (
    "https://services.arcgis.com/CXBb7LAjgIIdcsPt/arcgis/rest/services/"
    "GEO_MASTER_GIS_Carriageway/FeatureServer/0/query"
)

# Cache
_aadt_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL = 86400  # 24 hours — AADT doesn't change often


def _nztm_to_wgs84(x, y):
    """
    Convert NZTM (EPSG:2193) to WGS84 (lat/lng).
    Uses pyproj if available, otherwise a rough approximation.
    """
    try:
        from pyproj import Transformer
        transformer = Transformer.from_crs("EPSG:2193", "EPSG:4326", always_xy=True)
        lng, lat = transformer.transform(x, y)
        return lat, lng
    except ImportError:
        # Rough approximation for NZ
        lat = -34.0 - (y - 6100000) / 111320
        lng = 166.0 + (x - 1500000) / (111320 * math.cos(math.radians(-41)))
        return lat, lng


def fetch_aadt_data(min_adt=0):
    """
    Fetch carriageway segments with ADT counts from NZTA.
    Returns list of dicts with lat, lng, adt, road_name, urban_rural, pct_heavy.
    """
    cache_key = f"aadt_{min_adt}"
    with _cache_lock:
        cached = _aadt_cache.get(cache_key)
        if cached and time.time() - cached["fetched_at"] < CACHE_TTL:
            return cached["data"]

    results = []
    offset = 0
    page_size = 2000

    print("Fetching AADT data from NZTA...", flush=True)

    while True:
        params = {
            "where": f"trafficADTCount > {min_adt} OR trafficADTEst > {min_adt}",
            "outFields": "trafficADTCount,trafficADTEst,loadingPcHeavy,roadName,startName,endName,urbanRural,roadClass,lanes,ownerType",
            "returnGeometry": "true",
            "outSR": "4326",  # Request WGS84 directly
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": page_size,
        }

        try:
            resp = requests.get(CARRIAGEWAY_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  AADT fetch error at offset {offset}: {e}", flush=True)
            break

        features = data.get("features", [])
        if not features:
            break

        for feat in features:
            attrs = feat.get("attributes", {})
            geom = feat.get("geometry", {})

            adt = attrs.get("trafficADTCount") or attrs.get("trafficADTEst") or 0
            if adt <= 0:
                continue

            # Get midpoint of the polyline for H3 assignment
            paths = geom.get("paths", [])
            if not paths or not paths[0]:
                continue

            path = paths[0]
            mid_idx = len(path) // 2
            mid_point = path[mid_idx]
            lng, lat = mid_point[0], mid_point[1]

            # Build road description from start/end names
            start = attrs.get("startName") or ""
            end = attrs.get("endName") or ""
            road = attrs.get("roadName") or ""
            desc = f"{road} ({start} to {end})" if start and end else road

            results.append({
                "lat": lat,
                "lng": lng,
                "adt": int(adt),
                "pct_heavy": attrs.get("loadingPcHeavy") or 0,
                "road_name": desc,
                "urban_rural": attrs.get("urbanRural") or "",
                "road_class": attrs.get("roadClass") or "",
                "lanes": attrs.get("lanes") or 0,
                "owner": attrs.get("ownerType") or "",
            })

        offset += page_size
        print(f"  Fetched {len(results)} segments so far...", flush=True)

        # Check if there are more results
        if not data.get("exceededTransferLimit", False) and len(features) < page_size:
            break

    print(f"  Total: {len(results)} segments with ADT data.", flush=True)

    with _cache_lock:
        _aadt_cache[cache_key] = {"data": results, "fetched_at": time.time()}

    return results


def map_aadt_to_h3(aadt_data, resolution=9):
    """
    Map AADT segments to H3 cells.
    For cells with multiple overlapping segments, takes the maximum ADT.
    Returns dict: h3_index -> {adt, pct_heavy, road_name, ...}
    """
    cell_adt = {}

    for seg in aadt_data:
        try:
            h3_idx = h3.latlng_to_cell(seg["lat"], seg["lng"], resolution)
        except Exception:
            continue

        existing = cell_adt.get(h3_idx)
        if existing is None or seg["adt"] > existing["adt"]:
            cell_adt[h3_idx] = {
                "adt": seg["adt"],
                "pct_heavy": seg["pct_heavy"],
                "road_name": seg["road_name"],
                "urban_rural": seg["urban_rural"],
            }

    return cell_adt


def compute_exposure_rate(crash_count, years_span, adt):
    """
    Compute crashes per 100 million vehicle-km.
    This is the standard international metric for road safety comparison.

    Formula: (crashes / years) / (ADT * 365 * segment_length_km) * 1e8
    We approximate segment_length as 0.6km (H3 resolution 9 cell diameter).
    """
    if adt <= 0 or years_span <= 0:
        return None
    annual_crashes = crash_count / years_span
    # Vehicle-km per year = ADT * 365 * approx_segment_km
    veh_km_year = adt * 365 * 0.6
    rate = (annual_crashes / veh_km_year) * 1e8
    return round(rate, 1)


def classify_exposure_risk(rate):
    """
    Classify crash rate per 100M veh-km into risk bands.
    Based on NZ benchmarks for state highways.
    """
    if rate is None:
        return "unknown"
    if rate >= 50:
        return "extreme"
    elif rate >= 20:
        return "high"
    elif rate >= 10:
        return "elevated"
    elif rate >= 5:
        return "moderate"
    else:
        return "low"
