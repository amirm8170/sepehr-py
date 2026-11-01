"""Choke, actuator and PID tests (hand calculations and textbook control results)."""

import math
from dataclasses import replace

import pytest

from mpd.choke import actuator_rate, choke_area, choke_flow, choke_pressure, opening_for, smooth_positive
from mpd.controller import PID, controller_gains
from mpd.well import ActuatorSpec, ChokeSpec, PIDGains

SPEC = ChokeSpec(a_max=2e-3, cd=0.7, characteristic="linear", rangeability=50, z_min=0.01, z_max=1.0,
                 p0=0.0, eps=1e4)
ACT = ActuatorSpec(tau=1.0, max_rate=0.05, dead_time=0.0)


def test_orifice_equation_by_hand():
    # dp >> eps: q = Cd A z sqrt(2 dp / rho)
    q = choke_flow(20e5, 0.5, 1200, SPEC)
    dp_plus = 0.5 * (20e5 + math.sqrt(20e5 ** 2 + 1e8))
    assert q == pytest.approx(0.7 * 2e-3 * 0.5 * math.sqrt(2 * dp_plus / 1200))
    assert q == pytest.approx(0.7 * 2e-3 * 0.5 * math.sqrt(2 * 20e5 / 1200), rel=1e-4)


def test_smooth_positive_limits():
    assert smooth_positive(1e6, 1e3) == pytest.approx(1e6, rel=1e-6)
    assert smooth_positive(-1e6, 1e3) == pytest.approx(0.0, abs=1.0)
    assert smooth_positive(0.0, 1e3) == pytest.approx(500.0)


def test_choke_pressure_is_the_inverse():
    for q in (0.005, 0.03, 0.06):
        for z in (0.05, 0.4, 1.0):
            assert choke_flow(choke_pressure(q, z, 1200, SPEC), z, 1200, SPEC) == pytest.approx(q, rel=1e-9)


def test_equal_percentage_curve_end_points():
    eq = replace(SPEC, characteristic="equal_percentage")
    assert choke_area(1.0, eq) == pytest.approx(2e-3)
    assert choke_area(0.5, eq) == pytest.approx(2e-3 * 50 ** -0.5)


def test_opening_for_target_and_limits():
    p_c = choke_pressure(0.03, 0.37, 1200, SPEC)
    z, ok = opening_for(0.03, p_c, 1200, SPEC)
    assert ok and z == pytest.approx(0.37, rel=1e-8)
    assert opening_for(0.03, 1.0, 1200, SPEC) == (1.0, False)            # needs more than fully open
    assert opening_for(0.0001, 50e5, 1200, SPEC) == (0.01, False)        # needs less than z_min


def test_actuator_rate_limit_and_first_order_lag():
    assert actuator_rate(1.0, 0.0, ACT, SPEC) == pytest.approx(0.05, rel=1e-6)        # far: r_max
    small = 0.001                                                                       # near: (z_cmd - z) / tau
    assert actuator_rate(0.5 + small, 0.5, ACT, SPEC) == pytest.approx(small / ACT.tau, rel=1e-3)
    assert actuator_rate(0.0, 0.01, ACT, SPEC) == 0.0                                  # stops at z_min
    assert actuator_rate(2.0, 1.0, ACT, SPEC) == 0.0                                   # stops at z_max


# --- PID ---------------------------------------------------------------
def gains(kp, ki=0.0, kd=0.0, n=8, tt=math.nan):
    return PIDGains(kp=kp, ki=ki, kd=kd, n_filter=n, tracking_time=tt)


def test_reverse_action_closes_the_choke_when_bhp_is_low():
    pid = PID(gains(0.1), z0=0.5, z_min=0.01, z_max=1.0, sample_time=0.1)
    assert pid.update(+2.0) < 0.5          # target - BHP > 0 -> close
    assert pid.update(-2.0) > 0.5          # BHP above target -> open


def first_order_plant(kp, ki, steps=6000, ts=0.05):
    """y' = (u - y) / 5 with u = 1 - z_cmd (closing raises y), target 0.5."""
    pid = PID(gains(kp, ki), z0=1.0, z_min=-100, z_max=100, sample_time=ts)
    y = 0.0
    for _ in range(steps):
        z = pid.update(0.5 - y)
        y += ts / 5 * ((1.0 - z) - y)
    return y


def test_p_control_leaves_textbook_offset():
    # loop gain K = kp: error = target / (1 + kp)
    assert 0.5 - first_order_plant(4.0, 0.0) == pytest.approx(0.5 / 5, rel=1e-3)


def test_pi_control_removes_offset():
    assert first_order_plant(2.0, 0.5) == pytest.approx(0.5, abs=1e-4)


def test_back_calculation_limits_windup():
    # long saturation: with anti-windup the integral stays bounded (Ki e + (u_sat-u)/Tt -> 0)
    pid = PID(gains(0.1, ki=0.05), z0=0.5, z_min=0.01, z_max=1.0, sample_time=0.1)
    for _ in range(5000):
        pid.update(+10.0)                  # wants z far below z_min
    # equilibrium of the integral: Ki e = (u_unsat - u_sat)/Tt -> u_unsat = u_sat + Ki e Tt
    t_track = 0.1 / 0.05
    expected_u = (0.5 - 0.01) + 0.05 * 10.0 * t_track
    assert pid.terms["u_unsat"] == pytest.approx(expected_u, rel=1e-3)
    assert pid.terms["z_cmd"] == 0.01


def test_derivative_filter_first_step():
    # error step 1 -> D = Kd / (Tf + Ts), Tf = (Kd/Kp)/N
    pid = PID(gains(0.1, kd=0.4, n=8), z0=0.5, z_min=-10, z_max=10, sample_time=0.1)
    pid.update(0.0)
    pid.update(1.0)
    tf = (0.4 / 0.1) / 8
    assert pid.terms["d"] == pytest.approx(0.4 / (tf + 0.1))


def test_controller_kinds():
    g = gains(0.08, 0.008, 0.02)
    assert controller_gains(g, "P").ki == 0 and controller_gains(g, "P").kd == 0
    assert controller_gains(g, "PI").kd == 0 and controller_gains(g, "PI").ki == 0.008
    assert controller_gains(g, "PID") == g
