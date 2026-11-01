"""
Analysis tools:
  * steady_sensitivity / sweep - change one input of the steady model, see BHP, SPP, ECD
  * tune_pid                   - the proposal's trial-and-error PID tuning on S3, as a table
"""

import copy
from dataclasses import replace

import numpy as np
import pandas as pd

from . import units as u
from .dynamics import simulate
from .hydraulics import solve_steady
from .well import build_case, make_segments

# ----------------------------------------------------------------------
# Steady-state sensitivity (one input at a time, choke kept at the base opening)
# ----------------------------------------------------------------------
PARAMETERS = {
    "pump_rate": "Pump rate",
    "mud_weight": "Mud weight",
    "pv": "Plastic viscosity",
    "yp": "Yield point",
    "hole_diameter": "Hole diameter",
    "pipe_od": "Pipe OD",
    "choke_opening": "Choke opening",
    "choke_area": "Choke area A_max",
}


def changed_config(config, parameter, factor):
    """Copy of the case file with one parameter multiplied by `factor`."""
    cfg = copy.deepcopy(config)
    op, mud, well = cfg["operation"], cfg["mud"], cfg["well"]
    if parameter == "pump_rate":
        op["pump_rate_gpm"] *= factor
    elif parameter == "mud_weight":
        mud["density_ppg"] *= factor
    elif parameter == "pv":
        mud["pv_cp"] *= factor
    elif parameter == "yp":
        mud["yp_lbf100ft2"] *= factor
    elif parameter == "hole_diameter":
        for h in well["hole_sections"]:
            h["diameter_in"] *= factor
    elif parameter == "pipe_od":
        for p in well["drill_string"]:
            p["od_in"] *= factor
    elif parameter == "choke_area":
        cfg["choke"]["max_area_in2"] *= factor
    return cfg


def run_fixed_choke(cfg, opening):
    """Steady result with the choke held at `opening` (no target holding)."""
    case = build_case(cfg)
    return solve_steady(case, make_segments(case), z=min(max(opening, case.choke.z_min), case.choke.z_max))


def steady_sensitivity(config, change=0.10, parameters=None):
    """
    One-at-a-time sensitivity of the steady model: each parameter -change and
    +change; the choke stays at the base opening z0 (so BHP can move).
    Returns a table of the changes in BHP, SPP and ECD [Pa, Pa, kg/m3].
    """
    case = build_case(config)
    z0 = solve_steady(case, make_segments(case)).z
    base = run_fixed_choke(config, z0)
    rows = []
    for p in parameters or PARAMETERS:
        if p == "choke_opening":
            low, high = run_fixed_choke(config, z0 * (1 - change)), run_fixed_choke(config, z0 * (1 + change))
        else:
            low = run_fixed_choke(changed_config(config, p, 1 - change), z0)
            high = run_fixed_choke(changed_config(config, p, 1 + change), z0)
        rows.append({"parameter": PARAMETERS[p],
                     "bhp_low": low.bhp - base.bhp, "bhp_high": high.bhp - base.bhp,
                     "spp_low": low.spp - base.spp, "spp_high": high.spp - base.spp,
                     "ecd_low": low.ecd - base.ecd, "ecd_high": high.ecd - base.ecd})
    return pd.DataFrame(rows)


def sweep(config, parameter, factors):
    """BHP, SPP and ECD for a range of values of one parameter (choke at z0)."""
    case = build_case(config)
    z0 = solve_steady(case, make_segments(case)).z
    rows = []
    for f in factors:
        r = (run_fixed_choke(config, z0 * f) if parameter == "choke_opening"
             else run_fixed_choke(changed_config(config, parameter, f), z0))
        rows.append({"factor": f, "bhp": r.bhp, "spp": r.spp, "ecd": r.ecd})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------
# PID tuning (proposal 4-2-7) - reproducible trial and error on S3
# ----------------------------------------------------------------------
KP_CANDIDATES = [0.005, 0.01, 0.02, 0.04, 0.08, 0.16, 0.32]   # [1/bar]
KI_CANDIDATES = [0.001, 0.002, 0.004, 0.008, 0.016, 0.032]    # [1/(bar s)]
KD_CANDIDATES = [0.02, 0.05, 0.1]                             # [s/bar]
MAX_CROSSINGS = 6          # BHP crosses the target (0.1 % dead band) - about 3 per disturbance
MAX_REVERSALS = 20         # the choke command changes direction (steps > 0.001)


def oscillation(ts, case, start):
    """
    Two signs of "marked oscillation" after `start`:
      crossings  - BHP crosses the target by more than 0.1 % of the target
      reversals  - the choke command changes direction (choke "hunting")
    """
    part = ts[ts["time_s"] >= start]
    err = (part["bhp"] - part["target"]).values
    signs = np.sign(err[np.abs(err) > 1e-3 * abs(case.target)])
    dz = np.diff(part["z_cmd"].values)
    moves = np.sign(dz[np.abs(dz) > 1e-3])
    return int(np.count_nonzero(np.diff(signs))), int(np.count_nonzero(np.diff(moves)))


def calm(row):
    return row["crossings"] <= MAX_CROSSINGS and row["reversals"] <= MAX_REVERSALS


def _trial(case, seg, scenario, kind, gains, start):
    r = simulate(replace(case, pid=gains), seg, scenario, kind)
    m = r.metrics
    return {"kp_per_bar": gains.kp * u.BAR, "ki_per_bar_s": gains.ki * u.BAR, "kd_s_per_bar": gains.kd * u.BAR,
            "max_dev_bar": u.bar(m["max_abs_deviation"]), "rmse_bar": u.bar(m["rmse"]),
            "overshoot_%": m["overshoot_percent"], "settling_s": m["settling_time_s"],
            "in_window_%": m["time_in_window_percent"],
            **dict(zip(("crossings", "reversals"), oscillation(r.timeseries, case, start))),
            "saturated_%": float((r.timeseries["z_unsat"] != r.timeseries["z_cmd"]).mean() * 100)}


def tune_pid(case, seg, scenario, progress=None):
    """
    The proposal's tuning procedure, made repeatable:
      1. Ki = Kd = 0; increase Kp; keep the LARGEST Kp without marked oscillation
         (BHP crosses the target at most MAX_CROSSINGS times and the choke
         reverses at most MAX_REVERSALS times).
      2. add Ki; keep the Ki with the smallest RMSE that still does not oscillate.
      3. add Kd only if the overshoot is above the proposal limit (5 %).
    Returns (table of every trial, chosen gains as PIDGains, list of reasons).
    """
    start = (scenario.get("events_s") or [0])[0]
    rows, reasons = [], []
    total = len(KP_CANDIDATES) + len(KI_CANDIDATES) + len(KD_CANDIDATES)
    done = 0

    def step(stage, kind, gains):
        nonlocal done
        row = {"stage": stage, **_trial(case, seg, scenario, kind, gains, start)}
        rows.append(row)
        done += 1
        if progress:
            progress(done / total)
        return row

    base = replace(case.pid, ki=0.0, kd=0.0)
    p_rows = [step("1: Kp (P only)", "P", replace(base, kp=kp / u.BAR)) for kp in KP_CANDIDATES]
    ok = [r for r in p_rows if calm(r)]
    kp = (max(ok, key=lambda r: r["kp_per_bar"]) if ok else p_rows[0])["kp_per_bar"]
    reasons.append(f"Kp = {kp:g} 1/bar: largest Kp without marked oscillation (BHP crosses the target "
                   f"<= {MAX_CROSSINGS} times, choke reverses <= {MAX_REVERSALS} times).")

    i_rows = [step("2: Ki (PI)", "PI", replace(base, kp=kp / u.BAR, ki=ki / u.BAR)) for ki in KI_CANDIDATES]
    ok = [r for r in i_rows if calm(r)] or i_rows
    best = min(ok, key=lambda r: r["rmse_bar"])
    ki = best["ki_per_bar_s"]
    reasons.append(f"Ki = {ki:g} 1/(bar s): smallest RMSE ({best['rmse_bar']:.3f} bar) without marked "
                   f"oscillation; removes the steady error of P control.")

    kd = 0.0
    limit = case.criteria["max_overshoot_percent"]
    d_rows = [step("3: Kd (PID, only if needed)", "PID", replace(base, kp=kp / u.BAR, ki=ki / u.BAR, kd=kd_ / u.BAR))
              for kd_ in KD_CANDIDATES]
    if best["overshoot_%"] > limit:
        better = [r for r in d_rows if r["overshoot_%"] < best["overshoot_%"]]
        if better:
            kd = min(better, key=lambda r: r["overshoot_%"])["kd_s_per_bar"]
        reasons.append(f"PI overshoot {best['overshoot_%']:.2f} % > {limit:g} %: Kd = {kd:g} s/bar added.")
    else:
        reasons.append(f"Kd = 0: PI overshoot {best['overshoot_%']:.2f} % is already below {limit:g} % "
                       f"(the Kd trials are listed for reference only).")
    chosen = replace(case.pid, kp=kp / u.BAR, ki=ki / u.BAR, kd=kd / u.BAR)
    return pd.DataFrame(rows), chosen, reasons
