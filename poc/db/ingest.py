"""
CAS API data ingestion — fetches new crash records and updates the database.

The CAS ArcGIS FeatureServer is public (no auth required):
https://services.arcgis.com/CXBb7LAjgIIdcsPt/arcgis/rest/services/CAS_Data_Public/FeatureServer/0

Limitations:
- Only provides crashYear (integer), not exact dates
- Pagination limited to ~2000 records per request
- Records are immutable once published
"""
import os
import sys
import time
from datetime import datetime

import pandas as pd
import numpy as np
import requests

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

CAS_API_BASE = os.environ.get(
    "CAS_API_URL",
    "https://services.arcgis.com/CXBb7LAjgIIdcsPt/arcgis/rest/services/CAS_Data_Public/FeatureServer",
)
LAYER_ID = 0
PAGE_SIZE = 2000
MAX_RETRIES = 3


def fetch_from_api(min_year: int = None) -> pd.DataFrame:
    """
    Query the CAS ArcGIS FeatureServer for crash records.

    Args:
        min_year: Only fetch records with crashYear >= min_year.
                  If None, fetches all records (very slow — 900K+ records).

    Returns:
        DataFrame with raw API fields.
    """
    url = f"{CAS_API_BASE}/{LAYER_ID}/query"

    where = f"crashYear >= {min_year}" if min_year else "1=1"

    all_records = []
    offset = 0
    page = 0

    print(f"Fetching CAS data (where: {where})...")

    while True:
        params = {
            "where": where,
            "outFields": "*",
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
            "returnGeometry": "true",
            "outSR": "2193",  # NZTM coordinates
        }

        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.get(url, params=params, timeout=60)
                resp.raise_for_status()
                data = resp.json()
                break
            except (requests.RequestException, ValueError) as e:
                if attempt < MAX_RETRIES - 1:
                    wait = 2 ** (attempt + 1)
                    print(f"  Retry {attempt + 1} after error: {e} (waiting {wait}s)")
                    time.sleep(wait)
                else:
                    raise RuntimeError(f"Failed to fetch from CAS API after {MAX_RETRIES} attempts: {e}")

        features = data.get("features", [])
        if not features:
            break

        for f in features:
            record = f.get("attributes", {})
            geom = f.get("geometry", {})
            if geom:
                record["X"] = geom.get("x")
                record["Y"] = geom.get("y")
            all_records.append(record)

        page += 1
        offset += PAGE_SIZE

        if page % 10 == 0:
            print(f"  Fetched {len(all_records):,} records ({page} pages)...")

        # Respect rate limits
        time.sleep(0.5)

        # Check if there are more records
        if not data.get("exceededTransferLimit", False):
            break

    print(f"  Total fetched: {len(all_records):,} records")

    if not all_records:
        return pd.DataFrame()

    return pd.DataFrame(all_records)


def process_api_records(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply coordinate conversion and feature engineering to raw API records.

    Mirrors the notebook pipeline: load → clean → coords → features → H3.
    """
    if df.empty:
        return df

    from poc.utils.spatial import add_wgs84_coords, add_h3_index
    from poc.utils.feature_eng import engineer_features
    from poc.utils.data_loader import load_cas_data

    # The API field names match the CSV column names, so we can reuse
    # the existing cleaning and feature engineering pipeline.

    # Basic cleaning (subset of what load_cas_data does)
    # Remove rows without valid coordinates
    df = df.dropna(subset=["X", "Y"])
    df = df[(df["X"] != 0) & (df["Y"] != 0)]

    # Ensure crashYear is valid
    if "crashYear" in df.columns:
        df = df[(df["crashYear"] >= 2000) & (df["crashYear"] <= 2030)]

    # Convert severity fields to int
    for col in ["fatalCount", "seriousInjuryCount", "minorInjuryCount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    # Convert vehicle counts to int
    vehicle_cols = [
        "bicycle", "bus", "carStationWagon", "motorcycle", "moped",
        "pedestrian", "schoolBus", "suv", "taxi", "truck", "vanOrUtility",
    ]
    for col in vehicle_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    # Convert impact objects to int
    impact_cols = ["tree", "postOrPole", "ditch", "cliffBank", "overBank", "fence", "guardRail"]
    for col in impact_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    # Speed limit
    if "speedLimit" in df.columns:
        df["speedLimit"] = pd.to_numeric(df["speedLimit"], errors="coerce")

    # Add WGS84 coordinates
    df = add_wgs84_coords(df)

    # Engineer features
    df = engineer_features(df)

    # Add H3 index
    df = add_h3_index(df, resolution=8, col_name="h3_index")

    # Add H3 resolution 7 for area-level stats
    import h3
    df["h3_r7"] = df["h3_index"].apply(lambda x: h3.cell_to_parent(x, 7) if pd.notna(x) else None)

    # Convert category columns to string for DB storage
    for col in df.columns:
        if df[col].dtype.name == "category":
            df[col] = df[col].astype(str)

    return df


def upsert_records(engine, df: pd.DataFrame) -> int:
    """
    Insert new records into crash_records, skipping duplicates by OBJECTID.

    Returns the number of genuinely new records inserted.
    """
    if df.empty:
        return 0

    from sqlalchemy import text

    # Get existing OBJECTIDs
    with engine.connect() as conn:
        existing = set(
            row[0] for row in
            conn.execute(text('SELECT "OBJECTID" FROM crash_records')).fetchall()
        )

    new_df = df[~df["OBJECTID"].isin(existing)]
    if new_df.empty:
        print("  No new records to insert.")
        return 0

    print(f"  Inserting {len(new_df):,} new records...")
    new_df.to_sql("crash_records", engine, if_exists="append", index=False, method="multi", chunksize=2000)
    return len(new_df)


def refresh_cell_stats(engine):
    """Recompute cell_stats from crash_records."""
    from poc.utils.spatial import h3_cell_stats

    print("Recomputing cell stats...")
    df = pd.read_sql('SELECT * FROM crash_records', engine)

    stats = h3_cell_stats(df, h3_col="h3_index")
    stats.to_sql("cell_stats", engine, if_exists="replace", index=False, method="multi", chunksize=2000)
    print(f"  Updated {len(stats):,} cell stats.")


def run_refresh(engine) -> int:
    """
    Full refresh cycle: fetch new data from API, process, upsert, recompute stats.

    Returns the number of new records added.
    """
    from sqlalchemy import text

    # Log start
    with engine.begin() as conn:
        result = conn.execute(
            text("INSERT INTO data_refresh_log (started_at, status) VALUES (:t, 'running') RETURNING id"),
            {"t": datetime.utcnow()},
        )
        log_id = result.scalar()

    try:
        # Determine what year to fetch from
        current_year = datetime.now().year
        min_year = current_year - 1  # Fetch last year + current year to catch late entries

        # Fetch from API
        raw_df = fetch_from_api(min_year=min_year)

        if raw_df.empty:
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE data_refresh_log SET finished_at=:t, status='success', records_fetched=0, records_new=0 WHERE id=:id"),
                    {"t": datetime.utcnow(), "id": log_id},
                )
            print("No new data from API.")
            return 0

        # Process (clean, engineer features, add H3)
        processed_df = process_api_records(raw_df)

        # Upsert into database
        new_count = upsert_records(engine, processed_df)

        # Recompute cell stats if we got new data
        if new_count > 0:
            refresh_cell_stats(engine)

        # Log success
        max_year = int(processed_df["crashYear"].max()) if not processed_df.empty else None
        with engine.begin() as conn:
            conn.execute(
                text("""UPDATE data_refresh_log
                        SET finished_at=:t, status='success',
                            records_fetched=:fetched, records_new=:new_count,
                            max_crash_year=:max_year
                        WHERE id=:id"""),
                {"t": datetime.utcnow(), "fetched": len(raw_df), "new_count": new_count,
                 "max_year": max_year, "id": log_id},
            )

        print(f"Refresh complete: {len(raw_df):,} fetched, {new_count:,} new.")
        return new_count

    except Exception as e:
        # Log failure
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE data_refresh_log SET finished_at=:t, status='error', error_message=:err WHERE id=:id"),
                {"t": datetime.utcnow(), "err": str(e), "id": log_id},
            )
        print(f"Refresh FAILED: {e}")
        raise


if __name__ == "__main__":
    from poc.db.connection import get_engine
    engine = get_engine()
    run_refresh(engine)
