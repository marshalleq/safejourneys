# Crash Risk Calculation — How Route Risk is Computed

## Overview

The route risk calculator estimates the probability that **you, as an individual driver**, will be involved in a crash on a specific route under current conditions. It combines historical crash frequency data, real-time weather/holiday adjustments, and traffic volume data to produce a per-vehicle risk estimate.

---

## Core Concept: Per-Vehicle Risk

A common mistake in crash risk modelling is conflating "a crash will happen here" with "a crash will happen **to me** here." These are very different questions.

**Example — Symonds Street, Auckland:**
- Historical data shows a crash happens roughly every 2 days (ETNA = 48 hours)
- But ~8,000 vehicles/day use that road (ADT = 8,000)
- Over 48 hours, that's ~16,000 vehicles passing through
- If 1 crash occurs among 16,000 vehicles, **my** risk per transit is **1 in 16,000**

The cell-level crash rate tells us how dangerous the **location** is. To get individual risk, we must divide by the number of vehicles sharing that risk.

---

## Step-by-Step Calculation

### Step 1: Route to H3 Cells

The driving route (from OSRM) is a polyline of GPS coordinates. We convert this to an ordered list of H3 hexagonal cells (resolution 9, ~0.6 km diameter) by:

1. Walking along the polyline
2. Sampling intermediate points every ~200m to ensure no cells are skipped
3. Converting each point to its H3 cell index
4. Deduplicating while preserving route order

A typical urban route of 10 km passes through ~20–30 cells. Auckland to Wellington (~640 km) passes through ~400+ cells.

### Step 2: Per-Cell Data Lookup

For each H3 cell on the route, we look up:

| Field | Source | Description |
|-------|--------|-------------|
| `hourly_rate` | CAS crash database | Historical crashes per hour in this cell (= 1/ETNA) |
| `base_dsi_prob` | LightGBM severity model | Probability that a crash here results in death or serious injury |
| `speed_limit` | CAS crash records | Mean posted speed limit in the cell |
| `cell_pct_urban` | CAS crash records | Proportion of crashes in urban setting |

Cells with no historical crash data are assigned zero risk (no data = no known hazard).

### Step 3: Condition Multiplier

The base crash rate is adjusted for current conditions using a multiplier:

```
adjusted_hourly_rate = hourly_rate × condition_multiplier
```

The condition multiplier combines:

| Factor | Multiplier | Source |
|--------|-----------|--------|
| Rain | ~1.3–1.5× | Open-Meteo live weather API, nearest of 8 NZ stations |
| Darkness | ~1.2–1.4× | Astronomical sun position calculation |
| Holiday period | ~0.66× lower or ~1.03× higher | NZ public holiday calendar vs historical crash rates |

Each cell uses weather from its nearest weather station, so a route from Auckland to Wellington will use different weather data for different segments.

### Step 4: Traffic Volume (AADT)

We need to know how many vehicles share the road to convert from "cell risk" to "my risk."

**Primary source:** NZTA Carriageway API provides Annual Average Daily Traffic (AADT) counts for 10,834 road segments across NZ, mapped to H3 cells.

**Fallback estimates** (for cells without AADT data):

| Road Type | Estimated ADT |
|-----------|--------------|
| Urban local/collector (≤50 km/h) | 8,000 |
| Urban arterial (≤70 km/h) | 15,000 |
| Urban motorway (>70 km/h) | 25,000 |
| Rural local (≤60 km/h) | 1,000 |
| Rural collector (≤80 km/h) | 3,000 |
| Rural state highway (>80 km/h) | 5,000 |

Vehicles per hour is simplified as `ADT / 24`. In reality traffic varies by hour, but without real-time traffic data this is a reasonable average.

### Step 5: Per-Vehicle Crash Probability (Single Cell)

For each cell the driver transits:

```
P(crash in this cell) = hourly_rate / vehicles_per_hour
```

**Why this formula works — the derivation:**

1. `hourly_rate` = crashes per hour for ALL vehicles combined in this cell
2. `ETNA` (Expected Time to Next Accident) = `1 / hourly_rate` (hours between crashes)
3. Between two consecutive crashes, `vehicles_per_hour × ETNA` vehicles pass through
4. I am one of those vehicles, so my risk = `1 / (vehicles_per_hour × ETNA)`
5. Substituting ETNA: `1 / (vehicles_per_hour × (1 / hourly_rate))` = `hourly_rate / vehicles_per_hour`

**Why transit time doesn't appear in the formula:**

Intuitively, you might think spending more time in a cell means more risk. But the `hours_in_cell` factor cancels out:

- Numerator: my exposure is `hourly_rate × hours_in_cell` (fraction of hourly risk during my transit)
- Denominator: average vehicles *present* at any instant = `vehicles_per_hour × hours_in_cell`
- Result: `(hourly_rate × hours_in_cell) / (vehicles_per_hour × hours_in_cell)` = `hourly_rate / vehicles_per_hour`

The transit time affects both exposure and the number of vehicles sharing that exposure equally, so it cancels.

### Step 6: Route Aggregation

Individual cell probabilities are combined using the complement method:

```
P(no crash on route) = ∏ (1 - P_i)  for each cell i
P(crash on route)    = 1 - P(no crash on route)
```

This correctly handles the case where many small probabilities accumulate across a long route. It also avoids double-counting (simply summing probabilities would overstate risk).

The result is expressed as:
- **Percentage**: e.g., "0.042%"
- **1 in N trips**: e.g., "1 in 2,380 trips"

### Step 7: Severity Assessment

If a crash does occur, what's the severity? The route DSI (Death or Serious Injury) percentage is the crash-probability-weighted average of per-cell DSI scores:

```
Route DSI = Σ(dsi_prob_i × crash_prob_i) / Σ(crash_prob_i) × 100
```

Higher-risk cells contribute more to the severity estimate, which makes sense — if a crash is most likely in a high-speed rural cell, the severity profile should reflect that.

---

## Worked Example

**Route: Auckland CBD to Hamilton CBD (~125 km, ~90 cells with data)**

| Parameter | Value |
|-----------|-------|
| High-risk urban cell (Symonds St) | hourly_rate = 0.021 (ETNA ≈ 48 hrs), ADT = 8,000 |
| Per-vehicle risk for that cell | 0.021 / 333 = 0.0063% |
| Typical motorway cell (SH1) | hourly_rate = 0.005, ADT = 25,000 |
| Per-vehicle risk for that cell | 0.005 / 1,042 = 0.00048% |
| Typical rural cell (Waikato) | hourly_rate = 0.003, ADT = 5,000 |
| Per-vehicle risk for that cell | 0.003 / 208 = 0.0014% |

Aggregated across ~90 cells: **~0.04% crash probability (1 in ~2,500 trips)**

With rain: multiply hourly rates by ~1.4 → **~0.06% (1 in ~1,700 trips)**

---

## Sanity Check Against National Statistics

NZ averages approximately:
- ~30,000 injury crashes per year
- ~40 billion vehicle-km driven per year
- ≈ 1 injury crash per 1.3 million vehicle-km

A 125 km trip should have roughly `125 / 1,300,000 ≈ 0.01%` baseline crash probability. Our model produces slightly higher numbers because routes tend to follow busier, higher-risk corridors rather than the national average road. The numbers are in the right order of magnitude.

---

## What the UI Displays

The route results show multiple layers of information designed to be actionable:

### Risk Score (1–10)

A normalised score comparing the route's crash density (crashes per km per year) against the NZ state highway average (~0.75 crashes/km/year). The scale uses a logarithmic mapping:

| Score | Meaning | Crash density vs baseline |
|-------|---------|--------------------------|
| 1–2 | Low risk | Below average |
| 3–4 | Moderate | Around average |
| 5–6 | High | 2–4× average |
| 7–8 | Very high | 4–8× average |
| 9–10 | Extreme | 8×+ average |

This gives instant visual differentiation: a quiet rural route scores 2, a route through Auckland's worst corridors scores 7–8.

### Crashes on This Route

- **Crashes/year (all vehicles)**: Total crashes per year across all roads on the route. This is the raw danger of the route — "42 crashes/year happen on these roads."
- **Hotspot cells**: Number of cells where a crash occurs at least once per week (ETNA < 7 days). These are the stretches that drive the risk.

### Your Risk

- **Single trip**: Per-vehicle probability for one trip (the actuarial number).
- **If driven N times/week**: Adjustable frequency selector (2, 3, 5, 7, 10 times/week). Computes annual risk assuming return trips: `annual_risk = 1 - (1 - single_trip_prob) ^ (trips_per_week × 2 × 52)`. A daily commuter on a risky route can see meaningful annual risk percentages.
- **DSI if crash**: Probability of death or serious injury, should a crash occur.

---

## Limitations and Assumptions

1. **Uniform traffic distribution**: ADT is divided by 24 to get vehicles/hour. Real traffic peaks at rush hour (2-3× average) and drops overnight (0.1× average). This means risk is underestimated during quiet periods and overestimated during peak.

2. **No time-of-day crash patterns**: The CAS API doesn't provide hour-of-day data, so crash rates are uniform across 24 hours. In reality, crashes cluster at certain times.

3. **Cell-level granularity**: H3 resolution 9 cells are ~0.6 km across. A cell containing both a dangerous intersection and a straight road averages their risk together.

4. **AADT coverage gaps**: NZTA AADT data covers state highways and major roads well, but local roads often fall back to estimates.

5. **Independence assumption**: The complement aggregation assumes crash probabilities are independent across cells. In practice, conditions are correlated (rain affects many cells simultaneously), but the condition multiplier already adjusts individual cell rates.

6. **Historical basis**: The model is fundamentally retrospective — it assumes past crash patterns predict future risk. Road improvements, speed limit changes, or new developments may change actual risk.

---

*Last updated: 2026-03-13*
