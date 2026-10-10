"""
Read a case file and describe the well.

1. load_config()  - read the YAML case file (oilfield units)
2. build_case()   - convert everything to SI units and check the inputs
3. make_segments()- split the well (surface -> bit) into short segments

MD  = measured depth (length along the hole)  -> used for friction
TVD = true vertical depth                      -> used for hydrostatic pressure
"""

import copy
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import units as u

ROOT = Path(__file__).resolve().parents[1]      # project folder


# ----------------------------------------------------------------------
# 1. Case file
# ----------------------------------------------------------------------
def load_config(path):
    """Read a YAML case file and return it as a dictionary (oilfield units)."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass
class Case:
    """All inputs of one simulation, in SI units."""
    name: str
    survey: pd.DataFrame          # md, tvd, inc, azi   [m, m, deg, deg]
    holes: list                   # dicts: name, top, bottom, diameter [m]
    string: list                  # dicts: name, type, length, od, id [m]
    nozzles: list                 # nozzle diameters [m]
    cd: float                     # nozzle discharge coefficient [-]
    rho: float                    # mud density [kg/m3]
    pv: float                     # plastic viscosity [Pa.s]
    yp: float                     # yield point [Pa]
    gel: float                    # 10-min gel strength [Pa]
    pump_rate: float              # [m3/s]
    bp_mode: str                  # "choke" or "fixed"
    back_pressure: float          # [Pa] (fixed mode)
    choke_opening: object         # 0..1 or "auto"
    bp_pump_rate: float           # [m3/s]
    pipe_velocity: float          # [m/s], + down
    clinging: float               # clinging constant [-]
    pore: float                   # pore pressure at TD [Pa]
    frac: float                   # fracture pressure at TD [Pa]
    target: float                 # target BHP at TD [Pa]
    margin: float                 # warning margin [Pa]
    choke: dict                   # cv_max, cv_closed, min_opening, max_opening, max_speed_per_s
    dyn: dict                     # dynamic-model settings (SI)
    pid: dict                     # PID settings
    segment_length: float         # [m]
    low_flow: float               # [m3/s] friction smoothing below this flow (dynamics)
    config: dict = field(default_factory=dict)   # the original file (for the GUI)

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


def build_case(config):
    """Convert the case dictionary (oilfield units) into a Case object (SI units)."""
    cfg = copy.deepcopy(config)
    well, mud, op, win = cfg["well"], cfg["mud"], cfg["operation"], cfg["pressure_window"]

    survey = read_survey(well)
    tvd_td = float(np.interp(well["hole_sections"][-1]["bottom_m"], survey["md"], survey["tvd"]))

    case = Case(
        name=cfg.get("name", "case"),
        survey=survey,
        holes=[{"name": h["name"], "top": h["top_m"], "bottom": h["bottom_m"],
                "diameter": h["diameter_in"] * u.INCH} for h in well["hole_sections"]],
        string=[{"name": p["name"], "type": p["type"], "length": p["length_m"],
                 "od": p["od_in"] * u.INCH, "id": p["id_in"] * u.INCH} for p in well["drill_string"]],
        nozzles=[n / 32 * u.INCH for n in well["bit"]["nozzles_32nd"]],
        cd=well["bit"]["discharge_coefficient"],
        rho=mud["density_ppg"] * u.PPG,
        pv=mud["pv_cp"] * u.CP,
        yp=mud["yp_lbf100ft2"] * u.LBF_100FT2,
        gel=mud.get("gel_10min_lbf100ft2", 0.0) * u.LBF_100FT2,
        pump_rate=op["pump_rate_gpm"] * u.GPM,
        bp_mode=op["back_pressure_from"],
        back_pressure=op["back_pressure_psi"] * u.PSI,
        choke_opening=op["choke_opening"],
        bp_pump_rate=op["bp_pump_gpm"] * u.GPM,
        pipe_velocity=op["pipe_velocity_m_s"],
        clinging=op.get("clinging_constant", 0.45),
        pore=u.emw_to_pressure(win["pore_ppg"], tvd_td),
        frac=u.emw_to_pressure(win["fracture_ppg"], tvd_td),
        target=u.emw_to_pressure(win["target_ppg"], tvd_td),
        margin=win.get("warning_margin_psi", 0) * u.PSI,
        choke=cfg["choke"],
        dyn={"model": cfg["dynamics"]["model"], "actuator": cfg["dynamics"]["actuator"],
             "dt": float(cfg["dynamics"]["time_step_s"]),
             "bulk_modulus": float(cfg["dynamics"]["bulk_modulus_pa"]),   # float(): YAML reads 1.5e9 as text
             "bp_tau": cfg["dynamics"]["bp_time_constant_s"],
             "bp_max": cfg["dynamics"]["bp_max_psi"] * u.PSI,
             "profile_every": cfg["dynamics"]["profile_every_s"]},
        pid=cfg["pid"],
        segment_length=cfg["numerics"]["segment_length_m"],
        low_flow=cfg["numerics"].get("low_flow_gpm", 50) * u.GPM,
        config=config,
    )
    check_case(case)
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


def check_case(case):
    """Stop with a clear message if an input is physically impossible."""
    errors = []
    if case.rho <= 0:
        errors.append("Mud density must be > 0.")
    if case.pv <= 0 or case.yp < 0:
        errors.append("PV must be > 0 and YP >= 0.")
    if case.pump_rate < 0 or case.bp_pump_rate < 0:
        errors.append("Flow rates cannot be negative.")
    if np.any(np.diff(case.survey["md"]) <= 0):
        errors.append("Survey MD must increase.")
    if np.any(case.survey["tvd"] > case.survey["md"] + 1e-6):
        errors.append("TVD cannot be larger than MD.")
    if case.bit_depth > case.td + 1e-6:
        errors.append(f"Drill string ({case.bit_depth:.0f} m) is longer than the well ({case.td:.0f} m).")
    for p in case.string:
        if p["id"] >= p["od"]:
            errors.append(f"{p['name']}: ID must be smaller than OD.")
    top = 0.0
    for h in case.holes:
        if abs(h["top"] - top) > 1e-6:
            errors.append(f"Hole section '{h['name']}' must start where the previous one ends.")
        top = h["bottom"]
        for p in case.string:
            if h["diameter"] <= p["od"]:
                errors.append(f"Hole '{h['name']}' is smaller than pipe '{p['name']}' OD.")
    if not case.pore < case.frac:
        errors.append("Fracture pressure must be larger than pore pressure.")
    if not case.pore <= case.target <= case.frac:
        errors.append("Target BHP must be between pore and fracture pressure.")
    if errors:
        raise ValueError("\n".join(errors))


# ----------------------------------------------------------------------
# 2. Trajectory (minimum curvature method)
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
# 3. Segments
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
