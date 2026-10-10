"""Stage B tests: PID controller and dynamic simulation."""

import numpy as np
import pytest
import yaml

from mpd import units as u
from mpd.dynamics import PID, simulate
from mpd.hydraulics import solve_steady
from tests.conftest import ROOT

SCENARIOS = yaml.safe_load(open(ROOT / "config" / "scenarios.yaml"))


def first_order_plant(pid, steps=4000, dt=0.05):
    """Plant  5 dy/dt = -y + u, target 1."""
    y = 0.0
    for _ in range(steps):
        y += dt / 5 * (-y + pid.update(1.0, y, dt))
    return y


def test_p_control_leaves_offset():
    # textbook result: error = 1 / (1 + Kp)
    assert 1 - first_order_plant(PID(4, 0, 0, -100, 100)) == pytest.approx(1 / 5, rel=1e-3)


def test_pi_control_removes_offset():
    assert first_order_plant(PID(2, 0.5, 0, -100, 100)) == pytest.approx(1.0, abs=1e-4)


def test_output_limits_and_rate_limit():
    pid = PID(100, 0, 0, 0.0, 1.0, rate_limit=0.5)
    outputs = [pid.update(10, 0, 0.1) for _ in range(30)]
    assert max(outputs) <= 1.0
    assert max(np.diff([0.0] + outputs)) <= 0.05 + 1e-12


def test_reverse_action():
    pid = PID(1, 0, 0, 0, 1, reverse=True)
    pid.reset(0.5)
    assert pid.update(10, 9, 0.1) < 0.5          # pressure too low -> close the choke


def test_constant_scenario_stays_at_steady_state(base):
    case, seg = base
    ts = simulate(case, seg, SCENARIOS["constant_pump_rate"])["timeseries"]
    assert ts["bhp"].max() - ts["bhp"].min() < 1.0             # less than 1 Pa change
    assert ts["bhp"].iloc[0] == pytest.approx(solve_steady(case, seg)["bhp"])


def test_lumped_model_reaches_new_steady_state(base):
    case, seg = base
    sim = simulate(case, seg, SCENARIOS["choke_closing"])
    ts = sim["timeseries"]
    steady = solve_steady(case, seg, opening=ts["choke_opening"].iloc[-1])
    assert ts["bhp"].iloc[-1] == pytest.approx(steady["bhp"], rel=1e-4)
    assert ts["choke_flow"].iloc[-1] == pytest.approx(case.pump_rate, rel=1e-3)   # mass balance


def test_pump_stop_without_control_gives_kick_risk(base):
    case, seg = base
    sim = simulate(case, seg, SCENARIOS["pump_stop"])
    assert sim["timeseries"]["status"].iloc[-1] == "kick risk"


def test_pid_keeps_bhp_in_window(base):
    case, seg = base
    sim = simulate(case, seg, SCENARIOS["pid_control"])
    assert sim["metrics"]["window_held"]
    assert abs(sim["metrics"]["steady_state_error"]) < 0.1 * u.BAR


def test_back_pressure_actuator(base):
    case, seg = base
    ts = simulate(case, seg, SCENARIOS["back_pressure_change"])["timeseries"]
    assert u.psi(ts["sbp"].iloc[-1]) == pytest.approx(300, rel=1e-3)
