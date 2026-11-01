"""
Performance metrics of a dynamic run (proposal 4-2-8) and the proposal's
preliminary acceptance criteria.

  overshoot          step in the target (|step| larger than the settling band):
                         100 * (largest pass beyond the new target) / |step|
                     disturbance (target constant, BHP starts on target):
                         100 * max|BHP - target| / target   (peak deviation)
  settling time      smallest time after the first event from which
                     |BHP - target| <= band% x target for the rest of the run
  max deviation      max |BHP - target|
  RMSE               sqrt( 1/N sum (BHP_i - target_i)^2 )
  MAPE               100/N sum |BHP_i - target_i| / |target_i|   (target > 0)
  time in window     % of time with p_pore + delta <= BHP <= p_frac - delta

Criteria (proposal, preliminary): overshoot < 5 %, settling < 60 s, time in window >= 95 %.
They are checked and shown as PASS / FAIL; they are targets, not guaranteed results.
"""

import numpy as np


def performance_metrics(ts, event_time, criteria):
    """Metrics of a time series with columns time_s, bhp, target, safe_low, safe_high."""
    t, y, r = ts["time_s"].values, ts["bhp"].values, ts["target"].values
    error = y - r
    band = criteria["settling_band_percent"] / 100 * np.abs(r)

    # overshoot
    i0 = int(np.searchsorted(t, event_time))
    i0 = min(i0, len(t) - 1)
    step = r[-1] - y[i0]
    if abs(step) > band[-1]:
        beyond = (y[i0:] - r[-1]) if step > 0 else (r[-1] - y[i0:])
        overshoot = max(beyond.max(), 0.0) / abs(step) * 100
        basis = "target step"
    else:
        overshoot = np.max(np.abs(error)) / abs(r[-1]) * 100
        basis = "disturbance (peak deviation / target)"

    # settling time (from the first event)
    outside = np.abs(error[i0:]) > band[i0:]
    if not outside.any():
        settling = 0.0
    elif outside[-1]:
        settling = np.nan                                       # not settled at the end
    else:
        settling = t[i0 + np.nonzero(outside)[0][-1] + 1] - t[i0]

    inside = (y >= ts["safe_low"].values) & (y <= ts["safe_high"].values)
    m = {
        "overshoot_percent": float(overshoot),
        "overshoot_basis": basis,
        "settling_time_s": float(settling),
        "max_abs_deviation": float(np.max(np.abs(error))),
        "rmse": float(np.sqrt(np.mean(error ** 2))),
        "mape_percent": float(np.mean(np.abs(error) / np.abs(r)) * 100) if np.all(r > 0) else np.nan,
        "time_in_window_percent": float(inside.mean() * 100),
        "min_bhp": float(y.min()), "max_bhp": float(y.max()),
    }
    m["criteria"] = check_criteria(m, criteria)
    return m


def check_criteria(m, criteria):
    """PASS / FAIL of each proposal criterion: list of (name, limit text, value text, passed)."""
    settled = not np.isnan(m["settling_time_s"])
    return [
        ("Overshoot", f"< {criteria['max_overshoot_percent']:g} %", f"{m['overshoot_percent']:.2f} %",
         m["overshoot_percent"] < criteria["max_overshoot_percent"]),
        ("Settling time (±{:g} %)".format(criteria["settling_band_percent"]),
         f"< {criteria['max_settling_time_s']:g} s",
         f"{m['settling_time_s']:.1f} s" if settled else "not settled",
         settled and m["settling_time_s"] < criteria["max_settling_time_s"]),
        ("Time in safe window", f">= {criteria['min_time_in_window_percent']:g} %",
         f"{m['time_in_window_percent']:.1f} %",
         m["time_in_window_percent"] >= criteria["min_time_in_window_percent"]),
    ]


def percentage_error(model, reference):
    """Error(%) = |model - reference| / |reference| * 100"""
    return abs(model - reference) / abs(reference) * 100


def mape(model, reference):
    """MAPE = 100/N sum |model_i - ref_i| / |ref_i|"""
    model, reference = np.asarray(model, float), np.asarray(reference, float)
    return float(np.mean(np.abs(model - reference) / np.abs(reference)) * 100)
