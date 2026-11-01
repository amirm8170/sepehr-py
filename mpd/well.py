"""
Read a case file, check it and describe the well.

1. load_config()   - read the YAML case file (oilfield units, old files are migrated)
2. build_case()    - convert everything to SI units and reject impossible inputs
3. make_segments() - split the well (surface -> bit) into short segments
4. flow_path()     - volumes V_d, V_a and fluid inertance M for the dynamic model

MD  = measured depth (length along the hole)  -> used for friction
TVD = true vertical depth                      -> used for hydrostatic pressure
"""

import copy
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import units as u

ROOT = Path(__file__).resolve().parents[1]      # project folder
BASE_CASE = ROOT / "config" / "base_case.yaml"

FRICTION_MODELS = ("api_rp13d", "exact_bingham")
CHOKE_CURVES = ("linear", "equal_percentage")
SOLVERS = ("Radau", "BDF", "LSODA", "RK45")


# ----------------------------------------------------------------------
# 1. Case file
# ----------------------------------------------------------------------
def load_config(path):
    """Read a YAML case file and return it as a dictionary (oilfield units)."""
    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return migrate_config(config)[0]


def defaults():
    """The base case = the canonical schema with a default for every input."""
    with open(BASE_CASE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def migrate_config(config):
    """
    Bring a case file of the OLD format (Cv choke, one-state model) to the
    current schema. Returns (new config, list of notes). Files already in
    the current format are returned unchanged with no notes.

    Old inputs that are outside the proposal (fixed back pressure, back-pressure
    actuator, pipe movement, gel strength) are dropped and listed in the notes.
    """
    if "compressibility" in config:
        return config, []
    old = copy.deepcopy(config)
    new = defaults()
    notes = ["Old case-file format converted to the proposal schema."]
    new["name"] = old.get("name", new["name"])
    new["well"] = old["well"]
    mud = old.get("mud", {})
    new["mud"] = {k: mud[k] for k in ("density_ppg", "pv_cp", "yp_lbf100ft2")}
    if "name" in mud:
        new["mud"]["name"] = mud["name"]
    op = old.get("operation", {})
    new["operation"]["pump_rate_gpm"] = op.get("pump_rate_gpm", new["operation"]["pump_rate_gpm"])
    new["operation"]["bpp_rate_gpm"] = op.get("bp_pump_gpm", 0.0)
    win = old.get("pressure_window", {})
    new["pressure_window"].update({
        "pore_ppg": win["pore_ppg"], "fracture_ppg": win["fracture_ppg"],
        "safety_margin_psi": win.get("warning_margin_psi", new["pressure_window"]["safety_margin_psi"]),
        "target_ppg": win.get("target_ppg", "midpoint")})
    choke = old.get("choke", {})
    if "cv_max" in choke:
        # US Cv [gpm/sqrt(psi)] -> K [m2] with Q = K sqrt(dp/rho);  orifice: Q = Cd A sqrt(2 dp/rho)
        k_max = choke["cv_max"] * u.GPM * math.sqrt(1000 / u.PSI)
        cd = new["choke"]["discharge_coefficient"]
        new["choke"]["max_area_in2"] = round(k_max / (math.sqrt(2) * cd) / u.INCH ** 2, 4)
        new["choke"]["min_opening"] = max(choke.get("min_opening", 0.01), 0.01)
        new["choke"]["max_opening"] = choke.get("max_opening", 1.0)
        new["actuator"]["max_rate_per_s"] = choke.get("max_speed_per_s", new["actuator"]["max_rate_per_s"])
        notes.append(f"Choke Cv_max {choke['cv_max']} converted to A_max = "
                     f"{new['choke']['max_area_in2']} in2 with C_d = {cd}.")
    dyn = old.get("dynamics", {})
    if "bulk_modulus_pa" in dyn:
        new["compressibility"]["fluid_bulk_modulus_pa"] = float(dyn["bulk_modulus_pa"])
    if "numerics" in old:
        new["numerics"]["segment_length_m"] = old["numerics"].get("segment_length_m", 30)
    dropped = [name for name, present in (
        ("fixed back pressure", op.get("back_pressure_from") == "fixed"),
        ("manual choke opening", str(op.get("choke_opening", "auto")) != "auto"),
        ("pipe movement (surge/swab)", bool(op.get("pipe_velocity_m_s"))),
        ("gel strength", "gel_10min_lbf100ft2" in mud),
        ("back-pressure actuator", dyn.get("actuator") == "back_pressure")) if present]
    if dropped:
        notes.append("Not part of the proposal model, dropped: " + ", ".join(dropped) + ".")
    notes.append("PID gains, actuator, measurement and solver settings taken from the base case.")
    return new, notes


# ----------------------------------------------------------------------
# 2. Case in SI units
# ----------------------------------------------------------------------
@dataclass
class ChokeSpec:
    """Choke valve:  q_c = C_d A_c(z) sqrt(2 dp+ / rho)."""
    a_max: float                  # largest open area [m2]
    cd: float                     # discharge coefficient [-]
    characteristic: str           # "linear" or "equal_percentage"
    rangeability: float           # R = A_max / A_min of the equal-percentage curve [-]
    z_min: float                  # smallest opening [-]
    z_max: float                  # largest opening [-]
    p0: float                     # pressure downstream of the choke [Pa]
    eps: float                    # smoothing of dp+ near zero [Pa]


@dataclass
class ActuatorSpec:
    tau: float                    # first-order time constant tau_a [s]
    max_rate: float               # r_max [1/s]
    dead_time: float              # theta_a [s]


@dataclass
class MeasurementSpec:
    delay: float                  # theta_m [s]
    sample_time: float            # T_s [s]
    noise_std: float              # [Pa], 0 = off
    seed: int


@dataclass
class PIDGains:
    """Gains in SI: kp [1/Pa], ki [1/(Pa s)], kd [s/Pa]."""
    kp: float
    ki: float
    kd: float
    n_filter: float               # derivative filter: T_f = (Kd / Kp) / N
    tracking_time: float          # back-calculation T_t [s] (nan = auto)


@dataclass
class SolverSpec:
    method: str
    rtol: float
    atol: float
    max_step: float               # [s]
    convergence_limit: float      # [%]


@dataclass
class Case:
    """All inputs of one simulation, in SI units."""
    name: str
    survey: pd.DataFrame          # md, tvd, inc, azi   [m, m, deg, deg]
    holes: list                   # dicts: name, top, bottom, diameter [m]
    string: list                  # dicts: name, type, length, od, id [m]
    nozzle_area: float            # A_n [m2]
    cdn: float                    # nozzle discharge coefficient C_dn [-]
    rho: float                    # mud density [kg/m3] (same mud in string and annulus)
    mu_p: float                   # plastic viscosity [Pa.s]
    tau_y: float                  # yield stress [Pa]
    beta_f: float                 # fluid bulk modulus [Pa]
    beta_w: float                 # wall bulk modulus [Pa]
    q_pump: float                 # [m3/s]
    q_bpp: float                  # [m3/s]
    pore: float                   # pore pressure at TD [Pa]
    frac: float                   # fracture pressure at TD [Pa]
    delta: float                  # safety margin [Pa]
    target: float                 # target BHP at TD [Pa]
    target_is_midpoint: bool
    choke: ChokeSpec
    actuator: ActuatorSpec
    measurement: MeasurementSpec
    pid: PIDGains
    solver: SolverSpec
    segment_length: float         # [m]
    friction_model: str
    smoothing_fraction: float     # q_eps / q_pump
    transition_width: float       # laminar -> turbulent band, fraction of v_c
    criteria: dict
    config: dict = field(default_factory=dict)   # the original file (for the GUI)

    @property
    def beta_eff(self):
        """Effective bulk modulus:  1/beta_eff = 1/beta_f + 1/beta_w"""
        return 1.0 / (1.0 / self.beta_f + 1.0 / self.beta_w)

    @property
    def td(self):
        """Total depth MD [m] (bottom of the last hole section)."""
        return self.holes[-1]["bottom"]

    @property
    def td_tvd(self):
        """TVD at total depth [m]."""
        return float(np.interp(self.td, self.survey["md"], self.survey["tvd"]))

    @property
    def bit_depth(self):
        """Bit MD [m] = total length of the drill string."""
        return sum(p["length"] for p in self.string)

    @property
    def q_eps(self):
        """Flow scale of the tanh friction smoothing [m3/s]."""
        return max(self.smoothing_fraction * self.q_pump, 1e-9)

    @property
    def safe_low(self):
        return self.pore + self.delta

    @property
    def safe_high(self):
        return self.frac - self.delta


def thin_wall_bulk_modulus(e_modulus, thickness, diameter):
    """Bulk modulus of a thin elastic wall:  beta_w = 2 E t / D   [Pa]"""
    return 2 * e_modulus * thickness / diameter


def build_case(config):
    """Convert the case dictionary (oilfield units) into a Case object (SI units)."""
    errors = config_errors(config)
    if errors:
        raise ValueError("\n".join(errors))
    cfg = copy.deepcopy(config)
    well, mud, win = cfg["well"], cfg["mud"], cfg["pressure_window"]
    ch, act, meas, pid, sol, num = (cfg[k] for k in ("choke", "actuator", "measurement",
                                                    "controller", "solver", "numerics"))
    survey = read_survey(well)
    tvd_td = float(np.interp(well["hole_sections"][-1]["bottom_m"], survey["md"], survey["tvd"]))
    pore = u.emw_to_pressure(float(win["pore_ppg"]), tvd_td)
    frac = u.emw_to_pressure(float(win["fracture_ppg"]), tvd_td)
    midpoint = str(win.get("target_ppg", "midpoint")).strip().lower() == "midpoint"
    target = (pore + frac) / 2 if midpoint else u.emw_to_pressure(float(win["target_ppg"]), tvd_td)
    kp = float(pid["kp_per_bar"]) / u.BAR
    ki = float(pid["ki_per_bar_s"]) / u.BAR
    tracking = pid.get("tracking_time_s", "auto")

    case = Case(
        name=cfg.get("name", "case"),
        survey=survey,
        holes=[{"name": h["name"], "top": float(h["top_m"]), "bottom": float(h["bottom_m"]),
                "diameter": h["diameter_in"] * u.INCH} for h in well["hole_sections"]],
        string=[{"name": p["name"], "type": p["type"], "length": float(p["length_m"]),
                 "od": p["od_in"] * u.INCH, "id": p["id_in"] * u.INCH} for p in well["drill_string"]],
        nozzle_area=sum(math.pi / 4 * (n / 32 * u.INCH) ** 2 for n in well["bit"]["nozzles_32nd"]),
        cdn=float(well["bit"]["discharge_coefficient"]),
        rho=mud["density_ppg"] * u.PPG,
        mu_p=mud["pv_cp"] * u.CP,
        tau_y=mud["yp_lbf100ft2"] * u.LBF_100FT2,
        beta_f=float(cfg["compressibility"]["fluid_bulk_modulus_pa"]),   # float(): YAML reads 1.5e9 as text
        beta_w=float(cfg["compressibility"]["wall_bulk_modulus_pa"]),
        q_pump=cfg["operation"]["pump_rate_gpm"] * u.GPM,
        q_bpp=cfg["operation"]["bpp_rate_gpm"] * u.GPM,
        pore=pore, frac=frac,
        delta=win["safety_margin_psi"] * u.PSI,
        target=target, target_is_midpoint=midpoint,
        choke=ChokeSpec(a_max=ch["max_area_in2"] * u.INCH ** 2, cd=float(ch["discharge_coefficient"]),
                        characteristic=ch["characteristic"], rangeability=float(ch["rangeability"]),
                        z_min=float(ch["min_opening"]), z_max=float(ch["max_opening"]),
                        p0=ch["downstream_pressure_psi"] * u.PSI,
                        eps=1e-3 * ch["max_working_pressure_psi"] * u.PSI),
        actuator=ActuatorSpec(tau=float(act["time_constant_s"]), max_rate=float(act["max_rate_per_s"]),
                              dead_time=float(act["dead_time_s"])),
        measurement=MeasurementSpec(delay=float(meas["delay_s"]), sample_time=float(meas["sample_time_s"]),
                                    noise_std=meas.get("noise_std_psi", 0) * u.PSI,
                                    seed=int(meas.get("noise_seed", 1))),
        pid=PIDGains(kp=kp, ki=ki, kd=float(pid["kd_s_per_bar"]) / u.BAR,
                     n_filter=float(pid["derivative_filter_n"]),
                     tracking_time=math.nan if str(tracking) == "auto" else float(tracking)),
        solver=SolverSpec(method=sol["method"], rtol=float(sol["rtol"]), atol=float(sol["atol"]),
                          max_step=float(sol["max_step_s"]),
                          convergence_limit=float(sol["convergence_limit_percent"])),
        segment_length=float(num["segment_length_m"]),
        friction_model=num["friction_model"],
        smoothing_fraction=float(num["flow_smoothing_fraction"]),
        transition_width=float(num["regime_transition_width"]),
        criteria=dict(cfg["criteria"]),
        config=config,
    )
    return case


def read_survey(well):
    """Survey stations from a CSV file or from an inline list. TVD is computed if missing."""
    if "survey_file" in well:
        path = Path(well["survey_file"])
        df = pd.read_csv(path if path.is_absolute() else ROOT / path, comment="#")
        df = df.rename(columns={"md_m": "md", "tvd_m": "tvd", "inclination_deg": "inc",
                                "azimuth_deg": "azi"})
    else:
        df = pd.DataFrame(well["survey"], columns=["md", "tvd", "inc", "azi"])
    if "tvd" not in df or df["tvd"].isna().any():
        df["tvd"] = minimum_curvature(df["md"].values, df["inc"].values, df["azi"].values)[0]
    return df[["md", "tvd", "inc", "azi"]].astype(float)


# ----------------------------------------------------------------------
# Input checks (proposal 4-2-3: positive properties, p_pore < p_frac,
# pipe smaller than hole) - every problem is listed, nothing is changed.
# ----------------------------------------------------------------------
def config_errors(cfg):
    """List of readable problems with a case file (empty list = valid)."""
    try:
        return _check(cfg)
    except (KeyError, TypeError, ValueError) as error:
        return [f"Missing or unreadable input: {error}"]


def _positive(errors, name, value, allow_zero=False):
    value = float(value)
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        errors.append(f"{name} must be {'>= 0' if allow_zero else '> 0'} (got {value:g}).")


def _check(cfg):
    errors = []
    well, mud, win, ch = cfg["well"], cfg["mud"], cfg["pressure_window"], cfg["choke"]

    # geometry
    survey = read_survey(well)
    d_md, d_tvd = np.diff(survey["md"]), np.diff(survey["tvd"])
    if np.any(d_md <= 0):
        errors.append("Survey MD must increase from station to station.")
    elif np.any(np.abs(d_tvd) > d_md + 1e-6):
        errors.append("Survey TVD changes more than MD between two stations (impossible trajectory).")
    if np.any((survey["inc"] < 0) | (survey["inc"] > 180)):
        errors.append("Inclination must be between 0 and 180 deg.")
    top = 0.0
    for h in well["hole_sections"]:
        _positive(errors, f"Hole '{h['name']}' diameter", h["diameter_in"])
        if abs(float(h["top_m"]) - top) > 1e-6:
            errors.append(f"Hole section '{h['name']}' must start where the previous one ends ({top:g} m).")
        if float(h["bottom_m"]) <= float(h["top_m"]):
            errors.append(f"Hole section '{h['name']}': bottom must be deeper than top.")
        top = float(h["bottom_m"])
    td = top
    if survey["md"].iloc[-1] < td - 1e-6:
        errors.append(f"Survey ends at {survey['md'].iloc[-1]:g} m MD, above TD ({td:g} m).")
    string_length = 0.0
    for p in well["drill_string"]:
        _positive(errors, f"'{p['name']}' length", p["length_m"])
        _positive(errors, f"'{p['name']}' ID", p["id_in"])
        if p["type"] not in ("drill_pipe", "bha"):
            errors.append(f"'{p['name']}': type must be drill_pipe or bha.")
        if float(p["id_in"]) >= float(p["od_in"]):
            errors.append(f"'{p['name']}': ID must be smaller than OD.")
        # annulus must exist wherever this pipe is in the hole
        top_p, bottom_p = string_length, string_length + float(p["length_m"])
        for h in well["hole_sections"]:
            overlaps = float(h["top_m"]) < bottom_p and float(h["bottom_m"]) > top_p
            if overlaps and float(h["diameter_in"]) <= float(p["od_in"]):
                errors.append(f"Hole '{h['name']}' ({h['diameter_in']} in) is not larger than "
                              f"'{p['name']}' OD ({p['od_in']} in): no annulus.")
        string_length = bottom_p
    if string_length > td + 1e-6:
        errors.append(f"Drill string ({string_length:g} m) is longer than the well ({td:g} m).")
    for n in well["bit"]["nozzles_32nd"]:
        _positive(errors, "Nozzle size", n)
    cdn = float(well["bit"]["discharge_coefficient"])
    if not 0 < cdn <= 1:
        errors.append("Nozzle discharge coefficient C_dn must be in (0, 1].")

    # fluid and compressibility
    _positive(errors, "Mud density", mud["density_ppg"])
    _positive(errors, "Plastic viscosity", mud["pv_cp"])
    _positive(errors, "Yield point", mud["yp_lbf100ft2"], allow_zero=True)
    _positive(errors, "Fluid bulk modulus beta_f", cfg["compressibility"]["fluid_bulk_modulus_pa"])
    _positive(errors, "Wall bulk modulus beta_w", cfg["compressibility"]["wall_bulk_modulus_pa"])

    # operation
    _positive(errors, "Pump rate", cfg["operation"]["pump_rate_gpm"], allow_zero=True)
    _positive(errors, "Back-pressure pump rate", cfg["operation"]["bpp_rate_gpm"], allow_zero=True)

    # pressure window (EMW at TD -> compare as ppg; margin converted at TD)
    pore, frac = float(win["pore_ppg"]), float(win["fracture_ppg"])
    _positive(errors, "Safety margin delta", win["safety_margin_psi"], allow_zero=True)
    if pore >= frac:
        errors.append(f"Pore pressure ({pore} ppg) must be below fracture pressure ({frac} ppg).")
    elif not errors:
        tvd_td = float(np.interp(td, survey["md"], survey["tvd"]))
        margin = u.pressure_to_emw(float(win["safety_margin_psi"]) * u.PSI, tvd_td)
        low, high = pore + margin, frac - margin
        if low >= high:
            errors.append(f"The safety margin leaves no safe window: {low:.2f} >= {high:.2f} ppg.")
        target = str(win.get("target_ppg", "midpoint")).strip().lower()
        if target != "midpoint" and not low <= float(target) <= high:
            errors.append(f"Target {float(target):.2f} ppg is outside the safe window "
                          f"{low:.2f} - {high:.2f} ppg (use 'midpoint' = {(pore + frac) / 2:.2f} ppg).")

    # choke, actuator, measurement
    _positive(errors, "Choke A_max", ch["max_area_in2"])
    if not 0 < float(ch["discharge_coefficient"]) <= 1:
        errors.append("Choke discharge coefficient C_d must be in (0, 1].")
    if ch["characteristic"] not in CHOKE_CURVES:
        errors.append(f"Choke characteristic must be one of {CHOKE_CURVES}.")
    if ch["characteristic"] == "equal_percentage" and float(ch["rangeability"]) <= 1:
        errors.append("Choke rangeability R must be > 1.")
    if not 0 < float(ch["min_opening"]) < float(ch["max_opening"]) <= 1:
        errors.append("Choke limits must satisfy 0 < min_opening < max_opening <= 1.")
    _positive(errors, "Choke downstream pressure p_0", ch["downstream_pressure_psi"], allow_zero=True)
    _positive(errors, "Choke max working pressure", ch["max_working_pressure_psi"])
    _positive(errors, "Actuator time constant tau_a", cfg["actuator"]["time_constant_s"])
    _positive(errors, "Actuator max rate", cfg["actuator"]["max_rate_per_s"])
    _positive(errors, "Actuator dead time", cfg["actuator"]["dead_time_s"], allow_zero=True)
    _positive(errors, "Measurement delay theta_m", cfg["measurement"]["delay_s"], allow_zero=True)
    _positive(errors, "Sample time T_s", cfg["measurement"]["sample_time_s"])
    _positive(errors, "Measurement noise", cfg["measurement"].get("noise_std_psi", 0), allow_zero=True)

    # controller, solver, numerics
    pid = cfg["controller"]
    for key in ("kp_per_bar", "ki_per_bar_s", "kd_s_per_bar"):
        _positive(errors, f"PID {key}", pid[key], allow_zero=True)
    _positive(errors, "Derivative filter N", pid["derivative_filter_n"])
    if str(pid.get("tracking_time_s", "auto")) != "auto":
        _positive(errors, "Tracking time T_t", pid["tracking_time_s"])
    sol = cfg["solver"]
    if sol["method"] not in SOLVERS:
        errors.append(f"Solver method must be one of {SOLVERS}.")
    for key in ("rtol", "atol", "max_step_s", "convergence_limit_percent"):
        _positive(errors, f"Solver {key}", sol[key])
    num = cfg["numerics"]
    _positive(errors, "Segment length", num["segment_length_m"])
    _positive(errors, "Flow smoothing fraction", num["flow_smoothing_fraction"])
    _positive(errors, "Regime transition width", num["regime_transition_width"], allow_zero=True)
    if num["friction_model"] not in FRICTION_MODELS:
        errors.append(f"Friction model must be one of {FRICTION_MODELS}.")
    for key in ("max_overshoot_percent", "max_settling_time_s", "settling_band_percent",
                "min_time_in_window_percent"):
        _positive(errors, f"Criterion {key}", cfg["criteria"][key])
    return errors


# ----------------------------------------------------------------------
# 3. Trajectory (minimum curvature method)
# ----------------------------------------------------------------------
def minimum_curvature(md, inc_deg, azi_deg):
    """
    TVD, North and East of each survey station (standard minimum curvature method).

        dogleg   cos(B) = cos(I2-I1) - sin(I1) sin(I2) (1 - cos(A2-A1))
        RF       = 2/B * tan(B/2)
        dTVD     = dMD/2 * (cos I1 + cos I2) * RF
        dNorth   = dMD/2 * (sin I1 cos A1 + sin I2 cos A2) * RF
        dEast    = dMD/2 * (sin I1 sin A1 + sin I2 sin A2) * RF
    """
    inc, azi = np.radians(inc_deg), np.radians(azi_deg)
    i1, i2, a1, a2 = inc[:-1], inc[1:], azi[:-1], azi[1:]
    dmd = np.diff(md)
    cos_b = np.cos(i2 - i1) - np.sin(i1) * np.sin(i2) * (1 - np.cos(a2 - a1))
    b = np.arccos(np.clip(cos_b, -1, 1))
    rf = np.ones_like(b)
    curved = b > 1e-9
    rf[curved] = 2 / b[curved] * np.tan(b[curved] / 2)
    d_tvd = dmd / 2 * (np.cos(i1) + np.cos(i2)) * rf
    d_n = dmd / 2 * (np.sin(i1) * np.cos(a1) + np.sin(i2) * np.cos(a2)) * rf
    d_e = dmd / 2 * (np.sin(i1) * np.sin(a1) + np.sin(i2) * np.sin(a2)) * rf
    start = md[0]
    return (np.concatenate([[start], start + np.cumsum(d_tvd)]),
            np.concatenate([[0], np.cumsum(d_n)]), np.concatenate([[0], np.cumsum(d_e)]))


# ----------------------------------------------------------------------
# 4. Segments
# ----------------------------------------------------------------------
def make_segments(case):
    """
    Split the well from surface to bit into segments of about `segment_length`.

    Segment borders are also placed where the pipe or the hole size changes.
    Each row of the returned table is one segment with its own MD, TVD,
    inclination, azimuth, length and diameters.
    """
    # depths where something changes
    borders = [0.0, case.bit_depth]
    depth = 0.0
    for p in case.string:
        depth += p["length"]
        borders.append(depth)
    borders += [h["bottom"] for h in case.holes]
    borders = np.unique(np.round([b for b in borders if b <= case.bit_depth + 1e-6], 3))

    # fill each interval with equal segments
    nodes = [borders[0]]
    for a, b in zip(borders[:-1], borders[1:]):
        n = max(1, int(np.ceil((b - a) / case.segment_length - 1e-9)))
        nodes += list(np.linspace(a, b, n + 1)[1:])
    nodes = np.array(nodes)

    top, bottom = nodes[:-1], nodes[1:]
    mid = (top + bottom) / 2
    s = case.survey
    seg = pd.DataFrame({
        "md_top": top, "md_bottom": bottom, "length": bottom - top,
        "tvd_top": np.interp(top, s["md"], s["tvd"]),
        "tvd_bottom": np.interp(bottom, s["md"], s["tvd"]),
        "inclination": np.interp(mid, s["md"], s["inc"]),
        "azimuth": np.interp(mid, s["md"], s["azi"]),
    })
    # which pipe and which hole section is at the middle of each segment
    pipe_top = np.cumsum([0] + [p["length"] for p in case.string])[:-1]
    pipe_idx = np.searchsorted(pipe_top, mid, side="right") - 1
    hole_bottom = np.array([h["bottom"] for h in case.holes])
    hole_idx = np.minimum(np.searchsorted(hole_bottom, mid), len(case.holes) - 1)

    seg["pipe"] = [case.string[i]["name"] for i in pipe_idx]
    seg["pipe_type"] = [case.string[i]["type"] for i in pipe_idx]
    seg["pipe_id"] = [case.string[i]["id"] for i in pipe_idx]
    seg["pipe_od"] = [case.string[i]["od"] for i in pipe_idx]
    seg["hole"] = [case.holes[i]["name"] for i in hole_idx]
    seg["hole_d"] = [case.holes[i]["diameter"] for i in hole_idx]
    return seg


@dataclass
class FlowPath:
    """Lumped quantities of the dynamic model (proposal 4-2-5)."""
    v_d: float                    # drill-string volume  V_d = sum(A_i L_i)          [m3]
    v_a: float                    # annulus volume       V_a = sum(A_a,i L_i)        [m3]
    inertance: float              # M = sum(rho L_i / A_i) + sum(rho L_i / A_a,i)    [kg/m4]
    h: float                      # TVD used for hydrostatic pressure (TD)           [m]


def flow_path(case, seg):
    """Volumes and fluid inertance from the segment geometry."""
    a_string = np.pi / 4 * seg["pipe_id"] ** 2
    a_annulus = np.pi / 4 * (seg["hole_d"] ** 2 - seg["pipe_od"] ** 2)
    length = seg["length"]
    return FlowPath(
        v_d=float((a_string * length).sum()),
        v_a=float((a_annulus * length).sum()),
        inertance=float((case.rho * length / a_string).sum() + (case.rho * length / a_annulus).sum()),
        h=case.td_tvd)
