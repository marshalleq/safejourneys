# Safe Journeys — Model Performance Report

## Executive Summary

We trained crash severity prediction models on 910,823 NZ CAS crash records (2000–2025) using 80 engineered features. The best model achieves **AUC 0.8186** on a held-out 2024–2025 test set, predicting whether a crash will result in Death or Serious Injury (DSI).

This report documents every configuration tested, what worked, what didn't, and why.

---

## Dataset

| Split | Period | Records | DSI Rate |
|-------|--------|---------|----------|
| Train | 2000–2021 | 789,270 | 6.5% |
| Validation | 2022–2023 | 62,742 | 7.7% |
| Test | 2024–2025 | 58,811 | 8.0% |

**Temporal split** (not random) to prevent data leakage — the model never sees future crashes during training.

---

## Feature Set Evolution

### v1: Basic Features (28 features)
The initial model used 28 hand-picked features from `SEVERITY_FEATURES`: speed limit, lanes, urban/rural, weather, light, vehicle types, and compound risk factors (e.g. wet+dark).

### v2: Full Features + Target-Leaking Cell History (85 features) — FAILED
Added 11 categorical columns (region, TLA, road character, weather, light, etc.), all vehicle/impact types, and per-H3-cell historical statistics including `cell_dsi_rate`, `cell_mean_severity`, and `area_dsi_rate`.

**Result: AUC dropped to 0.68.** The cell-level severity features (DSI rate, mean severity) are essentially target-variable lookups — the model latched onto them and stopped learning after 3–18 iterations. This is a form of target leakage even though the features were computed from training data only.

### v3: Full Features, Non-Target Cell History (80 features) — FINAL
Removed all cell history features that encode the outcome (severity, DSI rate, fatal/serious counts). Kept only location-characteristic features: crash volume, annual crash rate, mean speed, and proportions of rain/dark/intersection/urban/hill crashes.

**Result: AUC 0.8186** — a genuine improvement over v1.

#### Final feature categories (80 total):
- **Road attributes** (6): speed limit, advisory speed, lanes, speed mismatch, etc.
- **Weather/visibility** (6): rain, poor visibility, fine, strong wind, frost, weather code
- **Light conditions** (3): dark, twilight, light code
- **Road characteristics** (7): urban, sealed, hill, intersection, traffic control, street light, multi-lane
- **Vehicle types** (16): bicycle, bus, car, motorcycle, moped, SUV, taxi, truck, van, totals, flags
- **Impact objects** (7): cliff/bank, ditch, fence, guard rail, tree, post/pole, over bank
- **Compound risk** (7): wet+dark, wet+high speed, dark+high speed, triple compound, hill+wet, vulnerable+dark, vulnerable+high speed
- **Temporal** (2): years since 2000, holiday flag
- **Cell history** (8): crash count, annual rate, mean speed, % rain/dark/intersection/urban/hill
- **Area history** (2): area crash count, area annual rate
- **Categoricals** (11): region, TLA, road character, road lane, traffic control, flat/hill, road surface, weather, light, urban, crash direction

---

## Model Configurations Tested — Ranked by Test AUC

### Rank 1: LightGBM `slow_deep` — AUC 0.8186

| Metric | Value |
|--------|-------|
| **Test AUC-ROC** | **0.8186** |
| **Test Avg Precision** | **0.3267** |
| **Val AUC** | 0.8119 |
| **Val Logloss** | 0.2199 |
| **Best Iteration** | 938 |
| **F1 (DSI class)** | 0.38 |
| **Optimal Threshold** | 0.18 |

```
learning_rate: 0.01      num_leaves: 127
min_child_samples: 50    subsample: 0.7
colsample_bytree: 0.6    reg_alpha: 0.5
reg_lambda: 2.0          max_rounds: 8000
early_stopping: 300      metric: binary_logloss
```

**Why it won:** The low learning rate (0.01) with strong regularisation (alpha=0.5, lambda=2.0) and deeper trees (127 leaves) allowed the model to make very fine-grained gradient steps over 938 iterations. Heavy regularisation prevented overfitting despite the deeper tree structure.

---

### Rank 2: LightGBM `moderate` — AUC 0.8182

| Metric | Value |
|--------|-------|
| Test AUC-ROC | 0.8182 |
| Val AUC | 0.8115 |
| Val Logloss | 0.2200 |
| Best Iteration | 379 |

```
learning_rate: 0.02      num_leaves: 127
min_child_samples: 75    subsample: 0.75
colsample_bytree: 0.65   reg_alpha: 0.3
reg_lambda: 1.5          max_rounds: 8000
early_stopping: 300
```

**Why it's close:** A middle ground between baseline and slow_deep. The 2x higher learning rate meant it converged in 379 iterations (vs 938), reaching nearly the same AUC. Slightly less regularisation than slow_deep.

---

### Rank 3: LightGBM `baseline` — AUC 0.8174

| Metric | Value |
|--------|-------|
| Test AUC-ROC | 0.8174 |
| Val AUC | 0.8109 |
| Val Logloss | 0.2202 |
| Best Iteration | 196 |

```
learning_rate: 0.05      num_leaves: 63
min_child_samples: 100   subsample: 0.8
colsample_bytree: 0.7    reg_alpha: 0.1
reg_lambda: 1.0          max_rounds: 5000
early_stopping: 200
```

**Why it's slightly worse:** Higher learning rate and shallower trees (63 leaves) — fast to converge (196 iterations) but makes coarser gradient steps, missing the fine detail the slow_deep model captures.

---

### Rank 4: Ensemble (3-model average) — AUC 0.8184

| Metric | Value |
|--------|-------|
| Test AUC-ROC | 0.8184 |
| Val AUC | 0.8118 |

Simple average of predictions from all three models above.

**Why it didn't win:** All three models learned very similar decision boundaries (they agree on what's a DSI crash). Averaging near-identical predictions doesn't add diversity. Ensembles help most when component models make different types of errors.

---

### Rank 5: LightGBM v1 (sklearn wrapper, 28 features) — AUC 0.7786

| Metric | Value |
|--------|-------|
| Test AUC-ROC | 0.7786 |
| F1 (DSI class) | 0.351 |
| Best Iteration | ~200 |

The original model using only 28 basic features and the sklearn `LGBMClassifier` wrapper with default-like parameters.

**Why it's lower:** Fewer features (no categoricals, no cell history, no vehicle subtypes, no impact objects). The 52 additional features in the final model provide +0.04 AUC.

---

### Rank 6: LightGBM with `is_unbalance=True` + `metric=auc` — AUC 0.6819

| Metric | Value |
|--------|-------|
| Test AUC-ROC | 0.6819 |
| Best Iteration | 3 |

```
is_unbalance: True       metric: auc
learning_rate: 0.03      num_leaves: 127
```

**Why it failed catastrophically:** The combination of `is_unbalance=True` (which upweights the minority DSI class ~14x) and `metric=auc` caused the model to find a degenerate solution in just 3 iterations. The massive class reweighting distorted the loss landscape so the AUC metric peaked immediately and declined.

---

### Rank 7: LightGBM with target-leaking cell history — AUC 0.6761–0.6856

| Metric | Value |
|--------|-------|
| Test AUC-ROC | 0.6761–0.6856 |
| Best Iteration | 3–18 |

Added `cell_dsi_rate`, `cell_mean_severity`, `cell_severity_score`, `area_dsi_rate`, `area_mean_severity` as features.

**Why it failed:** These features directly encode the target variable's historical average per location. The model learned to predict DSI purely from the cell's historical DSI rate, ignoring all other features. This "shortcut" doesn't generalise — cells in val/test have different conditions than their historical average, and new cells have no history at all.

**Key lesson:** Cell history features that describe the _location_ (crash volume, road characteristics) are useful. Cell history features that describe the _outcome_ (severity, DSI rate) are target leakage.

---

## XGBoost Multi-Class Severity Model

A secondary XGBoost model predicts the full 4-class severity distribution (Non-Injury / Minor / Serious / Fatal).

| Metric | Value |
|--------|-------|
| Best Iteration | 968 |
| Overall Accuracy | 69% |
| Non-Injury F1 | 0.82 |
| Minor F1 | 0.34 |
| Serious F1 | 0.13 |
| Fatal F1 | 0.03 |

```
learning_rate: 0.03      max_depth: 7
n_estimators: 1500       min_child_weight: 50
subsample: 0.8           colsample_bytree: 0.7
objective: multi:softprob eval_metric: mlogloss
early_stopping_rounds: 100
```

The model predicts Non-Injury well but struggles with rare classes (Fatal = 0.9% of data). This is expected — distinguishing Fatal from Serious requires information not in the dataset (vehicle safety, seatbelt use, medical response).

Used for the speed limit counterfactual analysis and scenario scoring engine.

---

## Top 15 Most Important Features

| Rank | Feature | Gain | Category |
|------|---------|------|----------|
| 1 | vulnerableUser | 962,546 | Vehicle type |
| 2 | cell_crash_count | 358,432 | Cell history |
| 3 | area_crash_count | 282,855 | Area history |
| 4 | cell_annual_rate | 273,211 | Cell history |
| 5 | tlaName | 265,180 | Categorical |
| 6 | vulnerableAndHighSpeed | 251,342 | Compound risk |
| 7 | hasPedestrian | 164,822 | Vehicle type |
| 8 | area_annual_rate | 148,678 | Area history |
| 9 | carStationWagon | 145,663 | Vehicle type |
| 10 | totalVehicles | 141,075 | Vehicle type |
| 11 | motorcycle | 129,872 | Vehicle type |
| 12 | speedLimit | 105,508 | Road attribute |
| 13 | moped | 93,744 | Vehicle type |
| 14 | yearsSince2000 | 89,124 | Temporal |
| 15 | bicycle | 80,490 | Vehicle type |

**Key insight:** Vulnerable road users (pedestrians, cyclists, motorcyclists) and location-based crash history dominate. Speed limit is important but ranks 12th — who's on the road and where matters more than how fast.

---

## What Would Improve the Model Further

The model has reached the ceiling of what the CAS dataset can provide (~0.82 AUC). All hyperparameter configurations converge to the same performance. Further improvement requires new data sources:

| Data Source | Expected Impact | Why |
|-------------|----------------|-----|
| Vehicle safety ratings (ANCAP) | High | Directly predicts occupant protection |
| Driver impairment (BAC, fatigue) | High | Strongest known predictor of fatal crashes |
| Road geometry (curvature, grade) | Medium | Currently only flat/hill binary flag |
| AADT traffic volumes | Medium | Enables crash rate vs exposure analysis |
| Real-time weather intensity | Medium | Currently only rain yes/no |
| EMS response times | Medium | Affects fatal vs serious outcome |
| Seatbelt/helmet compliance | High | Directly affects injury severity |

---

## Speed Limit Counterfactual Results

Using the XGBoost model on 15,442 crashes at 100 km/h:

| Speed Limit | Predicted DSI Rate | Change |
|-------------|-------------------|--------|
| 100 km/h (actual) | 11.4% | baseline |
| 80 km/h | 10.5% | -0.9% |

The model predicts a modest DSI reduction from 100→80 km/h. This is conservative because the model only captures the speed limit's correlation with severity, not the causal physics of impact energy (which scales with velocity squared).

---

## Scenario Scoring Examples

| Scenario | DSI Probability | Rating |
|----------|----------------|--------|
| Rural 100km/h, fine, day | 11.3% | ELEVATED |
| Rural 100km/h, rain, night | 12.8% | ELEVATED |
| Rural 80km/h, rain, night | 11.7% | ELEVATED |
| Urban 50km/h, night, pedestrian, intersection | 25.1% | HIGH RISK |
| Rural 100km/h, rain, motorcycle | 46.1% | HIGH RISK |

The model correctly identifies motorcycles and pedestrians as the highest-risk scenarios, consistent with NZ crash statistics.

---

*Generated from Safe Journeys PoC — Notebook 3: Crash Severity Prediction*
*Dataset: NZ CAS 2000–2025, 910,823 records*
*Best model: LightGBM Booster, 938 iterations, 80 features, AUC 0.8186*
