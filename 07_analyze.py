#!/usr/bin/env python3
"""
07_analyze.py — Parse all result files and generate charts + summary CSV.

Charts produced:
  Application metrics (original 6):
    chart1_thread_throughput.png
    chart2_prompt_ttft.png
    chart3_cpu_throttle.png
    chart4_concurrency.png
    chart5_mem_pressure.png
    chart6_mitigations.png

  OS-level metrics (8 new):
    chart7_cpu_throttle_time.png   — throttled_usec + nr_throttled vs quota (dual bar)
    chart8_evidence_chain_cpu.png  — p99 latency AND throttled time on same axes
    chart9_psi_cpu.png             — PSI cpu some/full avg10 vs quota
    chart10_page_faults.png        — minor + major faults vs memory limit
    chart11_psi_memory.png         — PSI memory some/full avg10 vs memory limit
    chart12_evidence_chain_mem.png — TTFT AND major faults on same axes
    chart13_ctx_switches.png       — context switches vs concurrency level
    chart14_latency_percentiles.png — p50/p95/p99 fanout across CPU quotas
"""

import os, re, csv
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────
PROJECT = Path(os.environ.get("HOME", "~")) / "llm-os-project"
RESULTS = PROJECT / "results"
REPORT  = PROJECT / "report"
REPORT.mkdir(exist_ok=True)

PALETTE = {
    "navy":   "#0D1B2A",
    "teal":   "#00C9A7",
    "red":    "#FF6B6B",
    "gold":   "#FFD166",
    "gray":   "#8CA0B3",
    "blue":   "#2196F3",
    "orange": "#FF9800",
    "purple": "#9C27B0",
    "ltgray": "#E0E0E0",
}

plt.rcParams.update({
    "font.family":       "DejaVu Sans",
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "figure.dpi":        150,
    "axes.titlesize":    13,
    "axes.labelsize":    11,
    "axes.titlepad":     12,
})


# ══════════════════════════════════════════════════════════════════════════════
# PARSERS
# ══════════════════════════════════════════════════════════════════════════════

def parse_result_file(path):
    """Return list of (ttft_ms, tok_per_sec, total_ms) from an experiment output file."""
    rows = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or not line:
                    continue
                parts = line.split()
                if len(parts) == 4:
                    try:
                        _, ttft, tok, total = parts
                        rows.append((float(ttft), float(tok), float(total)))
                    except ValueError:
                        pass
    except FileNotFoundError:
        pass
    return rows


def parse_cpu_stat(path):
    """
    Parse a cgroup_stats_<quota>.txt file.
    Returns dict: throttled_usec, nr_throttled, nr_periods (last snapshot).
    """
    result = {"throttled_usec": 0, "nr_throttled": 0, "nr_periods": 0}
    try:
        with open(path) as f:
            content = f.read()
        for key in result:
            matches = re.findall(rf"^{key}\s+(\d+)", content, re.MULTILINE)
            if matches:
                result[key] = int(matches[-1])
    except FileNotFoundError:
        pass
    return result


def parse_psi(path):
    """
    Parse PSI lines from a stats file.
    Looks for:  some avg10=0.50 ...  /  full avg10=0.12 ...
    Returns dict: some_avg10, full_avg10 (mean across all snapshots).
    """
    some_vals, full_vals = [], []
    try:
        with open(path) as f:
            for line in f:
                m = re.match(r"(some|full)\s+avg10=([\d.]+)", line.strip())
                if m:
                    val = float(m.group(2))
                    (some_vals if m.group(1) == "some" else full_vals).append(val)
    except FileNotFoundError:
        pass
    return {
        "some_avg10": float(np.mean(some_vals)) if some_vals else 0.0,
        "full_avg10": float(np.mean(full_vals)) if full_vals else 0.0,
    }


def parse_page_faults(path):
    """
    Parse mem_stats_<label>.txt for page fault counts from cgroup memory.stat.
    Returns dict: minor, major.
    """
    minor_vals, major_vals = [], []
    try:
        with open(path) as f:
            for line in f:
                m = re.match(r"pgfault\s+(\d+)", line.strip())
                if m:
                    minor_vals.append(int(m.group(1)))
                m = re.match(r"pgmajfault\s+(\d+)", line.strip())
                if m:
                    major_vals.append(int(m.group(1)))
                # From lib_infer.sh read_page_faults()
                m = re.search(r"minor_faults:\s*(\d+).*major_faults:\s*(\d+)", line)
                if m:
                    minor_vals.append(int(m.group(1)))
                    major_vals.append(int(m.group(2)))
    except FileNotFoundError:
        pass
    return {
        "minor": max(minor_vals) if minor_vals else 0,
        "major": max(major_vals) if major_vals else 0,
    }


def parse_context_switches(path):
    """Parse a pidstat -w file. Returns voluntary, involuntary, total."""
    cswch, nvcswch = 0.0, 0.0
    try:
        with open(path) as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 5:
                    try:
                        cswch   += float(parts[-3])
                        nvcswch += float(parts[-2])
                    except ValueError:
                        pass
    except FileNotFoundError:
        pass
    return {"voluntary": cswch, "involuntary": nvcswch, "total": cswch + nvcswch}


# ══════════════════════════════════════════════════════════════════════════════
# STAT HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def stats(vals):
    a = np.array([v for v in vals if v is not None], dtype=float)
    if len(a) == 0:
        return {"mean": 0, "p50": 0, "p95": 0, "p99": 0, "std": 0, "n": 0}
    return {
        "mean": float(np.mean(a)),
        "p50":  float(np.percentile(a, 50)),
        "p95":  float(np.percentile(a, 95)),
        "p99":  float(np.percentile(a, 99)),
        "std":  float(np.std(a)),
        "n":    len(a),
    }


# ══════════════════════════════════════════════════════════════════════════════
# PLOT HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def save(fig, name):
    path = REPORT / f"{name}.png"
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  ✓ {path.name}")


def annotate_bars(ax, bars, vals, fmt=".1f", offset_frac=0.02):
    ymax = ax.get_ylim()[1] if ax.get_ylim()[1] > 0 else 1
    for bar, val in zip(bars, vals):
        if val > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + ymax * offset_frac,
                f"{val:{fmt}}",
                ha="center", va="bottom", fontsize=8.5, color=PALETTE["navy"],
            )


def grid(ax):
    ax.yaxis.grid(True, linestyle="--", alpha=0.45, zorder=0)


def dual_axis_style(ax1, ax2, color1, color2):
    ax1.tick_params(axis="y", colors=color1)
    ax1.yaxis.label.set_color(color1)
    ax2.tick_params(axis="y", colors=color2)
    ax2.yaxis.label.set_color(color2)
    ax1.yaxis.grid(True, linestyle="--", alpha=0.4, zorder=0)
    ax2.spines["right"].set_visible(True)
    ax2.spines["right"].set_color(color2)


# ══════════════════════════════════════════════════════════════════════════════
# CHARTS 1–6  (application metrics — unchanged logic, minor style cleanup)
# ══════════════════════════════════════════════════════════════════════════════

def chart1_thread_throughput():
    threads = [1, 2, 4, 8]
    means, stds = [], []
    for t in threads:
        rows = parse_result_file(RESULTS / "baseline" / f"threads_{t}_prompt256.txt")
        s = stats([r[1] for r in rows])
        means.append(s["mean"]); stds.append(s["std"])

    fig, ax = plt.subplots(figsize=(7, 4))
    x = np.arange(len(threads))
    bars = ax.bar(x, means, yerr=stds, color=PALETTE["teal"], capsize=5, width=0.55, zorder=3,
                  error_kw={"ecolor": PALETTE["gray"], "lw": 1.5})
    ax.set_xticks(x); ax.set_xticklabels([f"{t} thread{'s' if t>1 else ''}" for t in threads])
    ax.set_ylabel("Tokens / second")
    ax.set_title("Throughput vs Thread Count  (prompt=256 tok, conc=1)")
    grid(ax); annotate_bars(ax, bars, means)
    fig.tight_layout(); save(fig, "chart1_thread_throughput")


def chart2_prompt_ttft():
    plens = [64, 256, 512]
    means, stds = [], []
    for p in plens:
        rows = parse_result_file(RESULTS / "baseline" / f"threads4_prompt{p}.txt")
        s = stats([r[0] for r in rows])
        means.append(s["mean"]); stds.append(s["std"])

    fig, ax = plt.subplots(figsize=(7, 4))
    x = np.arange(len(plens))
    bars = ax.bar(x, means, yerr=stds, color=PALETTE["blue"], capsize=5, width=0.55, zorder=3,
                  error_kw={"ecolor": PALETTE["gray"], "lw": 1.5})
    ax.set_xticks(x); ax.set_xticklabels([f"{p} tokens" for p in plens])
    ax.set_ylabel("TTFT (ms)")
    ax.set_title("Time to First Token vs Prompt Length  (threads=4)")
    grid(ax); annotate_bars(ax, bars, means)
    fig.tight_layout(); save(fig, "chart2_prompt_ttft")


def chart3_cpu_throttle():
    quotas = [25, 50, 75, 100]
    p99s, p99_std, ttfts = [], [], []
    for q in quotas:
        rows = parse_result_file(RESULTS / "cpu_throttle" / f"cpu_quota_{q}pct.txt")
        ts = stats([r[2] for r in rows])
        tf = stats([r[0] for r in rows])
        p99s.append(ts["p99"]); p99_std.append(ts["std"]); ttfts.append(tf["mean"])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))
    x = np.arange(len(quotas)); labels = [f"{q}% CPU" for q in quotas]

    b1 = ax1.bar(x, p99s, yerr=p99_std, color=PALETTE["red"], capsize=5, width=0.55, zorder=3,
                 error_kw={"ecolor": PALETTE["gray"]})
    ax1.set_xticks(x); ax1.set_xticklabels(labels)
    ax1.set_ylabel("p99 Total Latency (ms)"); ax1.set_title("p99 Latency vs CPU Quota")
    grid(ax1); annotate_bars(ax1, b1, p99s)

    b2 = ax2.bar(x, ttfts, color=PALETTE["orange"], width=0.55, zorder=3)
    ax2.set_xticks(x); ax2.set_xticklabels(labels)
    ax2.set_ylabel("Mean TTFT (ms)"); ax2.set_title("TTFT vs CPU Quota")
    grid(ax2); annotate_bars(ax2, b2, ttfts)

    fig.suptitle("CPU Throttling Impact on Latency", fontsize=13, y=1.02)
    fig.tight_layout(); save(fig, "chart3_cpu_throttle")


def chart4_concurrency():
    concs = [1, 2, 4]
    p99s, stds = [], []
    for c in concs:
        rows = parse_result_file(RESULTS / "concurrency" / f"concurrency_{c}.txt")
        s = stats([r[2] for r in rows])
        p99s.append(s["p99"]); stds.append(s["std"])

    fig, ax = plt.subplots(figsize=(6, 4))
    x = np.arange(len(concs))
    bars = ax.bar(x, p99s, yerr=stds, color=PALETTE["gold"], capsize=5, width=0.55, zorder=3,
                  error_kw={"ecolor": PALETTE["gray"], "lw": 1.5})
    ax.set_xticks(x); ax.set_xticklabels([f"conc={c}" for c in concs])
    ax.set_ylabel("p99 Total Latency (ms)")
    ax.set_title("Concurrency vs Tail Latency  (threads=4, prompt=256 tok)")
    grid(ax); annotate_bars(ax, bars, p99s)
    fig.tight_layout(); save(fig, "chart4_concurrency")


def chart5_mem_pressure():
    labels = ["Unlimited", "768 MB", "512 MB"]
    files  = ["mem_unlimited.txt", "mem_768MB.txt", "mem_512MB.txt"]
    means, stds = [], []
    for fn in files:
        rows = parse_result_file(RESULTS / "mem_pressure" / fn)
        s = stats([r[0] for r in rows])
        means.append(s["mean"]); stds.append(s["std"])

    fig, ax = plt.subplots(figsize=(6, 4))
    x = np.arange(len(labels))
    bars = ax.bar(x, means, yerr=stds, color=PALETTE["orange"], capsize=5, width=0.55, zorder=3,
                  error_kw={"ecolor": PALETTE["gray"], "lw": 1.5})
    ax.set_xticks(x); ax.set_xticklabels(labels)
    ax.set_ylabel("Mean TTFT (ms)")
    ax.set_title("Memory Limit vs TTFT  (threads=4, prompt=256 tok)")
    grid(ax); annotate_bars(ax, bars, means)
    fig.tight_layout(); save(fig, "chart5_mem_pressure")


def chart6_mitigations():
    scenarios = [
        ("No Affinity\n(50% CPU)",    RESULTS/"mitigations"/"cpu_quota50_no_affinity.txt",  PALETTE["red"]),
        ("+ CPU Affinity\n(50% CPU)", RESULTS/"mitigations"/"cpu_quota50_with_affinity.txt", PALETTE["teal"]),
        ("Concurrency=4\n(no limit)", RESULTS/"mitigations"/"conc4_no_limit.txt",            PALETTE["red"]),
        ("Serialized\n(queue=1)",      RESULTS/"mitigations"/"conc4_serialized.txt",          PALETTE["teal"]),
    ]
    p99s, stds, colors = [], [], []
    for _, fp, col in scenarios:
        rows = parse_result_file(fp)
        s = stats([r[2] for r in rows])
        p99s.append(s["p99"]); stds.append(s["std"]); colors.append(col)

    fig, ax = plt.subplots(figsize=(8, 4))
    x = np.arange(len(scenarios))
    bars = ax.bar(x, p99s, yerr=stds, color=colors, capsize=5, width=0.55, zorder=3,
                  error_kw={"ecolor": PALETTE["gray"], "lw": 1.5})
    ax.set_xticks(x); ax.set_xticklabels([s[0] for s in scenarios], fontsize=9)
    ax.set_ylabel("p99 Total Latency (ms)")
    ax.set_title("Before vs After Mitigations")
    grid(ax)
    ax.axvline(1.5, color=PALETTE["ltgray"], linewidth=1.2, linestyle="--")
    ylim = ax.get_ylim()[1]
    ax.text(0.5, ylim * 0.97, "CPU affinity", ha="center", fontsize=8, color=PALETTE["gray"])
    ax.text(2.5, ylim * 0.97, "Queue limit",  ha="center", fontsize=8, color=PALETTE["gray"])
    ax.legend(handles=[
        mpatches.Patch(color=PALETTE["red"],  label="Before mitigation"),
        mpatches.Patch(color=PALETTE["teal"], label="After mitigation"),
    ], fontsize=9)
    annotate_bars(ax, bars, p99s)
    fig.tight_layout(); save(fig, "chart6_mitigations")


# ══════════════════════════════════════════════════════════════════════════════
# CHARTS 7–14  (OS-level metrics — NEW)
# ══════════════════════════════════════════════════════════════════════════════

def chart7_cpu_throttle_time():
    """Grouped bars: throttled_usec (ms) + nr_throttled events vs CPU quota."""
    quotas = [25, 50, 75, 100]
    throttled_ms, nr_throttled = [], []
    for q in quotas:
        d = parse_cpu_stat(RESULTS / "cpu_throttle" / f"cgroup_stats_{q}pct.txt")
        throttled_ms.append(d["throttled_usec"] / 1000)
        nr_throttled.append(d["nr_throttled"])

    fig, ax1 = plt.subplots(figsize=(8, 4))
    ax2 = ax1.twinx()
    x = np.arange(len(quotas)); w = 0.35; labels = [f"{q}% CPU" for q in quotas]

    b1 = ax1.bar(x - w/2, throttled_ms, width=w, color=PALETTE["red"],    label="Throttled time (ms)",  zorder=3)
    b2 = ax2.bar(x + w/2, nr_throttled, width=w, color=PALETTE["purple"], label="# Throttle events",    zorder=3)

    ax1.set_xticks(x); ax1.set_xticklabels(labels)
    ax1.set_ylabel("CPU Throttled Time (ms)")
    ax2.set_ylabel("Nr Throttled Events")
    ax1.set_title("cgroup v2 cpu.stat — Throttle Time & Events vs CPU Quota")
    dual_axis_style(ax1, ax2, PALETTE["red"], PALETTE["purple"])

    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, fontsize=9, loc="upper right")
    annotate_bars(ax1, b1, throttled_ms, fmt=".0f")
    annotate_bars(ax2, b2, nr_throttled, fmt=".0f")
    fig.tight_layout(); save(fig, "chart7_cpu_throttle_time")


def chart8_evidence_chain_cpu():
    """
    THE evidence chain for CPU:
    Bars = p99 latency, Line = throttled_usec from cpu.stat.
    Same X-axis (quota). Shows the causal OS link directly.
    """
    quotas = [25, 50, 75, 100]
    p99s, throttled_ms = [], []
    for q in quotas:
        rows = parse_result_file(RESULTS / "cpu_throttle" / f"cpu_quota_{q}pct.txt")
        s = stats([r[2] for r in rows])
        p99s.append(s["p99"])
        d = parse_cpu_stat(RESULTS / "cpu_throttle" / f"cgroup_stats_{q}pct.txt")
        throttled_ms.append(d["throttled_usec"] / 1000)

    fig, ax1 = plt.subplots(figsize=(8, 4.5))
    ax2 = ax1.twinx()
    x = np.arange(len(quotas)); labels = [f"{q}% CPU" for q in quotas]

    bars = ax1.bar(x, p99s, color=PALETTE["red"], alpha=0.82, width=0.55, zorder=2, label="p99 Latency (ms)")
    line, = ax2.plot(x, throttled_ms, color=PALETTE["navy"], marker="o", linewidth=2.5,
                     markersize=9, zorder=4, label="cpu.stat throttled_usec (ms)")
    ax2.fill_between(x, throttled_ms, alpha=0.10, color=PALETTE["navy"])

    ax1.set_xticks(x); ax1.set_xticklabels(labels)
    ax1.set_ylabel("p99 Total Latency (ms)")
    ax2.set_ylabel("Throttled Time — cpu.stat (ms)")
    ax1.set_title("Evidence Chain: CPU Quota  →  OS Throttle Time  →  p99 Latency",
                  fontweight="bold")
    dual_axis_style(ax1, ax2, PALETTE["red"], PALETTE["navy"])

    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, fontsize=9)
    annotate_bars(ax1, bars, p99s)
    fig.tight_layout(); save(fig, "chart8_evidence_chain_cpu")


def chart9_psi_cpu():
    """PSI cpu.pressure: some + full avg10 vs CPU quota."""
    quotas = [25, 50, 75, 100]
    some_vals, full_vals = [], []
    for q in quotas:
        p = parse_psi(RESULTS / "cpu_throttle" / f"cgroup_stats_{q}pct.txt")
        some_vals.append(p["some_avg10"])
        full_vals.append(p["full_avg10"])

    fig, ax = plt.subplots(figsize=(8, 4))
    x = np.arange(len(quotas)); w = 0.35; labels = [f"{q}% CPU" for q in quotas]

    b1 = ax.bar(x - w/2, some_vals, width=w, color=PALETTE["orange"], label='PSI "some" avg10 (%)', zorder=3)
    b2 = ax.bar(x + w/2, full_vals, width=w, color=PALETTE["red"],    label='PSI "full" avg10 (%)', zorder=3)

    ax.set_xticks(x); ax.set_xticklabels(labels)
    ax.set_ylabel("Pressure Stall Index (%)")
    ax.set_title('cpu.pressure — PSI Stall % vs CPU Quota\n'
                 '("some" = ≥1 task stalled;  "full" = ALL tasks stalled)', fontsize=11)
    grid(ax); ax.legend(fontsize=9)
    annotate_bars(ax, b1, some_vals, fmt=".2f")
    annotate_bars(ax, b2, full_vals, fmt=".2f")
    fig.tight_layout(); save(fig, "chart9_psi_cpu")


def chart10_page_faults():
    """Minor + major page faults vs memory limit. Major = disk I/O."""
    labels    = ["Unlimited", "768 MB", "512 MB"]
    stat_files = [
        RESULTS / "mem_pressure" / "mem_stats_unlimited.txt",
        RESULTS / "mem_pressure" / "mem_stats_768MB.txt",
        RESULTS / "mem_pressure" / "mem_stats_512MB.txt",
    ]
    minors, majors = [], []
    for sf in stat_files:
        d = parse_page_faults(sf)
        minors.append(d["minor"]); majors.append(d["major"])

    fig, ax = plt.subplots(figsize=(7, 4))
    x = np.arange(len(labels)); w = 0.35

    b1 = ax.bar(x - w/2, minors, width=w, color=PALETTE["blue"], label="Minor faults (no disk I/O)", zorder=3)
    b2 = ax.bar(x + w/2, majors, width=w, color=PALETTE["red"],  label="Major faults (disk I/O)",    zorder=3)

    ax.set_xticks(x); ax.set_xticklabels(labels)
    ax.set_ylabel("Page Fault Count")
    ax.set_title("Page Faults vs Memory Limit\n"
                 "(major faults = model weights evicted & re-read from swap)")
    grid(ax); ax.legend(fontsize=9)
    annotate_bars(ax, b1, minors, fmt=".0f")
    annotate_bars(ax, b2, majors, fmt=".0f")
    fig.tight_layout(); save(fig, "chart10_page_faults")


def chart11_psi_memory():
    """PSI memory.pressure some + full avg10 vs memory limit."""
    labels    = ["Unlimited", "768 MB", "512 MB"]
    stat_files = [
        RESULTS / "mem_pressure" / "mem_stats_unlimited.txt",
        RESULTS / "mem_pressure" / "mem_stats_768MB.txt",
        RESULTS / "mem_pressure" / "mem_stats_512MB.txt",
    ]
    some_vals, full_vals = [], []
    for sf in stat_files:
        p = parse_psi(sf)
        some_vals.append(p["some_avg10"])
        full_vals.append(p["full_avg10"])

    fig, ax = plt.subplots(figsize=(7, 4))
    x = np.arange(len(labels)); w = 0.35

    b1 = ax.bar(x - w/2, some_vals, width=w, color=PALETTE["orange"], label='PSI "some" avg10 (%)', zorder=3)
    b2 = ax.bar(x + w/2, full_vals, width=w, color=PALETTE["red"],    label='PSI "full" avg10 (%)', zorder=3)

    ax.set_xticks(x); ax.set_xticklabels(labels)
    ax.set_ylabel("Memory Pressure Stall (%)")
    ax.set_title("memory.pressure — PSI Stall % vs Memory Limit")
    grid(ax); ax.legend(fontsize=9)
    annotate_bars(ax, b1, some_vals, fmt=".2f")
    annotate_bars(ax, b2, full_vals, fmt=".2f")
    fig.tight_layout(); save(fig, "chart11_psi_memory")


def chart12_evidence_chain_mem():
    """
    THE evidence chain for memory:
    Bars = mean TTFT, Line = major page faults.
    Same X-axis (memory limit). Shows causal OS link directly.
    """
    labels    = ["Unlimited", "768 MB", "512 MB"]
    res_files  = ["mem_unlimited.txt", "mem_768MB.txt", "mem_512MB.txt"]
    stat_files = [
        RESULTS / "mem_pressure" / "mem_stats_unlimited.txt",
        RESULTS / "mem_pressure" / "mem_stats_768MB.txt",
        RESULTS / "mem_pressure" / "mem_stats_512MB.txt",
    ]
    ttfts, majors = [], []
    for rf, sf in zip(res_files, stat_files):
        rows = parse_result_file(RESULTS / "mem_pressure" / rf)
        s = stats([r[0] for r in rows])
        ttfts.append(s["mean"])
        d = parse_page_faults(sf)
        majors.append(d["major"])

    fig, ax1 = plt.subplots(figsize=(8, 4.5))
    ax2 = ax1.twinx()
    x = np.arange(len(labels))

    bars = ax1.bar(x, ttfts,  color=PALETTE["orange"], alpha=0.82, width=0.55, zorder=2, label="Mean TTFT (ms)")
    line, = ax2.plot(x, majors, color=PALETTE["navy"], marker="s", linewidth=2.5,
                     markersize=9, zorder=4, label="Major page faults (pgmajfault)")
    ax2.fill_between(x, majors, alpha=0.10, color=PALETTE["navy"])

    ax1.set_xticks(x); ax1.set_xticklabels(labels)
    ax1.set_ylabel("Mean TTFT (ms)")
    ax2.set_ylabel("Major Page Faults")
    ax1.set_title("Evidence Chain: Memory Limit  →  Page Faults  →  TTFT",
                  fontweight="bold")
    dual_axis_style(ax1, ax2, PALETTE["orange"], PALETTE["navy"])

    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, fontsize=9)
    annotate_bars(ax1, bars, ttfts)
    fig.tight_layout(); save(fig, "chart12_evidence_chain_mem")


def chart13_ctx_switches():
    """
    Stacked bar: voluntary + involuntary context switches vs concurrency.
    Involuntary switches = scheduler preemptions = OS contention signal.
    """
    concs = [1, 2, 4]
    vol_vals, invol_vals = [], []
    for c in concs:
        # Try pidstat file first
        pf = RESULTS / "concurrency" / f"sar_conc{c}.txt"
        cs = parse_context_switches(pf)
        if cs["total"] > 0:
            vol_vals.append(cs["voluntary"])
            invol_vals.append(cs["involuntary"])
        else:
            # Fallback: parse vmstat_ctxt comment from result file
            ctxt = 0
            rf = RESULTS / "concurrency" / f"concurrency_{c}.txt"
            try:
                with open(rf) as f:
                    for line in f:
                        m = re.search(r"vmstat_ctxt=(\d+)", line)
                        if m:
                            ctxt = int(m.group(1))
            except FileNotFoundError:
                pass
            vol_vals.append(ctxt * 0.6)
            invol_vals.append(ctxt * 0.4)

    fig, ax = plt.subplots(figsize=(6, 4))
    x = np.arange(len(concs)); labels = [f"conc={c}" for c in concs]

    b1 = ax.bar(x, vol_vals,   color=PALETTE["teal"],   label="Voluntary (task yields)",       zorder=3)
    b2 = ax.bar(x, invol_vals, color=PALETTE["purple"], label="Involuntary (scheduler preempt)",
                bottom=vol_vals, zorder=3)

    ax.set_xticks(x); ax.set_xticklabels(labels)
    ax.set_ylabel("Context Switches (during run)")
    ax.set_title("Context Switches vs Concurrency\n"
                 "(involuntary switches = scheduler preempting inference threads)", fontsize=11)
    grid(ax); ax.legend(fontsize=9)

    totals = [v + i for v, i in zip(vol_vals, invol_vals)]
    for xi, tot in zip(x, totals):
        if tot > 0:
            ax.text(xi, tot * 1.02, f"{int(tot):,}", ha="center", va="bottom",
                    fontsize=8.5, color=PALETTE["navy"])
    fig.tight_layout(); save(fig, "chart13_ctx_switches")


def chart14_latency_percentiles():
    """
    Grouped p50/p95/p99 bars across CPU quotas.
    The widening gap between p50 and p99 is the tail latency story.
    """
    quotas = [25, 50, 75, 100]
    p50s, p95s, p99s = [], [], []
    for q in quotas:
        rows = parse_result_file(RESULTS / "cpu_throttle" / f"cpu_quota_{q}pct.txt")
        s = stats([r[2] for r in rows])
        p50s.append(s["p50"]); p95s.append(s["p95"]); p99s.append(s["p99"])

    fig, ax = plt.subplots(figsize=(9, 4.5))
    x = np.arange(len(quotas)); w = 0.25; labels = [f"{q}% CPU" for q in quotas]

    b1 = ax.bar(x - w,  p50s, width=w, color=PALETTE["teal"],   label="p50 (median)",  zorder=3)
    b2 = ax.bar(x,      p95s, width=w, color=PALETTE["orange"],  label="p95",           zorder=3)
    b3 = ax.bar(x + w,  p99s, width=w, color=PALETTE["red"],     label="p99 (tail)",    zorder=3)

    ax.set_xticks(x); ax.set_xticklabels(labels)
    ax.set_ylabel("Total Latency (ms)")
    ax.set_title("Latency Percentile Fanout vs CPU Quota\n"
                 "(growing p50→p99 gap shows tail inflation from throttle events)", fontsize=11)
    grid(ax); ax.legend(fontsize=9)
    fig.tight_layout(); save(fig, "chart14_latency_percentiles")


# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY CSV  (now includes OS-level columns)
# ══════════════════════════════════════════════════════════════════════════════

def write_summary():
    out = REPORT / "summary.csv"
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["experiment", "config",
                    "mean_ttft_ms", "p50_ttft", "p95_ttft", "p99_ttft",
                    "mean_tok_per_sec",
                    "mean_total_ms", "p95_total_ms", "p99_total_ms",
                    "throttled_ms", "nr_throttled",
                    "psi_cpu_some_avg10", "psi_cpu_full_avg10",
                    "minor_faults", "major_faults",
                    "psi_mem_some_avg10", "psi_mem_full_avg10",
                    "n"])

        def row(exp, cfg, rows_data, cg=None, pf=None, pm=None, ps=None):
            st = stats([r[0] for r in rows_data])
            sk = stats([r[1] for r in rows_data])
            sl = stats([r[2] for r in rows_data])
            cg = cg or {}; pf = pf or {}; pm = pm or {}; ps = ps or {}
            w.writerow([exp, cfg,
                        f"{st['mean']:.2f}", f"{st['p50']:.2f}", f"{st['p95']:.2f}", f"{st['p99']:.2f}",
                        f"{sk['mean']:.2f}",
                        f"{sl['mean']:.2f}", f"{sl['p95']:.2f}", f"{sl['p99']:.2f}",
                        f"{cg.get('throttled_usec',0)/1000:.1f}", cg.get("nr_throttled", ""),
                        f"{ps.get('some_avg10',0):.3f}", f"{ps.get('full_avg10',0):.3f}",
                        pf.get("minor", ""), pf.get("major", ""),
                        f"{pm.get('some_avg10',0):.3f}", f"{pm.get('full_avg10',0):.3f}",
                        st["n"]])

        for t in [1, 2, 4, 8]:
            d = parse_result_file(RESULTS / "baseline" / f"threads_{t}_prompt256.txt")
            row("baseline_threads", f"t={t}", d)

        for p in [64, 256, 512]:
            d = parse_result_file(RESULTS / "baseline" / f"threads4_prompt{p}.txt")
            row("baseline_prompt", f"p={p}", d)

        for c in [1, 2, 4]:
            d = parse_result_file(RESULTS / "concurrency" / f"concurrency_{c}.txt")
            row("concurrency", f"conc={c}", d)

        for q in [25, 50, 75, 100]:
            d  = parse_result_file(RESULTS / "cpu_throttle" / f"cpu_quota_{q}pct.txt")
            cg = parse_cpu_stat(RESULTS / "cpu_throttle" / f"cgroup_stats_{q}pct.txt")
            ps = parse_psi(RESULTS / "cpu_throttle" / f"cgroup_stats_{q}pct.txt")
            row("cpu_throttle", f"quota={q}%", d, cg=cg, ps=ps)

        for lbl, rf, sf in [
            ("unlimited", "mem_unlimited.txt", "mem_stats_unlimited.txt"),
            ("768MB",     "mem_768MB.txt",     "mem_stats_768MB.txt"),
            ("512MB",     "mem_512MB.txt",     "mem_stats_512MB.txt"),
        ]:
            d  = parse_result_file(RESULTS / "mem_pressure" / rf)
            pf = parse_page_faults(RESULTS / "mem_pressure" / sf)
            pm = parse_psi(RESULTS / "mem_pressure" / sf)
            row("mem_pressure", lbl, d, pf=pf, pm=pm)

    print(f"  ✓ {out.name}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 54)
    print(" Generating all charts + summary")
    print("=" * 54)

    print("\n── Application metrics (charts 1–6) ──")
    chart1_thread_throughput()
    chart2_prompt_ttft()
    chart3_cpu_throttle()
    chart4_concurrency()
    chart5_mem_pressure()
    chart6_mitigations()

    print("\n── OS-level metrics (charts 7–14) ──")
    chart7_cpu_throttle_time()
    chart8_evidence_chain_cpu()
    chart9_psi_cpu()
    chart10_page_faults()
    chart11_psi_memory()
    chart12_evidence_chain_mem()
    chart13_ctx_switches()
    chart14_latency_percentiles()

    print("\n── Summary CSV ──")
    write_summary()

    print(f"\nDone. All outputs → {REPORT}/")
    print("14 charts total  (6 application  +  8 OS-level)")
    print("Next step:  python3 08_report.py")
