# CLAUDE.md — MPD well hydraulics simulator (thesis project)

## Purpose
- University thesis project: Stage A steady-state Bingham hydraulics → Stage B dynamic MPD + PID BHP control.
- User presents the CODE to teachers → keep code SIMPLE (plain functions, few classes, clear comments). The UI DESIGN must look professional.
- All bundled data are synthetic — never claim validation.
- `README_FA.md` = full Persian presentation guide with Mermaid flowcharts; keep its numbers/sections in sync with code changes.

## Stack / run
- Python 3.12 from `uv` (Apple's Python 3.9 has Tk 8.5 that crashes on macOS 26). `.venv` built from `~/.local/share/uv/python/cpython-3.12-…/bin/python3.12`.
- `.venv/bin/python app.py` (Tkinter + matplotlib desktop window; user rejected web UI and PySide6).
- `.venv/bin/python main.py [--scenario name|all|none] [--sensitivity] [--validate] [--case file]` → `outputs/`.
- `.venv/bin/pytest` (33 tests, ~20 s). `data/hand_calculation.py` regenerates `data/validation_reference.csv`.
- `archive/advanced_version.zip` = old large version (PySide6 GUI, Streamlit, 3-state ODE); not used.

## Code layout (~2400 lines)
- `mpd/units.py` constants; `mpd/well.py` load_config → build_case (Case dataclass, SI) → make_segments (DataFrame, one row per segment); min-curvature.
- `mpd/hydraulics.py` friction() (Buckingham-Reiner / slot laminar, Hanks Re_c, Blasius), bit, choke (Cv→SI, linear below 1e4 Pa), solve_steady() returns dict + `segments` + `profile`.
- `mpd/dynamics.py` PID class + simulate(): 1-state lumped model dp_c/dt = β/V (Q_in − Q_choke); friction per step from friction_losses (cached), linear to 0 below `numerics.low_flow_gpm` (Bingham yield jump otherwise); actuators choke / back_pressure; model lumped / quasi_steady.
- `mpd/analysis.py` control_metrics (per event), sensitivity/sweep on the config dict, validate().
- `mpd/plots.py` all matplotlib charts (draw on a given Figure; used by CLI and app).
- `app.py` one App(tk.Tk): dark sidebar nav + pages (tkraise), card()/kpi()/chart()/table() helpers, ttk clam styles.

## Conventions / gotchas
- Config YAML in oilfield units with unit in key names; converted once in build_case. PyYAML reads `1.5e9` as a string → wrap in float().
- SI internally, gauge pressure; BHP at TD; SBP = annulus pressure before choke. Two ECDs: `ecd` (no SBP) and `emw` (with SBP).
- PID gains Kp 0.1, Ki 0.05 (per bar) tuned on synthetic scenario 10.
- Tk layout: pack the fixed-width side panel BEFORE the expanding chart card, or it gets squeezed.
- Screenshots for checking the UI: bring window to front via osascript + `-topmost`, then `screencapture -R`.
- Tests must compare with independent results (hand calcs, analytic solutions, textbook formulas).

## Current state
- Base case: BHP 380.4 bar, SPP 172.6 bar, SBP 11.1 bar (auto choke 0.426). Validation demo: annulus −9 % (exact vs linearised slot), others < 1.5 %.
