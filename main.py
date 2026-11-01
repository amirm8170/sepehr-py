"""
MPD well hydraulics simulator - command-line program.

    python main.py                          steady state + scenarios S0 - S4
    python main.py --scenario S3            one scenario (S0, S1, S2, S2b, S3, S4, S5)
    python main.py --scenario all --s5      ... and the S5 sensitivity (slower)
    python main.py --validate               three-level validation
    python main.py --tune                   PID tuning study on S3
    python main.py --case config/validation_case.yaml
    python main.py --case data/case_library.csv --variant "OBM 10.5 ppg"

Results (CSV files and PNG charts) are saved in the folder outputs/.
Inputs are never changed automatically: problems are listed with suggestions.
For the window version run:  python app.py
"""

import argparse
import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")                         # draw charts without a window
from matplotlib.figure import Figure

from mpd import analysis, plots, scenarios, validation
from mpd import units as u
from mpd.dynamics import depth_profiles
from mpd.hydraulics import solve_steady
from mpd.library import read_case_csv
from mpd.suggestions import suggestions
from mpd.well import build_case, config_errors, load_config, make_segments

OUT = Path("outputs")
warnings.filterwarnings("ignore", category=RuntimeWarning)   # scipy's numerical Jacobian notes


def save(fig, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130, bbox_inches="tight")
    print("   saved", path)


def chart(path, size, draw, *args):
    fig = Figure(figsize=size, layout="constrained")
    draw(fig, *args)
    save(fig, path)


def run_steady(case, seg):
    r = solve_steady(case, seg)
    print(f"\nSTEADY STATE  ({case.name}, friction model {case.friction_model})")
    for name, value in (("Drill pipe loss", r.dp_drill_pipe), ("BHA loss", r.dp_bha), ("Bit loss", r.dp_bit),
                        ("Annulus loss", r.dp_annulus), ("Choke pressure p_c", r.p_c), ("SPP", r.spp),
                        ("BHP", r.bhp), ("Target", case.target)):
        print(f"   {name:20s} {u.bar(value):8.2f} bar")
    print(f"   Choke opening z0     {r.z:8.3f}   {'(target held)' if r.target_reachable else '(LIMIT - target not reached)'}")
    print(f"   ECD = BHP/(g h)      {u.ppg(r.ecd):8.3f} ppg   (without p_c {u.ppg(r.ecd_no_sbp):.3f} ppg)  -> {r.status}")
    folder = OUT / "steady_state"
    folder.mkdir(parents=True, exist_ok=True)
    r.segments.to_csv(folder / "segments.csv", index=False)
    r.profile.to_csv(folder / "pressure_profile.csv", index=False)
    chart(folder / "pressure_profile.png", (11, 6.5), plots.pressure_profile, r, case)
    chart(folder / "pressure_path.png", (9, 4), plots.pressure_path, r)
    return r


def print_metrics(label, r):
    m = r.metrics
    crit = "  ".join(f"{name.split(' (')[0]} {'PASS' if ok else 'FAIL'}" for name, _, _, ok in m["criteria"])
    conv = r.convergence
    print(f"   {label:22s} max dev {u.bar(m['max_abs_deviation']):6.2f} bar  RMSE {u.bar(m['rmse']):6.3f} bar  "
          f"in window {m['time_in_window_percent']:5.1f} %  |  {crit}")
    print(f"   {'':22s} mass balance {r.mass_balance['relative_percent']:.1e} %   step-halving "
          f"{conv.get('change_percent', float('nan')):.4f} % ({'ok' if conv.get('passed') else 'CHECK'})")
    for w in r.warnings:
        print("   WARNING:", w)


def save_run(folder, r, case, seg):
    folder.mkdir(parents=True, exist_ok=True)
    r.timeseries.to_csv(folder / "timeseries.csv", index=False)
    chart(folder / "overview.png", (11, 10), plots.dynamic_overview, r)
    chart(folder / "states.png", (11, 6), plots.states_chart, r)
    chart(folder / "losses_ecd.png", (11, 6), plots.losses_chart, r, case)
    times = r.timeseries["time_s"].iloc[[0, len(r.timeseries) // 4, len(r.timeseries) // 2, -1]]
    chart(folder / "depth_profiles.png", (9, 6), plots.profiles_over_time, depth_profiles(case, seg, r.timeseries, times))
    if r.controller != "none":
        chart(folder / "controller.png", (11, 8), plots.controller_chart, r)


def run_scenario(config, case, seg, name, specs):
    spec = specs[name]
    folder = OUT / name
    print(f"\n{spec['title']}")
    if spec["kind"] == "steady":
        table, _ = scenarios.run_steady_rates(case, seg, spec)
        folder.mkdir(parents=True, exist_ok=True)
        table.to_csv(folder / "steady_rates.csv", index=False)
        for _, r in table.iterrows():
            print(f"   {r['pump_rate_gpm']:5.0f} gpm  z0 {r['choke_opening']:.3f}  BHP {u.bar(r['bhp']):7.2f} bar"
                  f"  SPP {u.bar(r['spp']):7.2f} bar  ECD {r['ecd_ppg']:.3f} ppg  dynamic drift "
                  f"{r['dynamic_drift']:.2e} Pa")
    elif spec["kind"] == "dynamic":
        r = scenarios.run_dynamic(case, seg, name, specs)
        print_metrics(r.controller, r)
        save_run(folder, r, case, seg)
        if name == "S3":
            b = scenarios.bpp_check(case, seg, specs)
            print(f"   BPP {b['bpp_gpm']:.0f} gpm: holding the target at zero pump flow needs the choke "
                  f"{b['opening_needed']:.3f} open ({'possible' if b['reachable'] else 'NOT possible'}).")
    elif spec["kind"] == "compare":
        results = scenarios.run_compare(case, seg, name, specs)
        for label, r in results.items():
            print_metrics(label, r)
        folder.mkdir(parents=True, exist_ok=True)
        scenarios.metrics_table(results).to_csv(folder / "metrics.csv", index=False)
        chart(folder / "comparison.png", (11, 7), plots.compare_chart, results)
        save_run(folder / "PID", results["PID"], case, seg)
        if case.pid.kd == 0:
            print("   Note: Kd = 0 from the tuning rule, so PID = PI.")
    elif spec["kind"] == "sensitivity":
        table, results = scenarios.run_sensitivity(config, name, specs)
        folder.mkdir(parents=True, exist_ok=True)
        table.to_csv(folder / "sensitivity.csv", index=False)
        print(table[["variant", "max_dev_bar", "rmse_bar", "in_window_%", "min_opening"]].to_string(
            index=False, float_format="%.3f"))
        chart(folder / "s5.png", (12, 5), plots.s5_chart, table, results)


def run_validation(config):
    print("\nVALIDATION")
    folder = OUT / "validation"
    folder.mkdir(parents=True, exist_ok=True)
    table, _ = validation.level1(config)
    print("Level 1 - code check (synthetic hand calculation + conservation checks):")
    print(table[["quantity", "unit", "model", "reference", "error_percent"]].to_string(
        index=False, float_format=lambda x: f"{x:.6g}"))
    table.to_csv(folder / "level1.csv", index=False)
    validation.model_form_table(config).to_csv(folder / "model_comparison.csv", index=False)
    chart(folder / "level1.png", (11, 4.5), plots.validation_chart, table)
    level2, status, _ = validation.level2()
    print("Level 2 - published steady example:", status)
    if level2 is not None:
        level2.to_csv(folder / "level2.csv", index=False)
    print("Level 3 - transient response:", validation.level3()[1])


def run_tuning(case, seg, specs):
    print("\nPID TUNING on S3 (proposal 4-2-7)")
    table, gains, reasons = analysis.tune_pid(case, seg, scenarios.schedule_of("S3", specs))
    OUT.mkdir(exist_ok=True)
    table.to_csv(OUT / "pid_tuning.csv", index=False)
    print(table.to_string(index=False, float_format="%.4g"))
    for line in reasons:
        print("  ", line)
    print(f"   -> kp_per_bar {gains.kp * u.BAR:g}, ki_per_bar_s {gains.ki * u.BAR:g}, "
          f"kd_s_per_bar {gains.kd * u.BAR:g}  (put these in the case file)")


def main():
    parser = argparse.ArgumentParser(description="MPD well hydraulics simulator")
    parser.add_argument("--case", default="config/base_case.yaml", help="YAML or CSV case file")
    parser.add_argument("--variant", help="case column of a CSV case file (default: the first)")
    parser.add_argument("--scenario", default="all", help="'all', 'none' or S0 / S1 / S2 / S2b / S3 / S4 / S5")
    parser.add_argument("--s5", action="store_true", help="include S5 when --scenario all")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--tune", action="store_true")
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
    errors = config_errors(config)
    for i, s in enumerate(suggestions(config), 1):
        print(f"\nSUGGESTION {i} (not applied): {s['problem']}\n   Why: {s['why']}\n   How: {s['how']}\n"
              f"   Change: {s['changes']}")
    if errors:
        print("\nINPUT ERRORS - nothing was calculated:\n  " + "\n  ".join(errors))
        return
    case = build_case(config)
    seg = make_segments(case)
    print(f"Well split into {len(seg)} segments; beta_eff = {case.beta_eff / 1e9:.3f} GPa.")
    run_steady(case, seg)

    specs = scenarios.load_scenarios()
    if args.scenario == "all":
        names = [n for n in specs if n != "S5" or args.s5]
    else:
        names = [] if args.scenario == "none" else [args.scenario]
    for name in names:
        run_scenario(config, case, seg, name, specs)
    if args.tune:
        run_tuning(case, seg, specs)
    if args.validate:
        run_validation(config)
    print("\nNote: synthetic demonstration data - research / teaching software, "
          "not certified for real drilling operations.")


if __name__ == "__main__":
    main()
