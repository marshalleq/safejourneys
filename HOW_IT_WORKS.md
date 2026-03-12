# Safe Journeys — How the Prediction Engine Works

A plain-language guide to how the system predicts crash risk, so you can explain it to anyone.

---

## The Big Picture

The system answers two questions for every location on the map:

1. **"If a crash happens here, how bad will it be?"** — the Severity Model
2. **"How often do crashes happen here?"** — the Frequency Model

These are combined to show both the colour of each hex (severity) and the pulsing red markers (frequency hotspots). Together they tell you: where crashes happen most often, and where they're most likely to kill or seriously injure someone.

---

## The Data

Everything is built on **910,823 real crash records** from NZTA's Crash Analysis System (CAS), spanning 2000–2025. Each record is a single crash with details about:

- **Location**: GPS coordinates, road name, region, urban/rural
- **Road**: speed limit, number of lanes, sealed/unsealed, hill/flat, intersection
- **Conditions**: weather (rain, fine, frost), light (dark, twilight, bright sun), visibility
- **Vehicles**: car, motorcycle, bicycle, truck, bus, pedestrian — and how many
- **Impact**: what was hit (tree, pole, ditch, barrier, cliff)
- **Outcome**: how many people were uninjured, had minor injuries, serious injuries, or died

### Dataset at a Glance

| Metric | Value |
|--------|-------|
| **Total crash records** | 910,823 |
| **Time period** | 2000–2025 |
| **Average crashes per year** | ~33,700 |
| **Total people killed** | 9,371 |
| **Total seriously injured** | 63,316 |
| **Total minor injuries** | 290,779 |
| **Crashes involving a fatality** | 8,302 (0.9%) |
| **Crashes involving serious injury** | 54,646 (6.0%) |
| **Death or Serious Injury (DSI) crashes** | 60,627 (6.7%) |
| **Average deaths per year** | ~347 |
| **Average serious injuries per year** | ~2,345 |

### Who's Involved

| Road User | Crashes | % of All | DSI Rate |
|-----------|---------|----------|----------|
| **Motorcyclist** | 33,109 | 3.6% | **31.9%** |
| **Pedestrian** | 30,097 | 3.3% | **26.2%** |
| **Cyclist** | 25,551 | 2.8% | **17.7%** |
| Any vulnerable user | 87,556 | 9.6% | — |
| No vulnerable user | 823,267 | 90.4% | **4.6%** |

Vulnerable road users are involved in fewer than 10% of crashes but face dramatically higher severity — a motorcyclist is **7x more likely** to die or be seriously injured than a car occupant.

### Where Crashes Happen

| Context | Crashes | DSI Rate |
|---------|---------|----------|
| **Urban** (67%) | 612,135 | **4.8%** |
| **Rural** (33%) | 298,688 | **10.4%** |
| **50 km/h zones** | 540,621 | **4.5%** |
| **100 km/h zones** | 249,507 | **11.0%** |

Rural roads have **double the DSI rate** of urban roads. Most crashes happen in 50 km/h urban zones, but most deaths happen on 100 km/h rural roads.

### Top Regions by Crash Volume

| Region | Crashes |
|--------|---------|
| Auckland | 313,213 (34%) |
| Waikato | 98,738 (11%) |
| Canterbury | 91,929 (10%) |
| Wellington | 87,527 (10%) |
| Bay of Plenty | 52,892 (6%) |

### Conditions

| Condition | Crashes | % of All | DSI Rate |
|-----------|---------|----------|----------|
| **Darkness** | 252,376 | 27.7% | 7.4% |
| **Rain** | 173,857 | 19.1% | 5.6% |
| **Hill road** | 177,224 | 19.5% | — |
| **Twilight** | 42,645 | 4.7% | — |
| Daylight, no rain | — | — | 6.4% / 6.9% |

Interestingly, rain is associated with a *lower* DSI rate (5.6% vs 6.9%) — likely because drivers slow down in rain. Darkness increases severity (7.4% vs 6.4%).

### Vehicle Count

| Vehicles | Crashes | % |
|----------|---------|---|
| Single vehicle | 308,472 | 33.9% |
| Two vehicles | 522,568 | 57.4% |
| Three or more | 77,478 | 8.5% |

One in three crashes involves only a single vehicle — typically loss-of-control events hitting a tree, pole, ditch, or barrier.

### Trends (Last 8 Full Years)

| Year | Crashes | Deaths | Serious Injuries |
|------|---------|--------|-----------------|
| 2017 | 39,310 | 377 | 2,861 |
| 2018 | 38,461 | 377 | 2,597 |
| 2019 | 36,921 | 350 | 2,527 |
| 2020 | 32,814 | 318 | 2,186 |
| 2021 | 34,151 | 320 | 2,327 |
| 2022 | 31,247 | 371 | 2,499 |
| 2023 | 31,495 | 341 | 2,459 |
| 2024 | 29,323 | 291 | 2,466 |

Total crashes are declining (~25% fewer in 2024 vs 2017), but deaths and serious injuries remain stubbornly high — the severity rate is *increasing* even as crash numbers fall. This is a key motivation for the project: fewer crashes, but the ones that happen are getting worse.

### What the data does NOT include

The public CAS dataset only records the **year** of each crash (e.g., 2023) — not the exact date or time. It also doesn't include driver impairment (alcohol, fatigue), seatbelt/helmet use, vehicle safety ratings, or emergency response times. These are real limitations — the model can only learn from what it can see.

---

## Step 1: Organising the Map into Hexagons

New Zealand is divided into a grid of **H3 hexagons** — a spatial indexing system developed by Uber. Each hex is roughly 0.74 km² (about 460m across). Every crash is assigned to the hex it occurred in.

We keep the **top 5,000 hexagons by crash count** — these cover all the high-activity areas across the country. Each hex has a profile built from its crash history: how many crashes, what types, what conditions, what outcomes.

---

## Step 2: The Severity Model (LightGBM)

### What it predicts

For any given set of road conditions, the model outputs the **probability that a crash at this location would result in Death or Serious Injury (DSI)**.

For example: "If a crash happened right now at this location, there's a 12.3% chance someone would die or be seriously injured."

### How it learns

The model is a **LightGBM gradient-boosted decision tree** — a type of machine learning that builds hundreds of small decision trees, each one correcting the mistakes of the ones before it. Think of it as a very sophisticated flowchart:

```
Is there a vulnerable road user (pedestrian/cyclist)?
  → Yes: much higher risk → Is it dark? → Yes: even higher risk...
  → No: Is the speed limit above 80? → Yes: check if wet...
```

Except instead of a single flowchart, it builds 938 of them, each adding a small refinement. The final prediction is the combined output of all 938 trees.

### What it uses (80 features)

The model considers 80 pieces of information about each location and scenario:

| Category | Examples | Why it matters |
|----------|----------|----------------|
| **Road attributes** | Speed limit, number of lanes, advisory speed | Higher speeds = more severe crashes |
| **Weather** | Rain, poor visibility, frost, wind | Wet roads increase stopping distance |
| **Light** | Dark, twilight, bright sun | Reduced visibility increases severity |
| **Road type** | Urban/rural, hill/flat, intersection, sealed | Rural roads have higher severity |
| **Vehicle types** | Pedestrian, cyclist, motorcycle, truck | Vulnerable users have much worse outcomes |
| **Impact objects** | Tree, pole, ditch, cliff, barrier | Fixed objects cause severe injuries |
| **Compound risks** | Wet AND dark, vulnerable AND high speed | Risk combinations are worse than individual risks |
| **Location history** | How many crashes per year, typical conditions | Some locations are consistently dangerous |
| **Regional context** | Region, TLA, crash volume in surrounding area | Regional patterns exist |

### How we know it works

The model was trained on crashes from **2000–2021** and tested on crashes from **2024–2025** that it had never seen. This "temporal split" is critical — it means the model must genuinely predict the future, not just memorise the past.

| Metric | Meaning | Value |
|--------|---------|-------|
| **AUC-ROC** | How well it separates DSI from non-DSI (1.0 = perfect, 0.5 = random guessing) | **0.82** |
| **Average Precision** | How well it identifies the rare DSI cases | **0.33** |
| **Optimal Threshold** | The probability cutoff for classifying "high risk" | **0.18** |

An AUC of 0.82 means: if you pick a random DSI crash and a random non-DSI crash, the model will correctly rank the DSI crash as higher risk **82% of the time**.

### What drives the predictions

The most influential features, ranked by importance:

1. **Vulnerable road user** (pedestrian, cyclist, motorcyclist) — by far the biggest factor
2. **Location crash history** — some places are consistently dangerous
3. **Area crash density** — dangerous locations cluster together
4. **Vehicle type** — pedestrians and motorcyclists fare worst
5. **Speed limit** — important, but ranks 12th — *who* is on the road matters more than *how fast*

### How the "what-if" scenario scoring works

When you change conditions on the map (e.g., set weather to "Rain", light to "Dark"), the system:

1. Takes the real feature values for every hex cell
2. Overrides the weather/light/speed/vehicle columns to match your scenario
3. Recomputes compound risk features (e.g., wet+dark, vulnerable+high speed)
4. Runs the model again on all 5,000 cells
5. Returns updated DSI probabilities

This lets you ask: "What would crash severity look like across the whole country if it were raining at night?"

---

## Step 3: The Frequency Model (Poisson Rate Estimation)

### What it predicts

The frequency model estimates **how often crashes occur** at each location, expressed as the **Expected Time to Next Accident (ETNA)**.

For example: "This location averages a crash every 4 days."

### How it works

This is a simpler statistical model, not machine learning:

1. **Count** the total crashes in each hex cell over the training period (2000–2021)
2. **Divide by years** to get an annual rate: e.g., 180 crashes ÷ 22 years = 8.2 crashes/year
3. **Convert to hourly rate**: 8.2 ÷ 8,760 hours/year = 0.000936 crashes/hour
4. **Invert** to get ETNA: 1 ÷ 0.000936 = 1,068 hours ≈ 44 days between crashes

This assumes crashes follow a **Poisson process** — random events occurring at a steady average rate. It's the same model used for earthquakes, radioactive decay, and call centre staffing.

### Condition adjustments

The base rate is adjusted for current conditions using **multipliers** calculated from the data:

- If 30% of crashes happen in rain, but rain only occurs ~18% of hours → rain multiplier = 30/18 = **1.67x**
- If 25% of crashes happen in darkness, but it's dark ~38% of hours → dark multiplier = 25/38 = **0.66x**
- If it's raining AND dark → combined multiplier (calculated from crashes in both conditions)

So if a location normally has ETNA of 44 days, in rain it becomes 44 ÷ 1.67 = **26 days**.

### Important: what ETNA is and isn't

**ETNA is a statistical average, not a countdown timer.** "Next crash in 2 days" really means "this location averages a crash every 2 days based on 22 years of data." It does not mean a crash will literally happen in 2 days.

**Known limitations:**
- Based on annual crash counts, not precise timestamps (the data only has crash year, not date)
- Assumes a constant rate (no seasonality — summer vs winter are not distinguished)
- Cannot detect whether crash rates are accelerating or decelerating at a location
- New road improvements or traffic changes aren't reflected until new crash data is available

If exact crash dates were available, we could model time-of-day patterns, day-of-week effects, seasonal variation, and trends over time.

---

## Step 4: Data-Driven Mitigations

For each location, the system analyses the crash history and recommends specific interventions, citing the evidence:

### How mitigations are generated

1. **Count** specific crash types at each location (rain crashes, dark crashes, intersection crashes, pedestrian crashes, etc.)
2. **Compare** to the national average — e.g., "35% of crashes here involve pedestrians vs 8% nationally = 4.4x the national average"
3. **Apply thresholds** — only recommend an intervention if the rate is at least 1.2x the national average AND there are enough crashes to be meaningful (not just 1 or 2)
4. **Cite the evidence** — every recommendation quotes the actual numbers: "42 out of 180 crashes (23%) occurred at intersections — 1.8x the national average"

### Example mitigations

| Finding | Recommendation |
|---------|----------------|
| 35% pedestrian crashes (4.4x national avg) | Add signalised crossings and speed tables |
| 28% dark crashes (1.5x national avg) | Install or upgrade street lighting |
| High speed + 8 fatalities | Reduce speed limit or add variable speed signs |
| 23% intersection crashes (1.8x national avg) | Upgrade to roundabout (reduces injury crashes by 75%) |
| 15% cyclist crashes (3.1x national avg) | Install separated cycle lanes |

The recommendations are based on established road safety engineering evidence (e.g., roundabouts reduce injury crashes by 75%, pedestrian fatality risk drops from 45% at 50 km/h to 5% at 30 km/h).

---

## Step 5: Putting It All Together on the Map

The interactive map combines all three outputs:

| Visual Element | What It Shows | Source |
|----------------|---------------|--------|
| **Hex colour** (green → red) | DSI severity probability | Severity Model |
| **Pulsing red markers** | Crash frequency hotspots (shortest ETNA) | Frequency Model |
| **Marker rank number** | Position in the top 50 most frequent | Frequency Model |
| **Tooltip** | Location, ETNA, DSI %, crash counts, mitigations | All models |
| **Sidebar list** | Top 50 hotspots with addresses and details | All models |

### How the layers interact

- A location can be **green but have a pulsing marker** — meaning crashes are frequent but rarely severe (e.g., busy urban intersection with many minor fender-benders)
- A location can be **red but have no marker** — meaning crashes are rare but very severe when they happen (e.g., remote rural highway with occasional fatal head-on collisions)
- The most dangerous locations are **both red and marked** — frequent crashes that are also severe

---

## What the Model Cannot Do

Being honest about limitations is essential for responsible use:

1. **It cannot predict individual crashes.** It estimates statistical risk — "locations like this, with these conditions, have X% DSI rate" — not "a crash will happen here on Tuesday."

2. **It only knows what the data contains.** Driver behaviour (alcohol, fatigue, distraction), vehicle condition, and medical response are not in the CAS dataset. These are major determinants of crash severity.

3. **Correlation, not causation.** The model finds patterns (e.g., rural 100 km/h roads have higher DSI rates) but can't prove that speed *caused* the severity — it might be that rural roads also lack median barriers, are further from hospitals, etc.

4. **Historical patterns may not hold.** New roads, changed speed limits, new infrastructure — the model doesn't know about these until new crash data reflects them.

5. **The 0.82 AUC ceiling.** The model correctly ranks DSI vs non-DSI crashes 82% of the time. The remaining 18% is driven by factors not in the data. More data from the same source won't improve this — new data types (vehicle safety ratings, impairment data) are needed.

---

## Summary for Non-Technical Audiences

> We analysed every recorded crash in New Zealand over the last 25 years — over 900,000 crashes. For each location, we know the road conditions, weather, who was involved, what was hit, and how severe the outcome was.
>
> Using this data, we built two models:
>
> 1. A **severity model** that predicts how bad a crash would be at any given location under any conditions — trained using machine learning on 80 different factors
> 2. A **frequency model** that estimates how often crashes happen at each location — based on historical crash rates adjusted for conditions
>
> The system shows these predictions on an interactive map, highlights the most dangerous locations, and recommends specific safety improvements backed by the actual crash evidence at each site.
>
> It's not a crystal ball — it's a statistical tool that says "based on 25 years of evidence, these are the highest-risk locations and here's what the data suggests would help."

---

*Based on NZ CAS data 2000–2025 (910,823 records) | LightGBM severity model (AUC 0.82) | Poisson frequency model | 80 engineered features | H3 hexagonal spatial index*
