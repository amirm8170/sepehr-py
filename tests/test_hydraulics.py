"""
Stage A tests. Every test compares with an INDEPENDENT result
(hand calculation, textbook formula or exact analytic solution).
"""

import numpy as np
import pytest
from scipy.integrate import quad

from mpd import units as u
from mpd.hydraulics import (annulus_area, bit_pressure_drop, choke_flow, choke_pressure_drop,
                            critical_reynolds, friction, hydraulic_diameter, laminar_wall_stress,
                            pipe_area, solve_steady)
from mpd.well import minimum_curvature


# --- units and geometry ------------------------------------------------
def test_units():
    assert 1 * u.PSI == pytest.approx(6894.757)
    assert 1 * u.PPG == pytest.approx(119.826, rel=1e-5)
    assert 1 * u.GPM == pytest.approx(6.309e-5, rel=1e-4)


def test_oilfield_hydrostatic_rule():
    # 0.052 x 10 ppg x 10 000 ft = 5200 psi  (oilfield rule of thumb, 0.1 % rounded)
    p = 10 * u.PPG * u.G * 10000 * u.FT
    assert u.psi(p) == pytest.approx(5200, rel=2e-3)


def test_areas_by_hand():
    assert pipe_area(0.1) == pytest.approx(0.0078540, rel=1e-4)
    assert annulus_area(0.2, 0.1) == pytest.approx(0.0235619, rel=1e-4)
    assert hydraulic_diameter(0.2159, 0.127) == pytest.approx(0.0889)


def test_minimum_curvature_quarter_circle():
    # building from 0 to 90 deg over a quarter circle of radius R: TVD = R, East = R
    r = 500.0
    tvd, north, east = minimum_curvature(np.array([0, np.pi * r / 2]), np.array([0, 90.0]),
                                         np.array([90.0, 90.0]))
    assert tvd[-1] == pytest.approx(r)
    assert east[-1] == pytest.approx(r)


# --- Bingham friction --------------------------------------------------
def test_newtonian_laminar_is_hagen_poiseuille():
    # YP = 0 -> dP/dL = 32 mu v / D^2
    tau = laminar_wall_stress(0.5, 0.05, 0.01, 0.0, "pipe")
    assert 4 * tau / 0.05 == pytest.approx(32 * 0.01 * 0.5 / 0.05 ** 2)


def test_buckingham_against_velocity_profile():
    # integrate the exact Bingham velocity profile in a pipe for a chosen wall stress,
    # then check that the solver gives back the same wall stress
    mu, yp, radius, tau_w = 0.02, 8.0, 0.05, 20.0
    r_plug = radius * yp / tau_w
    grad = 2 * tau_w / radius

    def v(r):
        return grad / (4 * mu) * (radius ** 2 - r ** 2) - yp / mu * (radius - r)
    q = 2 * np.pi * (quad(lambda r: v(r) * r, r_plug, radius)[0] + v(r_plug) * r_plug ** 2 / 2)
    v_mean = q / (np.pi * radius ** 2)
    assert laminar_wall_stress(v_mean, 2 * radius, mu, yp, "pipe") == pytest.approx(tau_w, rel=1e-6)


def test_hanks_newtonian_limit():
    assert critical_reynolds(0.0)[0] == pytest.approx(2100)
    assert 6500 < critical_reynolds(1e5)[0] < 9500        # published Hanks curve


def test_turbulent_pipe_matches_textbook_formula():
    # Applied Drilling Engineering: dp/dL = MW^.75 v^1.75 PV^.25 / (1800 d^1.25)  [psi/ft]
    q, d = 500.0, 4.276
    v_fts = q / (2.448 * d ** 2)
    expected = 10 ** 0.75 * v_fts ** 1.75 * 20 ** 0.25 / (1800 * d ** 1.25)
    v = q * u.GPM / pipe_area(d * u.INCH)
    f = friction(10 * u.PPG, 20 * u.CP, 15 * u.LBF_100FT2, np.array([v]), d * u.INCH, "pipe")
    assert f["regime"][0] == "turbulent"
    assert u.psi(f["gradient"][0]) * u.FT == pytest.approx(expected, rel=0.01)


def test_friction_follows_flow_direction():
    f = friction(1200, 0.02, 7.0, np.array([-1.0, 0.0, 1.0]), 0.1, "pipe")
    assert f["gradient"][0] == pytest.approx(-f["gradient"][2])
    assert f["gradient"][1] == 0


# --- bit and choke -----------------------------------------------------
def test_bit_matches_textbook_formula():
    # dP = 8.311e-5 MW q^2 / (Cd^2 A^2)   [psi, ppg, gpm, in2]
    nozzles = [12 / 32 * u.INCH] * 3
    area_in2 = 3 * np.pi / 4 * (12 / 32) ** 2
    expected = 8.311e-5 * 10 * 350 ** 2 / (0.95 ** 2 * area_in2 ** 2)
    assert u.psi(bit_pressure_drop(10 * u.PPG, 350 * u.GPM, nozzles, 0.95)) == pytest.approx(expected, rel=1e-3)


def test_choke_cv_definition():
    # Cv = 10 means 10 gpm of water at 1 psi
    choke = {"cv_max": 10, "cv_closed": 0, "min_opening": 0, "max_opening": 1}
    assert choke_flow(2 * u.PSI, 1.0, 1000, choke) == pytest.approx(10 * np.sqrt(2) * u.GPM, rel=1e-3)
    dp = choke_pressure_drop(0.02, 1.0, 1000, choke)
    assert choke_flow(dp, 1.0, 1000, choke) == pytest.approx(0.02)


# --- whole well --------------------------------------------------------
def test_static_bhp_is_rho_g_tvd(base):
    case, seg = base
    r = solve_steady(case, seg, q=0.0, sbp=0.0)
    assert r["bhp"] == pytest.approx(case.rho * u.G * case.td_tvd)
    assert r["dp_total"] == 0


def test_spp_is_sum_of_losses(base):
    case, seg = base
    r = solve_steady(case, seg)
    assert r["spp"] == pytest.approx(r["sbp"] + r["dp_drill_pipe"] + r["dp_bha"] + r["dp_bit"]
                                     + r["dp_annulus"])


def test_auto_choke_reaches_target(base):
    case, seg = base
    assert solve_steady(case, seg)["bhp"] == pytest.approx(case.target, rel=1e-4)


def test_back_pressure_adds_one_to_one(base):
    case, seg = base
    r1, r2 = solve_steady(case, seg, sbp=1e6), solve_steady(case, seg, sbp=2e6)
    assert r2["bhp"] - r1["bhp"] == pytest.approx(1e6)


def test_ecd_larger_than_mud_weight_when_circulating(base):
    case, seg = base
    r = solve_steady(case, seg)
    assert r["ecd"] > case.rho
    assert r["emw"] > r["ecd"]                      # EMW also contains the back pressure


def test_surge_raises_and_swab_lowers_bhp(base):
    case, seg = base
    b0 = solve_steady(case, seg, sbp=1e6)["bhp"]
    assert solve_steady(case, seg, sbp=1e6, v_pipe=0.5)["bhp"] > b0
    assert solve_steady(case, seg, sbp=1e6, v_pipe=-0.5)["bhp"] < b0


def test_u_tube_at_bit(base):
    # pressure inside the string at the bit minus the bit drop = annulus pressure at the bit
    case, seg = base
    r = solve_steady(case, seg)
    prof = r["profile"]
    inside = prof[prof.path == "string"]["pressure"].iloc[-1]
    outside = prof[prof.path == "annulus"]["pressure"].iloc[-1]
    assert inside - r["dp_bit"] == pytest.approx(outside)


def test_bad_input_is_rejected():
    from mpd.well import build_case, load_config
    from tests.conftest import ROOT
    cfg = load_config(ROOT / "config" / "base_case.yaml")
    cfg["well"]["drill_string"][0]["od_in"] = 9.0          # pipe larger than the hole
    with pytest.raises(ValueError):
        build_case(cfg)
