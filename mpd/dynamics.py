"""
Stage B - dynamic (time) simulation of the well and closed-loop BHP control.

Dynamic model (simplified MPD model, as used for controller design in
Kaasa et al. 2012 and Godhavn 2010):

    The mud in the annulus is slightly compressible. The choke pressure p_c
    changes when the flow into the annulus differs from the flow out
    through the choke:

        dp_c/dt = (bulk_modulus / V_annulus) * (Q_in - Q_choke)

        Q_in     = pump rate + back-pressure pump + pipe displacement
        Q_choke  = K(opening) * sqrt(p_c / rho)

    Friction is taken from the steady-state model for the current flow
    (quasi-steady friction), so:

        BHP = p_c + rho g TVD + annular friction
        SPP = p_c + all friction losses

    "quasi_steady" model: no compressibility - p_c is found directly from the
    choke equation at every time step.
    "back_pressure" actuator: the surface back pressure follows its set-point
    with a first-order delay (time constant tau).

Assumptions: single-phase mud, flow along the drill string is incompressible
(pump changes reach the bit immediately), no influx or losses.
"""

import math

import numpy as np
import pandas as pd

from . import units as u
from .analysis import control_metrics
from .hydraulics import (annulus_area, choke_flow, choke_pressure_drop, friction_losses,
                         hydrostatic, pipe_area, solve_steady, window_status)


# ----------------------------------------------------------------------
# PID controller
# ----------------------------------------------------------------------
class PID:
    """
    Discrete PID controller.

        error  = (target - measured)            (divided by `scale`, e.g. 1 bar)
        output = bias + Kp*error + Ki*integral(error) + Kd*d(error)/dt

    Extras that a real controller needs:
      * output limits (e.g. choke 2 % ... 100 % open)
      * rate limit   (the choke cannot move faster than its motor)
      * anti-windup  (the integral stops growing while the output is at a limit)
      * derivative on the measurement + filter (no jump when the target changes)
      * reverse action (closing the choke RAISES the pressure)
    """

    def __init__(self, kp, ki, kd, out_min, out_max, rate_limit=None, reverse=False,
                 scale=1.0, filter_time=0.0):
        self.kp, self.ki, self.kd = kp, ki, kd
        self.reverse = reverse
        self.scale = scale
        self.filter_time = filter_time
        self.set_limits(out_min, out_max, rate_limit)
        self.reset(0.0)

    def set_limits(self, out_min, out_max, rate_limit=None):
        self.out_min, self.out_max, self.rate_limit = out_min, out_max, rate_limit

    def reset(self, bias):
        """Start again from output = bias (no jump when the controller is switched on)."""
        self.bias = bias
        self.integral = 0.0
        self.last_measurement = None
        self.derivative = 0.0
        self.output = bias
        self.p_term = self.i_term = self.d_term = 0.0

    def update(self, target, measured, dt):
        sign = -1.0 if self.reverse else 1.0
        error = sign * (target - measured) / self.scale

        # derivative of the measurement, smoothed by a first-order filter
        if self.last_measurement is not None:
            raw = -sign * (measured - self.last_measurement) / self.scale / dt
            a = self.filter_time / (self.filter_time + dt)
            self.derivative = a * self.derivative + (1 - a) * raw
        self.last_measurement = measured

        new_integral = self.integral + error * dt
        wanted = self.bias + self.kp * error + self.ki * new_integral + self.kd * self.derivative

        # limits
        out = min(max(wanted, self.out_min), self.out_max)
        if self.rate_limit:
            step = self.rate_limit * dt
            out = min(max(out, self.output - step), self.output + step)

        # anti-windup: only integrate if we are not pushing further into a limit
        limited = abs(out - wanted) > 1e-12
        if not (limited and np.sign(error) == np.sign(wanted - out)):
            self.integral = new_integral

        self.p_term = self.kp * error
        self.i_term = self.ki * self.integral
        self.d_term = self.kd * self.derivative
        self.output = out
        return out


# ----------------------------------------------------------------------
# Schedules
# ----------------------------------------------------------------------
def schedule_value(points, t, default):
    """Value of a schedule [[time, value], ...] at time t (linear between points)."""
    if not points:
        return default
    times = [p[0] for p in points]
    values = [p[1] for p in points]
    return float(np.interp(t, times, values))


# ----------------------------------------------------------------------
# Simulation
# ----------------------------------------------------------------------
def simulate(case, seg, scenario, progress=None):
    """
    Run one dynamic scenario.

    case      : Case (SI units)
    seg       : segment table
    scenario  : dict from scenarios.yaml
    progress  : optional function(fraction) to show progress

    Returns a dict: 'timeseries' (DataFrame, one row per time step),
    'profiles' (pressure vs depth every few seconds), 'metrics', 'events'.
    """
    dt = case.dyn["dt"]
    duration = float(scenario["duration_s"])
    model = scenario.get("model", case.dyn["model"])
    actuator = scenario.get("actuator", case.dyn["actuator"])
    use_pid = bool(scenario.get("pid", False))

    # --- starting point = steady state of the case -----------------------
    start = solve_steady(case, seg, q=schedule_value(scenario.get("pump_gpm"), 0, None) * u.GPM
                         if scenario.get("pump_gpm") else None,
                         opening=scenario.get("start_choke_opening"))
    opening0 = start["opening"] if start["opening"] is not None else 0.5
    p_choke = start["sbp"]
    if actuator == "back_pressure" and scenario.get("back_pressure_psi"):
        p_choke = schedule_value(scenario["back_pressure_psi"], 0, 0) * u.PSI
    v_annulus_total = float((annulus_area(seg["hole_d"], seg["pipe_od"]) * seg["length"]).sum())
    hydro = hydrostatic(case.rho, case.td_tvd)
    a_disp = pipe_area(case.string[0]["od"])        # pipe area entering the well at surface

    # --- controller --------------------------------------------------------
    pid = None
    if use_pid:
        if actuator == "choke":
            pid = PID(case.pid["kp"], case.pid["ki"], case.pid["kd"],
                      case.choke["min_opening"], case.choke["max_opening"],
                      rate_limit=case.choke["max_speed_per_s"], reverse=True, scale=u.BAR,
                      filter_time=case.pid["derivative_filter_s"])
            pid.reset(opening0)
        else:
            pid = PID(case.pid["bp_kp"] * u.BAR, case.pid["bp_ki"] * u.BAR, 0.0,
                      0.0, case.dyn["bp_max"], scale=u.BAR)
            pid.reset(p_choke)

    def target_at(t):
        ppg_points = scenario.get("target_ppg")
        if ppg_points:
            return u.emw_to_pressure(schedule_value(ppg_points, t, 0), case.td_tvd)
        return case.target

    friction_cache = {}

    def friction_at(q, v_pipe):
        """
        Friction losses for flow q and pipe speed (cached: many steps repeat).

        A Bingham fluid needs the yield-point pressure even at a tiny flow, so
        friction would jump to zero in one step when the pumps stop. Below
        `low_flow` the friction is therefore reduced linearly to zero
        (smoothing; in reality the mud gels and the change is gradual).
        """
        low_flow = case.low_flow
        scale = 1.0
        if 0 < abs(q) < low_flow and abs(v_pipe) < 1e-9:
            scale, q = abs(q) / low_flow, low_flow * np.sign(q)
        key = (round(q, 7), round(v_pipe, 4))
        if key not in friction_cache:
            fl = friction_losses(case, seg, q, v_pipe)
            friction_cache[key] = (fl["dp_annulus"].sum(),
                                   fl["dp_string"].sum() + fl["dp_bit"])
        return tuple(scale * f for f in friction_cache[key])

    rows, profiles, events = [], [], []
    opening = opening0
    sbp_command = p_choke
    last_status, next_profile = None, 0.0
    bhp = start["bhp"]
    n_steps = int(round(duration / dt))

    for k in range(n_steps + 1):
        t = k * dt
        # 1. operating conditions from the schedules
        q = max(schedule_value(scenario.get("pump_gpm"), t, case.pump_rate / u.GPM), 0) * u.GPM
        q_bpp = max(schedule_value(scenario.get("bp_pump_gpm"), t, case.bp_pump_rate / u.GPM), 0) * u.GPM
        v_pipe = schedule_value(scenario.get("pipe_velocity_m_s"), t, case.pipe_velocity)
        target = target_at(t)

        # 2. actuator: PID or schedule (with the choke speed limit)
        if actuator == "choke":
            if pid and k > 0:
                opening = pid.update(target, bhp, dt)
            else:
                wanted = opening0 + schedule_value(scenario.get("choke_change"), t, 0.0)
                max_step = case.choke["max_speed_per_s"] * dt
                opening = min(max(wanted, opening - max_step), opening + max_step)
                opening = min(max(opening, case.choke["min_opening"]), case.choke["max_opening"])
        else:
            if pid and k > 0:
                sbp_command = pid.update(target, bhp, dt)
            else:
                sbp_command = schedule_value(scenario.get("back_pressure_psi"), t,
                                             start["sbp"] / u.PSI) * u.PSI

        # 3. hydraulics
        f_annulus, f_inside = friction_at(q, v_pipe)
        q_in = max(q + q_bpp + v_pipe * a_disp, 0.0)
        if k > 0:
            p_choke = update_choke_pressure(p_choke, q_in, opening, sbp_command, dt, case,
                                            v_annulus_total, model, actuator)
        q_choke = choke_flow(p_choke, opening, case.rho, case.choke) if actuator == "choke" else q_in
        bhp = p_choke + hydro + f_annulus
        spp = p_choke + f_annulus + f_inside
        status = window_status(bhp, case)

        # 4. store results
        rows.append({
            "time_s": t, "pump_rate": q, "bp_pump_rate": q_bpp, "pipe_velocity": v_pipe,
            "choke_opening": opening if actuator == "choke" else np.nan,
            "sbp_command": sbp_command if actuator == "back_pressure" else np.nan,
            "choke_flow": q_choke, "sbp": p_choke, "spp": spp, "bhp": bhp,
            "annulus_friction": f_annulus,
            "ecd": case.rho + f_annulus / (u.G * case.td_tvd),
            "emw": bhp / (u.G * case.td_tvd),
            "target": target, "pore": case.pore, "fracture": case.frac,
            "status": status, "error": target - bhp,
            "pid_p": pid.p_term if pid else np.nan, "pid_i": pid.i_term if pid else np.nan,
            "pid_d": pid.d_term if pid else np.nan,
        })
        if status != last_status and last_status is not None:
            events.append(f"t = {t:.1f} s: window status {last_status} -> {status}")
        last_status = status
        if t >= next_profile - 1e-9:
            prof = solve_steady(case, seg, q=q, sbp=p_choke, v_pipe=v_pipe)["profile"]
            prof.insert(0, "time_s", t)
            profiles.append(prof)
            next_profile += case.dyn["profile_every"]
        if progress and k % max(1, n_steps // 50) == 0:
            progress(k / n_steps)

    ts = pd.DataFrame(rows)
    events_s = scenario.get("events_s", [0])
    metrics = control_metrics(ts, case.pid["settling_band_psi"] * u.PSI, events_s)
    return {"timeseries": ts, "profiles": pd.concat(profiles, ignore_index=True),
            "metrics": metrics, "events": events, "actuator": actuator, "pid": use_pid,
            "title": scenario.get("title", ""), "description": scenario.get("description", "")}


def update_choke_pressure(p_choke, q_in, opening, sbp_command, dt, case, volume, model, actuator):
    """Advance the surface (choke) pressure by one time step."""
    if actuator == "back_pressure":
        # first-order actuator:  dp/dt = (command - p) / tau  (exact solution over dt)
        return sbp_command + (p_choke - sbp_command) * math.exp(-dt / case.dyn["bp_tau"])
    if model == "quasi_steady":
        return choke_pressure_drop(q_in, opening, case.rho, case.choke)
    # lumped model: dp/dt = beta/V (Q_in - Q_choke), 20 small Euler steps for stability
    beta = case.dyn["bulk_modulus"]
    h = dt / 20
    for _ in range(20):
        dp = beta / volume * (q_in - choke_flow(p_choke, opening, case.rho, case.choke)) * h
        p_choke = max(p_choke + dp, 0.0)
    return p_choke
