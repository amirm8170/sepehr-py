"""Case CSV (one file with every input) and the automatic fixes."""

import math

import pytest
import yaml

from mpd import units as u
from mpd.autofix import auto_fix, safe_limits
from mpd.hydraulics import solve_steady
from mpd.library import read_case_csv, write_case_csv
from mpd.well import build_case, load_config, make_segments
from tests.conftest import ROOT


@pytest.fixture
def config():
    return load_config(ROOT / "config" / "base_case.yaml")


def steady(cfg):
    case = build_case(cfg)
    return case, solve_steady(case, make_segments(case))


# --- one CSV with everything -------------------------------------------
def test_csv_round_trip_gives_the_same_results(config, tmp_path):
    # the CSV holds every input (survey written inline), so BHP and SPP must be identical
    path = tmp_path / "case.csv"
    write_case_csv(path, {"Base": config})
    loaded = read_case_csv(path)["Base"]
    assert "survey_file" not in loaded["well"] and len(loaded["well"]["survey"]) > 10
    (_, a), (_, b) = steady(config), steady(loaded)
    assert b["bhp"] == pytest.approx(a["bhp"], rel=1e-9)
    assert b["spp"] == pytest.approx(a["spp"], rel=1e-9)
    yaml.safe_dump(loaded)                                          # plain numbers only


def test_second_case_only_overrides_filled_cells(tmp_path):
    path = tmp_path / "case.csv"
    path.write_text((ROOT / "data" / "case_library.csv").read_text())
    cases = read_case_csv(path)
    base, obm = cases["Base WBM 10.0 ppg"], cases["OBM 10.5 ppg"]
    assert obm["mud"]["density_ppg"] == 10.5 and base["mud"]["density_ppg"] == 10.0
    assert obm["well"] == base["well"]                                # empty cells = first case


def test_missing_and_unknown_parameters_are_reported(config, tmp_path):
    path = tmp_path / "case.csv"
    write_case_csv(path, {"Base": config})
    lines = path.read_text().splitlines()
    path.write_text("\n".join(l for l in lines if not l.startswith("mud.pv_cp")))
    with pytest.raises(ValueError, match="mud.pv_cp"):
        read_case_csv(path)
    path.write_text("\n".join(lines + ["mud.density_kg,,,1200"]))
    with pytest.raises(ValueError, match="density_kg"):
        read_case_csv(path)


# --- automatic fixes ------------------------------------------------------
def test_base_case_needs_no_fix(config):
    assert auto_fix(config)[1] == []


def test_target_outside_window_moves_by_hand_calculated_margin(config):
    # margin 100 psi -> ppg by hand:  100 / (0.052 psi/ft/ppg * TVD_ft)
    config["pressure_window"]["target_ppg"] = 13.0
    cfg, report = auto_fix(config)
    tvd_ft = build_case(cfg).td_tvd / u.FT
    assert cfg["pressure_window"]["target_ppg"] == pytest.approx(11.8 - 100 / (0.052 * tvd_ft), abs=2e-3)
    assert "outside the safe window" in report[0]["problem"]


def test_pumps_off_starts_back_pressure_pump(config):
    # pumps off: no flow through the choke -> BHP = rho g TVD (10 ppg) < pore (kick risk)
    config["operation"]["pump_rate_gpm"] = 0
    case, before = steady(config)
    assert u.pressure_to_emw(before["bhp"], case.td_tvd) == pytest.approx(10.0)
    cfg, report = auto_fix(config)
    assert "KICK" in report[0]["problem"] and cfg["operation"]["bp_pump_gpm"] > 0
    case, after = steady(cfg)
    assert after["bhp"] == pytest.approx(case.target, rel=1e-4)


def test_fixed_back_pressure_is_set_to_the_needed_value(config):
    # needed SBP = target - rho g TVD - annular friction  (BHP = SBP + hydrostatic + friction)
    config["operation"].update(back_pressure_from="fixed", back_pressure_psi=900)
    case, r = steady(config)
    needed = case.target - case.rho * u.G * case.td_tvd - r["dp_annulus"]
    cfg, report = auto_fix(config)
    assert "LOSS" in report[0]["problem"]
    assert cfg["operation"]["back_pressure_psi"] == math.floor(u.psi(needed))


def test_too_heavy_mud_is_cut_to_reach_the_target(config):
    config["mud"]["density_ppg"] = 11.9                             # mud column alone above target
    cfg, report = auto_fix(config)
    assert "mud is too heavy" in report[0]["why"]
    case, r = steady(cfg)
    low, high = safe_limits(case)
    assert cfg["mud"]["density_ppg"] < 11.0 and low <= r["bhp"] <= high
    assert r["bhp"] == pytest.approx(case.target, abs=0.05 * u.BAR)


def test_report_only_when_fix_is_not_wanted(config):
    config["operation"]["pump_rate_gpm"] = 0
    auto_fix(config)
    assert config["operation"]["bp_pump_gpm"] == 0                  # the input is never changed
