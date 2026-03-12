# Crash Risk Calculation — How Route Risk is Computed

## Overview

The route risk calculator estimates the probability that **you, as an individual driver**, will be involved in a crash on a specific route under current conditions. It combines historical crash frequency data, real-time weather/holiday adjustments, and traffic volume data to produce a per-vehicle risk estimate alongside route-level danger metrics.

---

## Core Concept: Per-Vehicle Risk

A common mistake in crash risk modelling is conflating "a crash will happen here" with "a crash will happen **to me** here." These are very different questions.

**Example — Symonds Street, Auckland:**
- Historical data shows a crash happens roughly every 2 days (ETNA = 48 hours)
- But ~8,000 vehicles/day use that road (ADT = 8,000)
- Over 48 hours, that's ~16,000 vehicles passing through
- If 1 crash occurs among 16,000 vehicles, **my** risk per transit is **1 in 16,000**

The cell-level crash rate tells us how dangerous the **location** is. To get individual risk, we must divide by the number of vehicles sharing that risk.

However, per-vehicle probability alone is always small (driving is statistically safe on any single trip). To make the tool useful for route comparison, we also compute **route-level danger metrics**: total crashes/year, hotspot count, risk score, and annualised commuter risk.

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

For each H3 cell on the route, we look up crash data from the **full cell_profiles dataset** — all cells with any crash history, not just the top 5,000 shown on the map. This is critical: the map displays the top 5,000 cells by crash count for performance, but route scoring must search all cells to avoid treating most of the route as "no data."

| Field | Source | Description |
|-------|--------|-------------|
| `hourly_rate` | CAS crash database | Historical crashes per hour in this cell (= 1/ETNA) |
| `base_dsi_prob` | LightGBM severity model | Probability that a crash here results in death or serious injury |
| `speed_limit` | CAS crash records | Mean posted speed limit in the cell |
| `cell_pct_urban` | CAS crash records | Proportion of crashes in urban setting |

Cells with no historical crash data are assigned zero risk (no data = no known hazard).

**Bug fix note (2026-03-13):** An earlier version built the route lookup from `top_cells` (top 5,000 by crash count). This caused most route cells to show no data, producing 0 crashes/year and artificially low risk scores even through high-risk corridors. Fixed by using the full `cell_profiles` dataset.

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

**Bug fix note (2026-03-13):** An earlier version multiplied by `hours_in_cell` (~0.012 hours for a 50km/h zone), which incorrectly reduced per-vehicle risk by ~80x. The transit time cancels out mathematically and should not appear in the formula.

### Step 6: Route-Level Danger Metrics

In addition to per-vehicle probability, we compute metrics that describe how dangerous the **route itself** is:

**Crashes per year (all vehicles):** Sum of `hourly_rate × 8760` across all scored cells. This answers "how many crashes happen on these roads per year?" — independent of whether you personally are involved.

**Hotspot cells:** Count of cells where ETNA < 7 days (a crash occurs at least once per week). These are the most dangerous stretches on the route.

**Worst segment ETNA:** The ETNA of the single most dangerous cell on the route, expressed in days or hours. e.g., "crash every 3 days" — the most visceral indicator of route danger.

### Step 7: Route Aggregation

Individual cell probabilities are combined using the complement method:

```
P(no crash on route) = ∏ (1 - P_i)  for each cell i
P(crash on route)    = 1 - P(no crash on route)
```

This correctly handles the case where many small probabilities accumulate across a long route. It also avoids double-counting (simply summing probabilities would overstate risk).

The result is expressed as:
- **Percentage**: e.g., "0.042%"
- **1 in N trips**: e.g., "1 in 2,380 trips"

### Step 8: Risk Score (1–10)

A normalised score comparing the route's crash density against the NZ national average. The density is calculated using **only cells with crash data** (scored cells), not the total cells on the route — this prevents dilution from cells that simply lack crash history.

```
scored_km = cells_with_data × 0.6 km
crashes_per_km_year = total_crashes_per_year / scored_km
risk_ratio = crashes_per_km_year / NZ_baseline
risk_score = 5 + 2.9 × log₂(risk_ratio)    # clamped to 1–10
```

The NZ baseline is ~0.32 injury crashes per km of road per year (~30,000 crashes across ~94,000 km of road network).

| Score | Meaning | Crash density vs NZ average |
|-------|---------|--------------------------|
| 1–2 | Low risk | Well below average |
| 3–4 | Below average | ~0.5× average |
| 5 | Average | NZ national average |
| 6–7 | Above average | 2–4× average |
| 8–9 | High risk | 4–8× average |
| 10 | Extreme | 8×+ average |

**Bug fix note (2026-03-13):** The original formula placed NZ average at score 1 (the bottom of the scale), meaning even dangerous routes appeared "low risk." Recentered so average = 5, and changed the denominator from all route cells to scored cells only, preventing dilution from cells without crash history.

### Step 9: Severity and Commuter Risk

**DSI (Death or Serious Injury):** The crash-probability-weighted average of per-cell DSI scores:

```
Route DSI = Σ(dsi_prob_i × crash_prob_i) / Σ(crash_prob_i) × 100
```

Higher-risk cells contribute more to the severity estimate.

**Annual commuter risk:** The UI provides a frequency selector (2, 3, 5, 7, or 10 times per week). Annual risk is computed assuming return trips:

```
annual_trips = trips_per_week × 2 × 52
annual_risk = 1 - (1 - single_trip_prob) ^ annual_trips
```

This transforms a tiny per-trip probability into a meaningful annual figure. A 0.004% per-trip risk becomes ~2% per year for a daily commuter — a number worth paying attention to.

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

If driven 5x/week as a commute: **~17% annual risk** of being involved in a crash on this route.

Route-level metrics: ~35 crashes/year on these roads, 4 hotspot cells, worst segment ETNA ~12 days.

---

## Sanity Check Against National Statistics

NZ averages approximately:
- ~30,000 injury crashes per year
- ~40 billion vehicle-km driven per year
- ≈ 1 injury crash per 1.3 million vehicle-km

A 125 km trip should have roughly `125 / 1,300,000 ≈ 0.01%` baseline crash probability. Our model produces slightly higher numbers because routes tend to follow busier, higher-risk corridors rather than the national average road. The numbers are in the right order of magnitude.

---

## What the UI Displays

The route results panel shows three layers of information:

### Risk Score (1–10)

Large, colour-coded number at the top. Green (1–3), amber (4–6), orange (7–8), red (9–10). Provides instant visual differentiation between routes.

### Crashes on This Route

- **Crashes/year (all vehicles)**: Total crashes per year across all roads on the route. This is the raw danger of the route — "42 crashes/year happen on these roads."
- **Hotspot cells**: Number of cells where a crash occurs at least once per week (ETNA < 7 days). These are the stretches that drive the risk.
- **Worst segment**: ETNA of the most dangerous cell, plus its DSI and speed limit.

### Your Risk

- **Single trip**: Per-vehicle probability for one trip (the actuarial number).
- **If driven N times/week**: Adjustable frequency selector (2, 3, 5, 7, 10 times/week). Computes annual risk assuming return trips.
- **DSI if crash**: Probability of death or serious injury, should a crash occur.

---

## Bug History

| Date | Issue | Impact | Fix |
|------|-------|--------|-----|
| 2026-03-13 | Route lookup used `top_cells` (top 5,000) instead of full `cell_profiles` | Most route cells had no data → 0 crashes, 0 risk | Changed to search all `cell_profiles` |
| 2026-03-13 | Per-vehicle formula multiplied by `hours_in_cell` | Risk ~80× too low | Removed — transit time cancels out mathematically |
| 2026-03-13 | Risk score scale placed NZ average at score 1 | All routes showed "Low Risk" | Recentered: NZ average = score 5, baseline corrected to 0.32/km/year |
| 2026-03-13 | Risk score denominator used all cells (including no-data) | Crash density diluted by empty cells | Changed to scored cells only |

---

## Limitations and Assumptions

1. **Uniform traffic distribution**: ADT is divided by 24 to get vehicles/hour. Real traffic peaks at rush hour (2–3× average) and drops overnight (0.1× average). This means risk is underestimated during quiet periods and overestimated during peak.

2. **No time-of-day crash patterns**: The CAS API doesn't provide hour-of-day data, so crash rates are uniform across 24 hours. In reality, crashes cluster at certain times.

3. **Cell-level granularity**: H3 resolution 9 cells are ~0.6 km across. A cell containing both a dangerous intersection and a straight road averages their risk together.

4. **AADT coverage gaps**: NZTA AADT data covers state highways and major roads well, but local roads often fall back to estimates.

5. **Independence assumption**: The complement aggregation assumes crash probabilities are independent across cells. In practice, conditions are correlated (rain affects many cells simultaneously), but the condition multiplier already adjusts individual cell rates.

6. **Historical basis**: The model is fundamentally retrospective — it assumes past crash patterns predict future risk. Road improvements, speed limit changes, or new developments may change actual risk.

---

*Last updated: 2026-03-13*
