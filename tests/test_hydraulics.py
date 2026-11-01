"""
Steady hydraulics tests. Every test compares with an INDEPENDENT result
(hand calculation in field units, textbook formula or exact analytic solution).
"""

import math
from dataclasses import replace

import numpy as np
import pytest
from scipy.integrate import quad

from mpd import units as u
from mpd.hydraulics import (annulus_area, api_critical_velocity, bit_pressure_drop, critical_reynolds,
                            flow_geometry, friction_gradient, hydraulic_diameter, laminar_wall_stress,
                            pipe_area, pressure_losses, solve_steady)
from mpd.well import minimum_curvature

RHO, MU, TY = 10 * u.PPG, 20 * u.CP, 15 * u.LBF_100FT2      # 10 ppg, 20 cP, 15 lbf/100ft2


# --- units and geometry ------------------------------------------------
def test_units():
    assert 1 * u.PSI == pytest.approx(6894.757)
    assert 1 * u.PPG == pytest.approx(119.826, rel=1e-5)
    assert 1 * u.GPM == pytest.approx(6.309e-5, rel=1e-4)


def test_oilfield_hydrostatic_rule():
    # 0.052 x 10 ppg x 10 000 ft = 5200 psi  (oilfield rule of thumb, 0.1 % rounded)
    assert u.psi(10 * u.PPG * u.G * 10000 * u.FT) == pytest.approx(5200, rel=2e-3)


def test_areas_by_hand():
    assert pipe_area(0.1) == pytest.approx(0.0078540, rel=1e-4)
    assert annulus_area(0.2, 0.1) == pytest.approx(0.0235619, rel=1e-4)
    assert hydraulic_diameter(0.2159, 0.127) == pytest.approx(0.0889)


def test_minimum_curvature_quarter_circle():
    # building from 0 to 90 deg over a quarter circle of radius R: TVD = R, East = R
    r = 500.0
    tvd, _, east = minimum_curvature(np.array([0, np.pi * r / 2]), np.array([0, 90.0]), np.array([90.0, 90.0]))
    assert tvd[-1] == pytest.approx(r)
    assert east[-1] == pytest.approx(r)


# --- proposal friction correlation (API RP 13D / Bourgoyne) ------------
def test_critical_velocity_hand_calculation():
    # pipe, d = 4.276 in: (97*20 + 97 sqrt(20^2 + 8.2*10*4.276^2*15)) / (10*4.276) ft/min
    d = 4.276
    v_ftmin = (97 * 20 + 97 * math.sqrt(400 + 8.2 * 10 * d ** 2 * 15)) / (10 * d)
    assert api_critical_velocity(RHO, MU, TY, d * u.INCH, "pipe") == pytest.approx(v_ftmin * u.FT / 60, rel=1e-9)
    gap = 8.5 - 5.0
    v_ann = (97 * 20 + 97 * math.sqrt(400 + 6.2 * 10 * gap ** 2 * 15)) / (10 * gap)
    assert api_critical_velocity(RHO, MU, TY, gap * u.INCH, "annulus") == pytest.approx(v_ann * u.FT / 60, rel=1e-9)


@pytest.mark.parametrize("shape, d_in, gpm, laminar_c, yield_c, turb_c", [
    ("pipe", 4.276, 500, 1500, 225, 1800),        # turbulent
    ("annulus", 3.5, 350, 1000, 200, 1396),       # laminar
])
def test_friction_matches_field_formulas(shape, d_in, gpm, laminar_c, yield_c, turb_c):
    area_in2 = math.pi / 4 * d_in ** 2 if shape == "pipe" else math.pi / 4 * (8.5 ** 2 - 5.0 ** 2)
    v_fts = gpm / (2.448 * (d_in ** 2 if shape == "pipe" else 8.5 ** 2 - 5.0 ** 2))
    v_c = (97 * 20 + 97 * math.sqrt(400 + (8.2 if shape == "pipe" else 6.2) * 10 * d_in ** 2 * 15)) / (10 * d_in) / 60
    if v_fts < v_c:
        expected = 20 * v_fts / (laminar_c * d_in ** 2) + 15 / (yield_c * d_in)
    else:
        expected = 10 ** 0.75 * v_fts ** 1.75 * 20 ** 0.25 / (turb_c * d_in ** 1.25)
    v = gpm * u.GPM / (area_in2 * u.INCH ** 2)
    g, regime, _ = friction_gradient(RHO, MU, TY, np.array([v]), d_in * u.INCH, shape, width=0.0)
    assert regime[0] == (2 if v_fts >= v_c else 0)
    assert u.psi(g[0]) * u.FT == pytest.approx(expected, rel=1e-3)


def test_transition_band_is_continuous():
    # no jump in friction at v_c or at v_c (1 + width)
    d = 4.276 * u.INCH
    v_c = api_critical_velocity(RHO, MU, TY, d, "pipe")
    eps = 1e-7
    v = np.array([v_c * (1 - eps), v_c * (1 + eps), 1.1 * v_c * (1 - eps), 1.1 * v_c * (1 + eps)])
    g, regime, _ = friction_gradient(RHO, MU, TY, v, d, "pipe", width=0.1)
    assert list(regime) == [0, 1, 1, 2]
    assert g[0] == pytest.approx(g[1], rel=1e-5)
    assert g[2] == pytest.approx(g[3], rel=1e-5)


def test_tanh_smoothing_keeps_sign_and_vanishes_at_zero(base):
    case, seg = base
    geo = flow_geometry(seg, grouped=True)
    forward, backward = pressure_losses(case, geo, case.q_pump), pressure_losses(case, geo, -case.q_pump)
    assert backward["dp_a"] == pytest.approx(-forward["dp_a"])
    assert pressure_losses(case, geo, 0.0)["dp_d"] == 0.0
    # at q = q_eps the loss is tanh(1) = 76 % of the unsmoothed value
    small = pressure_losses(case, geo, case.q_eps)
    unsmoothed = pressure_losses(replace(case, smoothing_fraction=1e-12), geo, case.q_eps)
    assert small["dp_a"] == pytest.approx(math.tanh(1) * unsmoothed["dp_a"], rel=1e-6)


# --- exact Bingham comparison model ------------------------------------
def test_newtonian_laminar_is_hagen_poiseuille():
    tau = laminar_wall_stress(0.5, 0.05, 0.01, 0.0, "pipe")
    assert 4 * tau / 0.05 == pytest.approx(32 * 0.01 * 0.5 / 0.05 ** 2)


def test_buckingham_against_velocity_profile():
    # integrate the exact Bingham velocity profile for a chosen wall stress
    mu, ty, radius, tau_w = 0.02, 8.0, 0.05, 20.0
    r_plug = radius * ty / tau_w
    grad = 2 * tau_w / radius

    def v(r):
        return grad / (4 * mu) * (radius ** 2 - r ** 2) - ty / mu * (radius - r)
    q = 2 * np.pi * (quad(lambda r: v(r) * r, r_plug, radius)[0] + v(r_plug) * r_plug ** 2 / 2)
    assert laminar_wall_stress(q / (np.pi * radius ** 2), 2 * radius, mu, ty, "pipe") == pytest.approx(tau_w, rel=1e-6)


def test_hanks_newtonian_limit():
    assert critical_reynolds(0.0)[0] == pytest.approx(2100)
    assert 6500 < critical_reynolds(1e5)[0] < 9500        # published Hanks curve


# --- bit ---------------------------------------------------------------
def test_bit_matches_oilfield_formula():
    # dP = 8.311e-5 MW q^2 / (Cd^2 A^2)   [psi, ppg, gpm, in2]  (constant rounded -> 0.1 %)
    area_in2 = 3 * np.pi / 4 * (12 / 32) ** 2
    expected = 8.311e-5 * 10 * 350 ** 2 / (0.95 ** 2 * area_in2 ** 2)
    got = bit_pressure_drop(10 * u.PPG, 350 * u.GPM, area_in2 * u.INCH ** 2, 0.95)
    assert u.psi(got) == pytest.approx(expected, rel=1e-3)


# --- whole well --------------------------------------------------------
def test_static_bhp_is_rho_g_tvd(base):
    case, seg = base
    r = solve_steady(case, seg, q=0.0, q_bpp=0.0, p_c=0.0)
    assert r.bhp == pytest.approx(case.rho * u.G * case.td_tvd)
    assert r.dp_total == 0


def test_spp_is_choke_pressure_plus_losses(base):
    case, seg = base
    r = solve_steady(case, seg)
    assert r.spp == pytest.approx(r.p_c + r.dp_drill_pipe + r.dp_bha + r.dp_bit + r.dp_annulus)
    assert r.bhp == pytest.approx(r.p_c + case.rho * u.G * case.td_tvd + r.dp_annulus)


def test_choke_holds_the_midpoint_target(base):
    case, seg = base
    assert case.target == pytest.approx((case.pore + case.frac) / 2)
    r = solve_steady(case, seg)
    assert r.target_reachable
    assert r.bhp == pytest.approx(case.target, rel=1e-9)
    assert u.ppg(r.ecd) == pytest.approx((10.4 + 11.8) / 2, rel=1e-9)


def test_choke_pressure_adds_one_to_one(base):
    case, seg = base
    r1, r2 = solve_steady(case, seg, p_c=1e6), solve_steady(case, seg, p_c=2e6)
    assert r2.bhp - r1.bhp == pytest.approx(1e6)


def test_ecd_definitions(base):
    case, seg = base
    r = solve_steady(case, seg)
    assert r.ecd == pytest.approx(r.bhp / (u.G * case.td_tvd))                          # proposal
    assert r.ecd_no_sbp == pytest.approx(case.rho + r.dp_annulus / (u.G * case.td_tvd))  # conventional
    assert r.ecd > r.ecd_no_sbp > case.rho


def test_u_tube_at_bit(base):
    # pressure inside the string at the bit minus the bit drop = annulus pressure at the bit
    case, seg = base
    r = solve_steady(case, seg)
    prof = r.profile
    inside = prof[prof.path == "string"]["pressure"].iloc[-1]
    outside = prof[prof.path == "annulus"]["pressure"].iloc[-1]
    assert inside - r.dp_bit == pytest.approx(outside)


def test_unreachable_target_is_reported(base):
    case, seg = base
    r = solve_steady(case, seg, q=0.0, q_bpp=0.0)          # no flow: a choke cannot hold pressure
    assert not r.target_reachable
    assert r.z == case.choke.z_min
    assert r.bhp < case.target
