"""
Three-level validation (proposal 4-2-10). Every table has the columns
quantity, unit, model, reference, abs_error, error_percent, data_type, source.

Level 1 - code / equation check (runs with the bundled data)
    * independent hand calculation of the vertical validation well
    * oilfield hydrostatic rule, inverse choke equation
    * dynamic model with zero derivatives = steady hydraulics
    * mass balance of the dynamic model, step-halving convergence
Level 2 - steady hydraulics vs a PUBLISHED / textbook example (MAPE < 5 %)
    needs data/reference/steady_reference.csv - "pending reference data" until then
Level 3 - transient response vs Kaasa et al. (2012) / Naderi Lordejani et al. (2020)
    needs digitised curves in data/reference/transient_reference.csv - "pending" until then

Nothing in this file creates reference numbers; missing data stay missing.
"""

from pathlib import Path

import numpy as np
import pandas as pd

from . import units as u
from .choke import choke_flow, choke_pressure
from .dynamics import check_convergence, steady_state_residual
from .hydraulics import solve_steady
from .metrics import mape, percentage_error
from .scenarios import load_scenarios, run_dynamic
from .well import ROOT, build_case, load_config, make_segments

REFERENCE_DIR = ROOT / "data" / "reference"
STEADY_REFERENCE = REFERENCE_DIR / "steady_reference.csv"
TRANSIENT_REFERENCE = REFERENCE_DIR / "transient_reference.csv"
PENDING = "pending reference data"

CITATIONS = {
    "Kaasa 2012": "G.-O. Kaasa, O. N. Stamnes, O. M. Aamo, L. S. Imsland, Simplified hydraulics model used for "
                  "intelligent estimation of downhole pressure for a managed-pressure-drilling control system, "
                  "SPE Drilling & Completion 27(1), 127-138, 2012. doi:10.2118/143097-PA",
    "Naderi Lordejani 2020": "S. Naderi Lordejani et al., Modeling and numerical implementation of "
                             "managed-pressure-drilling systems for the assessment of pressure-control systems, "
                             "SPE Drilling & Completion 35(4), 598-619, 2020. doi:10.2118/201108-PA",
    "Bourgoyne 1986": "A. T. Bourgoyne Jr. et al., Applied Drilling Engineering, SPE Textbook Series vol. 2, 1986.",
}
COLUMNS = ["quantity", "unit", "model", "reference", "abs_error", "error_percent", "data_type", "source"]


def shown(path):
    """Path relative to the project folder when possible (for messages)."""
    path = Path(path)
    return path.relative_to(ROOT) if path.is_relative_to(ROOT) else path


def row(quantity, unit, model, reference, data_type, source):
    """One validation row; % error only when the reference is not zero."""
    err = abs(model - reference)
    pct = percentage_error(model, reference) if reference != 0 else np.nan
    return dict(zip(COLUMNS, (quantity, unit, model, reference, err, pct, data_type, source)))


# ----------------------------------------------------------------------
# Level 1
# ----------------------------------------------------------------------
HAND_QUANTITIES = {"drill_pipe_loss": "dp_drill_pipe", "bha_loss": "dp_bha", "bit_loss": "dp_bit",
                   "annulus_loss": "dp_annulus", "bhp": "bhp", "spp": "spp", "ecd": "ecd"}


def hand_calculation_table(case_path=None, reference_path=None):
    """Model vs the independent hand calculation (vertical well, p_c = 100 psi)."""
    case = build_case(load_config(case_path or ROOT / "config" / "validation_case.yaml"))
    result = solve_steady(case, make_segments(case), p_c=100 * u.PSI)
    ref = pd.read_csv(reference_path or ROOT / "data" / "validation_reference.csv")
    to_unit = {"psi": u.psi, "bar": u.bar, "pa": lambda x: x, "ppg": u.ppg, "kg_m3": lambda x: x}
    rows = []
    for _, r in ref.iterrows():
        model = to_unit[r["unit"]](getattr(result, HAND_QUANTITIES[r["quantity"]]))
        rows.append(row(r["quantity"], r["unit"], model, r["reference_value"], r["data_type"], r["source"]))
    return pd.DataFrame(rows, columns=COLUMNS)


def level1(config, progress=None, cancel=None):
    """All level-1 checks for the given case file (and the bundled validation well)."""
    rows = hand_calculation_table().to_dict("records")
    case = build_case(config)
    seg = make_segments(case)

    # oilfield rule: 0.052 psi/ft per ppg (exact factor 0.051948)
    p = u.psi(10 * u.PPG * u.G * 10000 * u.FT)
    rows.append(row("hydrostatic 10 ppg x 10 000 ft", "psi", p, 5200.0, "textbook rule (0.052 x MW x TVD)",
                    "Bourgoyne 1986"))
    # choke: flow through the choke at the pressure computed for that flow
    q = case.q_pump
    p_c = choke_pressure(q, 0.5, case.rho, case.choke)
    rows.append(row("choke q_c(p_c(q)) at z = 0.5", "m3/s", choke_flow(p_c, 0.5, case.rho, case.choke), q,
                    "identity check", "orifice equation and its inverse (mpd/choke.py)"))
    # zero derivatives at the steady operating point
    for name, value in steady_state_residual(case, seg).items():
        rows.append(row(f"steady point: {name}", "1/s", value, 0.0, "conservation check",
                        "dynamic model with zero derivatives = steady hydraulics"))
    if progress:
        progress(0.3)
    # mass balance and convergence on the connection scenario with PID
    sim = run_dynamic(case, seg, "S3", load_scenarios(), controller="PID", convergence=False, cancel=cancel)
    if progress:
        progress(0.6)
    check_convergence(case, seg, load_scenarios()["S3"], sim, cancel=cancel)
    rows.append(row("mass balance residual (S3 + PID), max", "m3", sim.mass_balance["max_residual_m3"], 0.0,
                    "conservation check", "(V_d/beta) dp_p + (V_a/beta) dp_c = integral(q_pump + q_bpp - q_c) dt"))
    c = sim.convergence
    rows.append(row("BHP RMSE change, step halved (S3 + PID)", "%", c["change_percent"], 0.0,
                    f"convergence check (limit {c['limit_percent']} %)", "proposal 4-2-5"))
    if progress:
        progress(1.0)
    return pd.DataFrame(rows, columns=COLUMNS), sim


def model_form_table(config):
    """Default API RP 13D friction vs the exact Bingham comparison model (informative only)."""
    import copy
    rows = []
    results = {}
    for model in ("api_rp13d", "exact_bingham"):
        cfg = copy.deepcopy(config)
        cfg["numerics"]["friction_model"] = model
        case = build_case(cfg)
        results[model] = solve_steady(case, make_segments(case), z=None)
    for name, key in (("drill-string loss", "dp_string"), ("bit loss", "dp_bit"),
                      ("annulus loss", "dp_annulus"), ("SPP", "spp")):
        rows.append(row(name, "bar", u.bar(getattr(results["exact_bingham"], key)),
                        u.bar(getattr(results["api_rp13d"], key)), "model comparison (not validation)",
                        "exact Bingham (model) vs API RP 13D default (reference column)"))
    return pd.DataFrame(rows, columns=COLUMNS)


# ----------------------------------------------------------------------
# Level 2 - published steady example
# ----------------------------------------------------------------------
def level2(path=None):
    """
    Compare with a published / textbook steady example.

    CSV columns: case_file, quantity (bhp, spp, drill_pipe_loss, bha_loss, bit_loss,
    annulus_loss, ecd), unit, p_c_psi, reference_value, data_type, source.
    Returns (table or None, status text, MAPE or None).
    """
    path = Path(path or STEADY_REFERENCE)
    if not path.exists():
        return None, f"{PENDING}: add a sourced textbook/published example as {shown(path)}", None
    ref = pd.read_csv(path, comment="#")
    rows = []
    to_unit = {"psi": u.psi, "bar": u.bar, "pa": lambda x: x, "ppg": u.ppg, "kg_m3": lambda x: x}
    for case_file, group in ref.groupby("case_file"):
        case_path = Path(case_file) if Path(case_file).is_absolute() else ROOT / case_file
        case = build_case(load_config(case_path))
        result = solve_steady(case, make_segments(case), p_c=float(group["p_c_psi"].iloc[0]) * u.PSI)
        for _, r in group.iterrows():
            model = to_unit[r["unit"]](getattr(result, HAND_QUANTITIES[r["quantity"]]))
            rows.append(row(r["quantity"], r["unit"], model, r["reference_value"], r["data_type"], r["source"]))
    table = pd.DataFrame(rows, columns=COLUMNS)
    value = mape(table["model"], table["reference"])
    status = f"MAPE = {value:.2f} % (proposal target < 5 %: {'PASS' if value < 5 else 'FAIL'})"
    return table, status, value


# ----------------------------------------------------------------------
# Level 3 - transient response shape
# ----------------------------------------------------------------------
def level3(sim=None, path=None):
    """
    Compare the response shape with digitised published curves.

    CSV (comment lines '# source: ...' allowed): time_s, bhp_normalised, source,
    figure, data_type. bhp_normalised = (BHP - BHP_start) / (BHP_end - BHP_start).
    Without the file (or without a simulation) the level stays pending.
    """
    path = Path(path or TRANSIENT_REFERENCE)
    if not path.exists():
        return None, (f"{PENDING}: no usable numerical data from Kaasa et al. (2012) or Naderi Lordejani "
                      f"et al. (2020) yet. Digitise the published curves into "
                      f"{shown(path)} (with figure number and citation). "
                      f"Qualitative comparison only until then.")
    if sim is None:
        return None, "Run a simulation first."
    ref = pd.read_csv(path, comment="#")
    ts = sim.timeseries
    y = ts["bhp"].values
    span = y[-1] - y[0] if abs(y[-1] - y[0]) > 1e-6 else (y.max() - y.min())
    norm = (y - y[0]) / span
    model = np.interp(ref["time_s"], ts["time_s"], norm)
    rmse = float(np.sqrt(np.mean((model - ref["bhp_normalised"]) ** 2)))
    source = str(ref["source"].iloc[0])
    table = pd.DataFrame([row("normalised BHP response, RMSE", "-", rmse, 0.0,
                              str(ref["data_type"].iloc[0]), source)], columns=COLUMNS)
    return table, f"Shape comparison with {source} (normalised curves)."
