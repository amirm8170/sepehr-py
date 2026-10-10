"""
Analysis tools:
  * control_metrics  - how well BHP was controlled (settling time, overshoot ...)
  * sensitivity      - change one input by +/- x % and see BHP, SPP and ECD
  * validation       - compare model values with reference values
"""

import copy

import numpy as np
import pandas as pd

from . import units as u


# ----------------------------------------------------------------------
# Control performance
# ----------------------------------------------------------------------
def _metrics_for(ts, tolerance, t0):
    """Metrics for the part of the time series that starts at time t0."""
    part = ts[ts["time_s"] >= t0]
    t, y, r = part["time_s"].values, part["bhp"].values, part["target"].values
    err = r - y
    before = ts[ts["time_s"] < t0]
    target_before = before["target"].iloc[-1] if len(before) else r[0]
    step = r[-1] - y[0]
    has_step = abs(r[-1] - target_before) >= tolerance      # did the target change?

    rise = overshoot = np.nan
    if has_step:
        up = step > 0
        def first(level):
            idx = np.nonzero(y >= level if up else y <= level)[0]
            return t[idx[0]] if len(idx) else np.nan
        rise = first(y[0] + 0.9 * step) - first(y[0] + 0.1 * step)
        peak = (y.max() - r[-1]) if up else (r[-1] - y.min())
        overshoot = max(peak, 0) / abs(step) * 100

    outside = np.abs(err) > tolerance
    if not outside.any():
        settling = 0.0
    elif outside[-1]:
        settling = np.nan                                     # never settled
    else:
        settling = t[np.nonzero(outside)[0][-1] + 1] - t0

    n_last = max(1, len(err) // 10)
    return {"settling_time_s": settling, "rise_time_s": rise, "overshoot_percent": overshoot,
            "max_above_target": max(-err.min(), 0), "max_below_target": max(err.max(), 0),
            "steady_state_error": err[-n_last:].mean()}


def control_metrics(ts, tolerance, events_s):
    """
    Control-performance metrics of a simulation.

    settling time : time after the event until |target - BHP| stays inside the tolerance
    rise time     : 10 % -> 90 % of a target step (only if the target changes)
    overshoot     : how far BHP goes past a new target, in % of the step
    steady-state error : mean error in the last 10 % of the run
    time outside window: total time with BHP < pore or BHP > fracture pressure
    With several events, metrics are also given for each event separately.
    """
    dt = ts["time_s"].diff().fillna(0).values
    metrics = _metrics_for(ts, tolerance, events_s[0])
    metrics.update({
        "min_bhp": ts["bhp"].min(), "max_bhp": ts["bhp"].max(),
        "time_outside_window_s": float(dt[(ts["status"] != "safe").values].sum()),
        "iae": float((ts["error"].abs() * dt).sum()),
        "tolerance": tolerance,
    })
    metrics["window_held"] = metrics["time_outside_window_s"] == 0
    bounds = list(events_s) + [ts["time_s"].iloc[-1] + 1]
    metrics["per_event"] = [
        {"event": i + 1, "time_s": t0, **_metrics_for(ts[ts["time_s"] < t1], tolerance, t0)}
        for i, (t0, t1) in enumerate(zip(bounds[:-1], bounds[1:]))]
    return metrics


# ----------------------------------------------------------------------
# Sensitivity analysis (one input at a time)
# ----------------------------------------------------------------------
PARAMETERS = {                       # name: (description, how to change the case file)
    "pump_rate": "Pump rate",
    "mud_weight": "Mud weight",
    "pv": "Plastic viscosity",
    "yp": "Yield point",
    "hole_diameter": "Hole diameter",
    "pipe_od": "Pipe OD",
    "back_pressure": "Surface back pressure",
    "choke_opening": "Choke opening",
}


def changed_config(config, parameter, factor, base_result):
    """Copy of the case file with one parameter multiplied by `factor`."""
    cfg = copy.deepcopy(config)
    op, mud, well = cfg["operation"], cfg["mud"], cfg["well"]
    # keep the operating point of the base case (fixed choke opening / SBP)
    if op["back_pressure_from"] == "choke":
        op["choke_opening"] = base_result["opening"]
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
    elif parameter == "back_pressure":
        op["back_pressure_from"] = "fixed"
        op["back_pressure_psi"] = u.psi(base_result["sbp"]) * factor
    elif parameter == "choke_opening":
        op["back_pressure_from"] = "choke"
        op["choke_opening"] = min(base_result["opening"] * factor, 1.0)
    return cfg


def run_case(cfg):
    """Build and solve a case file (import here to avoid circular imports)."""
    from .hydraulics import solve_steady
    from .well import build_case, make_segments
    case = build_case(cfg)
    return solve_steady(case, make_segments(case))


def sensitivity(config, change=0.10, parameters=None):
    """
    One-at-a-time sensitivity: each parameter is changed by -change and
    +change while all others stay the same. Returns a table of the changes
    in BHP, SPP and ECD.
    """
    base = run_case(config)
    rows = []
    for p in parameters or PARAMETERS:
        low = run_case(changed_config(config, p, 1 - change, base))
        high = run_case(changed_config(config, p, 1 + change, base))
        rows.append({
            "parameter": PARAMETERS[p],
            "bhp_low": low["bhp"] - base["bhp"], "bhp_high": high["bhp"] - base["bhp"],
            "spp_low": low["spp"] - base["spp"], "spp_high": high["spp"] - base["spp"],
            "ecd_low": low["ecd"] - base["ecd"], "ecd_high": high["ecd"] - base["ecd"],
        })
    return pd.DataFrame(rows)


def sweep(config, parameter, factors):
    """BHP, SPP and ECD for a range of values of one parameter."""
    base = run_case(config)
    rows = []
    for f in factors:
        r = run_case(changed_config(config, parameter, f, base))
        rows.append({"factor": f, "bhp": r["bhp"], "spp": r["spp"], "ecd": r["ecd"]})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------
def percentage_error(model, reference):
    """Error(%) = |model - reference| / |reference| * 100"""
    return abs(model - reference) / abs(reference) * 100


def validate(result, reference):
    """
    Compare a steady-state result with a reference table.

    The reference table has columns: quantity, reference_value, unit,
    data_type (field / laboratory / literature / synthetic), source.
    """
    model_values = {          # quantity -> (model value in SI, converter to the reference unit)
        "drill_pipe_loss": result["dp_drill_pipe"], "bha_loss": result["dp_bha"],
        "bit_loss": result["dp_bit"], "annulus_loss": result["dp_annulus"],
        "bhp": result["bhp"], "spp": result["spp"], "ecd": result["ecd"],
    }
    to_unit = {"psi": u.psi, "bar": u.bar, "pa": lambda x: x, "ppg": u.ppg,
               "kg_m3": lambda x: x}
    rows = []
    for _, ref in reference.iterrows():
        model = to_unit[ref["unit"]](model_values[ref["quantity"]])
        rows.append({"quantity": ref["quantity"], "unit": ref["unit"],
                     "reference": ref["reference_value"], "model": model,
                     "absolute_error": abs(model - ref["reference_value"]),
                     "error_percent": percentage_error(model, ref["reference_value"]),
                     "data_type": ref["data_type"], "source": ref["source"]})
    return pd.DataFrame(rows)
