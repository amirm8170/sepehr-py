"""
Stage A - steady-state well hydraulics with the Bingham Plastic model.

Flow path:  pump -> drill pipe -> BHA -> bit nozzles -> annulus -> choke

Sign / reference convention (all pressures are gauge, atmosphere = 0):
  * SBP  = annulus pressure at the surface, just before the choke
  * BHP  = annulus pressure at the bottom of the well (TD)
           BHP = SBP + rho*g*TVD + annular friction
  * SPP  = SBP + annular friction + bit drop + BHA friction + drill-pipe friction
           (the hydrostatic columns inside and outside the pipe cancel)
  * ECD  = rho + annular friction / (g*TVD)        (conventional, without SBP)
  * EMW  = BHP / (g*TVD)                            (includes SBP; often called ECD in MPD)
"""

import numpy as np
import pandas as pd

from . import units as u

# ----------------------------------------------------------------------
# Geometry
# ----------------------------------------------------------------------
def pipe_area(d):
    """Flow area inside a pipe:  A = pi D^2 / 4   [m2]"""
    return np.pi * np.asarray(d) ** 2 / 4


def annulus_area(d_hole, d_pipe):
    """Flow area of the annulus:  A = pi/4 (Dhole^2 - Dpipe^2)   [m2]"""
    return np.pi / 4 * (np.asarray(d_hole) ** 2 - np.asarray(d_pipe) ** 2)


def hydraulic_diameter(d_hole, d_pipe):
    """Hydraulic diameter of a concentric annulus:  Dh = Dhole - Dpipe   [m]"""
    return np.asarray(d_hole) - np.asarray(d_pipe)


def hydrostatic(rho, tvd):
    """Hydrostatic pressure:  P = rho * g * TVD   [Pa]  (uses TVD, never MD)"""
    return rho * u.G * np.asarray(tvd)


# ----------------------------------------------------------------------
# Bingham Plastic fluid:  tau = YP + PV * shear_rate
# ----------------------------------------------------------------------
def reynolds(rho, v, d, pv):
    """Reynolds number with plastic viscosity:  Re = rho |v| D / PV"""
    return rho * np.abs(v) * d / pv


def hedstrom(rho, yp, d, pv):
    """Hedstrom number:  He = rho YP D^2 / PV^2   (measures the effect of the yield point)"""
    return rho * yp * np.asarray(d) ** 2 / pv ** 2


def critical_reynolds(he):
    """
    Laminar -> turbulent limit for a Bingham fluid (Hanks, 1963):

        x / (1-x)^3 = He / 16800
        Re_c = He / (8x) * (1 - 4/3 x + x^4/3)

    For a Newtonian fluid (He = 0) this gives Re_c = 2100.
    x is found by bisection (x is between 0 and 1).
    """
    he = np.atleast_1d(np.asarray(he, dtype=float))
    lo, hi = np.zeros_like(he), np.ones_like(he)
    for _ in range(60):
        x = (lo + hi) / 2
        too_big = x / (1 - x) ** 3 > he / 16800
        hi = np.where(too_big, x, hi)
        lo = np.where(too_big, lo, x)
    x = np.maximum((lo + hi) / 2, 1e-12)
    re_c = he / (8 * x) * (1 - 4 / 3 * x + x ** 4 / 3)
    return np.where(he > 1e-6, re_c, 2100.0)


def laminar_wall_stress(v, d, pv, yp, shape):
    """
    Wall shear stress tau_w [Pa] in LAMINAR Bingham flow (exact solutions).

      pipe     (Buckingham-Reiner):  8 PV v / D  = tau_w (1 - 4/3 r + 1/3 r^4)
      annulus  (narrow slot):       12 PV v / Dh = tau_w (1 - 3/2 r + 1/2 r^3)
      with r = YP / tau_w

    Solved for tau_w with Newton's method. Start value = the linear
    (oilfield) approximation, from which Newton converges safely.
    """
    v = np.abs(np.asarray(v, dtype=float))
    c, k = (8.0, 4 / 3) if shape == "pipe" else (12.0, 1.5)
    viscous = c * pv * v / d
    tau = viscous + k * yp
    if yp > 0:
        for _ in range(50):
            r = np.clip(yp / tau, 0, 1)
            if shape == "pipe":
                g = tau * (1 - 4 / 3 * r + r ** 4 / 3) - viscous
                dg = 1 - r ** 4                                  # d(g)/d(tau)
            else:
                g = tau * (1 - 1.5 * r + 0.5 * r ** 3) - viscous
                dg = 1 - r ** 3
            step = g / np.maximum(dg, 1e-12)
            tau = np.maximum(tau - step, yp)
            if np.all(np.abs(step) < 1e-8 * np.maximum(tau, 1)):
                break
    return np.where(v > 0, tau, 0.0)


def friction(rho, pv, yp, v, d, shape):
    """
    Frictional pressure gradient for a list of segments.

    Steps (Bourgoyne et al., Applied Drilling Engineering, 1986):
      1. Reynolds and Hedstrom numbers, critical Reynolds (Hanks)
      2. laminar  -> exact Bingham wall stress  ->  f = 2 tau_w / (rho v^2)
         turbulent-> Blasius  f = 0.0791 Re^-0.25  (annulus uses De = 0.816 Dh)
         (in turbulent flow the larger of the two values is used, so the
          pressure loss never jumps down at the transition)
      3. dP/dL = 2 f rho v^2 / D       (Fanning friction factor f)

    The pressure loss has the same sign as the velocity.
    Returns a dict of arrays: re, he, re_c, regime, f, gradient [Pa/m].
    """
    v = np.asarray(v, dtype=float)
    d = np.broadcast_to(np.asarray(d, dtype=float), v.shape)
    speed = np.abs(v)
    moving = speed > 1e-9

    re = reynolds(rho, speed, d, pv)
    he = hedstrom(rho, yp, d, pv)
    re_c = critical_reynolds(he).reshape(v.shape)

    with np.errstate(divide="ignore", invalid="ignore"):
        f_lam = 2 * laminar_wall_stress(speed, d, pv, yp, shape) / (rho * speed ** 2)
        factor = 1.0 if shape == "pipe" else 0.816
        f_turb = 0.0791 * (re * factor) ** -0.25 / factor
    turbulent = moving & (re >= re_c)
    f = np.where(turbulent, np.fmax(f_lam, f_turb), f_lam)
    f = np.where(moving, f, np.nan)

    gradient = np.where(moving, np.sign(v) * 2 * np.nan_to_num(f) * rho * speed ** 2 / d, 0.0)
    regime = np.where(~moving, "static", np.where(turbulent, "turbulent", "laminar"))
    return {"re": re, "he": he, "re_c": re_c, "regime": regime, "f": f, "gradient": gradient}


# ----------------------------------------------------------------------
# Bit
# ----------------------------------------------------------------------
def bit_pressure_drop(rho, q, nozzles, cd):
    """
    Pressure drop across the bit nozzles:

        TFA = sum(pi d^2 / 4),   v_nozzle = Q / TFA,   dP = rho v^2 / (2 Cd^2)
    """
    tfa = np.sum(np.pi * np.asarray(nozzles) ** 2 / 4)
    v = q / tfa
    return rho * v * abs(v) / (2 * cd ** 2)


# ----------------------------------------------------------------------
# Choke:  Q = K(u) * sqrt(dP / rho)
# ----------------------------------------------------------------------
CV_TO_SI = u.GPM * np.sqrt(1000 / u.PSI)    # US Cv [gpm/sqrt(psi)] -> K [m2]
LINEAR_BELOW = 1.0e4                         # [Pa] below this dP the curve is made linear (numerics)


def choke_k(opening, choke):
    """Flow coefficient of the choke for an opening 0..1 (linear Cv curve), in SI [m2]."""
    opening = min(max(opening, choke["min_opening"]), choke["max_opening"])
    cv = choke["cv_closed"] + (choke["cv_max"] - choke["cv_closed"]) * opening
    return cv * CV_TO_SI


def choke_pressure_drop(q, opening, rho, choke):
    """Pressure drop needed to push flow q [m3/s] through the choke:  dP = rho (Q/K)^2"""
    k = choke_k(opening, choke)
    q = max(q, 0.0)
    q_linear = k * np.sqrt(LINEAR_BELOW / rho)
    if q < q_linear:
        return LINEAR_BELOW * q / q_linear
    return rho * (q / k) ** 2


def choke_flow(dp, opening, rho, choke):
    """Flow through the choke for a pressure drop dp [Pa] (no reverse flow)."""
    k = choke_k(opening, choke)
    dp = max(dp, 0.0)
    if dp < LINEAR_BELOW:
        return k * np.sqrt(LINEAR_BELOW / rho) * dp / LINEAR_BELOW
    return k * np.sqrt(dp / rho)


def choke_opening_for(q, dp, rho, choke):
    """Opening that gives pressure drop dp at flow q (found by bisection)."""
    lo, hi = choke["min_opening"], choke["max_opening"]
    if choke_pressure_drop(q, hi, rho, choke) >= dp:
        return hi
    if choke_pressure_drop(q, lo, rho, choke) <= dp:
        return lo
    for _ in range(50):
        mid = (lo + hi) / 2
        if choke_pressure_drop(q, mid, rho, choke) > dp:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ----------------------------------------------------------------------
# Friction in the whole well
# ----------------------------------------------------------------------
def annulus_velocity(case, seg, q, v_pipe):
    """
    Effective upward velocity in each annulus segment.

    Pipe moving down (v_pipe > 0) pushes mud up the annulus (surge);
    moving up pulls it down (swab). Simplified Burkhardt (1961) model:

        v = (Q + v_pipe * A_pipe) / A_annulus + K_c * v_pipe
    """
    a_ann = annulus_area(seg["hole_d"].values, seg["pipe_od"].values)
    a_pipe = pipe_area(seg["pipe_od"].values)       # closed-end pipe
    return (q + v_pipe * a_pipe) / a_ann + case.clinging * v_pipe


def friction_losses(case, seg, q, v_pipe=0.0):
    """
    Frictional pressure losses for pump rate q [m3/s] and pipe velocity [m/s].

    Returns a dict with per-segment arrays and the bit pressure drop.
    """
    v_string = q / pipe_area(seg["pipe_id"].values)
    v_annulus = annulus_velocity(case, seg, q, v_pipe)
    fs = friction(case.rho, case.pv, case.yp, v_string, seg["pipe_id"].values, "pipe")
    fa = friction(case.rho, case.pv, case.yp, v_annulus,
                  hydraulic_diameter(seg["hole_d"].values, seg["pipe_od"].values), "annulus")
    return {"string": fs, "annulus": fa, "v_string": v_string, "v_annulus": v_annulus,
            "dp_string": fs["gradient"] * seg["length"].values,
            "dp_annulus": fa["gradient"] * seg["length"].values,
            "dp_bit": bit_pressure_drop(case.rho, q, case.nozzles, case.cd)}


# ----------------------------------------------------------------------
# Steady state
# ----------------------------------------------------------------------
def surface_back_pressure(case, seg, q, annulus_friction, opening=None, bp_pump=None, v_pipe=0.0):
    """
    Surface back pressure (SBP) and choke opening.

      fixed mode : SBP = the given back pressure
      choke mode : SBP = choke pressure drop for the flow through the choke
                   (pump + back-pressure pump + pipe displacement).
                   If the opening is "auto", the opening that gives the
                   target BHP is calculated.
    """
    if case.bp_mode == "fixed":
        return case.back_pressure, None
    bp_pump = case.bp_pump_rate if bp_pump is None else bp_pump
    q_choke = max(q + bp_pump + v_pipe * pipe_area(case.string[0]["od"]), 0.0)
    opening = case.choke_opening if opening is None else opening
    if str(opening) == "auto":
        needed = case.target - hydrostatic(case.rho, case.td_tvd) - annulus_friction
        opening = choke_opening_for(q_choke, max(needed, 0.0), case.rho, case.choke)
    return choke_pressure_drop(q_choke, float(opening), case.rho, case.choke), float(opening)


def window_status(bhp, case):
    """'kick risk' if BHP < pore pressure, 'loss risk' if BHP > fracture pressure, else 'safe'."""
    if bhp < case.pore:
        return "kick risk"
    if bhp > case.frac:
        return "loss risk"
    return "safe"


def solve_steady(case, seg, q=None, sbp=None, opening=None, v_pipe=None):
    """
    Solve the steady-state hydraulics.

    q, sbp, opening, v_pipe override the values of the case (used by the
    dynamic model and the sensitivity analysis). Returns a dict of results
    plus two tables: 'segments' (one row per segment) and 'profile'
    (pressure vs depth for the drill string and the annulus).
    """
    q = case.pump_rate if q is None else q
    v_pipe = case.pipe_velocity if v_pipe is None else v_pipe
    fl = friction_losses(case, seg, q, v_pipe)
    annulus_dp = fl["dp_annulus"].sum()
    if sbp is None:
        sbp, opening = surface_back_pressure(case, seg, q, annulus_dp, opening, v_pipe=v_pipe)

    is_bha = (seg["pipe_type"] == "bha").values
    hydro_td = hydrostatic(case.rho, case.td_tvd)
    r = {
        "q": q, "sbp": sbp, "opening": opening,
        "dp_drill_pipe": fl["dp_string"][~is_bha].sum(),
        "dp_bha": fl["dp_string"][is_bha].sum(),
        "dp_bit": fl["dp_bit"],
        "dp_annulus": annulus_dp,
        "hydrostatic": hydro_td,
    }
    r["dp_total"] = r["dp_drill_pipe"] + r["dp_bha"] + r["dp_bit"] + r["dp_annulus"]
    r["spp"] = sbp + r["dp_total"]
    r["bhp"] = sbp + hydro_td + annulus_dp
    r["bhp_static"] = hydro_td
    r["ecd"] = case.rho + annulus_dp / (u.G * case.td_tvd)
    r["emw"] = r["bhp"] / (u.G * case.td_tvd)
    r["status"] = window_status(r["bhp"], case)
    # pressure needed to break the gel when starting circulation:  4 * gel * L / D
    r["gel_break"] = (4 * case.gel * seg["length"] / seg["pipe_id"]).sum() + \
        (4 * case.gel * seg["length"] / (seg["hole_d"] - seg["pipe_od"])).sum()
    r["segments"] = segment_table(seg, fl, q)
    r["profile"] = pressure_profile(case, seg, fl, sbp, r["spp"])
    return r


def segment_table(seg, fl, q):
    """One row per segment: geometry + velocity, Re, He, regime, friction factor, losses."""
    t = seg.copy()
    for path, key in (("string", "string"), ("annulus", "annulus")):
        f = fl[key]
        t[f"{path}_velocity"] = fl[f"v_{key}"]
        t[f"{path}_re"] = f["re"]
        t[f"{path}_he"] = f["he"]
        t[f"{path}_re_critical"] = f["re_c"]
        t[f"{path}_regime"] = f["regime"]
        t[f"{path}_friction_factor"] = f["f"]
        t[f"{path}_dp"] = fl[f"dp_{key}"]
    return t


def pressure_profile(case, seg, fl, sbp, spp):
    """
    Pressure at every segment border.

      annulus : P(z) = SBP + rho g TVD(z) + friction of the annulus above z
      string  : P(z) = SPP + rho g TVD(z) - friction of the string above z
    """
    md = np.append(seg["md_top"].values, seg["md_bottom"].values[-1])
    tvd = np.append(seg["tvd_top"].values, seg["tvd_bottom"].values[-1])
    hydro = hydrostatic(case.rho, tvd)
    f_ann = np.concatenate([[0], np.cumsum(fl["dp_annulus"])])
    f_str = np.concatenate([[0], np.cumsum(fl["dp_string"])])
    rows = []
    for i in range(len(md)):
        rows.append(("annulus", md[i], tvd[i], sbp + hydro[i] + f_ann[i], hydro[i], f_ann[i]))
        rows.append(("string", md[i], tvd[i], spp + hydro[i] - f_str[i], hydro[i], -f_str[i]))
    if case.td > case.bit_depth + 1e-6:        # open hole below the bit: no flow
        rows.append(("annulus", case.td, case.td_tvd,
                     sbp + hydrostatic(case.rho, case.td_tvd) + f_ann[-1],
                     hydrostatic(case.rho, case.td_tvd), f_ann[-1]))
    return pd.DataFrame(rows, columns=["path", "md", "tvd", "pressure", "hydrostatic", "friction"])
