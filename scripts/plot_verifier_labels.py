"""Render the frozen paired contrasts; requires optional matplotlib only."""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    root = Path(__file__).resolve().parents[1] / "docs/results/scifact-label-ablation-v1"
    data = json.loads((root / "analysis.json").read_text(encoding="utf-8"))
    if not data["complete"]:
        raise ValueError("Intervals require all six planned trainings")
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "svg.fonttype": "none"})
    fig, ax = plt.subplots(figsize=(12, 6))
    fig.subplots_adjust(left=.26, right=.83, top=.70, bottom=.29)
    rows = [("original", "macro_f1", "Original labels / Macro F1", "#176B87"),
            ("published", "macro_f1", "Proposal labels / Macro F1", "#176B87"),
            ("original", "false_support_rate", "Original labels / False support", "#80529C"),
            ("published", "false_support_rate", "Proposal labels / False support", "#80529C")]
    for position, (view, metric, label, color) in enumerate(rows):
        result = data["paired_contrasts"][view]
        point = 100 * result["delta"][metric]
        interval = result["intervals"][metric]
        outer = [100 * x for x in interval["simultaneous_ci9875"]]
        inner = [100 * x for x in interval["ci95"]]
        ax.plot(outer, [position, position], color=color, alpha=.4, linewidth=3, solid_capstyle="round")
        ax.plot(inner, [position, position], color=color, linewidth=6, solid_capstyle="round")
        ax.scatter(point, position, color=color, edgecolor="white", linewidth=1.2, s=80, zorder=4)
        ax.text(1.035, position, f"{point:+.2f} pp", transform=ax.get_yaxis_transform(), va="center", color=color, weight="bold")
    ax.axvline(0, color="#596675", linewidth=1, linestyle="--")
    ax.set_yticks(range(4), [r[2] for r in rows])
    ax.set_ylim(3.6, -.6)
    ax.set_xlabel("Proposal-label training minus raw-label training (percentage points)", labelpad=12)
    ax.grid(axis="x", color="#E2E7EC", zorder=0)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0, pad=12)
    fig.suptitle("Seven label proposals: paired training effect", x=.04, y=.96, ha="left", fontsize=20, weight="bold")
    fig.text(.04, .85, "Macro F1: higher is better. False support: lower is better.\nThick bars: 95% intervals. Thin bars: Bonferroni-adjusted 98.75% intervals.", color="#465361", linespacing=1.5, va="top")
    fig.text(.04, .105, "128 claim–document pairs / 103 connected groups / 3 training seeds / 5,000 paired group resamples\nObserved development data. Proposal labels have not received independent human adjudication.", color="#465361", fontsize=10, linespacing=1.6, va="top")
    fig.savefig(root / "paired-effects.png", dpi=180, facecolor="white")
    fig.savefig(root / "paired-effects.svg", facecolor="white")
    plt.close(fig)
    svg = root / "paired-effects.svg"
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text(encoding="utf-8").splitlines()) + "\n",
                   encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
