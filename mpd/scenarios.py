"""
Scenario runner for the proposal scenarios S0 - S5 (config/scenarios.yaml).

  steady       S0   steady state at several pump rates + "does the dynamic model stay put?"
  dynamic      S1, S2, S2b, S3   one time simulation
  compare      S4   the same schedule with no control / P / PI / PID
  sensitivity  S5   the PID run with each parameter -x % and +x %

No physics here - only the order of the runs and the result tables.
"""

import copy

import numpy as np
import pandas as pd
import yaml

from . import units as u
from .dynamics import check_convergence, simulate
from .hydraulics import solve_steady
from .well import ROOT, build_case, make_segments

SENSITIVITY_PARAMETERS = {     # name: (label, function that changes the case file)
    "beta_eff": ("Effective bulk modulus beta_eff", lambda c, f: (
        c["compressibility"].update(fluid_bulk_modulus_pa=float(c["compressibility"]["fluid_bulk_modulus_pa"]) * f,
                                    wall_bulk_modulus_pa=float(c["compressibility"]["wall_bulk_modulus_pa"]) * f))),
    "tau_y": ("Yield stress tau_y", lambda c, f: c["mud"].update(yp_lbf100ft2=c["mud"]["yp_lbf100ft2"] * f)),
    "mu_p": ("Plastic viscosity mu_p", lambda c, f: c["mud"].update(pv_cp=c["mud"]["pv_cp"] * f)),
    "choke_cd": ("Choke discharge coefficient C_d", lambda c, f: c["choke"].update(
        discharge_coefficient=min(c["choke"]["discharge_coefficient"] * f, 1.0))),
    "choke_area": ("Choke area A_max", lambda c, f: c["choke"].update(
        max_area_in2=c["choke"]["max_area_in2"] * f)),
}


def load_scenarios(path=None):
    """All scenarios from config/scenarios.yaml (dict name -> spec)."""
    with open(path or ROOT / "config" / "scenarios.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def schedule_of(name, scenarios):
    """The time schedule of a scenario (S4 / S5 reuse the S3 schedule)."""
    spec = scenarios[name]
    if "schedule_from" in spec:
        base = dict(scenarios[spec["schedule_from"]])
        base.update({k: v for k, v in spec.items() if k not in ("kind", "schedule_from")})
        return base
    return spec


# ----------------------------------------------------------------------
# S0
# ----------------------------------------------------------------------
def run_steady_rates(case, seg, spec, progress=None, cancel=None):
    """S0: steady solution at each pump rate, then hold the dynamic model there."""
    rows, runs = [], []
    rates = spec["pump_rates_gpm"]
    for i, gpm in enumerate(rates):
        r = solve_steady(case, seg, q=gpm * u.GPM)
        hold = simulate(case, seg, {"duration_s": spec["hold_s"], "pump_gpm": [[0, gpm]]},
                        "none", f"S0 {gpm:g} gpm", cancel=cancel)
        drift = float(np.abs(hold.timeseries["bhp"] - r.bhp).max())
        rows.append({"pump_rate_gpm": gpm, "choke_opening": r.z, "target_reachable": r.target_reachable,
                     "p_c": r.p_c, "dp_drill_pipe": r.dp_drill_pipe, "dp_bha": r.dp_bha,
                     "dp_bit": r.dp_bit, "dp_annulus": r.dp_annulus, "bhp": r.bhp, "spp": r.spp,
                     "ecd_ppg": u.ppg(r.ecd), "ecd_no_sbp_ppg": u.ppg(r.ecd_no_sbp), "status": r.status,
                     "dynamic_drift": drift})
        runs.append(r)
        if progress:
            progress((i + 1) / len(rates))
    return pd.DataFrame(rows), runs


# ----------------------------------------------------------------------
# S1 - S4
# ----------------------------------------------------------------------
def run_dynamic(case, seg, name, scenarios, controller=None, convergence=True,
                progress=None, cancel=None):
    """One time simulation (+ the step-halving convergence check)."""
    spec = schedule_of(name, scenarios)
    controller = controller or spec.get("controller", "none")
    result = simulate(case, seg, spec, controller, name, progress=progress, cancel=cancel)
    if convergence:
        check_convergence(case, seg, spec, result, cancel=cancel)
    return result


def run_compare(case, seg, name, scenarios, convergence=True, progress=None, cancel=None):
    """S4: the same schedule and initial state with each controller."""
    spec = scenarios[name]
    results = {}
    for i, ctrl in enumerate(spec["controllers"]):
        results[ctrl] = run_dynamic(case, seg, name, scenarios, ctrl, convergence, cancel=cancel)
        if progress:
            progress((i + 1) / len(spec["controllers"]))
    return results


def metrics_table(results, unit="bar"):
    """Performance metrics of several runs side by side (one row per run)."""
    conv = u.bar if unit == "bar" else u.psi
    rows = []
    for label, r in results.items():
        m = r.metrics
        row = {"run": label, "overshoot_%": m["overshoot_percent"], "settling_s": m["settling_time_s"],
               f"max_dev_{unit}": conv(m["max_abs_deviation"]), f"rmse_{unit}": conv(m["rmse"]),
               "mape_%": m["mape_percent"], "in_window_%": m["time_in_window_percent"],
               f"min_bhp_{unit}": conv(m["min_bhp"]), f"max_bhp_{unit}": conv(m["max_bhp"])}
        for crit, _, _, passed in m["criteria"]:
            row[f"pass: {crit}"] = "PASS" if passed else "FAIL"
        row["mass_balance_%"] = r.mass_balance["relative_percent"]
        row["convergence_%"] = r.convergence.get("change_percent", np.nan)
        rows.append(row)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------
# S5
# ----------------------------------------------------------------------
def changed_config(config, parameter, factor):
    """Copy of the case file with one S5 parameter multiplied by factor."""
    cfg = copy.deepcopy(config)
    SENSITIVITY_PARAMETERS[parameter][1](cfg, factor)
    return cfg


def run_sensitivity(config, name, scenarios, progress=None, cancel=None):
    """
    S5: the PID run with each parameter at (1 - x) and (1 + x).
    Returns (table, {label: SimulationResult}) - the base run is included.
    """
    spec = scenarios[name]
    sched = schedule_of(name, scenarios)
    x = spec["change_percent"] / 100
    variants = [("base", None, 1.0)]
    for p in spec["parameters"]:
        variants += [(f"{p} -{spec['change_percent']}%", p, 1 - x), (f"{p} +{spec['change_percent']}%", p, 1 + x)]
    if spec.get("equal_percentage"):
        variants.append(("equal-percentage choke", "equal_percentage", 1.0))
    results, rows = {}, []
    for i, (label, p, factor) in enumerate(variants):
        if p == "equal_percentage":
            cfg = copy.deepcopy(config)
            cfg["choke"]["characteristic"] = "equal_percentage"
        else:
            cfg = changed_config(config, p, factor) if p else config
        case = build_case(cfg)
        seg = make_segments(case)
        r = simulate(case, seg, sched, spec["controller"], label, cancel=cancel)
        results[label] = r
        m = r.metrics
        rows.append({"variant": label, "parameter": p or "-", "factor": factor,
                     "max_dev_bar": u.bar(m["max_abs_deviation"]), "rmse_bar": u.bar(m["rmse"]),
                     "overshoot_%": m["overshoot_percent"], "settling_s": m["settling_time_s"],
                     "in_window_%": m["time_in_window_percent"], "z0": r.initial.z,
                     "min_opening": r.timeseries["z"].min()})
        if progress:
            progress((i + 1) / len(variants))
    return pd.DataFrame(rows), results


# ----------------------------------------------------------------------
# helpers for the reports
# ----------------------------------------------------------------------
def bpp_check(case, seg, scenarios, name="S3"):
    """
    BPP assumed in the connection: opening the choke needs at zero pump flow
    to hold the target with only the BPP flowing (steady estimate).
    """
    sched = schedule_of(name, scenarios)
    q_bpp = max(v for _, v in sched["bpp_gpm"]) * u.GPM
    r = solve_steady(case, seg, q=0.0, q_bpp=q_bpp)
    return {"bpp_gpm": u.gpm(q_bpp), "opening_needed": r.z, "reachable": r.target_reachable,
            "p_c_needed": r.p_c}
