# CLAUDE.md — MPD well hydraulics simulator (thesis project)

## Purpose
- M.Sc. thesis (Univ. of Tehran). **Approved proposal = specification** (`~/Downloads/Sepehr Mashayekhfar Prop.Final.pdf`, Persian): 1-D single-phase CBHP model, 4-state dynamics, orifice choke + actuator, reverse PID, scenarios S0–S5, 3-level validation. Proposal wins over older docs.
- User presents the CODE to teachers → keep it SIMPLE (plain functions, small dataclasses, comments with equations). UI must look professional.
- All bundled data synthetic — never claim validation; levels 2/3 stay "pending reference data" until sourced data exist in `data/reference/`.
- Docs to keep in sync: `README.md` (EN, equations + "Assumptions and decisions"), `README_FA.md` (Persian guide, Mermaid), `GUIDE_FA.md` (Persian onion walkthrough). Update numbers, test count, file list.

## Stack / run
- Python 3.12 from `uv` (Apple's 3.9/Tk 8.5 crashes on macOS 26). `.venv` from `~/.local/share/uv/python/cpython-3.12-…/bin/python3.12`. Deps: numpy pandas scipy matplotlib pyyaml pytest (no pydantic).
- `.venv/bin/python app.py` (Tkinter + matplotlib; user rejected web UI and PySide6).
- `.venv/bin/python main.py [--scenario S0..S5|all|none] [--s5] [--validate] [--tune] [--case file.yaml|.csv] [--variant name]` → `outputs/`.
- `.venv/bin/pytest` (76 tests, ~50 s). `data/hand_calculation.py` regenerates `data/validation_reference.csv`.
- `archive/advanced_version.zip` = old large version, unused.

## Code layout (~4300 lines)
- `mpd/well.py` load_config → migrate_config (old Cv/1-state files) → config_errors (all physical checks, no changes) → build_case (Case + ChokeSpec/ActuatorSpec/MeasurementSpec/PIDGains/SolverSpec, SI) → make_segments → flow_path (V_d, V_a, M).
- `mpd/hydraulics.py` API RP13D/Bourgoyne friction (default) + exact_bingham (comparison only); friction_gradient with regime band; pressure_losses (× tanh(q/q_eps)); solve_steady → SteadyResult (holds target via brentq z0 unless z or p_c given).
- `mpd/choke.py` orifice q_c, smooth dp+, inverse, opening_for, actuator_rate. `mpd/controller.py` PID (reverse, D filter, back-calculation), controller_gains P/PI/PID.
- `mpd/dynamics.py` FourStateModel.rhs (+5th bookkeeping state for mass balance), simulate (solve_ivp per T_s, ZOH, θm delay, θa dead time, optional seeded noise, cancel/progress callbacks) → SimulationResult; check_convergence (halve max_step); steady_state_residual; depth_profiles.
- `mpd/metrics.py` overshoot/settling ±2 %/max dev/RMSE/MAPE/% in window + PASS/FAIL criteria. `mpd/scenarios.py` runs S0–S5 from `config/scenarios.yaml` (S4/S5 reuse S3 via schedule_from). `mpd/validation.py` levels 1–3. `mpd/analysis.py` steady sensitivity/sweep + tune_pid. `mpd/suggestions.py` explained suggestions with `changes` dict, applied only by user. `mpd/library.py` one-CSV case library (column title = case name; legacy CSV migrated). `mpd/extensions.py` surge/swab — out of scope, unused. `mpd/plots.py` all charts.
- `app.py` 6 pages (Project & Case, Steady, Dynamic, Control & Scenarios, Sensitivity, Validation); run_task = background thread + queue + footer progress/Cancel; UI has no physics.

## Conventions / gotchas
- YAML in oilfield units, unit in key names; SI inside; gauge pressure; BHP at TD. `target_ppg: midpoint` default. PyYAML reads `1.5e9` as str → float().
- Friction velocity units: v_c formula ft/min, gradients ft/s (proposal text says ft/min everywhere — flagged for supervisor).
- Regime jump at v_c traps the ODE (sliding mode, runs never finish) → keep `regime_transition_width` > 0 for dynamics.
- Tuned gains Kp 0.08 /bar, Ki 0.008 /(bar s), Kd 0 (→ PID = PI in S4). Re-tune with `main.py --tune` after model changes.
- numpy scalars break yaml.safe_dump → store plain float in config dicts.
- Tk: pack fixed side panels before expanding cards; long labels need `autowrap()`; per-axis legends squash stacked plots → figure legend.
- Screenshots: `screencapture -l <CGWindowID>` (find ID with Quartz in a scratch venv, window name starts "MPD"); `-R` grabs whatever is on top. Headless check: drive App() and pump `update()` while `task_busy`.
- Tests must compare with independent results (hand calcs, analytic solutions, textbook formulas).

## Current state
- Base: BHP 383.8 bar (11.10 ppg), SPP 176.1, p_c 14.4, z0 0.454. S3 open loop −20.9 bar (60 % in window); S4 PI max dev 4.3 bar, all criteria pass. L1 validation max error 0.093 %.
