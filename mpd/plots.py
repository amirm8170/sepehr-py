"""
Charts. Every function draws on a matplotlib Figure, so the same charts
are used by main.py (saved as PNG) and by app.py (shown in the window).

Pressures are shown in bar or psi (`unit`). Colours: one fixed categorical
order (validated for colour-blind readers); red is reserved for the pore /
fracture limits, the green band is the safe window p_pore + delta ... p_frac - delta.
"""

import numpy as np

from . import units as u

BLUE, ORANGE, AQUA, YELLOW, VIOLET = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#6250d6"
SERIES = [BLUE, ORANGE, AQUA, YELLOW]            # fixed order, never cycled
STYLES = ["-", "--", "-.", ":"]                  # second encoding for the same series
GREY, RED, SAFE = "#8a8984", "#d03b3b", "#e3f2e8"
INK, MUTED = "#1f2937", "#52514e"


def p_conv(unit):
    """Function that converts Pa to the chosen unit."""
    return u.bar if unit == "bar" else u.psi


def _style(ax):
    ax.set_axisbelow(True)                       # grid behind bars and lines
    ax.grid(True, color="#e4e3df", linewidth=0.7)
    ax.tick_params(colors=MUTED, labelsize=9)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#b9b8b2")


def _title(ax, text):
    ax.set_title(text, loc="left", fontweight="bold", fontsize=11, color=INK)


def _legend(ax, **kw):
    """Legend outside the plot, on the right, so it never hides a curve."""
    ax.legend(**({"loc": "upper left", "bbox_to_anchor": (1.01, 1.0), "fontsize": 8.5, "frameon": False} | kw))


def _window(ax, ts, conv):
    """Safe window as a green band, pore and fracture pressure as red lines (horizontal)."""
    ax.axhspan(conv(ts["safe_low"].iloc[0]), conv(ts["safe_high"].iloc[0]), color=SAFE, lw=0,
               label="Safe window (±δ)")
    ax.axhline(conv(ts["pore"].iloc[0]), color=RED, ls="--", lw=1.2, label="Pore pressure")
    ax.axhline(conv(ts["frac"].iloc[0]), color=RED, ls=":", lw=1.4, label="Fracture pressure")


# ----------------------------------------------------------------------
# Steady state
# ----------------------------------------------------------------------
def pressure_profile(fig, result, case, unit="bar"):
    """Pressure vs MD and pressure vs TVD side by side."""
    conv = p_conv(unit)
    prof = result.profile
    ann, st = prof[prof["path"] == "annulus"], prof[prof["path"] == "string"]
    axes = fig.subplots(1, 2, sharex=True)
    for ax, depth, label in ((axes[0], "md", "Measured depth MD [m]"),
                             (axes[1], "tvd", "True vertical depth TVD [m]")):
        bottom = ann[depth].iloc[-1]
        ax.axvspan(conv(case.safe_low), conv(case.safe_high), color=SAFE, lw=0)
        ax.plot(conv(ann["hydrostatic"]), ann[depth], color=GREY, ls="-.", lw=1.2, label="Hydrostatic ρgh")
        ax.plot(conv(st["pressure"]), st[depth], color=BLUE, lw=2, label="Inside drill string")
        ax.plot(conv(ann["pressure"]), ann[depth], color=ORANGE, lw=2, label="Annulus")
        ax.plot([conv(case.pore)], [bottom], "<", color=RED, ms=9, label="Pore / fracture at TD")
        ax.plot([conv(case.frac)], [bottom], ">", color=RED, ms=9)
        ax.invert_yaxis()
        ax.set_ylabel(label)
        ax.set_xlabel(f"Pressure [{unit}]")
        _style(ax)
    _title(axes[0], "Pressure vs MD")
    _title(axes[1], "Pressure vs TVD")
    fig.legend(*axes[0].get_legend_handles_labels(), loc="outside lower center", ncol=4, fontsize=8.5, frameon=False)


def pressure_path(fig, result, unit="bar"):
    """
    Pressure path pump -> bit -> choke as a waterfall:
    SPP minus drill-pipe, BHA, bit and annulus losses leaves the choke pressure p_c.
    """
    conv = p_conv(unit)
    steps = [("SPP (pump)", result.spp, True), ("Drill pipe", -result.dp_drill_pipe, False),
             ("BHA", -result.dp_bha, False), ("Bit nozzles", -result.dp_bit, False),
             ("Annulus", -result.dp_annulus, False), ("Choke p_c", result.p_c, True)]
    ax = fig.add_subplot(111)
    level = 0.0
    for i, (name, value, total) in enumerate(steps):
        bottom, height = (0.0, value) if total else (level + value, -value)
        ax.bar(i, conv(height), bottom=conv(bottom), width=0.62, color=BLUE if total else ORANGE)
        ax.text(i, conv(bottom + height), f"{conv(abs(value)):,.1f}", ha="center", va="bottom",
                fontsize=9, color=INK)
        level = value if total else level + value
    ax.set_xticks(range(len(steps)))
    ax.set_xticklabels([s[0] for s in steps], fontsize=9)
    ax.set_ylabel(f"Pressure [{unit}]")
    _title(ax, f"Pressure path: SPP {conv(result.spp):,.1f} → losses → choke {conv(result.p_c):,.1f} {unit}")
    _style(ax)
    ax.grid(axis="x", visible=False)


def window_gauge(fig, result, case, unit="bar"):
    """Where BHP sits between pore and fracture pressure (with the safety margin)."""
    conv = p_conv(unit)
    pore, frac = conv(case.pore), conv(case.frac)
    pad = 0.35 * (frac - pore)
    lo = min(pore - pad, conv(result.hydrostatic) - 2)
    hi = max(frac + pad, conv(result.bhp) + 2)
    ax = fig.add_subplot(111)
    ax.axvspan(lo, pore, color="#f9e3e3")
    ax.axvspan(pore, conv(case.safe_low), color="#fdf3dc")
    ax.axvspan(conv(case.safe_low), conv(case.safe_high), color=SAFE)
    ax.axvspan(conv(case.safe_high), frac, color="#fdf3dc")
    ax.axvspan(frac, hi, color="#f9e3e3")
    for x, text in (((lo + pore) / 2, "Kick risk"), ((pore + frac) / 2, "Safe window"),
                    ((frac + hi) / 2, "Loss risk")):
        ax.text(x, 0.8, text, ha="center", fontsize=9, color=MUTED)
    for x, name, color, size in ((conv(result.hydrostatic), "Static (ρgh)", GREY, 60),
                                 (conv(case.target), "Target", ORANGE, 80),
                                 (conv(result.bhp), "BHP", BLUE, 140)):
        ax.scatter([x], [0.42], s=size, marker="D", color=color, edgecolor="white", zorder=5, label=name)
    ax.set_xlim(lo, hi)
    ax.set_ylim(0, 1)
    ax.set_yticks([])
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.set_xlabel(f"Pressure at TD [{unit}]   (amber = inside the safety margin δ)", fontsize=9, color=MUTED)
    ax.legend(loc="lower right", bbox_to_anchor=(1, 1), ncol=3, fontsize=8.5, frameon=False)


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
    _title(ax1, "Vertical section")
    ax2.plot(east, north, "-o", ms=3, color=BLUE)
    ax2.set_xlabel("East [m]")
    ax2.set_ylabel("North [m]")
    _title(ax2, "Plan view")
    for ax in (ax1, ax2):
        ax.set_aspect("equal", adjustable="datalim")
        _style(ax)


# ----------------------------------------------------------------------
# Dynamic results
# ----------------------------------------------------------------------
def dynamic_overview(fig, sim, unit="bar"):
    """BHP with the window, SPP and p_c, flows, choke command and opening."""
    conv = p_conv(unit)
    ts = sim.timeseries
    t = ts["time_s"]
    ax1, ax2, ax3, ax4 = fig.subplots(4, 1, sharex=True, height_ratios=[2, 1, 1, 1])
    _window(ax1, ts, conv)
    ax1.plot(t, conv(ts["target"]), color=ORANGE, ls="--", lw=1.4, label="Target")
    ax1.plot(t, conv(ts["bhp"]), color=BLUE, lw=2, label="BHP")
    ax1.set_ylabel(f"BHP [{unit}]")
    _title(ax1, f"Bottomhole pressure  -  {sim.title}  ({_controller_name(sim.controller)})")

    ax2.plot(t, conv(ts["spp"]), color=BLUE, lw=1.6, label="SPP  p_p")
    ax2.plot(t, conv(ts["p_c"]), color=ORANGE, lw=1.6, ls="--", label="Choke  p_c")
    ax2.set_ylabel(f"[{unit}]")

    for i, (col, name) in enumerate((("q_pump", "Rig pump"), ("q", "String / bit  q"),
                                     ("q_bpp", "Back-pressure pump"), ("q_choke", "Through choke"))):
        ax3.plot(t, u.gpm(ts[col]), color=SERIES[i], ls=STYLES[i], lw=1.6, label=name)
    ax3.set_ylabel("Flow [gpm]")

    ax4.plot(t, ts["z_cmd"] * 100, color=ORANGE, ls="--", lw=1.4, label="Command  z_cmd")
    ax4.plot(t, ts["z"] * 100, color=BLUE, lw=1.8, label="Opening  z")
    ax4.set_ylabel("Choke [%]")
    ax4.set_xlabel("Time [s]")
    # one legend for all four panels (per-panel legends are taller than the panels in a small window)
    handles, labels = [], []
    for ax in (ax1, ax2, ax3, ax4):
        h, l = ax.get_legend_handles_labels()
        handles += h
        labels += l
    fig.legend(handles, labels, loc="outside right upper", fontsize=8.5, frameon=False)
    for ax in (ax1, ax2, ax3, ax4):
        _style(ax)
        ax.yaxis.label.set_size(8.5)
    fig.align_ylabels()


def states_chart(fig, sim, unit="bar"):
    """The four state variables of the model: p_p, p_c, q, z."""
    conv = p_conv(unit)
    ts = sim.timeseries
    t = ts["time_s"]
    axes = fig.subplots(2, 2, sharex=True).ravel()
    for ax, (values, label, title) in zip(axes, (
            (conv(ts["spp"]), f"[{unit}]", "State 1: stand-pipe pressure p_p"),
            (conv(ts["p_c"]), f"[{unit}]", "State 2: choke pressure p_c"),
            (u.gpm(ts["q"]), "[gpm]", "State 3: flow through string and bit q"),
            (ts["z"] * 100, "[%]", "State 4: choke opening z"))):
        ax.plot(t, values, color=BLUE, lw=1.8)
        ax.set_ylabel(label)
        _title(ax, title)
        _style(ax)
    for ax in axes[2:]:
        ax.set_xlabel("Time [s]")


def losses_chart(fig, sim, case, unit="bar"):
    """Pressure losses and ECD over time."""
    conv = p_conv(unit)
    ts = sim.timeseries
    t = ts["time_s"]
    ax1, ax2 = fig.subplots(2, 1, sharex=True)
    for i, (col, name) in enumerate((("dp_d", "Drill string  ΔP_d"), ("dp_bit", "Bit  ΔP_bit"),
                                     ("dp_a", "Annulus  ΔP_a"))):
        ax1.plot(t, conv(ts[col]), color=SERIES[i], ls=STYLES[i], lw=1.8, label=name)
    ax1.set_ylabel(f"[{unit}]")
    _title(ax1, "Friction and bit losses (quasi-steady)")
    _legend(ax1)
    ax2.axhspan(u.pressure_to_emw(case.safe_low, case.td_tvd), u.pressure_to_emw(case.safe_high, case.td_tvd),
                color=SAFE, lw=0, label="Safe window (±δ)")
    ax2.plot(t, u.ppg(ts["ecd"]), color=BLUE, lw=2, label="ECD = BHP/(g h)")
    ax2.plot(t, u.ppg(ts["ecd_no_sbp"]), color=ORANGE, ls="--", lw=1.6, label="ECD without p_c")
    ax2.axhline(u.ppg(case.rho), color=GREY, ls="-.", lw=1, label="Mud weight")
    ax2.set_ylabel("[ppg]")
    ax2.set_xlabel("Time [s]")
    _title(ax2, "Equivalent circulating density at TD")
    _legend(ax2)
    for ax in (ax1, ax2):
        _style(ax)


def controller_chart(fig, sim, unit="bar"):
    """Error, P / I / D terms and the saturated vs unsaturated choke command."""
    conv = p_conv(unit)
    ts = sim.timeseries
    t = ts["time_s"]
    ax1, ax2, ax3 = fig.subplots(3, 1, sharex=True)
    ax1.plot(t, conv(ts["target"] - ts["bhp_measured"]), color=BLUE, lw=1.6, label="e = target − BHP_m")
    ax1.axhline(0, color=GREY, lw=0.8)
    ax1.set_ylabel(f"[{unit}]")
    _title(ax1, "Control error (measured BHP, delayed and sampled)")
    _legend(ax1)
    for i, (col, name) in enumerate((("pid_p", "P term"), ("pid_i", "I term"), ("pid_d", "D term"))):
        ax2.plot(t, ts[col], color=SERIES[i], ls=STYLES[i], lw=1.6, label=name)
    ax2.set_ylabel("Opening change [-]")
    _title(ax2, "Controller terms  (z_cmd = z0 − (P + I + D))")
    _legend(ax2)
    ax3.plot(t, ts["z_unsat"] * 100, color=ORANGE, ls="--", lw=1.4, label="Unsaturated command")
    ax3.plot(t, ts["z_cmd"] * 100, color=BLUE, lw=1.8, label="Saturated command")
    ax3.set_ylabel("[%]")
    ax3.set_xlabel("Time [s]")
    _title(ax3, "Choke command and saturation (anti-windup acts when they differ)")
    _legend(ax3)
    for ax in (ax1, ax2, ax3):
        _style(ax)


def mass_balance_chart(fig, sim):
    """Residual of the mass balance (stored volume change - net inflow)."""
    ts = sim.timeseries
    ax = fig.add_subplot(111)
    ax.plot(ts["time_s"], ts["mass_residual_m3"] * 1e3, color=BLUE, lw=1.6)
    ax.set_xlabel("Time [s]")
    ax.set_ylabel("Residual [litre]")
    _title(ax, "Mass balance: (V_d/β)Δp_p + (V_a/β)Δp_c − ∫(q_pump + q_bpp − q_c)dt")
    _style(ax)


def profiles_over_time(fig, profiles, unit="bar"):
    """Annulus pressure above hydrostatic versus TVD at several times."""
    conv = p_conv(unit)
    ann = profiles[profiles["path"] == "annulus"]
    times = sorted(ann["time_s"].unique())
    ax = fig.add_subplot(111)
    for i, t in enumerate(times):
        p = ann[ann["time_s"] == t]
        ax.plot(conv(p["pressure"] - p["hydrostatic"]), p["tvd"], color=BLUE,
                alpha=0.3 + 0.7 * i / max(len(times) - 1, 1), lw=1.6, label=f"t = {t:.0f} s")
    ax.invert_yaxis()
    ax.set_xlabel(f"Annulus pressure above hydrostatic [{unit}]")
    ax.set_ylabel("TVD [m]")
    _title(ax, "Annulus pressure profile over time (p_c + friction above depth)")
    _legend(ax)
    _style(ax)


def _controller_name(kind):
    return "fixed choke" if kind == "none" else f"{kind} control"


def compare_chart(fig, results, unit="bar"):
    """BHP and choke opening of several runs (S4: fixed / P / PI / PID)."""
    conv = p_conv(unit)
    ax1, ax2 = fig.subplots(2, 1, sharex=True, height_ratios=[2, 1])
    first = next(iter(results.values())).timeseries
    _window(ax1, first, conv)
    ax1.plot(first["time_s"], conv(first["target"]), color=GREY, lw=1.2, label="Target")
    for i, (label, r) in enumerate(results.items()):
        ts = r.timeseries
        name = _controller_name(label) if label in ("none", "P", "PI", "PID") else label
        ax1.plot(ts["time_s"], conv(ts["bhp"]), color=SERIES[i % 4], ls=STYLES[i % 4], lw=1.8, label=name)
        ax2.plot(ts["time_s"], ts["z"] * 100, color=SERIES[i % 4], ls=STYLES[i % 4], lw=1.6, label=name)
    ax1.set_ylabel(f"BHP [{unit}]")
    _title(ax1, "Bottomhole pressure")
    _legend(ax1)
    ax2.set_ylabel("Opening [%]")
    ax2.set_xlabel("Time [s]")
    _title(ax2, "Choke opening")
    _legend(ax2)
    for ax in (ax1, ax2):
        _style(ax)


# ----------------------------------------------------------------------
# Analysis
# ----------------------------------------------------------------------
def tornado(fig, table, unit="bar"):
    """Change of BHP, SPP and ECD for -x % and +x % of each parameter (steady, choke fixed)."""
    conv = p_conv(unit)
    axes = fig.subplots(1, 3)
    for ax, (key, label, f) in zip(axes, (("bhp", f"BHP [{unit}]", conv), ("spp", f"SPP [{unit}]", conv),
                                          ("ecd", "ECD [ppg]", u.ppg))):
        t = table.reindex((table[f"{key}_high"] - table[f"{key}_low"]).abs().sort_values().index)
        y = np.arange(len(t))
        ax.barh(y - 0.17, f(t[f"{key}_low"]), color=BLUE, height=0.32, label="− change")
        ax.barh(y + 0.17, f(t[f"{key}_high"]), color=ORANGE, height=0.32, label="+ change")
        ax.set_yticks(y)
        ax.set_yticklabels(t["parameter"], fontsize=8.5)
        ax.axvline(0, color=GREY, lw=0.8)
        ax.set_xlabel(f"Change in {label}")
        _title(ax, f"Change in {key.upper()}")
        _style(ax)
    axes[0].legend(loc="lower right", fontsize=8.5, frameon=False)


def sweep_chart(fig, table, x_label, unit="bar"):
    """BHP, SPP and ECD for a range of values of one parameter."""
    conv = p_conv(unit)
    axes = fig.subplots(1, 3)
    for ax, (col, label, f) in zip(axes, (("bhp", f"BHP [{unit}]", conv), ("spp", f"SPP [{unit}]", conv),
                                          ("ecd", "ECD [ppg]", u.ppg))):
        ax.plot(table["factor"], f(table[col]), "-o", ms=5, color=BLUE)
        ax.set_xlabel(x_label)
        _title(ax, label)
        _style(ax)


def s5_chart(fig, table, results, unit="bar"):
    """S5: max BHP deviation per parameter (-x % / +x %) and the BHP curves."""
    conv = p_conv(unit)
    ax1, ax2 = fig.subplots(1, 2, width_ratios=[1, 1.5])
    base = table[table["variant"] == "base"].iloc[0]
    params = [p for p in table["parameter"].unique() if p not in ("-", "equal_percentage")]
    y = np.arange(len(params))
    low = [table[(table.parameter == p) & (table.factor < 1)]["max_dev_bar"].iloc[0] for p in params]
    high = [table[(table.parameter == p) & (table.factor > 1)]["max_dev_bar"].iloc[0] for p in params]
    to_unit = (lambda b: b) if unit == "bar" else (lambda b: b * u.BAR / u.PSI)
    ax1.barh(y - 0.17, to_unit(np.array(low)), height=0.32, color=BLUE, label="−20 %")
    ax1.barh(y + 0.17, to_unit(np.array(high)), height=0.32, color=ORANGE, label="+20 %")
    ax1.axvline(to_unit(base["max_dev_bar"]), color=GREY, ls="--", lw=1.2, label="Base")
    ax1.set_yticks(y)
    ax1.set_yticklabels(params, fontsize=9)
    ax1.set_xlabel(f"Max |BHP − target| [{unit}]")
    _title(ax1, "Effect on the largest deviation")
    ax1.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=3, fontsize=8.5, frameon=False)
    first = results["base"].timeseries
    _window(ax2, first, conv)
    for label, r in results.items():
        ts = r.timeseries
        if label == "base":
            continue
        ax2.plot(ts["time_s"], conv(ts["bhp"]), color=GREY, lw=0.8, alpha=0.6)
    ax2.plot(first["time_s"], conv(first["bhp"]), color=BLUE, lw=2, label="Base")
    ax2.plot([], [], color=GREY, lw=0.8, label="±20 % variants")
    ax2.set_xlabel("Time [s]")
    ax2.set_ylabel(f"BHP [{unit}]")
    _title(ax2, "BHP of all variants")
    _legend(ax2)
    for ax in (ax1, ax2):
        _style(ax)


def validation_chart(fig, table):
    """Model vs reference (left) and percentage error (right), rows with a non-zero reference."""
    t = table[table["error_percent"].notna()]
    ax1, ax2 = fig.subplots(1, 2)
    main = t[t["unit"] == t["unit"].mode().iloc[0]]
    top = max(main["reference"].max(), main["model"].max()) * 1.1
    ax1.plot([0, top], [0, top], color=GREY, lw=1, label="Perfect agreement")
    ax1.plot([0, top], [0, top * 1.05], color=GREY, lw=0.8, ls=":", label="±5 %")
    ax1.plot([0, top], [0, top * 0.95], color=GREY, lw=0.8, ls=":")
    ax1.plot(main["reference"], main["model"], "o", ms=8, color=BLUE)
    for _, r in main.iterrows():
        ax1.annotate(str(r["quantity"]).replace("_", " "), (r["reference"], r["model"]),
                     xytext=(6, -10), textcoords="offset points", fontsize=8, color=MUTED)
    ax1.set_xlabel(f"Reference [{main['unit'].iloc[0]}]")
    ax1.set_ylabel(f"Model [{main['unit'].iloc[0]}]")
    _title(ax1, "Model vs reference")
    ax1.legend(fontsize=8.5, frameon=False)
    s = t.sort_values("error_percent")
    labels = [str(q).replace("_", " ")[:34] for q in s["quantity"]]
    ax2.barh(labels, s["error_percent"], color=BLUE, height=0.6)
    for i, v in enumerate(s["error_percent"]):
        ax2.text(v, i, f"  {v:.3f} %", va="center", fontsize=8.5, color=INK)
    ax2.set_xlim(0, max(s["error_percent"].max() * 1.4, 0.1))
    ax2.set_xlabel("Error [%]")
    _title(ax2, "Percentage error")
    for ax in (ax1, ax2):
        _style(ax)
