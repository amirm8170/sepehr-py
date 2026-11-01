"""
Steady-state well hydraulics with the Bingham Plastic model (proposal 4-2-4).

Flow path:  pump -> drill string -> bit nozzles -> annulus -> choke
(the back-pressure pump, BPP, adds a known flow at the top of the annulus).

Pressure convention (all gauge, atmosphere = 0; dP > 0 = loss in the flow direction):
  p_c  = choke (upstream annulus) pressure at the surface
  BHP  = p_c + rho_a g h + dP_a                          (at TD)
  SPP  = p_c + dP_d + dP_bit + (rho_a - rho_d) g h + dP_a
         (one mud in string and annulus -> rho_a = rho_d, the middle term is 0)
  ECD  = BHP / (g h)                    proposal definition (contains p_c)
  ECD without back pressure = rho + dP_a / (g h)   (conventional drilling)

Friction models:
  api_rp13d     (DEFAULT, proposal) Bourgoyne / API RP 13D field correlations
  exact_bingham (comparison only)   exact laminar Bingham solutions
                                    (Buckingham-Reiner pipe, narrow-slot annulus),
                                    Hanks critical Reynolds number, Blasius turbulence
Two numerical regularisations keep the dynamic model continuous (both configurable):
  * each loss is multiplied by tanh(q / q_eps) so it goes smoothly to zero at
    q = 0 (the yield-stress term would otherwise jump)          (proposal 4-2-4)
  * laminar -> turbulent goes linearly over v_c ... (1 + width) v_c instead of
    jumping at v_c (a jump traps the flow exactly at v_c)     (added, see README)
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import units as u
from .choke import choke_pressure, opening_for

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
# Friction model 1 (DEFAULT): Bourgoyne / API RP 13D field correlations
# ----------------------------------------------------------------------
API = {   # shape: (critical-velocity constant, laminar viscous, laminar yield, turbulent)
    "pipe": (8.2, 1500.0, 225.0, 1800.0),
    "annulus": (6.2, 1000.0, 200.0, 1396.0),
}


def api_critical_velocity(rho, mu_p, tau_y, d, shape):
    """
    Critical velocity [m/s] (proposal 4-2-4, field units: ppg, cP, lbf/100ft2, in):

        pipe     v_c = (97 mu_p + 97 sqrt(mu_p^2 + 8.2 rho d^2  tau_y)) / (rho d)    [ft/min]
        annulus  v_c = (97 mu_p + 97 sqrt(mu_p^2 + 6.2 rho dd^2 tau_y)) / (rho dd)   [ft/min]
    """
    c = API[shape][0]
    rho_f, mu_f, ty_f, d_f = rho / u.PPG, mu_p / u.CP, tau_y / u.LBF_100FT2, np.asarray(d) / u.INCH
    v_ft_min = (97 * mu_f + 97 * np.sqrt(mu_f ** 2 + c * rho_f * d_f ** 2 * ty_f)) / (rho_f * d_f)
    return v_ft_min * u.FT / 60


def api_laminar(rho, mu_p, tau_y, v, d, shape):
    """
    Laminar gradient [Pa/m] (Bourgoyne et al. 1986, field units, v in ft/s, result psi/ft):
        pipe     dP/dL = mu_p v / (1500 d^2)  + tau_y / (225 d)
        annulus  dP/dL = mu_p v / (1000 dd^2) + tau_y / (200 dd)
    """
    _, lam_v, lam_y, _ = API[shape]
    mu_f, ty_f, d_f, v_f = mu_p / u.CP, tau_y / u.LBF_100FT2, np.asarray(d) / u.INCH, np.asarray(v) / u.FT
    return (mu_f * v_f / (lam_v * d_f ** 2) + ty_f / (lam_y * d_f)) * u.PSI / u.FT


def api_turbulent(rho, mu_p, tau_y, v, d, shape):
    """
    Turbulent gradient [Pa/m] (Bourgoyne et al. 1986, field units, v in ft/s, result psi/ft):
        pipe     dP/dL = rho^0.75 v^1.75 mu_p^0.25 / (1800 d^1.25)
        annulus  dP/dL = rho^0.75 v^1.75 mu_p^0.25 / (1396 dd^1.25)
    """
    turb = API[shape][3]
    rho_f, mu_f, d_f, v_f = rho / u.PPG, mu_p / u.CP, np.asarray(d) / u.INCH, np.asarray(v) / u.FT
    return rho_f ** 0.75 * v_f ** 1.75 * mu_f ** 0.25 / (turb * d_f ** 1.25) * u.PSI / u.FT


# ----------------------------------------------------------------------
# Friction model 2 (comparison only): exact laminar Bingham solutions
# ----------------------------------------------------------------------
def hedstrom(rho, tau_y, d, mu_p):
    """Hedstrom number:  He = rho tau_y D^2 / mu_p^2"""
    return rho * tau_y * np.asarray(d) ** 2 / mu_p ** 2


def critical_reynolds(he):
    """
    Laminar -> turbulent limit for a Bingham fluid (Hanks, 1963):

        x / (1-x)^3 = He / 16800
        Re_c = He / (8x) * (1 - 4/3 x + x^4/3)          (Re_c = 2100 when He = 0)
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


def laminar_wall_stress(v, d, mu_p, tau_y, shape):
    """
    Wall shear stress tau_w [Pa] in LAMINAR Bingham flow (exact solutions).

      pipe     (Buckingham-Reiner):  8 mu_p v / D  = tau_w (1 - 4/3 r + 1/3 r^4)
      annulus  (narrow slot):       12 mu_p v / Dh = tau_w (1 - 3/2 r + 1/2 r^3)
      with r = tau_y / tau_w, solved with Newton's method.
    """
    v = np.abs(np.asarray(v, dtype=float))
    c, k = (8.0, 4 / 3) if shape == "pipe" else (12.0, 1.5)
    viscous = c * mu_p * v / d
    tau = viscous + k * tau_y
    if tau_y > 0:
        for _ in range(50):
            r = np.clip(tau_y / tau, 0, 1)
            if shape == "pipe":
                g = tau * (1 - 4 / 3 * r + r ** 4 / 3) - viscous
                dg = 1 - r ** 4
            else:
                g = tau * (1 - 1.5 * r + 0.5 * r ** 3) - viscous
                dg = 1 - r ** 3
            step = g / np.maximum(dg, 1e-12)
            tau = np.maximum(tau - step, tau_y)
            if np.all(np.abs(step) < 1e-8 * np.maximum(tau, 1)):
                break
    return np.where(v > 0, tau, tau_y * k)


def exact_critical_velocity(rho, mu_p, tau_y, d, shape):
    """v_c = Re_c mu_p / (rho D) with the Hanks critical Reynolds number [m/s]."""
    d = np.asarray(d, dtype=float)
    return critical_reynolds(hedstrom(rho, tau_y, d, mu_p)).reshape(d.shape) * mu_p / (rho * d)


def exact_laminar(rho, mu_p, tau_y, v, d, shape):
    """Laminar gradient [Pa/m] from the exact wall stress:  dP/dL = 4 tau_w / D"""
    return 4 * laminar_wall_stress(v, d, mu_p, tau_y, shape) / np.asarray(d)


def exact_turbulent(rho, mu_p, tau_y, v, d, shape):
    """Blasius:  f = 0.0791 Re^-0.25,  dP/dL = 2 f rho v^2 / D  (annulus: De = 0.816 Dh)"""
    v, d = np.asarray(v, dtype=float), np.asarray(d, dtype=float)
    factor = 1.0 if shape == "pipe" else 0.816
    re = np.maximum(rho * v * d * factor / mu_p, 1e-12)
    return 2 * 0.0791 * re ** -0.25 / factor * rho * v ** 2 / d


MODELS = {"api_rp13d": (api_critical_velocity, api_laminar, api_turbulent),
          "exact_bingham": (exact_critical_velocity, exact_laminar, exact_turbulent)}


def friction_gradient(rho, mu_p, tau_y, v, d, shape, model="api_rp13d", width=0.1):
    """
    Friction gradient [Pa/m] for flow speed v >= 0.

      v <  v_c             laminar formula
      v >= v_c (1+width)   turbulent formula
      in between           straight line from laminar(v_c) to turbulent(v_c (1+width))

    The short transition band is a numerical regularisation: a sudden jump of
    friction at v_c would trap the dynamic model exactly at v_c (the flow
    would switch regime back and forth). width = 0 gives the plain switch.
    Returns (gradient, regime code 0 laminar / 1 transition / 2 turbulent, v_c).
    """
    critical, laminar, turbulent = MODELS[model]
    v = np.asarray(v, dtype=float)
    d = np.broadcast_to(np.asarray(d, dtype=float), v.shape)
    v_c = critical(rho, mu_p, tau_y, d, shape)
    v_t = v_c * (1 + width)
    lam, turb = laminar(rho, mu_p, tau_y, v, d, shape), turbulent(rho, mu_p, tau_y, v, d, shape)
    if width > 0:
        lam_c, turb_t = laminar(rho, mu_p, tau_y, v_c, d, shape), turbulent(rho, mu_p, tau_y, v_t, d, shape)
        s = np.clip((v - v_c) / (v_t - v_c), 0, 1)
        band = lam_c + s * (turb_t - lam_c)
    else:
        band = turb
    regime = np.where(v < v_c, 0, np.where(v >= v_t, 2, 1))
    gradient = np.where(regime == 0, lam, np.where(regime == 2, turb, band))
    return gradient, regime, v_c


# ----------------------------------------------------------------------
# Bit
# ----------------------------------------------------------------------
def bit_pressure_drop(rho, q, nozzle_area, cdn):
    """Bit nozzles:  dP_bit = rho q^2 / (2 C_dn^2 A_n^2)   (sign of q kept)"""
    return rho * q * abs(q) / (2 * cdn ** 2 * nozzle_area ** 2)


# ----------------------------------------------------------------------
# Losses along the flow path
# ----------------------------------------------------------------------
def flow_geometry(seg, grouped=False):
    """
    Geometry arrays needed for the friction calculation.

    grouped=True adds up the lengths of segments with the same diameters
    (same friction gradient) - the dynamic model calls the friction function
    thousands of times, and 3-4 groups are much faster than ~120 segments.
    """
    if grouped:
        seg = seg.groupby(["pipe_id", "pipe_od", "hole_d", "pipe_type"], as_index=False,
                          sort=False)["length"].sum()
    return {"length": seg["length"].values.astype(float),
            "d_in": seg["pipe_id"].values.astype(float),
            "d_h": hydraulic_diameter(seg["hole_d"].values, seg["pipe_od"].values).astype(float),
            "a_pipe": pipe_area(seg["pipe_id"].values).astype(float),
            "a_ann": annulus_area(seg["hole_d"].values, seg["pipe_od"].values).astype(float),
            "is_bha": (seg["pipe_type"] == "bha").values}


def pressure_losses(case, geo, q):
    """
    Frictional and bit pressure losses for flow q [m3/s] (q > 0 = normal circulation).

    Every loss is computed for |q| and multiplied by tanh(q / q_eps): it keeps
    the sign of q and goes smoothly to 0 at q = 0 (proposal 4-2-4).
    Returns a dict with per-row arrays and the totals [Pa].
    """
    smooth = np.tanh(q / case.q_eps)
    v_string = abs(q) / geo["a_pipe"]
    v_annulus = abs(q) / geo["a_ann"]
    args = (case.rho, case.mu_p, case.tau_y)
    g_s, reg_s, vc_s = friction_gradient(*args, v_string, geo["d_in"], "pipe",
                                         case.friction_model, case.transition_width)
    g_a, reg_a, vc_a = friction_gradient(*args, v_annulus, geo["d_h"], "annulus",
                                         case.friction_model, case.transition_width)
    dp_string = smooth * g_s * geo["length"]
    dp_annulus = smooth * g_a * geo["length"]
    return {"dp_string": dp_string, "dp_annulus": dp_annulus,
            "v_string": v_string, "v_annulus": v_annulus,
            "regime_string": reg_s, "regime_annulus": reg_a, "vc_string": vc_s, "vc_annulus": vc_a,
            "dp_drill_pipe": float(dp_string[~geo["is_bha"]].sum()),
            "dp_bha": float(dp_string[geo["is_bha"]].sum()),
            "dp_d": float(dp_string.sum()),
            "dp_a": float(dp_annulus.sum()),
            "dp_bit": bit_pressure_drop(case.rho, q, case.nozzle_area, case.cdn)}


# ----------------------------------------------------------------------
# Pressure window
# ----------------------------------------------------------------------
def window_status(bhp, case):
    """
    Where BHP is in the pressure window:
      'kick risk'  BHP < p_pore          'loss risk'  BHP > p_frac
      'margin'     inside [p_pore, p_frac] but closer than delta to a limit
      'safe'       p_pore + delta <= BHP <= p_frac - delta
    """
    if bhp < case.pore:
        return "kick risk"
    if bhp > case.frac:
        return "loss risk"
    if bhp < case.safe_low or bhp > case.safe_high:
        return "margin"
    return "safe"


# ----------------------------------------------------------------------
# Steady state
# ----------------------------------------------------------------------
@dataclass
class SteadyResult:
    """Steady-state solution (all pressures in Pa, flows in m3/s)."""
    q: float                      # flow through string and bit
    q_bpp: float                  # back-pressure pump flow
    z: float                      # choke opening
    target_reachable: bool        # False when the choke hit a limit while holding the target
    p_c: float                    # choke (upstream) pressure
    hydrostatic: float            # rho g h at TD
    dp_drill_pipe: float
    dp_bha: float
    dp_bit: float
    dp_annulus: float
    bhp: float
    spp: float
    ecd: float                    # BHP / (g h)   [kg/m3]
    ecd_no_sbp: float             # rho + dP_a / (g h)   [kg/m3]
    status: str
    segments: pd.DataFrame
    profile: pd.DataFrame

    @property
    def dp_string(self):
        return self.dp_drill_pipe + self.dp_bha

    @property
    def dp_total(self):
        """All flow losses = SPP - p_c."""
        return self.dp_string + self.dp_bit + self.dp_annulus


def solve_steady(case, seg, q=None, q_bpp=None, z=None, p_c=None, target=None):
    """
    Steady-state hydraulics (proposal 4-2-4).

    By default the choke holds the target:  p_c* = p_target - rho g h - dP_a,
    and the opening z0 that passes q + q_bpp at p_c* is found with brentq.
    Give z (fixed opening) or p_c (known choke pressure) to override this.
    """
    q = case.q_pump if q is None else q
    q_bpp = case.q_bpp if q_bpp is None else q_bpp
    target = case.target if target is None else target
    loss = pressure_losses(case, flow_geometry(seg), q)
    hydro = float(hydrostatic(case.rho, case.td_tvd))
    q_choke = q + q_bpp
    reachable = True
    if p_c is None and z is None:
        p_c = target - hydro - loss["dp_a"]
        z, reachable = opening_for(q_choke, p_c, case.rho, case.choke)
        if not reachable:
            p_c = choke_pressure(q_choke, z, case.rho, case.choke)
    elif p_c is None:
        p_c = choke_pressure(q_choke, z, case.rho, case.choke)
    bhp = p_c + hydro + loss["dp_a"]
    spp = p_c + loss["dp_d"] + loss["dp_bit"] + 0.0 + loss["dp_a"]    # (rho_a - rho_d) g h = 0
    return SteadyResult(
        q=q, q_bpp=q_bpp, z=z if z is None else float(z), target_reachable=reachable, p_c=float(p_c),
        hydrostatic=hydro, dp_drill_pipe=loss["dp_drill_pipe"], dp_bha=loss["dp_bha"],
        dp_bit=loss["dp_bit"], dp_annulus=loss["dp_a"], bhp=bhp, spp=spp,
        ecd=bhp / (u.G * case.td_tvd), ecd_no_sbp=case.rho + loss["dp_a"] / (u.G * case.td_tvd),
        status=window_status(bhp, case),
        segments=segment_table(seg, loss), profile=pressure_profile(case, seg, loss, p_c, spp))


def segment_table(seg, loss):
    """One row per segment: geometry, velocity, critical velocity, regime, losses."""
    t = seg.copy()
    for path in ("string", "annulus"):
        t[f"{path}_velocity"] = loss[f"v_{path}"]
        t[f"{path}_critical_velocity"] = loss[f"vc_{path}"]
        names = np.array(["laminar", "transition", "turbulent"])[loss[f"regime_{path}"]]
        t[f"{path}_regime"] = np.where(loss[f"v_{path}"] > 0, names, "static")
        t[f"{path}_dp"] = loss[f"dp_{path}"]
    return t


def pressure_profile(case, seg, loss, p_c, spp):
    """
    Pressure at every segment border.

      annulus : P(z) = p_c + rho g TVD(z) + annulus friction above z
      string  : P(z) = SPP + rho g TVD(z) - string friction above z
    """
    md = np.append(seg["md_top"].values, seg["md_bottom"].values[-1])
    tvd = np.append(seg["tvd_top"].values, seg["tvd_bottom"].values[-1])
    hydro = hydrostatic(case.rho, tvd)
    f_ann = np.concatenate([[0], np.cumsum(loss["dp_annulus"])])
    f_str = np.concatenate([[0], np.cumsum(loss["dp_string"])])
    rows = []
    for i in range(len(md)):
        rows.append(("annulus", md[i], tvd[i], p_c + hydro[i] + f_ann[i], hydro[i], f_ann[i]))
        rows.append(("string", md[i], tvd[i], spp + hydro[i] - f_str[i], hydro[i], -f_str[i]))
    if case.td > case.bit_depth + 1e-6:        # open hole below the bit: no flow
        h_td = float(hydrostatic(case.rho, case.td_tvd))
        rows.append(("annulus", case.td, case.td_tvd, p_c + h_td + f_ann[-1], h_td, f_ann[-1]))
    return pd.DataFrame(rows, columns=["path", "md", "tvd", "pressure", "hydrostatic", "friction"])
