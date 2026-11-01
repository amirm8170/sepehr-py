"""
Four-state dynamic model of the well (proposal 4-2-5) and its simulation.

States:  p_p  stand-pipe pressure          q  flow through string and bit
         p_c  choke (upstream) pressure    z  physical choke opening

    (V_d / beta_eff) dp_p/dt = q_pump - q                                     (1)
    (V_a / beta_eff) dp_c/dt = q + q_bpp - q_c                                (2)
    M dq/dt = p_p - p_c - (rho_a - rho_d) g h - dP_d - dP_bit - dP_a          (3)
    dz/dt   = r_max tanh(((z_cmd - z) / tau_a) / r_max)                       (4)

    BHP = p_c + rho g h + dP_a(q)     (inertia of the annulus column not included)

Friction is quasi-steady: the steady formulas with the flow of the moment.
A fifth "bookkeeping" variable integrates q_pump + q_bpp - q_c, so the
mass balance (1) + (2) can be checked at every output time:

    (V_d/beta)(p_p - p_p0) + (V_a/beta)(p_c - p_c0) = integral(q_pump + q_bpp - q_c) dt

Time loop: the controller runs every T_s with a zero-order hold; between two
samples the ODEs are integrated with scipy solve_ivp (Radau by default).
The measurement is BHP(t - theta_m), the command reaches the actuator after theta_a.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.integrate import solve_ivp

from . import units as u
from .choke import actuator_rate, choke_flow
from .controller import PID, controller_gains
from .hydraulics import flow_geometry, pressure_losses, pressure_profile, solve_steady, window_status
from .metrics import performance_metrics
from .well import flow_path


class Cancelled(Exception):
    """Raised when the user cancels a running simulation."""


def schedule(points, default):
    """Function of time from [[t, value], ...] (linear between points, constant outside)."""
    if not points:
        return lambda t: default
    times = np.array([p[0] for p in points], float)
    values = np.array([p[1] for p in points], float)
    return lambda t: float(np.interp(t, times, values))


@dataclass
class SimulationResult:
    name: str
    title: str
    controller: str               # "none", "P", "PI" or "PID"
    timeseries: pd.DataFrame
    metrics: dict
    mass_balance: dict
    solver: dict
    initial: object               # SteadyResult at t = 0
    convergence: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


class FourStateModel:
    """Right-hand side of the ODEs (1)-(4) for one case."""

    def __init__(self, case, seg, pump, bpp):
        self.case, self.pump, self.bpp = case, pump, bpp
        self.path = flow_path(case, seg)
        self.geo = flow_geometry(seg, grouped=True)
        self.beta = case.beta_eff

    def losses(self, q):
        loss = pressure_losses(self.case, self.geo, q)
        return loss["dp_d"], loss["dp_bit"], loss["dp_a"]

    def rhs(self, t, x, z_cmd):
        p_p, p_c, q, z, _ = x
        case, path = self.case, self.path
        q_pump, q_bpp = self.pump(t), self.bpp(t)
        q_c = choke_flow(p_c, z, case.rho, case.choke)
        dp_d, dp_bit, dp_a = self.losses(q)
        return [self.beta / path.v_d * (q_pump - q),                                  # (1)
                self.beta / path.v_a * (q + q_bpp - q_c),                             # (2)
                (p_p - p_c - 0.0 - dp_d - dp_bit - dp_a) / path.inertance,            # (3) rho_a = rho_d
                actuator_rate(z_cmd, z, case.actuator, case.choke),                   # (4)
                q_pump + q_bpp - q_c]                                                 # bookkeeping

    def outputs(self, t, x):
        """Measured quantities for one state vector."""
        p_p, p_c, q, z, net_in = x
        case = self.case
        dp_d, dp_bit, dp_a = self.losses(q)
        bhp = p_c + case.rho * u.G * self.path.h + dp_a
        return {"time_s": t, "q_pump": self.pump(t), "q_bpp": self.bpp(t), "q": q,
                "q_choke": choke_flow(p_c, z, case.rho, case.choke),
                "spp": p_p, "p_c": p_c, "bhp": bhp, "z": min(max(z, case.choke.z_min), case.choke.z_max),
                "dp_d": dp_d, "dp_bit": dp_bit, "dp_a": dp_a,
                "ecd": bhp / (u.G * self.path.h), "ecd_no_sbp": case.rho + dp_a / (u.G * self.path.h),
                "net_inflow_m3": net_in}


def simulate(case, seg, scenario, controller="none", name="", max_step=None, method=None,
             progress=None, cancel=None):
    """
    Run one dynamic scenario.

    scenario   : dict from config/scenarios.yaml (schedules of [time_s, value])
                   duration_s, events_s, pump_gpm, bpp_gpm,
                   choke_change (added to z0), target_change_bar (added to the target)
    controller : "none" (choke command from the schedule), "P", "PI" or "PID"
    progress   : optional function(fraction); cancel: optional function() -> True to stop
    """
    duration = float(scenario["duration_s"])
    pump = schedule(scenario.get("pump_gpm") and [[t, v * u.GPM] for t, v in scenario["pump_gpm"]],
                    case.q_pump)
    bpp = schedule(scenario.get("bpp_gpm") and [[t, v * u.GPM] for t, v in scenario["bpp_gpm"]],
                   case.q_bpp)
    choke_change = schedule(scenario.get("choke_change"), 0.0)
    target_change = schedule(scenario.get("target_change_bar")
                             and [[t, v * u.BAR] for t, v in scenario["target_change_bar"]], 0.0)
    method = method or case.solver.method
    max_step = max_step or case.solver.max_step
    model = FourStateModel(case, seg, pump, bpp)

    # --- consistent initial state = steady operating point at t = 0 ------------
    start = solve_steady(case, seg, q=pump(0.0), q_bpp=bpp(0.0), target=case.target + target_change(0.0))
    z0 = start.z
    x = np.array([start.spp, start.p_c, start.q, z0, 0.0])
    warnings = [] if start.target_reachable else [
        f"Initial target not reachable: choke at its limit z = {z0:.3f}; BHP starts at "
        f"{u.bar(start.bhp):.1f} bar instead of {u.bar(case.target):.1f} bar."]

    ch, meas = case.choke, case.measurement
    pid = None
    if controller != "none":
        pid = PID(controller_gains(case.pid, controller), z0, ch.z_min, ch.z_max, meas.sample_time)
    rng = np.random.default_rng(meas.seed)

    ts_s = meas.sample_time
    n = int(np.ceil(duration / ts_s - 1e-9))
    hist_t, hist_bhp = [0.0], [start.bhp]           # true BHP history (for the measurement delay)
    cmd_t, cmd_z = [], []                           # command history (for the actuator dead time)
    rows, n_rhs, failures = [], 0, 0
    row = model.outputs(0.0, x)

    for k in range(n + 1):
        t = k * ts_s
        if cancel and cancel():
            raise Cancelled()
        # measurement: delayed, sampled (zero-order hold until the next sample), optional noise
        bhp_m = float(np.interp(t - meas.delay, hist_t, hist_bhp))
        if meas.noise_std > 0:
            bhp_m += rng.normal(0.0, meas.noise_std)
        target = case.target + target_change(t)
        if pid:
            z_cmd = pid.update(target - bhp_m)
            terms = pid.terms
        else:
            z_cmd = min(max(z0 + choke_change(t), ch.z_min), ch.z_max)
            terms = {"p": np.nan, "i": np.nan, "d": np.nan, "u_unsat": np.nan, "z_unsat": z_cmd}
        cmd_t.append(t)
        cmd_z.append(z_cmd)
        i_cmd = max(int(np.searchsorted(cmd_t, t - case.actuator.dead_time + 1e-9, side="right")) - 1, 0)
        z_applied = cmd_z[i_cmd]

        row.update({"bhp_measured": bhp_m, "target": target, "z_cmd": z_cmd, "z_unsat": terms["z_unsat"],
                    "pid_p": terms["p"], "pid_i": terms["i"], "pid_d": terms["d"],
                    "u_unsat": terms["u_unsat"], "z_applied": z_applied})
        rows.append(row)
        if k == n:
            break

        sol = solve_ivp(model.rhs, (t, t + ts_s), x, method=method, args=(z_applied,),
                        rtol=case.solver.rtol, atol=case.solver.atol, max_step=max_step)
        n_rhs += sol.nfev
        if not sol.success:
            failures += 1
        x = sol.y[:, -1]
        row = model.outputs(t + ts_s, x)
        hist_t.append(t + ts_s)
        hist_bhp.append(row["bhp"])
        if progress and k % max(1, n // 50) == 0:
            progress(k / n)

    ts = pd.DataFrame(rows)
    ts["pore"], ts["frac"] = case.pore, case.frac
    ts["safe_low"], ts["safe_high"] = case.safe_low, case.safe_high
    ts["status"] = [window_status(b, case) for b in ts["bhp"]]

    # mass balance:  stored volume change = net volume in
    path = model.path
    stored = (path.v_d * (ts["spp"] - start.spp) + path.v_a * (ts["p_c"] - start.p_c)) / case.beta_eff
    residual = stored - ts["net_inflow_m3"]
    circulated = np.trapezoid(ts["q_pump"] + ts["q_bpp"], ts["time_s"])
    ts["mass_residual_m3"] = residual
    mass = {"max_residual_m3": float(residual.abs().max()),
            "circulated_m3": float(circulated),
            "relative_percent": float(residual.abs().max() / max(circulated, path.v_d + path.v_a) * 100)}
    if failures:
        warnings.append(f"Solver reported {failures} failed interval(s).")

    events = scenario.get("events_s") or [0.0]
    return SimulationResult(
        name=name, title=scenario.get("title", name), controller=controller, timeseries=ts,
        metrics=performance_metrics(ts, events[0], case.criteria), mass_balance=mass,
        solver={"method": method, "rtol": case.solver.rtol, "atol": case.solver.atol,
                "max_step_s": max_step, "sample_time_s": ts_s, "rhs_evaluations": n_rhs,
                "failed_intervals": failures},
        initial=start, warnings=warnings)


def check_convergence(case, seg, scenario, result, progress=None, cancel=None):
    """
    Numerical convergence (proposal 4-2-5): repeat the run with half the
    integration step and compare the BHP RMSE (relative to the target).

        change [%] = |RMSE_h - RMSE_h/2| / max(RMSE_h/2, 0.1 % of target) x 100

    The floor of 0.1 % of the target avoids dividing by ~0 when BHP stays on target.
    """
    half = simulate(case, seg, scenario, result.controller, result.name,
                    max_step=result.solver["max_step_s"] / 2, method=result.solver["method"],
                    progress=progress, cancel=cancel)
    rmse_h, rmse_half = result.metrics["rmse"], half.metrics["rmse"]
    floor = 1e-3 * abs(case.target)
    change = abs(rmse_h - rmse_half) / max(rmse_half, floor) * 100
    diff = np.abs(result.timeseries["bhp"].values - half.timeseries["bhp"].values).max()
    result.convergence = {"rmse_h": rmse_h, "rmse_half": rmse_half, "change_percent": change,
                          "max_bhp_difference": float(diff), "limit_percent": case.solver.convergence_limit,
                          "passed": change < case.solver.convergence_limit}
    if not result.convergence["passed"]:
        result.warnings.append(f"Convergence warning: BHP RMSE changed {change:.3f} % when the step "
                               f"was halved (limit {case.solver.convergence_limit} %).")
    return result.convergence


def steady_state_residual(case, seg):
    """
    Level-1 check: at the steady operating point all time derivatives of the
    model must be ~0 ("setting the derivatives to zero gives the steady
    hydraulics"). Returns each derivative scaled by its natural size per second.
    """
    start = solve_steady(case, seg)
    model = FourStateModel(case, seg, lambda t: case.q_pump, lambda t: case.q_bpp)
    d = model.rhs(0.0, [start.spp, start.p_c, start.q, start.z, 0.0], start.z)
    return {"dp_p/dt / SPP": d[0] / start.spp, "dp_c/dt / p_c": d[1] / max(start.p_c, 1.0),
            "dq/dt / q": d[2] / max(start.q, 1e-9), "dz/dt": d[3]}


def depth_profiles(case, seg, ts, times):
    """Annulus and string pressure profiles at chosen times (from q and p_c of the run)."""
    geo = flow_geometry(seg)
    frames = []
    for t in times:
        row = ts.iloc[int(np.argmin(np.abs(ts["time_s"].values - t)))]
        loss = pressure_losses(case, geo, row["q"])
        prof = pressure_profile(case, seg, loss, row["p_c"], row["spp"])
        prof.insert(0, "time_s", row["time_s"])
        frames.append(prof)
    return pd.concat(frames, ignore_index=True)
