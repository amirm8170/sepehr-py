"""
Charts. Every function draws on a matplotlib Figure, so the same charts
are used by main.py (saved as PNG) and by app.py (shown in the window).

Pressures are shown in bar or psi depending on `unit` ("bar" or "psi").
"""

import numpy as np

from . import units as u

BLUE, ORANGE, GREEN, PURPLE, GREY, RED = "#2a78d6", "#eb6834", "#1baf7a", "#4a3aa7", "#8a8984", "#d64545"


def p_conv(unit):
    """Function that converts Pa to the chosen unit."""
    return u.bar if unit == "bar" else u.psi


def _style(ax):
    ax.grid(True, color="#e4e3df", linewidth=0.7)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def _legend_right(ax):
    """Legend outside the plot, on the right, so it never hides a curve."""
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=8, frameon=False)


def _limits(ax, pore, frac):
    """Pore and fracture pressure lines."""
    ax.axhline(pore, color=RED, ls="--", lw=1.2, label="Pore pressure")
    ax.axhline(frac, color=RED, ls=":", lw=1.4, label="Fracture pressure")


def _shade_unsafe(ax, ts):
    """Light red background where BHP is outside the pressure window."""
    bad = (ts["status"] != "safe").values
    if bad.any():
        ax.fill_between(ts["time_s"], 0, 1, where=bad, transform=ax.get_xaxis_transform(),
                        color=RED, alpha=0.10, lw=0, label="Outside window")


# ----------------------------------------------------------------------
# Steady state
# ----------------------------------------------------------------------
def pressure_profile(fig, result, case, unit="bar"):
    """Pressure vs MD and pressure vs TVD side by side."""
    conv = p_conv(unit)
    prof = result["profile"]
    ann, st = prof[prof["path"] == "annulus"], prof[prof["path"] == "string"]
    axes = fig.subplots(1, 2, sharex=True)
    for ax, depth, title in ((axes[0], "md", "Measured depth MD [m]"),
                             (axes[1], "tvd", "True vertical depth TVD [m]")):
        ax.plot(conv(ann["hydrostatic"]), ann[depth], color=GREY, ls="-.", lw=1.2,
                label="Hydrostatic only")
        ax.plot(conv(st["pressure"]), st[depth], color=BLUE, lw=2, label="Inside drill string")
        ax.plot(conv(ann["pressure"]), ann[depth], color=ORANGE, lw=2, label="Annulus")
        bottom = ann[depth].iloc[-1]
        ax.plot([conv(case.pore)], [bottom], "<", color=RED, ms=9, label="Pore / fracture at TD")
        ax.plot([conv(case.frac)], [bottom], ">", color=RED, ms=9)
        ax.invert_yaxis()
        ax.set_ylabel(title)
        ax.set_xlabel(f"Pressure [{unit}]")
        _style(ax)
    axes[0].set_title("Pressure vs MD", loc="left", fontweight="bold")
    axes[1].set_title("Pressure vs TVD", loc="left", fontweight="bold")
    axes[0].legend(loc="lower left", fontsize=8, frameon=False)


def spp_breakdown(fig, result, unit="bar"):
    """Horizontal bars: what the stand-pipe pressure is made of."""
    conv = p_conv(unit)
    names = ["Drill pipe friction", "BHA friction", "Bit nozzles", "Annulus friction",
             "Surface back pressure"]
    values = [conv(result[k]) for k in ("dp_drill_pipe", "dp_bha", "dp_bit", "dp_annulus", "sbp")]
    ax = fig.add_subplot(111)
    ax.barh(names[::-1], values[::-1], color=BLUE, height=0.6)
    for i, v in enumerate(values[::-1]):
        ax.text(v, i, f"  {v:,.1f}", va="center", fontsize=9)
    ax.set_xlim(0, max(values) * 1.25)
    ax.set_xlabel(f"Pressure [{unit}]")
    ax.set_title(f"SPP = {conv(result['spp']):,.1f} {unit}", loc="left", fontweight="bold")
    _style(ax)
    ax.grid(axis="y", visible=False)


def window_gauge(fig, result, case, unit="bar"):
    """Where BHP sits between pore and fracture pressure."""
    conv = p_conv(unit)
    pore, frac = conv(case.pore), conv(case.frac)
    pad = 0.35 * (frac - pore)
    lo, hi = min(pore - pad, conv(result["bhp_static"]) - 2), max(frac + pad, conv(result["bhp"]) + 2)
    ax = fig.add_subplot(111)
    ax.axvspan(lo, pore, color="#fbe9e9")
    ax.axvspan(pore, frac, color="#e6f4ec")
    ax.axvspan(frac, hi, color="#fbe9e9")
    for x, text in (((lo + pore) / 2, "Kick risk"), ((pore + frac) / 2, "Safe window"),
                    ((frac + hi) / 2, "Loss risk")):
        ax.text(x, 0.8, text, ha="center", fontsize=9, color="#55554f")
    for x, name, color, size in ((conv(result["bhp_static"]), "Static BHP", GREY, 60),
                                 (conv(case.target), "Target", ORANGE, 80),
                                 (conv(result["bhp"]), "Circulating BHP", BLUE, 140)):
        ax.scatter([x], [0.45], s=size, marker="D", color=color, edgecolor="white", zorder=5,
                   label=name)
    ax.set_xlim(lo, hi)
    ax.set_ylim(0, 1)
    ax.set_yticks([])
    ax.set_xlabel(f"Pressure at the bottom of the well [{unit}]")
    ax.legend(loc="lower right", bbox_to_anchor=(1, 1), ncol=3, fontsize=8, frameon=False)


def trajectory(fig, case):
    """Vertical section and plan view of the well path."""
    from .well import minimum_curvature
    s = case.survey
    _, north, east = minimum_curvature(s["md"].values, s["inc"].values, s["azi"].values)
    ax1, ax2 = fig.subplots(1, 2)
    ax1.plot(np.hypot(north, east), s["tvd"], "-o", ms=3, color=BLUE)
    ax1.invert_yaxis()
    ax1.set_xlabel("Horizontal departure [m]")
    ax1.set_ylabel("TVD [m]")
    ax1.set_title("Vertical section", loc="left", fontweight="bold")
    ax2.plot(east, north, "-o", ms=3, color=BLUE)
    ax2.set_xlabel("East [m]")
    ax2.set_ylabel("North [m]")
    ax2.set_title("Plan view", loc="left", fontweight="bold")
    for ax in (ax1, ax2):
        ax.set_aspect("equal", adjustable="datalim")
        _style(ax)


# ----------------------------------------------------------------------
# Dynamic results
# ----------------------------------------------------------------------
def dynamic_overview(fig, sim, unit="bar", compare=None):
    """Four stacked charts: BHP, SPP / SBP, flow rates, choke (or SBP set-point)."""
    conv = p_conv(unit)
    ts = sim["timeseries"]
    t = ts["time_s"]
    ax1, ax2, ax3, ax4 = fig.subplots(4, 1, sharex=True, height_ratios=[2.4, 1, 1, 1])

    _shade_unsafe(ax1, ts)
    ax1.plot(t, conv(ts["target"]), color=ORANGE, ls="--", lw=1.4, label="Target BHP")
    ax1.plot(t, conv(ts["bhp"]), color=BLUE, lw=2, label="BHP")
    if compare is not None:
        ax1.plot(compare["time_s"], conv(compare["bhp"]), color=GREY, lw=1.6, label="BHP without PID")
    _limits(ax1, conv(ts["pore"].iloc[0]), conv(ts["fracture"].iloc[0]))
    ax1.set_ylabel(f"BHP [{unit}]")
    ax1.set_title("Bottomhole pressure", loc="left", fontweight="bold")
    _legend_right(ax1)

    ax2.plot(t, conv(ts["spp"]), color=BLUE, label="SPP")
    ax2.plot(t, conv(ts["sbp"]), color=GREEN, label="SBP (choke pressure)")
    ax2.set_ylabel(f"[{unit}]")
    ax2.set_title("Stand-pipe and surface back pressure", loc="left", fontweight="bold")
    _legend_right(ax2)

    ax3.plot(t, u.gpm(ts["pump_rate"]), color=BLUE, label="Rig pump")
    ax3.plot(t, u.gpm(ts["bp_pump_rate"]), color=ORANGE, label="Back-pressure pump")
    ax3.plot(t, u.gpm(ts["choke_flow"]), color=PURPLE, ls=":", label="Flow through choke")
    ax3.set_ylabel("Flow [gpm]")
    ax3.set_title("Flow rates", loc="left", fontweight="bold")
    _legend_right(ax3)

    if sim["actuator"] == "choke":
        ax4.plot(t, ts["choke_opening"] * 100, color=BLUE)
        ax4.set_ylabel("Opening [%]")
        ax4.set_title("Choke position", loc="left", fontweight="bold")
    else:
        ax4.plot(t, conv(ts["sbp_command"]), color=BLUE)
        ax4.set_ylabel(f"[{unit}]")
        ax4.set_title("Back-pressure set-point", loc="left", fontweight="bold")
    ax4.set_xlabel("Time [s]")
    for ax in (ax1, ax2, ax3, ax4):
        _style(ax)


def ecd_chart(fig, sim, case):
    """Equivalent densities (ppg) versus time."""
    ts = sim["timeseries"]
    ax = fig.add_subplot(111)
    _shade_unsafe(ax, ts)
    ax.plot(ts["time_s"], u.ppg(ts["emw"]), color=BLUE, lw=2, label="EMW of BHP (with SBP)")
    ax.plot(ts["time_s"], u.ppg(ts["ecd"]), color=GREEN, label="ECD (mud weight + annular friction)")
    ax.axhline(u.ppg(case.rho), color=GREY, ls="-.", lw=1, label="Mud weight")
    _limits(ax, u.pressure_to_emw(case.pore, case.td_tvd), u.pressure_to_emw(case.frac, case.td_tvd))
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Equivalent density [ppg]")
    ax.set_title("ECD at the bottom of the well", loc="left", fontweight="bold")
    ax.legend(loc="best", fontsize=8, frameon=False)
    _style(ax)


def pid_chart(fig, sim, unit="bar"):
    """Control error and the P, I, D parts of the controller output."""
    conv = p_conv(unit)
    ts = sim["timeseries"]
    ax1, ax2 = fig.subplots(2, 1, sharex=True)
    band = conv(sim["metrics"]["tolerance"])
    ax1.axhspan(-band, band, color=GREEN, alpha=0.12, lw=0, label=f"+/-{band:.1f} {unit} band")
    ax1.plot(ts["time_s"], conv(ts["error"]), color=BLUE)
    ax1.set_ylabel(f"Error [{unit}]")
    ax1.set_title("Control error (target - BHP)", loc="left", fontweight="bold")
    ax1.legend(loc="best", fontsize=8, frameon=False)
    for col, name, color in (("pid_p", "P", BLUE), ("pid_i", "I", ORANGE), ("pid_d", "D", GREEN)):
        ax2.plot(ts["time_s"], ts[col], color=color, label=name)
    ax2.set_ylabel("Output part")
    ax2.set_xlabel("Time [s]")
    ax2.set_title("PID terms", loc="left", fontweight="bold")
    ax2.legend(loc="best", fontsize=8, ncol=3, frameon=False)
    for ax in (ax1, ax2):
        _style(ax)


def profiles_over_time(fig, sim, unit="bar"):
    """Annulus pressure above hydrostatic versus depth at several times."""
    conv = p_conv(unit)
    prof = sim["profiles"]
    ann = prof[prof["path"] == "annulus"]
    times = sorted(ann["time_s"].unique())
    if len(times) > 8:
        times = [times[i] for i in np.linspace(0, len(times) - 1, 8).astype(int)]
    ax = fig.add_subplot(111)
    for i, t in enumerate(times):
        p = ann[ann["time_s"] == t]
        ax.plot(conv(p["pressure"] - p["hydrostatic"]), p["tvd"], color=BLUE,
                alpha=0.3 + 0.7 * i / max(len(times) - 1, 1), label=f"t = {t:.0f} s")
    ax.invert_yaxis()
    ax.set_xlabel(f"Annulus pressure above hydrostatic [{unit}]")
    ax.set_ylabel("TVD [m]")
    ax.set_title("Annulus pressure profile over time", loc="left", fontweight="bold")
    ax.legend(loc="best", fontsize=8, frameon=False)
    _style(ax)


# ----------------------------------------------------------------------
# Analysis
# ----------------------------------------------------------------------
def tornado(fig, table, unit="bar"):
    """Change of BHP, SPP and ECD for -x % and +x % of each parameter."""
    conv = p_conv(unit)
    axes = fig.subplots(1, 3)
    for ax, (key, label, f) in zip(axes, (("bhp", f"BHP [{unit}]", conv),
                                          ("spp", f"SPP [{unit}]", conv),
                                          ("ecd", "ECD [ppg]", u.ppg))):
        t = table.reindex((table[f"{key}_high"] - table[f"{key}_low"]).abs().sort_values().index)
        y = np.arange(len(t))
        ax.barh(y, f(t[f"{key}_low"]), color=BLUE, height=0.6, label="- change")
        ax.barh(y, f(t[f"{key}_high"]), color=ORANGE, height=0.6, alpha=0.9, label="+ change")
        ax.set_yticks(y)
        ax.set_yticklabels(t["parameter"], fontsize=8)
        ax.axvline(0, color=GREY, lw=0.8)
        ax.set_xlabel(f"Change in {label}")
        ax.set_title(f"Change in {key.upper()}", loc="left", fontweight="bold")
        _style(ax)
    axes[0].legend(loc="lower right", fontsize=8, frameon=False)


def sweep_chart(fig, table, x_label, unit="bar"):
    """BHP, SPP and ECD for a range of values of one parameter."""
    conv = p_conv(unit)
    axes = fig.subplots(1, 3)
    for ax, (col, label, f) in zip(axes, (("bhp", f"BHP [{unit}]", conv),
                                          ("spp", f"SPP [{unit}]", conv),
                                          ("ecd", "ECD [ppg]", u.ppg))):
        ax.plot(table["x"], f(table[col]), "-o", ms=4, color=BLUE)
        ax.set_xlabel(x_label)
        ax.set_ylabel(label)
        _style(ax)


def validation_chart(fig, table):
    """Model vs reference (left) and percentage error (right)."""
    ax1, ax2 = fig.subplots(1, 2)
    t = table[table["unit"] == table["unit"].mode().iloc[0]]
    top = max(t["reference"].max(), t["model"].max()) * 1.1
    ax1.plot([0, top], [0, top], color=GREY, lw=1, label="Perfect agreement")
    ax1.plot([0, top], [0, top * 1.1], color=GREY, lw=0.8, ls=":", label="+/-10 %")
    ax1.plot([0, top], [0, top * 0.9], color=GREY, lw=0.8, ls=":")
    ax1.plot(t["reference"], t["model"], "o", ms=8, color=BLUE)
    for _, r in t.iterrows():
        ax1.annotate(r["quantity"].replace("_", " "), (r["reference"], r["model"]),
                     xytext=(5, -10), textcoords="offset points", fontsize=7)
    ax1.set_xlabel(f"Reference [{t['unit'].iloc[0]}]")
    ax1.set_ylabel(f"Model [{t['unit'].iloc[0]}]")
    ax1.set_title("Model vs reference", loc="left", fontweight="bold")
    ax1.legend(fontsize=8, frameon=False)
    s = table.sort_values("error_percent")
    ax2.barh(s["quantity"].str.replace("_", " "), s["error_percent"], color=BLUE, height=0.6)
    for i, v in enumerate(s["error_percent"]):
        ax2.text(v, i, f"  {v:.2f} %", va="center", fontsize=8)
    ax2.set_xlabel("Error [%]")
    ax2.set_title("Percentage error", loc="left", fontweight="bold")
    for ax in (ax1, ax2):
        _style(ax)
