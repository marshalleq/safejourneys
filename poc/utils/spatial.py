"""Spatial utilities — coordinate transforms and H3 indexing."""

import numpy as np
import pandas as pd
from pyproj import Transformer

# NZTM (EPSG:2193) → WGS84 (EPSG:4326) transformer
_transformer = Transformer.from_crs("EPSG:2193", "EPSG:4326", always_xy=True)


def nztm_to_wgs84(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Convert NZTM coordinates to WGS84 latitude/longitude.

    Parameters
    ----------
    x : Easting values (NZTM)
    y : Northing values (NZTM)

    Returns
    -------
    (longitude, latitude) arrays in WGS84
    """
    lng, lat = _transformer.transform(x, y)
    return lng, lat


def add_h3_index(
    df: pd.DataFrame,
    lat_col: str = "lat",
    lng_col: str = "lng",
    resolution: int = 8,
    col_name: str = "h3_index",
) -> pd.DataFrame:
    """
    Add an H3 hexagonal grid index to each row.

    Parameters
    ----------
    df : DataFrame with lat/lng columns
    lat_col : name of latitude column
    lng_col : name of longitude column
    resolution : H3 resolution (7=rural ~1.2km, 8=~460m, 9=urban ~174m)
    col_name : name for the new H3 index column

    Returns
    -------
    DataFrame with H3 index column added
    """
    import h3

    print(f"Computing H3 indices at resolution {resolution}...")

    df[col_name] = df.apply(
        lambda row: h3.latlng_to_cell(row[lat_col], row[lng_col], resolution),
        axis=1,
    )

    n_cells = df[col_name].nunique()
    print(f"  {len(df):,} crashes mapped to {n_cells:,} unique H3 cells")
    print(f"  Average {len(df) / n_cells:.1f} crashes per cell")

    return df


def add_wgs84_coords(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert NZTM X,Y columns to WGS84 lat,lng and add to DataFrame.
    """
    print("Converting NZTM → WGS84...")
    lng, lat = nztm_to_wgs84(df["X"].values, df["Y"].values)
    df["lng"] = lng
    df["lat"] = lat

    # Sanity check — NZ bounding box
    nz_bounds = (lat > -48) & (lat < -34) & (lng > 165) & (lng < 179)
    n_outside = (~nz_bounds).sum()
    if n_outside > 0:
        print(f"  Warning: {n_outside:,} points outside NZ bounding box — filtering")
        df = df[nz_bounds].copy()

    print(f"  Coordinates converted: {len(df):,} records with valid NZ lat/lng")
    return df


def h3_cell_stats(df: pd.DataFrame, h3_col: str = "h3_index") -> pd.DataFrame:
    """
    Compute per-H3-cell crash statistics.

    Returns a DataFrame indexed by H3 cell with aggregated metrics.
    """
    import h3

    stats = df.groupby(h3_col).agg(
        crash_count=("OBJECTID", "count"),
        fatal_count=("fatalCount", "sum"),
        serious_count=("seriousInjuryCount", "sum"),
        minor_count=("minorInjuryCount", "sum"),
        mean_severity=("severityCode", "mean"),
        max_severity=("severityCode", "max"),
        years_span=("crashYear", lambda x: x.max() - x.min() + 1),
        first_year=("crashYear", "min"),
        last_year=("crashYear", "max"),
        mean_speed_limit=("speedLimit", "mean"),
    ).reset_index()

    # Annual crash rate
    stats["annual_crash_rate"] = stats["crash_count"] / stats["years_span"]

    # Severity-weighted score: Fatal=100, Serious=10, Minor=2, Non-Injury=1
    stats["severity_score"] = (
        stats["fatal_count"] * 100
        + stats["serious_count"] * 10
        + stats["minor_count"] * 2
        + (stats["crash_count"] - stats["fatal_count"]
           - stats["serious_count"] - stats["minor_count"])
    )
    stats["annual_severity_score"] = stats["severity_score"] / stats["years_span"]

    # Cell centre coordinates for mapping
    stats["cell_lat"] = stats[h3_col].apply(lambda h: h3.cell_to_latlng(h)[0])
    stats["cell_lng"] = stats[h3_col].apply(lambda h: h3.cell_to_latlng(h)[1])

    return stats
