"""
Four-state dynamic model: consistency with the steady model, conservation,
convergence, actuator / measurement behaviour, and scenario regression (S0 - S5).
"""

from dataclasses import replace

import numpy as np
import pytest

from mpd import units as u
from mpd.dynamics import check_convergence, simulate, steady_state_residual
from mpd.hydraulics import solve_steady
from mpd.scenarios import (bpp_check, metrics_table, run_compare, run_dynamic, run_sensitivity,
                           run_steady_rates, schedule_of)


@pytest.fixture(scope="module")
def s3(base, specs):
    case, seg = base
    return run_compare(case, seg, "S4", specs, convergence=False)


def test_steady_point_has_zero_derivatives(base):
    case, seg = base
    for name, value in steady_state_residual(case, seg).items():
        assert abs(value) < 1e-12, name


def test_s0_dynamic_model_stays_at_each_steady_point(base, specs):
    case, seg = base
    table, _ = run_steady_rates(case, seg, specs["S0"])
    assert (table["dynamic_drift"] < 1.0).all()               # less than 1 Pa
    assert np.allclose(table["bhp"], case.target)              # choke holds the target at every rate


def test_s1_open_loop_ends_at_the_new_steady_state(base, specs):
    # independent check: after the pump step the dynamic model must settle at the
    # steady solution for 650 gpm with the same (fixed) choke opening
    case, seg = base
    r = run_dynamic(case, seg, "S1", specs, convergence=False)
    final = r.timeseries.iloc[-1]
    expected = solve_steady(case, seg, q=650 * u.GPM, z=r.initial.z)
    assert final["bhp"] == pytest.approx(expected.bhp, rel=1e-4)
    assert final["spp"] == pytest.approx(expected.spp, rel=5e-4)          # still settling slowly
    assert final["q_choke"] == pytest.approx(650 * u.GPM, rel=1e-4)


def test_s2_choke_step_follows_actuator_limits(base, specs):
    case, seg = base
    r = run_dynamic(case, seg, "S2", specs, convergence=False)
    ts = r.timeseries
    rate = np.abs(np.diff(ts["z"]) / np.diff(ts["time_s"]))
    assert rate.max() <= case.actuator.max_rate + 1e-9          # r_max respected
    assert ts["z"].iloc[-1] == pytest.approx(r.initial.z - 0.05, abs=1e-4)
    expected = solve_steady(case, seg, z=r.initial.z - 0.05)
    assert ts["bhp"].iloc[-1] == pytest.approx(expected.bhp, rel=1e-4)
    assert ts["bhp"].iloc[-1] > ts["bhp"].iloc[0]                # closing raises BHP


def test_measurement_is_delayed(base, specs):
    case, seg = base
    r = run_dynamic(case, seg, "S2", specs, convergence=False)
    ts = r.timeseries
    t = ts["time_s"].values
    expected = np.interp(t - case.measurement.delay, t, ts["bhp"].values)    # BHP(t - theta_m)
    assert np.allclose(ts["bhp_measured"].values, expected, rtol=0, atol=1e-6)


def test_mass_balance_and_convergence(base, specs, s3):
    case, seg = base
    pid = s3["PID"]
    assert pid.mass_balance["relative_percent"] < 1e-6
    conv = check_convergence(case, seg, schedule_of("S3", specs), pid)
    assert conv["passed"] and conv["change_percent"] < case.solver.convergence_limit


def test_rk45_gives_the_same_answer(base, specs):
    case, seg = base
    a = simulate(case, seg, specs["S2"], "none")
    b = simulate(case, seg, specs["S2"], "none", method="RK45")
    assert np.abs(a.timeseries["bhp"] - b.timeseries["bhp"]).max() < 100      # < 0.001 bar


def test_s3_without_control_leaves_the_safe_window(s3, base):
    case, _ = base
    m = s3["none"].metrics
    assert m["time_in_window_percent"] < 95
    assert m["min_bhp"] < case.safe_low


def test_s4_controllers_meet_the_proposal_criteria(s3):
    for kind in ("PI", "PID"):
        assert all(passed for *_, passed in s3[kind].metrics["criteria"]), kind
    # PI removes most of the error left by P (regression on the tuned gains)
    assert s3["PI"].metrics["rmse"] < 0.5 * s3["P"].metrics["rmse"] < s3["none"].metrics["rmse"]
    assert u.bar(s3["PI"].metrics["max_abs_deviation"]) == pytest.approx(4.33, abs=0.1)
    table = metrics_table(s3)
    assert list(table["run"]) == ["none", "P", "PI", "PID"]


def test_reverse_acting_control_closes_the_choke_when_pumps_stop(s3):
    ts = s3["PID"].timeseries
    stopped = ts[(ts.time_s > 150) & (ts.time_s < 260)]
    assert stopped["z"].mean() < 0.5 * ts["z"].iloc[0]


def test_bpp_assumption_is_feasible(base, specs):
    case, seg = base
    b = bpp_check(case, seg, specs)
    assert b["reachable"] and case.choke.z_min < b["opening_needed"] < 0.5


def test_s5_sensitivity_directions(config, specs):
    small = dict(specs)
    small["S5"] = dict(specs["S5"], parameters=["beta_eff"], equal_percentage=False)
    table, _ = run_sensitivity(config, "S5", small)
    low, high = table.iloc[1], table.iloc[2]
    # a stiffer system (larger beta_eff) reacts faster to the choke -> smaller deviation
    assert high["max_dev_bar"] < table.iloc[0]["max_dev_bar"] < low["max_dev_bar"]


def test_noise_is_off_by_default_and_reproducible(base, specs):
    case, seg = base
    assert case.measurement.noise_std == 0
    noisy = replace(case, measurement=replace(case.measurement, noise_std=0.5 * u.BAR))
    a = simulate(noisy, seg, specs["S2b"], "PID")
    b = simulate(noisy, seg, specs["S2b"], "PID")
    assert np.array_equal(a.timeseries["bhp_measured"], b.timeseries["bhp_measured"])
