"""Visualisation helpers for the Safe Journeys PoC."""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns


def set_style():
    """Set consistent plot styling."""
    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update({
        "figure.figsize": (12, 6),
        "font.size": 11,
        "axes.titlesize": 14,
        "axes.labelsize": 12,
    })


def plot_severity_distribution(df: pd.DataFrame):
    """Bar chart of crash severity distribution."""
    set_style()
    order = ["Non-Injury", "Minor", "Serious", "Fatal"]
    colors = ["#2ecc71", "#f39c12", "#e74c3c", "#8e44ad"]

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Count
    counts = df["crashSeverity"].value_counts().reindex(order)
    axes[0].bar(order, counts.values, color=colors)
    axes[0].set_title("Crash Count by Severity")
    axes[0].set_ylabel("Number of Crashes")
    for i, v in enumerate(counts.values):
        axes[0].text(i, v + 1000, f"{v:,}", ha="center", fontsize=10)

    # Proportion
    props = counts / counts.sum() * 100
    axes[1].bar(order, props.values, color=colors)
    axes[1].set_title("Crash Severity Distribution (%)")
    axes[1].set_ylabel("Percentage")
    for i, v in enumerate(props.values):
        axes[1].text(i, v + 0.5, f"{v:.1f}%", ha="center", fontsize=10)

    plt.tight_layout()
    return fig


def plot_yearly_trends(df: pd.DataFrame):
    """Line chart of crash trends over time by severity."""
    set_style()
    yearly = df.groupby(["crashYear", "crashSeverity"]).size().unstack(fill_value=0)

    order = ["Non-Injury", "Minor", "Serious", "Fatal"]
    colors = {"Non-Injury": "#2ecc71", "Minor": "#f39c12",
              "Serious": "#e74c3c", "Fatal": "#8e44ad"}

    fig, axes = plt.subplots(2, 1, figsize=(14, 10))

    # All severities
    for sev in order:
        if sev in yearly.columns:
            axes[0].plot(yearly.index, yearly[sev], label=sev,
                        color=colors[sev], linewidth=2)
    axes[0].set_title("Annual Crash Count by Severity")
    axes[0].set_xlabel("Year")
    axes[0].set_ylabel("Number of Crashes")
    axes[0].legend()

    # Fatal + Serious (the ones that matter most)
    for sev in ["Serious", "Fatal"]:
        if sev in yearly.columns:
            axes[1].plot(yearly.index, yearly[sev], label=sev,
                        color=colors[sev], linewidth=2, marker="o", markersize=4)
    axes[1].set_title("Fatal & Serious Crashes Over Time")
    axes[1].set_xlabel("Year")
    axes[1].set_ylabel("Number of Crashes")
    axes[1].legend()

    plt.tight_layout()
    return fig


def plot_weather_severity(df: pd.DataFrame):
    """Heatmap: weather condition vs crash severity proportion."""
    set_style()
    ct = pd.crosstab(df["weatherA"], df["crashSeverity"], normalize="index") * 100
    order = ["Non-Injury", "Minor", "Serious", "Fatal"]
    ct = ct.reindex(columns=[c for c in order if c in ct.columns])

    # Sort by fatal+serious proportion
    if "Fatal" in ct.columns and "Serious" in ct.columns:
        ct["_sort"] = ct["Fatal"] + ct["Serious"]
        ct = ct.sort_values("_sort", ascending=True).drop(columns="_sort")

    fig, ax = plt.subplots(figsize=(10, 6))
    sns.heatmap(ct, annot=True, fmt=".1f", cmap="YlOrRd", ax=ax)
    ax.set_title("Crash Severity by Weather Condition (%)")
    ax.set_ylabel("Weather")
    ax.set_xlabel("Severity")
    plt.tight_layout()
    return fig


def plot_speed_severity(df: pd.DataFrame):
    """Box/violin plot: speed limit vs severity."""
    set_style()
    order = ["Non-Injury", "Minor", "Serious", "Fatal"]

    fig, ax = plt.subplots(figsize=(10, 6))
    data = df[df["speedLimit"].notna() & df["crashSeverity"].isin(order)]
    sns.violinplot(
        data=data, x="crashSeverity", y="speedLimit",
        order=order, palette=["#2ecc71", "#f39c12", "#e74c3c", "#8e44ad"],
        ax=ax, inner="quartile",
    )
    ax.set_title("Speed Limit Distribution by Crash Severity")
    ax.set_xlabel("Crash Severity")
    ax.set_ylabel("Speed Limit (km/h)")

    # Add median annotations
    medians = data.groupby("crashSeverity")["speedLimit"].median().reindex(order)
    for i, (sev, med) in enumerate(medians.items()):
        ax.text(i, med + 2, f"median: {med:.0f}", ha="center",
                fontsize=9, fontweight="bold")

    plt.tight_layout()
    return fig


def plot_feature_importance(
    model,
    feature_names: list[str],
    top_n: int = 25,
    title: str = "Feature Importance",
):
    """Plot top N feature importances from a tree model."""
    set_style()
    importances = model.feature_importances_
    indices = np.argsort(importances)[-top_n:]

    fig, ax = plt.subplots(figsize=(10, max(6, top_n * 0.3)))
    ax.barh(
        range(len(indices)),
        importances[indices],
        color="#3498db",
    )
    ax.set_yticks(range(len(indices)))
    ax.set_yticklabels([feature_names[i] for i in indices])
    ax.set_title(title)
    ax.set_xlabel("Importance")
    plt.tight_layout()
    return fig


def plot_risk_by_hour_proxy(df: pd.DataFrame):
    """
    Since we don't have exact time, use light condition as a proxy
    to show risk patterns by time-of-day proxy.
    """
    set_style()
    light_order = ["Bright sun", "Overcast", "Twilight", "Dark"]
    severity_order = ["Non-Injury", "Minor", "Serious", "Fatal"]

    ct = pd.crosstab(df["light"], df["crashSeverity"], normalize="index") * 100
    ct = ct.reindex(index=[l for l in light_order if l in ct.index])
    ct = ct.reindex(columns=[s for s in severity_order if s in ct.columns])

    fig, ax = plt.subplots(figsize=(10, 6))
    ct.plot(kind="bar", stacked=True, ax=ax,
            color=["#2ecc71", "#f39c12", "#e74c3c", "#8e44ad"])
    ax.set_title("Crash Severity Distribution by Light Condition")
    ax.set_xlabel("Light Condition (Time-of-Day Proxy)")
    ax.set_ylabel("Percentage")
    ax.set_xticklabels(ax.get_xticklabels(), rotation=0)
    ax.legend(title="Severity")
    plt.tight_layout()
    return fig
