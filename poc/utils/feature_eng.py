"""Feature engineering for crash prediction models."""

import pandas as pd
import numpy as np


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Create model-ready features from cleaned CAS data.

    Adds derived columns and encodes categoricals for ML models.
    """
    df = df.copy()
    print("Engineering features...")

    # --- Temporal features ---
    # Month proxy from financial year (Jul-Jun) — imperfect but usable
    # crashFinancialYear format: "2006/2007"
    if "fyStart" in df.columns:
        # Estimate a "season" from the financial year boundary
        # We know the FY but not the exact month, so derive season from
        # the combination of FY + crashYear
        df["fy_second_half"] = (df["crashYear"] == df["fyStart"] + 1).astype(int)
        # fy_second_half=1 means Jan-Jun, 0 means Jul-Dec

    # Decade for trend analysis
    df["decade"] = (df["crashYear"] // 10) * 10

    # Years since 2000 (for trend modelling)
    df["yearsSince2000"] = df["crashYear"] - 2000

    # --- Weather features ---
    weather_encoding = {
        "Fine": 0,
        "Light rain": 1,
        "Heavy rain": 2,
        "Mist or Fog": 3,
        "Snow": 4,
        "Hail or Sleet": 5,
    }
    df["weatherCode"] = df["weatherA"].map(weather_encoding).fillna(-1).astype(int)
    df["isRain"] = df["weatherA"].isin(["Light rain", "Heavy rain"]).astype(int)
    df["isPoorVisibility"] = df["weatherA"].isin(
        ["Mist or Fog", "Heavy rain", "Snow", "Hail or Sleet"]
    ).astype(int)
    df["isFine"] = (df["weatherA"] == "Fine").astype(int)

    # Secondary weather
    df["hasStrongWind"] = (df["weatherB"] == "Strong wind").astype(int)
    df["hasFrost"] = (df["weatherB"] == "Frost").astype(int)

    # --- Light features ---
    light_encoding = {
        "Bright sun": 0,
        "Overcast": 1,
        "Twilight": 2,
        "Dark": 3,
    }
    df["lightCode"] = df["light"].map(light_encoding).fillna(-1).astype(int)
    df["isDark"] = (df["light"] == "Dark").astype(int)
    df["isTwilight"] = (df["light"] == "Twilight").astype(int)

    # --- Road features ---
    df["isUrban"] = (df["urban"] == "Urban").astype(int)
    df["isSealed"] = (df["roadSurface"] == "Sealed").astype(int)
    df["isHill"] = df["flatHill"].isin(["Hill Road", "Hill"]).astype(int)
    df["isIntersection"] = df["intersection"].apply(
        lambda x: 0 if x in [0, "0", ""] else 1
    ).astype(int)

    # Speed limit bins
    df["speedBin"] = pd.cut(
        df["speedLimit"],
        bins=[0, 30, 50, 60, 80, 100, 120],
        labels=["0-30", "31-50", "51-60", "61-80", "81-100", "101+"],
        right=True,
    )

    # Advisory speed mismatch (potential "hidden hazard" indicator)
    df["speedMismatch"] = df["speedLimit"] - df["advisorySpeed"]
    df["hasAdvisorySpeed"] = df["advisorySpeed"].notna().astype(int)
    df["largeSpeedMismatch"] = (df["speedMismatch"] > 30).astype(int)

    # Number of lanes
    df["multiLane"] = (df["NumberOfLanes"] > 2).astype(int)

    # --- Traffic control ---
    df["hasTrafficControl"] = (
        df["trafficControl"].notna()
        & ~df["trafficControl"].isin(["Nil", "Unknown", "0"])
    ).astype(int)

    # --- Vehicle mix features ---
    df["hasTruck"] = (df["truck"] > 0).astype(int)
    df["hasMotorcycle"] = (df["motorcycle"] > 0).astype(int)
    df["hasBicycle"] = (df["bicycle"] > 0).astype(int)
    df["hasPedestrian"] = (df["pedestrian"] > 0).astype(int)
    df["hasBus"] = (df["bus"] > 0).astype(int)

    # Vulnerable road users
    df["vulnerableUser"] = (
        (df["hasPedestrian"] + df["hasBicycle"] + df["hasMotorcycle"]) > 0
    ).astype(int)

    # --- Impact features ---
    df["hitTree"] = df["tree"].apply(lambda x: 1 if x not in [0, "0"] else 0).astype(int)
    df["hitPole"] = df["postOrPole"].apply(lambda x: 1 if x not in [0, "0"] else 0).astype(int)
    df["wentOffRoad"] = (
        (df["ditch"].apply(lambda x: 0 if x in [0, "0"] else 1))
        | (df["cliffBank"].apply(lambda x: 0 if x in [0, "0"] else 1))
        | (df["overBank"].apply(lambda x: 0 if x in [0, "0"] else 1))
    ).astype(int)

    # --- Holiday flag ---
    df["isHoliday"] = df["holiday"].notna().astype(int)

    # --- Street lighting ---
    light_map = {"On": 1, "Off": 0, "None": -1}
    df["streetLightCode"] = df["streetLight"].map(light_map).fillna(-1).astype(int)
    df["hasStreetLight"] = (df["streetLightCode"] == 1).astype(int)

    # --- Compound risk features (novel combinations) ---
    # Wet + dark + high speed = compounding risk factors
    df["wetAndDark"] = (df["isRain"] & df["isDark"]).astype(int)
    df["wetAndHighSpeed"] = (df["isRain"] & (df["speedLimit"] >= 80)).astype(int)
    df["darkAndHighSpeed"] = (df["isDark"] & (df["speedLimit"] >= 80)).astype(int)
    df["wetDarkHighSpeed"] = (
        df["isRain"] & df["isDark"] & (df["speedLimit"] >= 80)
    ).astype(int)
    df["hillAndWet"] = (df["isHill"] & df["isRain"]).astype(int)
    df["vulnerableAndDark"] = (df["vulnerableUser"] & df["isDark"]).astype(int)
    df["vulnerableAndHighSpeed"] = (
        df["vulnerableUser"] & (df["speedLimit"] >= 70)
    ).astype(int)

    # --- Region encoding (keep top regions, group rest) ---
    top_regions = df["region"].value_counts().head(10).index
    df["regionGroup"] = df["region"].where(df["region"].isin(top_regions), "Other")

    n_features = len([c for c in df.columns if c not in [
        "X", "Y", "OBJECTID", "crashLocation1", "crashLocation2",
        "crashFinancialYear", "crashRoadSideRoad", "crashSHDescription",
        "meshblockId", "areaUnitID", "tlaId",
    ]])
    print(f"  Engineered {n_features} total columns")

    return df


# Feature sets for different models
CRASH_PROBABILITY_FEATURES = [
    "speedLimit", "NumberOfLanes", "isUrban", "isSealed", "isHill",
    "isIntersection", "hasTrafficControl", "hasStreetLight",
    "hasAdvisorySpeed", "largeSpeedMismatch",
    "weatherCode", "isRain", "isPoorVisibility", "hasStrongWind", "hasFrost",
    "lightCode", "isDark", "isTwilight",
    "yearsSince2000",
    "multiLane",
    "wetAndDark", "wetAndHighSpeed", "darkAndHighSpeed",
    "wetDarkHighSpeed", "hillAndWet",
]

SEVERITY_FEATURES = [
    "speedLimit", "NumberOfLanes", "isUrban", "isSealed", "isHill",
    "isIntersection", "hasTrafficControl", "hasStreetLight",
    "weatherCode", "isRain", "isPoorVisibility",
    "lightCode", "isDark",
    "hasTruck", "hasMotorcycle", "hasBicycle", "hasPedestrian", "hasBus",
    "vulnerableUser", "totalVehicles",
    "hitTree", "hitPole", "wentOffRoad",
    "wetAndDark", "wetAndHighSpeed", "darkAndHighSpeed",
    "vulnerableAndDark", "vulnerableAndHighSpeed",
]

SPEED_ANALYSIS_FEATURES = [
    "NumberOfLanes", "isUrban", "isSealed", "isHill",
    "isIntersection", "hasTrafficControl", "hasStreetLight",
    "hasAdvisorySpeed", "multiLane",
]
