"""Performance metrics, suggestions, validation levels and analysis helpers."""

import copy

import numpy as np
import pandas as pd
import pytest

from mpd import units as u
from mpd.analysis import steady_sensitivity
from mpd.extensions import surge_swab_bhp
from mpd.hydraulics import solve_steady
from mpd.metrics import mape, performance_metrics
from mpd.suggestions import apply_suggestion, suggestions
from mpd.validation import COLUMNS, PENDING, hand_calculation_table, level2, level3
from mpd.well import build_case, config_errors, make_segments
from tests.conftest import ROOT

CRITERIA = {"max_overshoot_percent": 5, "max_settling_time_s": 60, "settling_band_percent": 2,
            "min_time_in_window_percent": 95}


def series(t, y, r, low=0.0, high=1e9):
    return pd.DataFrame({"time_s": t, "bhp": y, "target": r, "safe_low": low, "safe_high": high})


def test_settling_time_of_first_order_step():
    # y = 100 + 10 (1 - e^(-t/5)), target 110: within 2 % (2.2) when 10 e^(-t/5) = 2.2
    t = np.arange(0, 100, 0.01)
    m = performance_metrics(series(t, 100 + 10 * (1 - np.exp(-t / 5)), 110.0), 0.0, CRITERIA)
    assert m["settling_time_s"] == pytest.approx(5 * np.log(10 / 2.2), abs=0.02)


def test_overshoot_of_second_order_step():
    # underdamped 2nd order: overshoot = exp(-zeta pi / sqrt(1 - zeta^2))
    zeta, wn = 0.3, 1.0
    t = np.arange(0, 60, 0.001)
    wd = wn * np.sqrt(1 - zeta ** 2)
    y = 1 - np.exp(-zeta * wn * t) * (np.cos(wd * t) + zeta / np.sqrt(1 - zeta ** 2) * np.sin(wd * t))
    m = performance_metrics(series(t, 100 + 10 * y, 110.0), 0.0, CRITERIA)
    assert m["overshoot_percent"] == pytest.approx(100 * np.exp(-zeta * np.pi / np.sqrt(1 - zeta ** 2)), rel=1e-3)
    assert m["overshoot_basis"] == "target step"


def test_rmse_mape_window_and_criteria():
    t = np.arange(10.0)
    y = np.array([100, 100, 104, 96, 100, 100, 100, 100, 100, 100.0])
    m = performance_metrics(series(t, y, 100.0, low=97, high=103), 0.0, CRITERIA)
    assert m["rmse"] == pytest.approx(np.sqrt(32 / 10))
    assert m["mape_percent"] == pytest.approx(0.8)
    assert m["max_abs_deviation"] == 4
    assert m["time_in_window_percent"] == pytest.approx(80)
    assert m["overshoot_percent"] == pytest.approx(4)           # disturbance: peak deviation / target
    passed = {name.split(" (")[0]: ok for name, _, _, ok in m["criteria"]}
    assert passed == {"Overshoot": True, "Settling time": True, "Time in safe window": False}
    assert mape([110, 90], [100, 100]) == pytest.approx(10)


# --- suggestions (nothing changed without the user) ---------------------
def test_base_case_needs_no_suggestion(config):
    assert suggestions(config) == []


def test_target_outside_window_suggests_midpoint(config):
    cfg = copy.deepcopy(config)
    cfg["pressure_window"]["target_ppg"] = 12.0
    before = copy.deepcopy(cfg)
    s = suggestions(cfg)
    assert cfg == before                                          # input untouched
    assert s[0]["changes"] == {"pressure_window.target_ppg": "midpoint"}
    assert config_errors(apply_suggestion(cfg, s[0])) == []


@pytest.mark.parametrize("path, value, change", [
    ("mud.density_ppg", 11.9, "mud.density_ppg"),                 # too heavy -> lighter mud
    ("operation.pump_rate_gpm", 0, "operation.bpp_rate_gpm"),     # pumps off -> back-pressure pump
])
def test_accepted_suggestion_makes_the_target_reachable(config, path, value, change):
    cfg = copy.deepcopy(config)
    section, key = path.split(".")
    cfg[section][key] = value
    s = [x for x in suggestions(cfg) if change in x["changes"]][0]
    fixed = build_case(apply_suggestion(cfg, s))
    assert solve_steady(fixed, make_segments(fixed)).target_reachable


# --- validation --------------------------------------------------------
def test_level1_hand_calculation_agrees():
    table = hand_calculation_table()
    assert list(table.columns) == COLUMNS
    errors = dict(zip(table.quantity, table.error_percent))
    assert max(errors[q] for q in ("drill_pipe_loss", "bha_loss", "annulus_loss")) < 0.01
    assert errors["bit_loss"] < 0.05 and errors["bhp"] < 0.2      # rounded oilfield constants
    assert (table["data_type"] == "synthetic hand calculation").all()


def test_levels_2_and_3_are_pending_without_reference_data(tmp_path):
    table, status, value = level2(tmp_path / "missing.csv")
    assert table is None and value is None and status.startswith(PENDING)
    assert level3(path=tmp_path / "missing.csv")[0] is None


def test_level2_with_a_reference_file(tmp_path):
    # a reference file pointing at the validation well (here: the hand calculation itself)
    path = tmp_path / "ref.csv"
    path.write_text("case_file,quantity,unit,p_c_psi,reference_value,data_type,source\n"
                    "config/validation_case.yaml,annulus_loss,psi,100,281.1683,test,unit test\n"
                    "config/validation_case.yaml,spp,psi,100,1925.6334,test,unit test\n")
    table, status, value = level2(path)
    assert value < 0.01 and "PASS" in status and list(table.columns) == COLUMNS


# --- analysis and the optional extension ------------------------------
def test_steady_sensitivity_signs(config):
    table = steady_sensitivity(config, 0.1, ["mud_weight", "choke_opening"])
    mw, choke = table.iloc[0], table.iloc[1]
    assert mw.bhp_high > 0 > mw.bhp_low                 # heavier mud -> higher BHP
    assert choke.bhp_high < 0 < choke.bhp_low           # opening the choke -> lower BHP


def test_surge_swab_extension(base):
    case, seg = base
    still = surge_swab_bhp(case, seg, 1e6, 0.0)
    assert surge_swab_bhp(case, seg, 1e6, 0.5) > still > surge_swab_bhp(case, seg, 1e6, -0.5)
    assert still == pytest.approx(1e6 + case.rho * u.G * case.td_tvd)
