"""Load and clean the CAS crash dataset."""

import pandas as pd
import numpy as np
from pathlib import Path

# Column type mappings for efficient loading
DTYPE_MAP = {
    "OBJECTID": "int32",
    "advisorySpeed": "float32",
    "bicycle": "int8",
    "bridge": "str",
    "bus": "int8",
    "carStationWagon": "int8",
    "crashYear": "int16",
    "debris": "str",
    "ditch": "str",
    "fatalCount": "int16",
    "fence": "str",
    "guardRail": "str",
    "houseOrBuilding": "str",
    "intersection": "str",
    "kerb": "str",
    "minorInjuryCount": "int16",
    "moped": "int8",
    "motorcycle": "int8",
    "objectThrownOrDropped": "str",
    "otherObject": "str",
    "otherVehicleType": "str",
    "overBank": "str",
    "parkedVehicle": "str",
    "pedestrian": "str",
    "phoneBoxEtc": "str",
    "postOrPole": "str",
    "schoolBus": "str",
    "seriousInjuryCount": "int16",
    "slipOrFlood": "str",
    "strayAnimal": "str",
    "suv": "int8",
    "taxi": "int8",
    "trafficIsland": "str",
    "trafficSign": "str",
    "train": "str",
    "tree": "str",
    "truck": "int8",
    "unknownVehicleType": "int8",
    "vanOrUtility": "int8",
    "vehicle": "str",
    "waterRiver": "str",
}

# Fields we actually need for modelling
KEEP_COLUMNS = [
    "X", "Y", "OBJECTID",
    "advisorySpeed", "bicycle", "bus", "carStationWagon",
    "crashDirectionDescription", "crashFinancialYear",
    "crashLocation1", "crashLocation2",
    "crashSeverity", "crashYear",
    "fatalCount", "seriousInjuryCount", "minorInjuryCount",
    "flatHill", "light", "NumberOfLanes",
    "motorcycle", "moped", "pedestrian",
    "region", "roadCharacter", "roadLane", "roadSurface",
    "speedLimit", "streetLight", "suv", "taxi",
    "tlaName", "trafficControl",
    "tree", "truck", "urban", "vanOrUtility",
    "weatherA", "weatherB",
    "intersection", "holiday",
    "cliffBank", "ditch", "fence", "guardRail",
    "postOrPole", "overBank",
]


def load_cas_data(
    path: str | Path | None = None,
    sample_frac: float | None = None,
) -> pd.DataFrame:
    """
    Load the CAS CSV, clean it, and return a typed DataFrame.

    Parameters
    ----------
    path : path to CSV file. Defaults to ../CAS_Data_public.csv relative to poc/
    sample_frac : if set, randomly sample this fraction (e.g. 0.1 for 10%)

    Returns
    -------
    pd.DataFrame with cleaned, typed columns and WGS84 coordinates added
    """
    if path is None:
        path = Path(__file__).resolve().parent.parent.parent / "CAS_Data_public.csv"

    print(f"Loading CAS data from {path}...")
    df = pd.read_csv(
        path,
        low_memory=False,
        encoding="utf-8-sig",
    )
    print(f"  Loaded {len(df):,} records with {len(df.columns)} columns")

    # --- Basic cleaning ---
    # Drop rows with missing coordinates
    df = df.dropna(subset=["X", "Y"])
    df = df[df["X"] != 0]
    df = df[df["Y"] != 0]

    # Clean crashYear — remove rows where it parsed as non-numeric
    df["crashYear"] = pd.to_numeric(df["crashYear"], errors="coerce")
    df = df.dropna(subset=["crashYear"])
    df["crashYear"] = df["crashYear"].astype(int)
    df = df[df["crashYear"].between(2000, 2026)]

    # Clean crashSeverity — standardise
    severity_map = {
        "Fatal Crash": "Fatal",
        "Serious Crash": "Serious",
        "Minor Crash": "Minor",
        "Non-Injury Crash": "Non-Injury",
    }
    df["crashSeverity"] = df["crashSeverity"].map(severity_map)
    df = df.dropna(subset=["crashSeverity"])

    # Severity as ordered numeric
    severity_order = {"Non-Injury": 0, "Minor": 1, "Serious": 2, "Fatal": 3}
    df["severityCode"] = df["crashSeverity"].map(severity_order).astype("int8")

    # Clean numeric fields
    for col in ["fatalCount", "seriousInjuryCount", "minorInjuryCount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    df["speedLimit"] = pd.to_numeric(df["speedLimit"], errors="coerce")
    df["advisorySpeed"] = pd.to_numeric(df["advisorySpeed"], errors="coerce")
    df["NumberOfLanes"] = pd.to_numeric(df["NumberOfLanes"], errors="coerce")

    # Clean categorical "Null" strings
    null_strings = {"Null", "null", "NULL", "", "Unknown"}
    for col in ["weatherA", "weatherB", "light", "flatHill", "roadSurface",
                 "roadCharacter", "roadLane", "trafficControl", "urban",
                 "streetLight", "intersection", "holiday", "region", "tlaName"]:
        if col in df.columns:
            df[col] = df[col].replace(null_strings, np.nan)

    # Clean binary-ish fields stored as strings
    for col in ["bicycle", "bus", "carStationWagon", "motorcycle", "moped",
                 "suv", "taxi", "truck", "unknownVehicleType", "vanOrUtility"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    # Boolean-ish fields (0/1 or string)
    for col in ["tree", "cliffBank", "ditch", "fence", "guardRail",
                 "postOrPole", "overBank", "intersection", "pedestrian"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    # Extract financial year start
    if "crashFinancialYear" in df.columns:
        df["fyStart"] = df["crashFinancialYear"].str.extract(r"(\d{4})").astype(float)

    # Total vehicles involved
    vehicle_cols = ["bicycle", "bus", "carStationWagon", "motorcycle", "moped",
                    "suv", "taxi", "truck", "vanOrUtility"]
    existing_vcols = [c for c in vehicle_cols if c in df.columns]
    df["totalVehicles"] = df[existing_vcols].sum(axis=1)

    # Total injuries
    df["totalInjuries"] = (
        df["fatalCount"] + df["seriousInjuryCount"] + df["minorInjuryCount"]
    )

    if sample_frac is not None:
        df = df.sample(frac=sample_frac, random_state=42)
        print(f"  Sampled to {len(df):,} records ({sample_frac:.0%})")

    print(f"  After cleaning: {len(df):,} records")
    print(f"  Year range: {df['crashYear'].min()} – {df['crashYear'].max()}")
    print(f"  Severity distribution:")
    for sev, count in df["crashSeverity"].value_counts().items():
        print(f"    {sev}: {count:,}")

    return df
