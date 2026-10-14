"""
MPD well hydraulics simulator - command-line program.

    python main.py                         steady state + all 10 scenarios
    python main.py --scenario pid_control  one scenario
    python main.py --sensitivity           + sensitivity analysis
    python main.py --validate              + comparison with reference data
    python main.py --case config/validation_case.yaml
    python main.py --case data/case_library.csv --variant "OBM 11.0 ppg"

Results (CSV files and PNG charts) are saved in the folder outputs/.
For the window version run:  python app.py
"""

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")                         # draw charts without a window
import pandas as pd
import yaml
from matplotlib.figure import Figure

from mpd import analysis, plots
from mpd import units as u
from mpd.dynamics import simulate
from mpd.hydraulics import solve_steady
from mpd.autofix import auto_fix
from mpd.library import read_case_csv
from mpd.well import build_case, load_config, make_segments

OUT = Path("outputs")


def save(fig, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130, bbox_inches="tight")
    print("   saved", path)


def run_steady(case, seg):
    r = solve_steady(case, seg)
    print(f"\nSTEADY STATE  ({case.name})")
    print(f"   Drill pipe friction   {u.bar(r['dp_drill_pipe']):8.2f} bar")
    print(f"   BHA friction          {u.bar(r['dp_bha']):8.2f} bar")
    print(f"   Bit pressure drop     {u.bar(r['dp_bit']):8.2f} bar")
    print(f"   Annulus friction      {u.bar(r['dp_annulus']):8.2f} bar")
    print(f"   Surface back pressure {u.bar(r['sbp']):8.2f} bar")
    print(f"   SPP                   {u.bar(r['spp']):8.2f} bar")
    print(f"   BHP                   {u.bar(r['bhp']):8.2f} bar   ({r['status']})")
    print(f"   ECD                   {u.ppg(r['ecd']):8.3f} ppg   EMW of BHP {u.ppg(r['emw']):.3f} ppg")
    folder = OUT / "steady_state"
    folder.mkdir(parents=True, exist_ok=True)
    r["segments"].to_csv(folder / "segments.csv", index=False)
    r["profile"].to_csv(folder / "pressure_profile.csv", index=False)
    fig = Figure(figsize=(11, 6.5), layout="constrained")
    plots.pressure_profile(fig, r, case)
    save(fig, folder / "pressure_profile.png")
    fig = Figure(figsize=(8, 3.5), layout="constrained")
    plots.spp_breakdown(fig, r)
    save(fig, folder / "spp_breakdown.png")
    return r


def run_scenario(case, seg, name, scenario):
    sim = simulate(case, seg, scenario)
    m = sim["metrics"]
    print(f"\n{scenario.get('title', name)}: BHP {u.bar(m['min_bhp']):.1f} - {u.bar(m['max_bhp']):.1f} bar,"
          f" outside window {m['time_outside_window_s']:.1f} s")
    folder = OUT / name
    folder.mkdir(parents=True, exist_ok=True)
    sim["timeseries"].to_csv(folder / "timeseries.csv", index=False)
    fig = Figure(figsize=(10, 10), layout="constrained")
    plots.dynamic_overview(fig, sim)
    save(fig, folder / "overview.png")
    fig = Figure(figsize=(10, 4), layout="constrained")
    plots.ecd_chart(fig, sim, case)
    save(fig, folder / "ecd.png")
    if sim["pid"]:
        fig = Figure(figsize=(10, 6), layout="constrained")
        plots.pid_chart(fig, sim)
        save(fig, folder / "pid_response.png")


def run_sensitivity(config):
    print("\nSENSITIVITY (+/-10 %)")
    table = analysis.sensitivity(config, 0.10)
    for _, r in table.iterrows():
        print(f"   {r['parameter']:22s} BHP {u.bar(r['bhp_low']):+7.2f} / {u.bar(r['bhp_high']):+7.2f} bar")
    folder = OUT / "sensitivity"
    folder.mkdir(parents=True, exist_ok=True)
    table.to_csv(folder / "sensitivity.csv", index=False)
    fig = Figure(figsize=(13, 4.5), layout="constrained")
    plots.tornado(fig, table)
    save(fig, folder / "tornado.png")


def run_validation():
    config = load_config("config/validation_case.yaml")
    case = build_case(config)
    result = solve_steady(case, make_segments(case))
    table = analysis.validate(result, pd.read_csv("data/validation_reference.csv"))
    print("\nVALIDATION  (synthetic hand-calculation data - demonstration only)")
    print(table[["quantity", "unit", "reference", "model", "error_percent"]].to_string(
        index=False, float_format="%.2f"))
    folder = OUT / "validation"
    folder.mkdir(parents=True, exist_ok=True)
    table.to_csv(folder / "validation.csv", index=False)
    fig = Figure(figsize=(11, 4.5), layout="constrained")
    plots.validation_chart(fig, table)
    save(fig, folder / "validation.png")


def main():
    parser = argparse.ArgumentParser(description="MPD well hydraulics simulator")
    parser.add_argument("--case", default="config/base_case.yaml", help="YAML or CSV case file")
    parser.add_argument("--scenario", default="all", help="'all', 'none' or a scenario name")
    parser.add_argument("--sensitivity", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--variant", help="case column of a CSV case file (default: the first)")
    parser.add_argument("--no-fix", action="store_true", help="only report problems, do not auto-fix")
    args = parser.parse_args()

    if args.case.endswith(".csv"):
        cases = read_case_csv(args.case)
        name = args.variant or next(iter(cases))
        if name not in cases:
            parser.error(f"case '{name}' not found; choose from: {', '.join(cases)}")
        config = cases[name]
        print(f"Case: {name}")
    else:
        config = load_config(args.case)
    fixed, report = auto_fix(config)
    if not args.no_fix:
        config = fixed
    for i, item in enumerate(report, 1):
        print(f"\nAUTO-FIX {i}: {item['problem']}\n   Why:  {item['why']}\n"
              f"   {'Done' if not args.no_fix else 'Would do'}: {item['done']}\n   How to fix on the rig: {item['how']}")
    case = build_case(config)
    seg = make_segments(case)
    print(f"Well split into {len(seg)} segments.")
    run_steady(case, seg)

    with open("config/scenarios.yaml", encoding="utf-8") as f:
        scenarios = yaml.safe_load(f)
    names = list(scenarios) if args.scenario == "all" else (
        [] if args.scenario == "none" else [args.scenario])
    for name in names:
        run_scenario(case, seg, name, scenarios[name])
    if args.sensitivity:
        run_sensitivity(config)
    if args.validate:
        run_validation()
    print("\nNote: synthetic demonstration data - research / teaching software, "
          "not certified for real drilling operations.")


if __name__ == "__main__":
    main()
