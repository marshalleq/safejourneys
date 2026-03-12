# Safe Journeys — Roadmap to Predictive Model

## Current State

Safe Journeys is a **retrospective risk scoring system**. It identifies where crashes have historically occurred and estimates severity probability using a pre-trained LightGBM model. Condition adjustments (rain/dark multipliers) provide basic real-time context, but the system fundamentally says "crashes happened here before, so the risk is elevated."

## Goal

Evolve to a **predictive forecasting system** that can say: *"This intersection has 3.2x elevated risk Friday 5–7pm due to forecast rain, high traffic volume, and a holiday weekend."*

---

## Phase 1: Temporal Patterns (from existing data)

**Effort:** Low | **Impact:** Medium | **Data:** Already available in CAS

The existing 910K crash records contain hour-of-day and day-of-week information that isn't currently modelled. Crashes are not uniformly distributed across time — Friday 5pm is fundamentally different from Tuesday 3am.

### Deliverables

- [ ] Compute per-cell hourly crash rate distributions (24-hour profile)
- [ ] Compute per-cell day-of-week multipliers (7-day profile)
- [ ] Combine into a time-varying rate: `adjusted_rate = base_rate × hour_multiplier × day_multiplier`
- [ ] Update ETNA calculation to use time-aware rates
- [ ] Add time-of-day context to risk factor explanations in popups
- [ ] Visualise how risk changes across the day (sparkline or heat strip in popup)

### What this enables

"This intersection's crash rate is 2.4x higher between 3–5pm on weekdays" — useful for school zone identification and commuter corridor risk.

---

## Phase 2: Live Weather Integration

**Effort:** Low | **Impact:** Medium | **Data:** Free API (OpenWeatherMap or Open-Meteo)

Replace static condition multipliers with real-time weather data mapped to H3 cells.

### Deliverables

- [ ] Integrate weather API (Open-Meteo preferred — free, no key required, NZ coverage)
- [ ] Fetch current conditions + 24-hour forecast per region
- [ ] Map weather grid to H3 cells (nearest-point or interpolation)
- [ ] Replace binary rain/dark flags with continuous variables:
  - Rainfall intensity (mm/h)
  - Temperature (black ice risk below 3°C)
  - Wind speed (gusts affecting vehicle stability)
  - Visibility (fog, heavy rain)
- [ ] Compute dynamic condition multipliers from weather data
- [ ] Show current weather context in UI ("Risk elevated: heavy rain forecast 4–6pm")
- [ ] Add weather overlay toggle to the map

### What this enables

"Risk at this location is currently 1.4x elevated due to 12mm/h rainfall and dropping to normal by 8pm" — forward-looking risk that changes with conditions.

---

## Phase 3: Traffic Volume Data (AADT)

**Effort:** Medium | **Impact:** High | **Data:** NZTA open data

Traffic volume is the single biggest gap. You cannot meaningfully predict crash likelihood without knowing how many vehicles use a road.

### Deliverables

- [ ] Download NZTA Annual Average Daily Traffic (AADT) counts for state highways
- [ ] Map AADT to H3 cells (spatial join of count stations to nearest cells)
- [ ] Compute crash rate per vehicle-km (exposure-adjusted risk)
- [ ] Add AADT as a feature to the severity model (retrain)
- [ ] Add traffic volume context to risk explanations
- [ ] Differentiate between "dangerous road" and "busy road" in the UI

### Data sources

- NZTA Traffic Monitoring System: https://opendata-nzta.opendata.arcgis.com/
- TMS count sites with AADT estimates for state highways and some local roads
- Updated annually

### What this enables

Separates genuine danger from mere volume. A rural highway with 500 vehicles/day and 10 crashes/year is far more dangerous per-vehicle than an urban motorway with 50,000 vehicles/day and 100 crashes/year.

---

## Phase 4: Calendar and Event Awareness

**Effort:** Low | **Impact:** Low–Medium | **Data:** Public

### Deliverables

- [ ] NZ public holiday calendar (statutory holidays, regional anniversary days)
- [ ] School term dates (Ministry of Education)
- [ ] Long weekend flags (Friday/Monday adjacent to holidays)
- [ ] Major event feeds (optional — concerts, rugby, festivals near specific cells)
- [ ] Add calendar features to the temporal model from Phase 1
- [ ] Holiday weekend warning overlay in UI

### What this enables

"Long weekend — crash risk on SH1 Taupo corridor is historically 1.8x higher than normal weekends."

---

## Phase 5: Real-Time Traffic

**Effort:** Medium | **Impact:** High | **Data:** Paid API (Google Maps Platform / TomTom / HERE)

Move from static AADT to live traffic conditions.

### Deliverables

- [ ] Integrate traffic API for real-time speed and congestion data
- [ ] Map traffic segments to H3 cells
- [ ] Compute speed differential (posted limit vs actual flow speed) as a risk feature
- [ ] Detect congestion transitions (free-flow → stop-and-go) which correlate with rear-end crashes
- [ ] Real-time traffic overlay on map
- [ ] Retrain model with traffic features

### Cost consideration

Google Maps Routes API: ~$5–10/1000 requests. For continuous monitoring of 5,000 cells, this could be $50–200/day depending on polling frequency. TomTom and HERE have similar pricing. Consider polling only the top 500 highest-risk cells frequently and the rest hourly.

### What this enables

"SH1 northbound is currently 40km/h in a 100km/h zone — congestion-related crash risk is elevated."

---

## Phase 6: Model Architecture Evolution

**Effort:** High | **Impact:** High | **Prerequisite:** Phases 1–3 complete

With temporal, weather, and traffic features available, the model architecture should evolve.

### Options (in order of complexity)

1. **Poisson GLM with time-varying covariates**
   - Straightforward extension of current approach
   - `log(λ) = β₀ + β₁·hour + β₂·rain_mm + β₃·AADT + β₄·cell_history + ...`
   - Interpretable, fast, good baseline
   - Implemented in scikit-learn or statsmodels

2. **Gradient-boosted Poisson regression (LightGBM)**
   - Same framework as current severity model but with `objective=poisson`
   - Handles non-linear interactions automatically
   - Can use all 80+ features plus new temporal/weather/traffic ones

3. **Spatio-temporal model**
   - Learns that neighbouring cells influence each other (a crash on SH1 affects adjacent SH1 cells)
   - Options: Spatial Durbin model, ST-GCN, or simpler spatial lag terms
   - Requires more engineering but captures spatial spillover effects

4. **Bayesian hierarchical model**
   - Naturally handles cells with sparse data by borrowing strength from similar locations
   - Provides uncertainty intervals ("70% chance of 0–2 crashes this week" rather than a point estimate)
   - Useful for communicating confidence to stakeholders

### Recommendation

Start with option 2 (LightGBM Poisson) — it reuses your existing toolchain and is the fastest path to a working predictive model. Move to option 3 or 4 only if spatial correlation proves important.

---

## Phase 7: Road Network Change Detection

**Effort:** High | **Impact:** Medium | **Data:** NZTA

### Deliverables

- [ ] Ingest NZTA road network datasets (centreline, speed limits, intersection geometry)
- [ ] Detect changes between data snapshots (new roads, changed speed limits, redesigned intersections)
- [ ] Flag cells where the road environment has changed since the historical data was collected
- [ ] Adjust model confidence downward for recently-changed locations (historical patterns may not apply)
- [ ] Track the nationwide speed limit review and update cells as limits change

### What this enables

"Speed limit on this road changed from 100 to 80 km/h in 2025 — historical crash data may overstate current risk."

---

## Summary

| Phase | Enhancement | Effort | Impact | Data | Status |
|-------|------------|--------|--------|------|--------|
| 1 | Hour/day-of-week temporal patterns | Low | Medium | Already have | BLOCKED — CAS API has no time-of-day data |
| 2 | Live weather integration | Low | Medium | Free API | DONE — Open-Meteo + sun position |
| 3 | AADT traffic volumes | Medium | High | NZTA open data | DONE — Carriageway API, exposure rates |
| 4 | Calendar / events / holidays | Low | Low–Medium | Public | DONE — NZ holidays + period multipliers |
| 5 | Real-time traffic | Medium | High | Paid API | Planned |
| 6 | Model architecture evolution | High | High | Phases 1–3 | Planned |
| 7 | Road network change detection | High | Medium | NZTA | Planned |

### Completed

- **Phase 2**: Open-Meteo weather API with 8 NZ sampling points, 24hr forecast strip, sun position for light detection, 10-minute auto-refresh
- **Phase 3**: NZTA Carriageway AADT data (10,834 road segments), mapped to H3 cells, exposure-adjusted crash rates (per 100M vehicle-km), background loading
- **Phase 4**: Full NZ public holiday calendar (including Mondayisation, Easter, Matariki), holiday period detection, crash rate multiplier during holiday periods, next-holiday countdown

---

*Last updated: 2026-03-12*
