"""
Case CSV: ONE file that contains EVERYTHING the simulator needs
(well path, hole, drill string, bit, mud, compressibility, operation, pressure
window, choke, actuator, measurement, PID, solver, numerics, criteria) - and
several muds / variants side by side. CSV files of the old format (Cv choke)
are converted with well.migrate_config().

    parameter,               unit, description,          Base WBM, OBM 10.5 ppg
    mud.density_ppg,         ppg,  Mud density,          10.0,     10.5
    well.hole_sections.1.diameter_in, in, Hole diameter, 8.681,
    well.survey.1,           md_m;tvd_m;inclination_deg;azimuth_deg, Survey station, 0;0;0;0,
    well.bit.nozzles_32nd,   1/32 in, Nozzle sizes,      13;13;13;13,

Rules
  * one row = one input; 'parameter' is its path in the case file
    (section.key, list items numbered from 1)
  * columns after 'description' = cases (e.g. one per mud). The FIRST case
    must be complete; in the other cases an empty cell = same as the first case
  * lists are written in one cell, separated by ';'
  * lines starting with # are comments
"""

import copy
import io

import pandas as pd

from .well import BASE_CASE, load_config, migrate_config, read_survey

LABELS = ("parameter", "unit", "description")
OPTIONAL = {"name", "measurement.noise_std_psi", "measurement.noise_seed"}
LEGACY_MARKERS = {"choke.cv_max", "dynamics.bulk_modulus_pa"}     # rows of the old (Cv, 1-state) format
DESCRIPTIONS = {
    "name": "Case name", "survey": "Survey station: MD; TVD; inclination; azimuth",
    "hole_sections": "Hole section", "drill_string": "Drill string part",
    "type": "drill_pipe or bha", "nozzles_32nd": "Bit nozzle sizes (A_n)",
    "discharge_coefficient": "Discharge coefficient (C_dn bit / C_d choke)",
    "density_ppg": "Mud density rho", "pv_cp": "Plastic viscosity mu_p", "yp_lbf100ft2": "Yield stress tau_y",
    "fluid_bulk_modulus_pa": "Fluid bulk modulus beta_f", "wall_bulk_modulus_pa": "Wall bulk modulus beta_w",
    "pump_rate_gpm": "Rig pump flow q_pump", "bpp_rate_gpm": "Back-pressure pump flow q_bpp",
    "pore_ppg": "Pore pressure at TD (EMW)", "fracture_ppg": "Fracture pressure at TD (EMW)",
    "safety_margin_psi": "Safety margin delta", "target_ppg": "Target BHP (EMW) or midpoint",
    "max_area_in2": "Choke area fully open A_max", "characteristic": "linear or equal_percentage",
    "rangeability": "Equal-percentage rangeability R", "min_opening": "Smallest choke opening z_min",
    "max_opening": "Largest choke opening z_max", "downstream_pressure_psi": "Pressure after the choke p_0",
    "max_working_pressure_psi": "Choke max working pressure (eps = 1e-3 x)",
    "time_constant_s": "Actuator time constant tau_a", "max_rate_per_s": "Actuator max rate r_max",
    "dead_time_s": "Actuator dead time theta_a", "delay_s": "Measurement delay theta_m",
    "sample_time_s": "Sample time T_s", "noise_std_psi": "Measurement noise (0 = off)",
    "noise_seed": "Noise random seed", "kp_per_bar": "PID Kp", "ki_per_bar_s": "PID Ki",
    "kd_s_per_bar": "PID Kd", "derivative_filter_n": "Derivative filter N",
    "tracking_time_s": "Anti-windup tracking time T_t (auto = Ti)", "method": "ODE solver (Radau)",
    "rtol": "Solver relative tolerance", "atol": "Solver absolute tolerance",
    "max_step_s": "Solver max step", "convergence_limit_percent": "Step-halving limit",
    "segment_length_m": "Segment length", "friction_model": "api_rp13d or exact_bingham",
    "flow_smoothing_fraction": "q_eps / q_pump (tanh smoothing)",
    "regime_transition_width": "Laminar-turbulent band (fraction of v_c)",
    "max_overshoot_percent": "Criterion: overshoot", "max_settling_time_s": "Criterion: settling time",
    "settling_band_percent": "Settling band (% of target)",
    "min_time_in_window_percent": "Criterion: time in safe window",
}


# ----------------------------------------------------------------------
# case file (nested dict) <-> rows (parameter, value)
# ----------------------------------------------------------------------
def complete_config(config):
    """Copy of a case file with the survey written inline (so nothing is in another file)."""
    cfg = copy.deepcopy(config)
    well = cfg["well"]
    if "survey_file" in well:
        well["survey"] = read_survey(well).values.tolist()
        del well["survey_file"]
    return cfg


def flatten(value, path=""):
    """Nested case file -> list of (parameter, value). Lists of numbers stay one value."""
    if isinstance(value, dict):
        return [row for k, v in value.items() for row in flatten(v, f"{path}.{k}" if path else k)]
    if isinstance(value, list) and value and isinstance(value[0], (dict, list)):
        return [row for i, v in enumerate(value, 1) for row in flatten(v, f"{path}.{i}")]
    return [(path, value)]


def unflatten(rows):
    """List of (parameter, value) -> nested case file (numbered parts become lists)."""
    root = {}
    for path, value in rows:
        place = root
        *parents, key = path.split(".")
        for p in parents:
            place = place.setdefault(p, {})
        place[key] = value

    def to_lists(node):
        if not isinstance(node, dict):
            return node
        if node and all(k.isdigit() for k in node):
            return [to_lists(node[k]) for k in sorted(node, key=int)]
        return {k: to_lists(v) for k, v in node.items()}
    return to_lists(root)


def generic(path):
    """'well.hole_sections.2.top_m' -> 'well.hole_sections.#.top_m' (to compare with the template)."""
    return ".".join("#" if p.isdigit() else p for p in path.split("."))


TEMPLATE = {generic(p): v for p, v in flatten(complete_config(load_config(BASE_CASE)))}


def cell_value(text, path):
    """CSV cell -> list (for list inputs), number, or text (e.g. 'auto')."""
    def number(t):
        try:
            return float(t)                    # plain float: PyYAML cannot save numpy numbers
        except ValueError:
            return t.strip()
    text = str(text).strip()
    if isinstance(TEMPLATE.get(generic(path)), list):
        return [number(t) for t in text.split(";") if t.strip()]
    return number(text)


# ----------------------------------------------------------------------
# read / write
# ----------------------------------------------------------------------
def read_table(path):
    """The CSV as a table of text (comment lines removed), checked for unknown parameters."""
    with open(path, encoding="utf-8-sig") as f:          # utf-8-sig: files saved by Excel
        lines = [line for line in f if not line.lstrip().startswith("#")]
    table = pd.read_csv(io.StringIO("".join(lines)), skipinitialspace=True, dtype=str)
    table.columns = [str(c).strip() for c in table.columns]
    if "parameter" not in table:
        raise ValueError("The CSV needs a 'parameter' column.")
    table = table.dropna(subset=["parameter"])
    table["parameter"] = table["parameter"].str.strip()
    if not case_names(table):
        raise ValueError("The CSV needs at least one case column after 'description'.")
    if is_legacy(table):
        return table                           # old format: converted by read_case_csv
    unknown = [p for p in table["parameter"] if generic(p) not in TEMPLATE]
    if unknown:
        raise ValueError("Unknown parameters:\n  " + "\n  ".join(unknown))
    return table


def is_legacy(table):
    """True for a CSV of the old format (Cv choke, one-state model)."""
    return bool(LEGACY_MARKERS & set(table["parameter"]))


def case_names(table):
    """Names of the case columns (all columns except parameter, unit, description)."""
    return [c for c in table.columns if c not in LABELS]


def read_case_csv(path):
    """Read the CSV. Returns {case name: case file (dict)}, one per case column."""
    table = read_table(path)
    names = case_names(table)
    first = table[names[0]]
    cases = {}
    for name in names:
        values = table[name].where(table[name].notna() & (table[name].str.strip() != ""), first)
        rows = [(p, cell_value(v, p)) for p, v in zip(table["parameter"], values) if pd.notna(v)]
        cfg = unflatten(rows)
        if is_legacy(table):
            cfg = migrate_config(cfg)[0]
        check_complete(cfg, name)
        cfg["name"] = name                     # the column title names the case
        cfg["mud"]["name"] = name
        cases[name] = cfg
    return cases


def check_complete(cfg, name):
    """Every input of the template must be given (lists need at least one item)."""
    given = {generic(p) for p, _ in flatten(cfg)}
    missing = sorted(p for p in TEMPLATE if p not in given and p not in OPTIONAL)
    if missing:
        raise ValueError(f"Case '{name}' is missing these parameters:\n  " + "\n  ".join(missing))


def write_case_csv(path, cases):
    """Save {case name: case file} as one CSV (first case complete, others only differences)."""
    flat = {name: dict(flatten(complete_config(cfg))) for name, cfg in cases.items()}
    names = list(flat)
    rows = []
    for p, v in flat[names[0]].items():
        if generic(p) not in TEMPLATE:         # e.g. mud.name: the case name is the column title
            continue
        row = {"parameter": p, "unit": unit_of(p), "description": description_of(p)}
        for name in names:
            value = flat[name].get(p, "")
            row[name] = "" if name != names[0] and value == v else text_of(value)
        rows.append(row)
    pd.DataFrame(rows).to_csv(path, index=False)


def text_of(value):
    """Value -> CSV cell (lists joined with ';')."""
    if isinstance(value, list):
        return ";".join(f"{x:.10g}" if isinstance(x, float) else str(x) for x in value)
    return value


UNIT_SUFFIXES = [("_per_bar_s", "1/(bar s)"), ("_s_per_bar", "s/bar"), ("_per_bar", "1/bar"),
                 ("_per_s", "1/s"), ("_lbf100ft2", "lbf/100ft2"), ("_32nd", "1/32 in"), ("_in2", "in2"),
                 ("_ppg", "ppg"), ("_cp", "cP"), ("_gpm", "gpm"), ("_psi", "psi"), ("_pa", "Pa"),
                 ("_percent", "%"), ("_deg", "deg"), ("_in", "in"), ("_m", "m"), ("_s", "s")]


def unit_of(path):
    """Unit from the end of the key name, e.g. 'density_ppg' -> 'ppg'."""
    if generic(path) == "well.survey.#":
        return "md_m;tvd_m;inclination_deg;azimuth_deg"
    key = path.split(".")[-1]
    return next((unit for suffix, unit in UNIT_SUFFIXES if key.endswith(suffix)), "")


def description_of(path):
    """Short explanation of a parameter (list items: 'Hole section 2: top_m')."""
    parts = path.split(".")
    numbered = [i for i, x in enumerate(parts) if x.isdigit()]
    if numbered:
        i = numbered[0]
        text = f"{DESCRIPTIONS.get(parts[i - 1], parts[i - 1])} {parts[i]}"
        return text if i == len(parts) - 1 else f"{text}: {parts[-1]}"
    return DESCRIPTIONS.get(parts[-1], "")
