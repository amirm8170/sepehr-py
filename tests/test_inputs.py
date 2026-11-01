"""Input model: validation, defaults, effective bulk modulus, volumes, migration, case CSV."""

import copy
import math

import pytest
import yaml

from mpd import units as u
from mpd.hydraulics import solve_steady
from mpd.library import read_case_csv, write_case_csv
from mpd.well import (build_case, config_errors, flow_path, load_config, make_segments, migrate_config,
                      thin_wall_bulk_modulus)
from tests.conftest import ROOT


@pytest.mark.parametrize("path, value, message", [
    ("mud.density_ppg", 0.0, "Mud density"),
    ("mud.pv_cp", -1.0, "Plastic viscosity"),
    ("compressibility.fluid_bulk_modulus_pa", 0.0, "beta_f"),
    ("pressure_window.fracture_ppg", 10.0, "below fracture"),
    ("pressure_window.target_ppg", 12.0, "outside the safe window"),
    ("pressure_window.safety_margin_psi", 5000.0, "no safe window"),
    ("choke.min_opening", 0.0, "Choke limits"),
    ("choke.max_opening", 1.5, "Choke limits"),
    ("choke.discharge_coefficient", 1.2, "C_d"),
    ("measurement.sample_time_s", 0.0, "Sample time"),
    ("numerics.friction_model", "power_law", "Friction model"),
])
def test_invalid_input_is_rejected(config, path, value, message):
    cfg = copy.deepcopy(config)
    *parents, key = path.split(".")
    place = cfg
    for p in parents:
        place = place[p]
    place[key] = value
    errors = config_errors(cfg)
    assert any(message in e for e in errors), errors
    with pytest.raises(ValueError):
        build_case(cfg)


def test_impossible_geometry_is_rejected(config):
    cfg = copy.deepcopy(config)
    cfg["well"]["drill_string"][0]["od_in"] = 9.0                 # pipe larger than the hole
    assert any("no annulus" in e for e in config_errors(cfg))
    cfg = copy.deepcopy(config)
    cfg["well"]["drill_string"][0]["length_m"] = 4000             # string longer than the well
    assert any("longer than the well" in e for e in config_errors(cfg))
    cfg = copy.deepcopy(config)
    cfg["well"]["survey"] = [[0, 0, 0, 0], [1000, 1200, 0, 0], [3500, 3400, 0, 0]]   # TVD > MD step
    del cfg["well"]["survey_file"]
    assert any("impossible trajectory" in e for e in config_errors(cfg))


def test_base_case_is_valid_and_target_defaults_to_midpoint(base):
    case, _ = base
    assert case.target_is_midpoint
    assert u.pressure_to_emw(case.target, case.td_tvd) == pytest.approx(11.1)
    assert case.safe_low == pytest.approx(case.pore + 100 * u.PSI)


def test_effective_bulk_modulus(base):
    case, _ = base
    assert 1 / case.beta_eff == pytest.approx(1 / case.beta_f + 1 / case.beta_w)
    # beta_w of the base case from the thin-wall formula 2 E t / D (steel, 5-in drill pipe)
    t, d = (5.0 - 4.276) / 2 * u.INCH, 4.276 * u.INCH
    assert thin_wall_bulk_modulus(200e9, t, d) == pytest.approx(case.beta_w, rel=0.01)


def test_volumes_and_inertance_by_hand(vertical):
    case, seg = vertical
    fp = flow_path(case, seg)
    a_dp, a_dc = math.pi / 4 * (4.276 * u.INCH) ** 2, math.pi / 4 * (2.8125 * u.INCH) ** 2
    hole = (8.5 * u.INCH) ** 2
    a_adp, a_adc = math.pi / 4 * (hole - (5 * u.INCH) ** 2), math.pi / 4 * (hole - (6.5 * u.INCH) ** 2)
    assert fp.v_d == pytest.approx(a_dp * 2895.6 + a_dc * 152.4)
    assert fp.v_a == pytest.approx(a_adp * 2895.6 + a_adc * 152.4)
    m = case.rho * (2895.6 / a_dp + 152.4 / a_dc + 2895.6 / a_adp + 152.4 / a_adc)
    assert fp.inertance == pytest.approx(m)


OLD_FORMAT = {
    "name": "old", "well": load_config(ROOT / "config" / "validation_case.yaml")["well"],
    "mud": {"density_ppg": 10.0, "pv_cp": 20.0, "yp_lbf100ft2": 15.0, "gel_10min_lbf100ft2": 10.0},
    "operation": {"pump_rate_gpm": 350, "back_pressure_from": "choke", "back_pressure_psi": 100,
                  "choke_opening": "auto", "bp_pump_gpm": 0, "pipe_velocity_m_s": 0.0},
    "pressure_window": {"pore_ppg": 9.5, "fracture_ppg": 12.5, "target_ppg": 10.5, "warning_margin_psi": 100},
    "choke": {"cv_max": 100.0, "cv_closed": 1.0, "min_opening": 0.02, "max_opening": 1.0, "max_speed_per_s": 0.05},
    "dynamics": {"model": "lumped", "actuator": "choke", "time_step_s": 0.1, "bulk_modulus_pa": 1.5e9},
}


def test_old_case_files_are_migrated():
    new, notes = migrate_config(OLD_FORMAT)
    assert config_errors(new) == []
    assert any("gel strength" in n for n in notes)
    # same choke: Q = Cv sqrt(dp/SG) [gpm, psi] = Cd A sqrt(2 dp / rho)
    k = 100 * u.GPM * math.sqrt(1000 / u.PSI)
    assert new["choke"]["max_area_in2"] * u.INCH ** 2 * 0.7 * math.sqrt(2) == pytest.approx(k, rel=1e-4)


# --- one CSV with everything -------------------------------------------
def steady(cfg):
    case = build_case(cfg)
    return solve_steady(case, make_segments(case))


def test_csv_round_trip_gives_the_same_results(config, tmp_path):
    path = tmp_path / "case.csv"
    write_case_csv(path, {"Base": config})
    loaded = read_case_csv(path)["Base"]
    assert "survey_file" not in loaded["well"] and len(loaded["well"]["survey"]) > 10
    a, b = steady(config), steady(loaded)
    assert b.bhp == pytest.approx(a.bhp, rel=1e-9)
    assert b.spp == pytest.approx(a.spp, rel=1e-9)
    yaml.safe_dump(loaded)                                          # plain numbers only


def test_second_case_only_overrides_filled_cells(tmp_path):
    path = tmp_path / "case.csv"
    path.write_text(open(ROOT / "data" / "case_library.csv").read())
    cases = read_case_csv(path)
    first, light = cases["Base WBM 10.0 ppg"], cases["Light WBM 9.6 ppg"]
    assert light["mud"]["density_ppg"] == 9.6
    assert light["choke"] == first["choke"]


def test_unknown_parameter_is_reported(config, tmp_path):
    path = tmp_path / "case.csv"
    write_case_csv(path, {"Base": config})
    text = path.read_text() + "mud.colour,,,blue\n"
    path.write_text(text)
    with pytest.raises(ValueError, match="Unknown parameters"):
        read_case_csv(path)
