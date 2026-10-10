"""Metrics, sensitivity and validation tests."""

import numpy as np
import pandas as pd
import pytest

from mpd import analysis
from mpd.well import load_config
from tests.conftest import ROOT


def test_percentage_error():
    assert analysis.percentage_error(110, 100) == pytest.approx(10)
    assert analysis.percentage_error(95, 100) == pytest.approx(5)


def test_settling_time_of_first_order_response():
    # y = 10 (1 - e^(-t/5)), band 0.2 -> settles when 10 e^(-t/5) = 0.2 -> t = 5 ln 50
    t = np.arange(0, 100, 0.1)
    ts = pd.DataFrame({"time_s": t, "bhp": 10 * (1 - np.exp(-t / 5)), "target": 10.0,
                       "status": "safe", "error": 10 * np.exp(-t / 5)})
    m = analysis.control_metrics(ts, 0.2, [0])
    assert m["settling_time_s"] == pytest.approx(5 * np.log(50), abs=0.15)


def test_rise_time_of_target_step():
    # target steps at t = 10; first-order response -> 10-90 % rise time = 5 ln 9
    t = np.arange(0, 100, 0.1)
    y = np.where(t >= 10, 10 * (1 - np.exp(-(t - 10) / 5)), 0.0)
    target = np.where(t >= 10, 10.0, 0.0)
    ts = pd.DataFrame({"time_s": t, "bhp": y, "target": target, "status": "safe",
                       "error": target - y})
    assert analysis.control_metrics(ts, 0.2, [10])["rise_time_s"] == pytest.approx(5 * np.log(9), abs=0.15)


def test_sensitivity_signs():
    table = analysis.sensitivity(load_config(ROOT / "config" / "base_case.yaml"), 0.1)
    mw = table[table.parameter == "Mud weight"].iloc[0]
    assert mw.bhp_high > 0 > mw.bhp_low                       # heavier mud -> higher BHP


def test_validation_against_hand_calculation():
    from mpd.hydraulics import solve_steady
    from mpd.well import build_case, make_segments
    case = build_case(load_config(ROOT / "config" / "validation_case.yaml"))
    table = analysis.validate(solve_steady(case, make_segments(case)),
                              pd.read_csv(ROOT / "data" / "validation_reference.csv"))
    errors = dict(zip(table.quantity, table.error_percent))
    assert errors["drill_pipe_loss"] < 0.5 and errors["bit_loss"] < 0.5
    assert errors["bhp"] < 1.0
    assert errors["annulus_loss"] < 12        # exact vs simplified laminar formula
