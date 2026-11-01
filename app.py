"""
MPD well hydraulics simulator - desktop application (Tkinter + matplotlib).

    python app.py

Pages follow the thesis workflow (proposal sections in brackets):
  1. Project & Case        scope, inputs, input checks, suggestions, CSV / YAML   [4-2-2, 4-2-3]
  2. Steady Hydraulics     pressure path, BHP / SPP / ECD, profile, regimes       [4-2-4]
  3. Dynamic Model         four states, solver, mass balance, convergence          [4-2-5, 4-2-6]
  4. Control & Scenarios   S0 - S4, fixed / P / PI / PID, criteria, PID tuning     [4-2-7 - 4-2-9]
  5. Sensitivity           S5 (+/-20 %) and steady one-at-a-time sensitivity       [4-2-9]
  6. Validation & Evidence three validation levels with sources                    [4-2-10]

The window only shows results: every calculation is in the mpd package.
Long calculations run in a background thread (progress bar + Cancel in the footer).
"""

import copy
import queue
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import matplotlib
import numpy as np
import pandas as pd
import yaml
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from mpd import analysis, plots, scenarios, validation
from mpd import units as u
from mpd.dynamics import Cancelled, check_convergence, depth_profiles, simulate
from mpd.hydraulics import solve_steady
from mpd.library import case_names, read_case_csv, read_table, write_case_csv
from mpd.suggestions import apply_suggestion, get_path, set_path, suggestions
from mpd.well import ROOT, build_case, config_errors, load_config, make_segments, migrate_config

# ----------------------------------------------------------------------
# Look and feel: calm, high contrast, one accent colour, status colours only for status
# ----------------------------------------------------------------------
SIDEBAR, SIDEBAR_TEXT, SIDEBAR_ACTIVE = "#0f1b2d", "#a3adbd", "#1d2c44"
ACCENT, ACCENT_DARK = "#2a78d6", "#1f5fae"
PAGE, CARD, BORDER = "#f2f3f5", "#ffffff", "#dfe2e7"
TEXT, MUTED = "#1f2937", "#5b6472"
GREEN, GREEN_BG = "#1a7f4b", "#e3f2e8"
RED, RED_BG = "#b42318", "#fbe7e5"
AMBER, AMBER_BG = "#8a5a00", "#fdf3dc"
FONT = "Helvetica Neue"
MONO = "Menlo"

matplotlib.rcParams.update({"font.family": "sans-serif", "font.size": 9.5, "axes.titlesize": 11,
                            "figure.facecolor": CARD, "axes.facecolor": CARD})

SCOPE = [
    "One-dimensional, single-phase, homogeneous mud; CBHP managed pressure drilling.",
    "Bingham Plastic mud (ρ, μp, τy); concentric segments of string and annulus.",
    "Friction: Bourgoyne / API RP 13D field correlations (default), quasi-steady in time.",
    "Four states: stand-pipe pressure p_p, choke pressure p_c, flow q, choke opening z.",
    "The choke is the only actuator; the back-pressure pump is a known input.",
    "Not modelled: influx / losses, cuttings, temperature, surge / swab, eccentricity,",
    "Power-law / Herschel-Bulkley fluids, multiphase flow, MPC.",
    "All bundled data are SYNTHETIC demonstration data - no field validation is claimed.",
]

# editable key inputs: (group, [(path, label, unit, choices or None)])
FORM = [
    ("Fluid and compressibility", [
        ("mud.density_ppg", "Density ρ", "ppg", None), ("mud.pv_cp", "Plastic viscosity μp", "cP", None),
        ("mud.yp_lbf100ft2", "Yield stress τy", "lbf/100ft²", None),
        ("compressibility.fluid_bulk_modulus_pa", "Fluid bulk modulus βf", "Pa", None),
        ("compressibility.wall_bulk_modulus_pa", "Wall bulk modulus βw", "Pa", None)]),
    ("Operation and pressure window", [
        ("operation.pump_rate_gpm", "Pump flow q_pump", "gpm", None),
        ("operation.bpp_rate_gpm", "Back-pressure pump q_bpp", "gpm", None),
        ("pressure_window.pore_ppg", "Pore pressure (EMW)", "ppg", None),
        ("pressure_window.fracture_ppg", "Fracture pressure (EMW)", "ppg", None),
        ("pressure_window.safety_margin_psi", "Safety margin δ", "psi", None),
        ("pressure_window.target_ppg", "Target (number or midpoint)", "ppg", None)]),
    ("Bit, choke and actuator", [
        ("well.bit.discharge_coefficient", "Nozzle coefficient C_dn", "-", None),
        ("choke.max_area_in2", "Choke area A_max", "in²", None),
        ("choke.discharge_coefficient", "Choke coefficient C_d", "-", None),
        ("choke.characteristic", "Choke curve", "", ("linear", "equal_percentage")),
        ("choke.min_opening", "Smallest opening z_min", "-", None),
        ("actuator.time_constant_s", "Actuator τa", "s", None),
        ("actuator.max_rate_per_s", "Actuator rate r_max", "1/s", None)]),
    ("Measurement and PID", [
        ("measurement.delay_s", "Measurement delay θm", "s", None),
        ("measurement.sample_time_s", "Sample time Ts", "s", None),
        ("measurement.noise_std_psi", "Noise (0 = off)", "psi", None),
        ("controller.kp_per_bar", "Kp", "1/bar", None), ("controller.ki_per_bar_s", "Ki", "1/(bar·s)", None),
        ("controller.kd_s_per_bar", "Kd", "s/bar", None)]),
    ("Model and solver", [
        ("numerics.friction_model", "Friction model", "", ("api_rp13d", "exact_bingham")),
        ("numerics.flow_smoothing_fraction", "q_ε / q_pump", "-", None),
        ("numerics.regime_transition_width", "Regime band width", "× v_c", None),
        ("solver.method", "ODE solver", "", ("Radau", "BDF", "LSODA", "RK45")),
        ("solver.max_step_s", "Max step", "s", None)]),
]


# ----------------------------------------------------------------------
# Building blocks
# ----------------------------------------------------------------------
def card(parent, title=None, subtitle=None, **pack):
    """A white panel with a thin border and an optional title. Returns the inner frame."""
    outer = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
    outer.pack(**({"fill": "both", "expand": True, "pady": 5} | pack))
    if title:
        tk.Label(outer, text=title, bg=CARD, fg=TEXT, font=(FONT, 13, "bold")).pack(anchor="w", padx=14, pady=(10, 0))
    if subtitle:
        sub = tk.Label(outer, text=subtitle, bg=CARD, fg=MUTED, font=(FONT, 11), justify="left", anchor="w")
        sub.pack(fill="x", padx=14)
        autowrap(sub)
    inner = tk.Frame(outer, bg=CARD)
    inner.pack(fill="both", expand=True, padx=14, pady=(6, 10))
    return inner


def autowrap(label, pad=12):
    """Wrap a label's text to its current width (no clipped text in a small window)."""
    label.bind("<Configure>", lambda e: label.config(wraplength=max(e.width - pad, 80)))


def kpi(parent, title, compact=False):
    """Result tile: small title, big number, note. Returns (value, note) labels."""
    box = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
    box.pack(side="left", fill="both", expand=True, padx=(0, 8))
    box.pack_propagate(True)
    pad = 6 if compact else 10
    tk.Label(box, text=title.upper(), bg=CARD, fg=MUTED, font=(FONT, 10, "bold")).pack(anchor="w", padx=10, pady=(pad, 0))
    value = tk.Label(box, text="–", bg=CARD, fg=TEXT, font=(FONT, 15 if compact else 20, "bold"))
    value.pack(anchor="w", padx=10)
    note = tk.Label(box, text="", bg=CARD, fg=MUTED, font=(FONT, 10 if compact else 11), justify="left", anchor="w")
    note.pack(fill="x", padx=10, pady=(0, pad))
    autowrap(note)
    return value, note


def chart(parent, height=5.0):
    """A matplotlib figure with the zoom / pan / save toolbar."""
    fig = Figure(figsize=(9, height), layout="constrained")
    canvas = FigureCanvasTkAgg(fig, master=parent)
    bar = NavigationToolbar2Tk(canvas, parent, pack_toolbar=False)
    bar.config(background=CARD)
    bar.pack(side="bottom", fill="x")
    canvas.get_tk_widget().pack(side="top", fill="both", expand=True)
    return fig, canvas


def redraw(fig, canvas, draw_function, *args):
    """Clear a figure, draw it again and show it."""
    fig.clear()
    draw_function(fig, *args)
    canvas.draw_idle()


def message(fig, canvas, text):
    """Empty chart with a short hint."""
    fig.clear()
    fig.text(0.5, 0.5, text, ha="center", va="center", color=MUTED, fontsize=12)
    canvas.draw_idle()


def table(parent, height=10):
    """A table (Treeview) with scroll bars."""
    frame = tk.Frame(parent, bg=CARD)
    tree = ttk.Treeview(frame, show="headings", height=height)
    ybar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    xbar = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
    xbar.pack(side="bottom", fill="x")
    ybar.pack(side="right", fill="y")
    tree.pack(side="left", fill="both", expand=True)
    frame.pack(fill="both", expand=True, pady=4)
    return tree


def fill_table(tree, df, decimals=3):
    """Show a DataFrame in a table (columns wide enough for their text)."""
    def text(v):
        if isinstance(v, (float, np.floating)):
            if np.isnan(v):
                return "–"
            return f"{v:.3e}" if v != 0 and (abs(v) < 1e-3 or abs(v) >= 1e7) else f"{v:,.{decimals}f}"
        return str(v)
    rows = [[text(v) for v in row] for row in df.itertuples(index=False)]
    tree.delete(*tree.get_children())
    tree["columns"] = list(df.columns)
    for i, col in enumerate(df.columns):
        longest = max([len(str(col))] + [len(r[i]) for r in rows])
        tree.heading(col, text=col)
        tree.column(col, width=min(longest * 8 + 22, 520), minwidth=60, anchor="w" if i == 0 else "e", stretch=False)
    for i, values in enumerate(rows):
        tag = "odd" if i % 2 else "even"
        if "FAIL" in values:
            tag = "fail"
        tree.insert("", "end", values=values, tags=(tag,))
    tree.tag_configure("odd", background="#f7f8fa")
    tree.tag_configure("fail", background=RED_BG)


def button(parent, text, command, primary=True):
    return ttk.Button(parent, text=text, command=command, style="Primary.TButton" if primary else "Secondary.TButton")


def badge(label, state, text):
    """Coloured status label: state = ok / warn / fail / info."""
    fg, bg = {"ok": (GREEN, GREEN_BG), "warn": (AMBER, AMBER_BG), "fail": (RED, RED_BG),
              "info": (MUTED, "#eceef1")}[state]
    label.config(text=text, fg=fg, bg=bg)


def criteria_cards(cards, metrics):
    """Three PASS / FAIL tiles for the proposal's preliminary criteria."""
    for (name, limit, value, passed), (title, note) in zip(metrics["criteria"], cards):
        title.config(text=("✓  " if passed else "✗  ") + value, fg=GREEN if passed else RED)
        note.config(text=f"limit {limit}")


def metric_rows(m, unit):
    """Performance metrics of one run as (metric, value) rows."""
    p = plots.p_conv(unit)
    return pd.DataFrame([
        ("Overshoot", f"{m['overshoot_percent']:.2f} %  ({m['overshoot_basis']})"),
        ("Settling time (±2 %)", "not settled" if np.isnan(m["settling_time_s"]) else f"{m['settling_time_s']:.1f} s"),
        ("Max |BHP − target|", f"{p(m['max_abs_deviation']):,.2f} {unit}"),
        ("RMSE", f"{p(m['rmse']):,.3f} {unit}"), ("MAPE", f"{m['mape_percent']:.3f} %"),
        ("Time in safe window", f"{m['time_in_window_percent']:.1f} %"),
        ("Min / max BHP", f"{p(m['min_bhp']):,.1f} / {p(m['max_bhp']):,.1f} {unit}")],
        columns=["Metric", "Value"])


# ----------------------------------------------------------------------
# Main window
# ----------------------------------------------------------------------
class App(tk.Tk):

    PAGES = [("case", "Project & Case", "1"), ("steady", "Steady Hydraulics", "2"),
             ("dynamic", "Dynamic Model", "3"), ("control", "Control & Scenarios", "4"),
             ("sensitivity", "Sensitivity", "5"), ("validation", "Validation & Evidence", "6")]
    MIN_SIZE = (1200, 760)

    def __init__(self):
        super().__init__()
        self.title("MPD Well Hydraulics Simulator - thesis model")
        width = min(1480, self.winfo_screenwidth() - 40)
        height = min(940, self.winfo_screenheight() - 100)
        self.geometry(f"{width}x{height}+20+20")
        self.minsize(*self.MIN_SIZE)
        self.configure(bg=PAGE)
        self.setup_style()

        self.config_data = load_config(ROOT / "config" / "base_case.yaml")
        self.specs = scenarios.load_scenarios()
        self.unit = tk.StringVar(value="bar")
        self.library = {}
        self.dyn_result = None           # last run on the Dynamic page
        self.control_result = None       # ("dynamic", result) / ("compare", dict) / ("steady", table)
        self.s5 = None
        self.validation = None
        self.task_queue, self.task_busy, self.cancel_flag = None, False, False

        self.sidebar()
        main = tk.Frame(self, bg=PAGE)
        main.pack(side="left", fill="both", expand=True)
        self.footer(main)
        self.content = tk.Frame(main, bg=PAGE)
        self.content.pack(side="top", fill="both", expand=True)
        self.pages = {}
        for key, _, _ in self.PAGES:
            self.pages[key] = tk.Frame(self.content, bg=PAGE)
            self.pages[key].place(relx=0, rely=0, relwidth=1, relheight=1)
        self.case_page()
        self.steady_page()
        self.dynamic_page()
        self.control_page()
        self.sensitivity_page()
        self.validation_page()
        self.apply_config(self.config_data)
        self.show("case")

    def setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=(FONT, 12), background=CARD)
        style.configure("Primary.TButton", font=(FONT, 12, "bold"), foreground="white", background=ACCENT,
                        borderwidth=0, padding=(14, 6))
        style.map("Primary.TButton", background=[("active", ACCENT_DARK), ("disabled", "#9db8de")])
        style.configure("Secondary.TButton", font=(FONT, 12), foreground=TEXT, background="#e6e9ee",
                        borderwidth=0, padding=(12, 6))
        style.map("Secondary.TButton", background=[("active", "#d5dae2")])
        style.configure("TNotebook", background=CARD, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(12, 5), font=(FONT, 12), background="#eceef1", borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", ACCENT)])
        style.configure("Treeview", rowheight=24, font=(FONT, 11), borderwidth=0, fieldbackground=CARD)
        style.configure("Treeview.Heading", font=(FONT, 11, "bold"), background="#eceef1", borderwidth=0)
        style.configure("TCombobox", padding=3)
        style.configure("TEntry", padding=3)
        style.configure("TCheckbutton", background=CARD, font=(FONT, 12))
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=BORDER, borderwidth=0, thickness=8)

    # ------------------------------------------------------------------
    # Navigation, footer, background tasks
    # ------------------------------------------------------------------
    def sidebar(self):
        bar = tk.Frame(self, bg=SIDEBAR, width=230)
        bar.pack(side="left", fill="y")
        bar.pack_propagate(False)
        logo = tk.Frame(bar, bg=SIDEBAR)
        logo.pack(fill="x", padx=18, pady=(22, 2))
        tk.Label(logo, text="MPD", bg=ACCENT, fg="white", font=(FONT, 13, "bold"), padx=7, pady=3).pack(side="left")
        tk.Label(logo, text="  Hydraulics", bg=SIDEBAR, fg="white", font=(FONT, 16, "bold")).pack(side="left")
        tk.Label(bar, text="CBHP model · choke · PID", bg=SIDEBAR, fg=SIDEBAR_TEXT,
                 font=(FONT, 11)).pack(anchor="w", padx=20, pady=(0, 22))
        self.nav = {}
        for key, title, number in self.PAGES:
            item = tk.Label(bar, text=f"  {number}   {title}", bg=SIDEBAR, fg=SIDEBAR_TEXT, font=(FONT, 13),
                            anchor="w", padx=10, pady=9, cursor="hand2")
            item.pack(fill="x", padx=8, pady=1)
            item.bind("<Button-1>", lambda e, k=key: self.show(k))
            self.nav[key] = item
        bottom = tk.Frame(bar, bg=SIDEBAR)
        bottom.pack(side="bottom", fill="x", padx=20, pady=18)
        tk.Label(bottom, text="PRESSURE UNIT", bg=SIDEBAR, fg="#6f7a8c", font=(FONT, 10, "bold")).pack(anchor="w")
        units = tk.Frame(bottom, bg=SIDEBAR)
        units.pack(anchor="w", pady=(4, 14))
        for text in ("bar", "psi"):
            tk.Radiobutton(units, text=text, value=text, variable=self.unit, command=self.refresh_units,
                           bg=SIDEBAR, fg="white", selectcolor=SIDEBAR, activebackground=SIDEBAR,
                           activeforeground="white", font=(FONT, 12)).pack(side="left", padx=(0, 10))
        self.case_label = tk.Label(bottom, text="", bg=SIDEBAR, fg=SIDEBAR_TEXT, font=(FONT, 10),
                                   wraplength=190, justify="left")
        self.case_label.pack(anchor="w")
        tk.Label(bottom, text="SYNTHETIC DATA\nteaching / research only", bg=SIDEBAR, fg="#e3b341",
                 font=(FONT, 10, "bold"), justify="left", wraplength=190).pack(anchor="w", pady=(10, 0))

    def footer(self, parent):
        bar = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        bar.pack(side="bottom", fill="x")
        self.status = tk.Label(bar, text="Ready.", bg=CARD, fg=MUTED, font=(FONT, 11), anchor="w")
        self.status.pack(side="left", padx=14, pady=6, fill="x", expand=True)
        self.cancel_button = button(bar, "Cancel", self.cancel_task, primary=False)
        self.cancel_button.pack(side="right", padx=(6, 12), pady=4)
        self.cancel_button.state(["disabled"])
        self.progress = ttk.Progressbar(bar, length=220)
        self.progress.pack(side="right", pady=4)

    def show(self, key):
        self.pages[key].tkraise()
        for k, item in self.nav.items():
            item.config(bg=SIDEBAR_ACTIVE if k == key else SIDEBAR, fg="white" if k == key else SIDEBAR_TEXT)

    def page_header(self, key, title, subtitle):
        page = self.pages[key]
        head = tk.Frame(page, bg=PAGE)
        head.pack(fill="x", padx=24, pady=(16, 2))
        tk.Label(head, text=title, bg=PAGE, fg=TEXT, font=(FONT, 22, "bold")).pack(anchor="w")
        sub = tk.Label(head, text=subtitle, bg=PAGE, fg=MUTED, font=(FONT, 12), justify="left", anchor="w")
        sub.pack(fill="x")
        autowrap(sub)
        body = tk.Frame(page, bg=PAGE)
        body.pack(fill="both", expand=True, padx=24, pady=(4, 12))
        return body

    def run_task(self, title, work, done):
        """Run work(progress, cancel) in a thread; call done(result) in the window thread."""
        if self.task_busy:
            messagebox.showinfo("Busy", "Another calculation is running. Wait or press Cancel.")
            return
        self.task_busy, self.cancel_flag = True, False
        self.task_queue = q = queue.Queue()
        self.status.config(text=f"{title} …", fg=TEXT)
        self.progress["value"] = 0
        self.cancel_button.state(["!disabled"])
        self.config(cursor="watch")

        def worker():
            try:
                q.put(("done", work(lambda f: q.put(("progress", f)), lambda: self.cancel_flag)))
            except Cancelled:
                q.put(("cancelled", None))
            except Exception:                                   # shown to the user, not swallowed
                q.put(("error", traceback.format_exc()))
        threading.Thread(target=worker, daemon=True).start()
        self.after(100, lambda: self.poll_task(title, done))

    def poll_task(self, title, done):
        try:
            while True:
                kind, value = self.task_queue.get_nowait()
                if kind == "progress":
                    self.progress["value"] = 100 * value
                    continue
                self.task_busy = False
                self.cancel_button.state(["disabled"])
                self.config(cursor="")
                self.progress["value"] = 0
                if kind == "done":
                    self.status.config(text=f"{title}: finished.", fg=GREEN)
                    done(value)
                elif kind == "cancelled":
                    self.status.config(text=f"{title}: cancelled.", fg=AMBER)
                else:
                    self.status.config(text=f"{title}: failed.", fg=RED)
                    messagebox.showerror("Calculation failed", value[-2000:])
                return
        except queue.Empty:
            self.after(100, lambda: self.poll_task(title, done))

    def cancel_task(self):
        self.cancel_flag = True
        self.status.config(text="Cancelling …", fg=AMBER)

    def save_csv(self, df, name="results.csv"):
        if df is None:
            messagebox.showinfo("Nothing to export", "Run the calculation first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=name)
        if path:
            df.to_csv(path, index=False)
            self.status.config(text=f"Exported {Path(path).name}", fg=GREEN)

    def refresh_units(self):
        self.show_steady()
        if self.dyn_result:
            self.show_dynamic()
        if self.control_result:
            self.show_control()
        if self.s5:
            self.show_s5()

    # ------------------------------------------------------------------
    # Case handling (no automatic changes: errors and suggestions are shown)
    # ------------------------------------------------------------------
    def apply_config(self, config):
        """Check a case file; use it only if it is valid. Returns True if applied."""
        config, notes = migrate_config(config)
        errors = config_errors(config)
        self.show_check(config, errors, notes)
        if errors:
            return False
        self.config_data = config
        self.case = build_case(config)
        self.seg = make_segments(self.case)
        self.dyn_result = self.control_result = self.s5 = self.validation = None
        self.fill_form()
        self.case_text.delete("1.0", "end")
        self.case_text.insert("1.0", yaml.safe_dump(config, sort_keys=False, allow_unicode=True))
        self.case_label.config(text=f"{self.case.name}\n{len(self.seg)} segments · "
                                    f"βeff {self.case.beta_eff / 1e9:.2f} GPa")
        self.show_steady()
        for fig, canvas in ((self.dyn_fig, self.dyn_canvas), (self.ctl_fig, self.ctl_canvas)):
            message(fig, canvas, "Inputs changed - run again.")
        return True

    def show_check(self, config, errors, notes):
        """Input-check card: errors (red), suggestions with an Apply button, migration notes."""
        for child in self.check_box.winfo_children():
            child.destroy()
        if errors:
            badge(self.check_badge, "fail", f"✗  {len(errors)} input problem(s) - not applied")
        else:
            badge(self.check_badge, "ok", "✓  Inputs valid - physical checks passed")
        for e in errors:
            tk.Label(self.check_box, text="•  " + e, bg=CARD, fg=RED, font=(FONT, 11), justify="left",
                     wraplength=350).pack(anchor="w")
        for n in notes:
            tk.Label(self.check_box, text="ⓘ  " + n, bg=CARD, fg=MUTED, font=(FONT, 11), justify="left",
                     wraplength=350).pack(anchor="w")
        try:
            items = suggestions(config)
        except Exception as error:                     # incomplete file: errors above explain it
            items = []
            tk.Label(self.check_box, text=str(error), bg=CARD, fg=MUTED).pack(anchor="w")
        if not errors and not items:
            tk.Label(self.check_box, text="The choke can hold the target BHP at the starting point.",
                     bg=CARD, fg=MUTED, font=(FONT, 11)).pack(anchor="w")
        for i, s in enumerate(items, 1):
            box = tk.Frame(self.check_box, bg=AMBER_BG)
            box.pack(fill="x", pady=4)
            tk.Label(box, text=f"Suggestion {i}: {s['problem']}\nWhy: {s['why']}\nHow: {s['how']}\n"
                               f"Change: " + ", ".join(f"{k} = {v}" for k, v in s["changes"].items()),
                     bg=AMBER_BG, fg=AMBER, font=(FONT, 11), justify="left", wraplength=340).pack(
                anchor="w", padx=8, pady=(6, 2))
            button(box, "Apply this suggestion", lambda s=s, c=config: self.accept_suggestion(c, s),
                   primary=False).pack(anchor="e", padx=6, pady=(0, 6))

    def accept_suggestion(self, config, suggestion):
        if self.apply_config(apply_suggestion(config, suggestion)):
            self.status.config(text="Suggestion applied: " + suggestion["how"], fg=GREEN)

    # ------------------------------------------------------------------
    # Page 1: Project & Case
    # ------------------------------------------------------------------
    def case_page(self):
        body = self.page_header("case", "Project & Case",
                                "Scope of the proposal model, the inputs (oilfield units in, SI inside) and "
                                "the physical input checks. Inputs are never changed without your OK.")
        right = tk.Frame(body, bg=PAGE, width=400)
        right.pack(side="right", fill="y", padx=(12, 0))
        right.pack_propagate(False)
        check = card(right, "Input check and suggestions")
        self.check_badge = tk.Label(check, text="", font=(FONT, 12, "bold"), padx=10, pady=4)
        self.check_badge.pack(anchor="w", pady=(0, 6))
        self.check_box = tk.Frame(check, bg=CARD)
        self.check_box.pack(fill="both", expand=True)

        left = card(body, None, pady=5)
        tabs = ttk.Notebook(left)
        tabs.pack(fill="both", expand=True)
        form_tab, scope_tab, yaml_tab, csv_tab = (tk.Frame(tabs, bg=CARD) for _ in range(4))
        tabs.add(form_tab, text="Inputs")
        tabs.add(scope_tab, text="Scope")
        tabs.add(yaml_tab, text="YAML file")
        tabs.add(csv_tab, text="CSV library")
        scope = tk.Label(scope_tab, text="Proposal scope and assumptions\n\n" + "\n".join("•  " + s for s in SCOPE),
                         bg=CARD, fg=TEXT, font=(FONT, 12), justify="left", anchor="nw")
        scope.pack(fill="both", expand=True, pady=8)
        autowrap(scope)

        # key inputs: groups in a scrolling area, 1 or 2 columns depending on the width
        self.form = {}
        buttons = tk.Frame(form_tab, bg=CARD)
        buttons.pack(side="bottom", fill="x", pady=(4, 0))
        area = tk.Canvas(form_tab, bg=CARD, highlightthickness=0)
        scroll = ttk.Scrollbar(form_tab, orient="vertical", command=area.yview)
        area.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        area.pack(side="left", fill="both", expand=True)
        grid = tk.Frame(area, bg=CARD)
        area.create_window((0, 0), window=grid, anchor="nw")
        grid.bind("<Configure>", lambda e: area.configure(scrollregion=area.bbox("all")))
        groups = []
        for g, (group, fields) in enumerate(FORM):
            col = tk.Frame(grid, bg=CARD)
            groups.append(col)
            tk.Label(col, text=group, bg=CARD, fg=ACCENT, font=(FONT, 12, "bold")).pack(anchor="w", pady=(8, 2))
            box = tk.Frame(col, bg=CARD)
            box.pack(anchor="w")
            for r, (path, label, unit, choices) in enumerate(fields):
                tk.Label(box, text=label, bg=CARD, fg=TEXT, font=(FONT, 11)).grid(row=r, column=0, sticky="w", pady=1)
                if choices:
                    w = ttk.Combobox(box, values=choices, state="readonly", width=15, font=(FONT, 11))
                else:
                    w = ttk.Entry(box, width=12, font=(FONT, 11))
                w.grid(row=r, column=1, padx=8, pady=1, sticky="w")
                tk.Label(box, text=unit, bg=CARD, fg=MUTED, font=(FONT, 10)).grid(row=r, column=2, sticky="w")
                self.form[path] = w

        def layout(event):
            two = event.width > 760
            for g, col in enumerate(groups):
                row, column = ((g // 2, g % 2) if two else (g, 0))
                col.grid(row=row, column=column, sticky="nw", padx=(0, 28))
        area.bind("<Configure>", layout)
        area.bind_all("<MouseWheel>", lambda e: area.yview_scroll(-e.delta, "units")
                      if str(e.widget).startswith(str(form_tab)) else None)
        button(buttons, "Check and apply", self.apply_form).pack(side="left")
        button(buttons, "Reset to base case",
               lambda: self.apply_config(load_config(ROOT / "config" / "base_case.yaml")), primary=False).pack(
            side="left", padx=8)
        hint = tk.Label(buttons, text="Geometry, survey, nozzles, criteria: YAML tab.", bg=CARD, fg=MUTED,
                        font=(FONT, 11), justify="left", anchor="w")
        hint.pack(side="left", fill="x", expand=True, padx=8)
        autowrap(hint)

        # full YAML
        row = tk.Frame(yaml_tab, bg=CARD)
        row.pack(fill="x", pady=4)
        button(row, "Check and apply", self.apply_case_text).pack(side="left")
        button(row, "Open YAML…", self.open_case, primary=False).pack(side="left", padx=8)
        button(row, "Save YAML…", self.save_case, primary=False).pack(side="left")
        self.case_text = tk.Text(yaml_tab, font=(MONO, 11), undo=True, wrap="none", bg="#fbfbfc", fg=TEXT,
                                 relief="flat", highlightthickness=1, highlightbackground=BORDER)
        self.case_text.pack(fill="both", expand=True)

        # CSV library
        row = tk.Frame(csv_tab, bg=CARD)
        row.pack(fill="x", pady=4)
        button(row, "Upload CSV…", self.upload_library).pack(side="left")
        button(row, "Load example", lambda: self.open_library(ROOT / "data" / "case_library.csv"),
               primary=False).pack(side="left", padx=8)
        button(row, "Export current case…", self.export_case, primary=False).pack(side="left")
        button(row, "Use this case", self.use_case).pack(side="right")
        self.case_box = ttk.Combobox(row, state="readonly", width=30, font=(FONT, 11))
        self.case_box.pack(side="right", padx=8)
        self.library_file = tk.Label(csv_tab, text="One CSV = every input; one column per case. Empty cell = "
                                                   "same as the first case. Lists use ';'.",
                                     bg=CARD, fg=MUTED, font=(FONT, 11))
        self.library_file.pack(anchor="w")
        self.library_tree = table(csv_tab, height=14)

    def fill_form(self):
        for path, widget in self.form.items():
            value = get_path(self.config_data, path)
            if isinstance(widget, ttk.Combobox):
                widget.set(str(value))
            else:
                widget.delete(0, "end")
                widget.insert(0, str(value))

    def apply_form(self):
        cfg = copy.deepcopy(self.config_data)
        for path, widget in self.form.items():
            text = widget.get().strip()
            try:
                value = float(text)
            except ValueError:
                value = text
            set_path(cfg, path, value)
        if self.apply_config(cfg):
            self.status.config(text="Inputs applied.", fg=GREEN)

    def apply_case_text(self):
        try:
            cfg = yaml.safe_load(self.case_text.get("1.0", "end"))
        except yaml.YAMLError as error:
            messagebox.showerror("YAML error", str(error))
            return
        if self.apply_config(cfg):
            self.status.config(text="Case file applied.", fg=GREEN)

    def open_case(self):
        path = filedialog.askopenfilename(initialdir=ROOT / "config", filetypes=[("YAML", "*.yaml *.yml")])
        if path:
            self.apply_config(load_config(path))

    def save_case(self):
        path = filedialog.asksaveasfilename(initialdir=ROOT / "config", defaultextension=".yaml")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(self.config_data, f, sort_keys=False, allow_unicode=True)

    def upload_library(self):
        path = filedialog.askopenfilename(initialdir=ROOT / "data", filetypes=[("CSV", "*.csv")])
        if path:
            self.open_library(path)

    def open_library(self, path):
        try:
            shown = read_table(path)
            self.library = read_case_csv(path)
        except Exception as error:
            messagebox.showerror("CSV error", str(error))
            return
        fill_table(self.library_tree, shown.fillna(""), 3)
        names = case_names(shown)
        self.case_box.config(values=names)
        self.case_box.current(0)
        self.library_file.config(text=f"{len(shown)} inputs  ·  {len(names)} cases  ·  {Path(path).name}")

    def use_case(self):
        if not self.library:
            messagebox.showinfo("Case library", "Upload a CSV (or load the example) first.")
            return
        if self.apply_config(self.library[self.case_box.get()]):
            self.show("steady")

    def export_case(self):
        path = filedialog.asksaveasfilename(initialdir=ROOT / "data", defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")])
        if path:
            write_case_csv(path, {self.config_data.get("name", "case"): self.config_data})

    # ------------------------------------------------------------------
    # Page 2: Steady Hydraulics
    # ------------------------------------------------------------------
    def steady_page(self):
        body = self.page_header("steady", "Steady Hydraulics",
                                "Pump → drill string → bit → annulus → choke.   BHP = p_c + ρgh + ΔP_a     "
                                "SPP = p_c + ΔP_d + ΔP_bit + ΔP_a     ECD = BHP / (g h)")
        top = tk.Frame(body, bg=PAGE)
        top.pack(fill="x")
        self.steady_badge = tk.Label(top, text="", font=(FONT, 12, "bold"), padx=10, pady=4)
        self.steady_badge.pack(side="left", pady=(0, 6))
        tiles = tk.Frame(body, bg=PAGE)
        tiles.pack(fill="x")
        self.kpi = {k: kpi(tiles, t) for k, t in (("bhp", "Bottomhole pressure"), ("spp", "Stand-pipe pressure"),
                                                  ("pc", "Choke pressure p_c"), ("ecd", "ECD"))}
        gauge = card(body, None, fill="x", expand=False, pady=(8, 4))
        self.gauge_fig = Figure(figsize=(9, 1.35), layout="constrained")
        self.gauge_canvas = FigureCanvasTkAgg(self.gauge_fig, master=gauge)
        self.gauge_canvas.get_tk_widget().pack(fill="x")

        row = tk.Frame(body, bg=PAGE)
        row.pack(fill="both", expand=True)
        acc = card(row, "Pressure accounting", side="right", fill="y", expand=False, padx=(10, 0))
        acc.master.config(width=330)
        acc.master.pack_propagate(False)
        self.accounting = tk.Label(acc, text="", bg=CARD, fg=TEXT, font=(MONO, 11), justify="left", anchor="nw")
        self.accounting.pack(fill="both", expand=True)
        charts = card(row, None)
        tabs = ttk.Notebook(charts)
        tabs.pack(fill="both", expand=True)
        frames = {n: tk.Frame(tabs, bg=CARD) for n in ("Pressure profile", "Pressure path", "Segments and regimes",
                                                       "Trajectory")}
        for name, frame in frames.items():
            tabs.add(frame, text=name)
        self.profile_fig, self.profile_canvas = chart(frames["Pressure profile"])
        self.path_fig, self.path_canvas = chart(frames["Pressure path"])
        self.traj_fig, self.traj_canvas = chart(frames["Trajectory"])
        button(frames["Segments and regimes"], "Export CSV",
               lambda: self.save_csv(self.steady.segments, "segments.csv"), primary=False).pack(anchor="w", pady=4)
        self.segment_tree = table(frames["Segments and regimes"], height=14)

    def show_steady(self):
        case = self.case
        r = self.steady = solve_steady(case, self.seg)
        unit, p = self.unit.get(), plots.p_conv(self.unit.get())
        self.kpi["bhp"][0].config(text=f"{p(r.bhp):,.1f} {unit}")
        self.kpi["bhp"][1].config(text=f"target {p(case.target):,.1f} {unit}")
        self.kpi["spp"][0].config(text=f"{p(r.spp):,.1f} {unit}")
        self.kpi["spp"][1].config(text=f"losses {p(r.dp_total):,.1f} {unit}")
        self.kpi["pc"][0].config(text=f"{p(r.p_c):,.1f} {unit}")
        self.kpi["pc"][1].config(text=f"choke z0 = {r.z:.3f}" + ("" if r.target_reachable else "  (LIMIT)"))
        self.kpi["ecd"][0].config(text=f"{u.ppg(r.ecd):.3f} ppg")
        self.kpi["ecd"][1].config(text=f"without p_c {u.ppg(r.ecd_no_sbp):.3f} ppg")
        text = {"safe": ("ok", "✓  SAFE - BHP inside p_pore + δ … p_frac − δ"),
                "margin": ("warn", "!  BHP inside the window but within the safety margin δ"),
                "kick risk": ("fail", "✗  KICK RISK - BHP below pore pressure"),
                "loss risk": ("fail", "✗  LOSS RISK - BHP above fracture pressure")}[r.status]
        if not r.target_reachable:
            text = (text[0] if text[0] == "fail" else "warn", text[1] + "   ·   target NOT reachable "
                    "(see suggestions on page 1)")
        badge(self.steady_badge, *text)
        lines = [("ρ g h  (hydrostatic)", r.hydrostatic), ("+ ΔP_a  annulus", r.dp_annulus),
                 ("+ p_c  choke", r.p_c), ("= BHP", r.bhp), ("", None),
                 ("ΔP_d  drill pipe", r.dp_drill_pipe), ("ΔP_d  BHA", r.dp_bha), ("ΔP_bit  nozzles", r.dp_bit),
                 ("ΔP_a  annulus", r.dp_annulus), ("p_c  choke", r.p_c), ("= SPP", r.spp)]
        self.accounting.config(text="\n".join(f"{n:<22}{p(v):>9,.2f}" if v is not None else "" for n, v in lines)
                               + f"\n\n[{unit}]\n\nfriction: {case.friction_model}\n"
                                 f"flow {u.gpm(r.q):.0f} gpm, BPP {u.gpm(r.q_bpp):.0f} gpm")
        redraw(self.gauge_fig, self.gauge_canvas, plots.window_gauge, r, case, unit)
        redraw(self.profile_fig, self.profile_canvas, plots.pressure_profile, r, case, unit)
        redraw(self.path_fig, self.path_canvas, plots.pressure_path, r, unit)
        redraw(self.traj_fig, self.traj_canvas, plots.trajectory, case)
        s = r.segments
        fill_table(self.segment_tree, pd.DataFrame({
            "MD top [m]": s["md_top"], "MD bottom [m]": s["md_bottom"], "TVD [m]": s["tvd_bottom"],
            "Pipe": s["pipe"], "v pipe [m/s]": s["string_velocity"], "v_c pipe": s["string_critical_velocity"],
            "Pipe regime": s["string_regime"], f"ΔP pipe [{unit}]": p(s["string_dp"]),
            "v ann [m/s]": s["annulus_velocity"], "v_c ann": s["annulus_critical_velocity"],
            "Annulus regime": s["annulus_regime"], f"ΔP ann [{unit}]": p(s["annulus_dp"])}), 3)

    # ------------------------------------------------------------------
    # Page 3: Dynamic Model
    # ------------------------------------------------------------------
    def dynamic_page(self):
        body = self.page_header("dynamic", "Dynamic Model",
                                "(V_d/β) ṗ_p = q_pump − q     (V_a/β) ṗ_c = q + q_bpp − q_c     "
                                "M q̇ = p_p − p_c − ΔP_d − ΔP_bit − ΔP_a     ż = r_max tanh((z_cmd − z)/(τa r_max))")
        top = card(body, None, fill="x", expand=False)
        names = [n for n, s in self.specs.items() if s["kind"] == "dynamic"]
        self.dyn_names = names
        tk.Label(top, text="Scenario", bg=CARD, fg=MUTED).grid(row=0, column=0, sticky="w")
        self.dyn_scenario = ttk.Combobox(top, state="readonly", width=30, font=(FONT, 11),
                                         values=[self.specs[n]["title"] for n in names])
        self.dyn_scenario.current(names.index("S3"))
        self.dyn_scenario.grid(row=0, column=1, padx=6)
        tk.Label(top, text="Controller", bg=CARD, fg=MUTED).grid(row=0, column=2, padx=(10, 0))
        self.dyn_controller = ttk.Combobox(top, state="readonly", width=12, font=(FONT, 11),
                                           values=["scenario default", "none", "P", "PI", "PID"])
        self.dyn_controller.current(0)
        self.dyn_controller.grid(row=0, column=3, padx=6)
        tk.Label(top, text="Solver", bg=CARD, fg=MUTED).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.dyn_method = ttk.Combobox(top, state="readonly", width=8, font=(FONT, 11),
                                       values=["Radau", "BDF", "LSODA", "RK45"])
        self.dyn_method.current(0)
        self.dyn_method.grid(row=1, column=1, padx=6, sticky="w", pady=(6, 0))
        self.dyn_conv = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Step-halving check", variable=self.dyn_conv).grid(
            row=1, column=2, columnspan=2, sticky="w", padx=10, pady=(6, 0))
        button(top, "▶  Run", self.run_dynamic).grid(row=0, column=4, rowspan=2, padx=12, sticky="ns")

        tiles = tk.Frame(body, bg=PAGE)
        tiles.pack(fill="x", pady=(6, 0))
        self.dyn_kpi = {k: kpi(tiles, t, compact=True) for k, t in (
            ("mass", "Mass balance"), ("conv", "Convergence"), ("solver", "Solver"), ("range", "BHP range"))}
        charts = card(body, None, pady=(6, 0))
        tabs = ttk.Notebook(charts)
        tabs.pack(fill="both", expand=True)
        frames = {n: tk.Frame(tabs, bg=CARD) for n in ("Overview", "Four states", "Losses and ECD",
                                                       "Depth profiles", "Mass balance", "Data")}
        for name, frame in frames.items():
            tabs.add(frame, text=name)
        self.dyn_fig, self.dyn_canvas = chart(frames["Overview"], 8)
        self.states_fig, self.states_canvas = chart(frames["Four states"], 6)
        self.loss_fig, self.loss_canvas = chart(frames["Losses and ECD"], 6)
        self.prof_fig, self.prof_canvas = chart(frames["Depth profiles"], 6)
        self.mass_fig, self.mass_canvas = chart(frames["Mass balance"], 4)
        button(frames["Data"], "Export time series CSV",
               lambda: self.save_csv(self.dyn_result.timeseries if self.dyn_result else None, "timeseries.csv"),
               primary=False).pack(anchor="w", pady=4)
        self.dyn_table = table(frames["Data"], height=14)
        message(self.dyn_fig, self.dyn_canvas, "Choose a scenario and press Run.")

    def run_dynamic(self):
        name = self.dyn_names[self.dyn_scenario.current()]
        choice = self.dyn_controller.get()
        controller = None if choice == "scenario default" else choice
        method, conv = self.dyn_method.get(), self.dyn_conv.get()
        case, seg, specs = self.case, self.seg, self.specs

        def work(progress, cancel):
            spec = scenarios.schedule_of(name, specs)
            r = simulate(case, seg, spec, controller or spec.get("controller", "none"), name, method=method,
                         progress=lambda f: progress(f * (0.5 if conv else 1.0)), cancel=cancel)
            if conv:
                check_convergence(case, seg, spec, r, progress=lambda f: progress(0.5 + f / 2), cancel=cancel)
            ts = r.timeseries
            times = np.linspace(ts["time_s"].iloc[0], ts["time_s"].iloc[-1], 7)
            r.profiles = depth_profiles(case, seg, ts, times)
            return r
        self.run_task(f"Dynamic run {name}", work, self.dynamic_done)

    def dynamic_done(self, result):
        self.dyn_result = result
        self.show_dynamic()

    def show_dynamic(self):
        r, unit = self.dyn_result, self.unit.get()
        p = plots.p_conv(unit)
        mb = r.mass_balance
        self.dyn_kpi["mass"][0].config(text=f"{mb['relative_percent']:.1e} %", fg=GREEN if mb["relative_percent"] < 1e-3 else RED)
        self.dyn_kpi["mass"][1].config(text=f"max residual {mb['max_residual_m3'] * 1e3:.2e} L")
        c = r.convergence
        if c:
            self.dyn_kpi["conv"][0].config(text=f"{c['change_percent']:.4f} %", fg=GREEN if c["passed"] else RED)
            self.dyn_kpi["conv"][1].config(text=f"step halved (limit {c['limit_percent']} %)")
        else:
            self.dyn_kpi["conv"][0].config(text="not checked", fg=MUTED)
            self.dyn_kpi["conv"][1].config(text="tick 'Step-halving check'")
        s = r.solver
        self.dyn_kpi["solver"][0].config(text=s["method"], fg=TEXT if not s["failed_intervals"] else RED)
        self.dyn_kpi["solver"][1].config(text=f"rtol {s['rtol']:g} · atol {s['atol']:g} · "
                                              f"{s['rhs_evaluations']:,} RHS calls")
        m = r.metrics
        self.dyn_kpi["range"][0].config(text=f"{p(m['min_bhp']):,.1f} – {p(m['max_bhp']):,.1f}")
        self.dyn_kpi["range"][1].config(text=f"{unit} · {m['time_in_window_percent']:.0f} % in safe window"
                                        + ("  ·  " + r.warnings[0] if r.warnings else ""))
        redraw(self.dyn_fig, self.dyn_canvas, plots.dynamic_overview, r, unit)
        redraw(self.states_fig, self.states_canvas, plots.states_chart, r, unit)
        redraw(self.loss_fig, self.loss_canvas, plots.losses_chart, r, self.case, unit)
        redraw(self.prof_fig, self.prof_canvas, plots.profiles_over_time, r.profiles, unit)
        redraw(self.mass_fig, self.mass_canvas, plots.mass_balance_chart, r)
        ts = r.timeseries
        step = max(1, len(ts) // 400)
        fill_table(self.dyn_table, pd.DataFrame({
            "t [s]": ts["time_s"], "q_pump [gpm]": u.gpm(ts["q_pump"]), "q_bpp [gpm]": u.gpm(ts["q_bpp"]),
            "q [gpm]": u.gpm(ts["q"]), "q_c [gpm]": u.gpm(ts["q_choke"]), f"p_p [{unit}]": p(ts["spp"]),
            f"p_c [{unit}]": p(ts["p_c"]), f"BHP [{unit}]": p(ts["bhp"]), f"target [{unit}]": p(ts["target"]),
            "z_cmd": ts["z_cmd"], "z": ts["z"], "ECD [ppg]": u.ppg(ts["ecd"]), "status": ts["status"]}).iloc[::step], 3)

    # ------------------------------------------------------------------
    # Page 4: Control & Scenarios
    # ------------------------------------------------------------------
    def control_page(self):
        body = self.page_header("control", "Control & Scenarios",
                                "e = p_target − BHP_m     z_cmd = z0 − (Kp e + Ki ∫e dt + Kd ė_f)     reverse acting: "
                                "BHP low → choke closes · back-calculation anti-windup · BHP_m = BHP(t − θm) every Ts")
        left = tk.Frame(body, bg=PAGE, width=330)
        left.pack(side="left", fill="y", padx=(0, 10))
        left.pack_propagate(False)
        pick = card(left, "Scenario", fill="both", expand=True)
        self.ctl_names = [n for n, s in self.specs.items() if s["kind"] in ("steady", "dynamic", "compare")]
        self.ctl_list = tk.Listbox(pick, height=7, font=(FONT, 12), activestyle="none", relief="flat",
                                   highlightthickness=1, highlightbackground=BORDER, selectbackground=ACCENT,
                                   exportselection=False)
        for n in self.ctl_names:
            self.ctl_list.insert("end", self.specs[n]["title"])
        self.ctl_list.pack(fill="x")
        self.ctl_list.bind("<<ListboxSelect>>", lambda e: self.describe_control())
        self.ctl_text = tk.Label(pick, text="", bg=CARD, fg=MUTED, font=(FONT, 11), justify="left", wraplength=290)
        self.ctl_text.pack(anchor="w", pady=6)
        tk.Label(pick, text="Schedule (YAML, editable)", bg=CARD, fg=TEXT,
                 font=(FONT, 11, "bold")).pack(anchor="w")
        self.schedule_text = tk.Text(pick, height=5, font=(MONO, 10), wrap="none", bg="#fbfbfc", relief="flat",
                                     highlightthickness=1, highlightbackground=BORDER)
        self.schedule_text.pack(fill="both", expand=True)
        tune = button(pick, "PID tuning study on S3", self.run_tuning, primary=False)
        tune.pack(side="bottom", anchor="w", pady=(6, 0), before=self.schedule_text)   # buttons stay visible
        row = tk.Frame(pick, bg=CARD)
        row.pack(side="bottom", fill="x", pady=(6, 0), before=self.schedule_text)
        button(row, "▶  Run", self.run_control).pack(side="left")
        button(row, "Reset schedules", self.reset_schedules, primary=False).pack(side="left", padx=6)
        self.ctl_list.selection_set(self.ctl_names.index("S4"))
        self.describe_control()

        cards = tk.Frame(body, bg=PAGE)
        cards.pack(fill="x")
        tk.Label(cards, text="PROPOSAL CRITERIA (preliminary targets, controlled run)",
                 bg=PAGE, fg=MUTED, font=(FONT, 10, "bold")).pack(anchor="w")
        tiles = tk.Frame(cards, bg=PAGE)
        tiles.pack(fill="x")
        self.crit_cards = [kpi(tiles, t, compact=True) for t in ("Overshoot", "Settling", "In window")]
        charts = card(body, None, pady=(6, 0))
        tabs = ttk.Notebook(charts)
        tabs.pack(fill="both", expand=True)
        frames = {n: tk.Frame(tabs, bg=CARD) for n in ("Response", "Controller terms", "Metrics", "Tuning")}
        for name, frame in frames.items():
            tabs.add(frame, text=name)
        self.ctl_fig, self.ctl_canvas = chart(frames["Response"], 7)
        self.terms_fig, self.terms_canvas = chart(frames["Controller terms"], 7)
        self.ctl_note = tk.Label(frames["Metrics"], text="", bg=CARD, fg=AMBER, font=(FONT, 11), justify="left",
                                 anchor="w")
        self.ctl_note.pack(fill="x")
        autowrap(self.ctl_note)
        button(frames["Metrics"], "Export metrics CSV", lambda: self.save_csv(self.ctl_table_df, "metrics.csv"),
               primary=False).pack(anchor="w", pady=4)
        self.ctl_table_df = None
        self.ctl_table = table(frames["Metrics"], height=10)
        self.tune_text = tk.Label(frames["Tuning"], text="Proposal 4-2-7: Ki = Kd = 0 and raise Kp until the "
                                  "response is fast without marked oscillation; then add Ki; Kd only if the "
                                  "overshoot is above 5 %. Press 'PID tuning (S3)' (about one minute).",
                                  bg=CARD, fg=MUTED, font=(FONT, 11), justify="left", anchor="w")
        self.tune_text.pack(fill="x", pady=4)
        autowrap(self.tune_text)
        self.tune_table = table(frames["Tuning"], height=12)
        message(self.ctl_fig, self.ctl_canvas, "Select a scenario and press Run.")

    def selected_control(self):
        return self.ctl_names[self.ctl_list.curselection()[0]]

    def describe_control(self):
        name = self.selected_control()
        spec = self.specs[name]
        self.ctl_text.config(text=spec.get("description", ""))
        sched = {k: v for k, v in spec.items() if k not in ("title", "description", "kind")}
        self.schedule_text.delete("1.0", "end")
        self.schedule_text.insert("1.0", yaml.safe_dump(sched, sort_keys=False, default_flow_style=None))

    def read_schedule(self, name):
        """Take the edited schedule into the in-memory scenarios (never into physics code)."""
        try:
            edited = yaml.safe_load(self.schedule_text.get("1.0", "end")) or {}
        except yaml.YAMLError as error:
            messagebox.showerror("Schedule error", str(error))
            return False
        self.specs[name] = {**{k: self.specs[name][k] for k in ("title", "description", "kind")}, **edited}
        return True

    def reset_schedules(self):
        self.specs = scenarios.load_scenarios()
        self.describe_control()

    def run_control(self):
        name = self.selected_control()
        if not self.read_schedule(name):
            return
        case, seg, specs = self.case, self.seg, copy.deepcopy(self.specs)
        kind = specs[name]["kind"]

        def work(progress, cancel):
            if kind == "steady":
                return "steady", scenarios.run_steady_rates(case, seg, specs[name], progress, cancel)[0]
            if kind == "compare":
                return "compare", scenarios.run_compare(case, seg, name, specs, True, progress, cancel)
            return "dynamic", scenarios.run_dynamic(case, seg, name, specs, None, True, progress, cancel)
        self.run_task(f"Scenario {name}", work, self.control_done)

    def control_done(self, result):
        self.control_result = result
        self.show_control()

    def show_control(self):
        kind, data = self.control_result
        unit = self.unit.get()
        p = plots.p_conv(unit)
        self.ctl_note.config(text="")
        if kind == "steady":
            t = data
            self.ctl_table_df = pd.DataFrame({
                "pump [gpm]": t["pump_rate_gpm"], "z0": t["choke_opening"], f"p_c [{unit}]": p(t["p_c"]),
                f"ΔP_d [{unit}]": p(t["dp_drill_pipe"] + t["dp_bha"]), f"ΔP_bit [{unit}]": p(t["dp_bit"]),
                f"ΔP_a [{unit}]": p(t["dp_annulus"]), f"BHP [{unit}]": p(t["bhp"]), f"SPP [{unit}]": p(t["spp"]),
                "ECD [ppg]": t["ecd_ppg"], "status": t["status"], "dynamic drift [Pa]": t["dynamic_drift"]})
            fill_table(self.ctl_table, self.ctl_table_df, 3)
            self.ctl_fig.clear()
            ax1, ax2 = self.ctl_fig.subplots(1, 2)
            ax1.bar([f"{q:.0f} gpm" for q in t["pump_rate_gpm"]], p(t["spp"]), color=plots.BLUE, width=0.55)
            ax1.set_ylabel(f"SPP [{unit}]")
            plots._title(ax1, "S0: SPP grows with the pump rate")
            ax2.bar([f"{q:.0f} gpm" for q in t["pump_rate_gpm"]], t["choke_opening"] * 100, color=plots.ORANGE,
                    width=0.55)
            ax2.set_ylabel("Choke opening z0 [%]")
            plots._title(ax2, "… the choke opens to keep BHP on target")
            for ax in (ax1, ax2):
                plots._style(ax)
            self.ctl_canvas.draw_idle()
            message(self.terms_fig, self.terms_canvas, "S0 is steady - no controller terms.")
            for title, note in self.crit_cards:
                title.config(text="–", fg=MUTED)
                note.config(text="not a transient scenario")
            return
        runs = data if kind == "compare" else {data.controller: data}
        main = runs.get("PID") or next(iter(runs.values()))
        if kind == "compare":
            redraw(self.ctl_fig, self.ctl_canvas, plots.compare_chart, runs, unit)
            if self.case.pid.kd == 0:
                self.ctl_note.config(text="Note: the tuning rule gave Kd = 0 (PI overshoot already below 5 %), "
                                          "so the PID run equals the PI run.")
        else:
            redraw(self.ctl_fig, self.ctl_canvas, plots.dynamic_overview, main, unit)
        if main.controller != "none":
            redraw(self.terms_fig, self.terms_canvas, plots.controller_chart, main, unit)
        else:
            message(self.terms_fig, self.terms_canvas, "Fixed choke - no controller. Choose S2b or S4.")
        criteria_cards(self.crit_cards, main.metrics)
        self.ctl_table_df = scenarios.metrics_table(runs, unit)
        fill_table(self.ctl_table, self.ctl_table_df, 3)
        warnings = [w for r in runs.values() for w in r.warnings]
        if warnings:
            self.ctl_note.config(text=self.ctl_note.cget("text") + "\n" + "\n".join(warnings))

    def run_tuning(self):
        case, seg, spec = self.case, self.seg, scenarios.schedule_of("S3", self.specs)
        self.run_task("PID tuning on S3", lambda progress, cancel: analysis.tune_pid(case, seg, spec, progress),
                      self.tuning_done)

    def tuning_done(self, result):
        table_df, gains, reasons = result
        self.tune_text.config(text="\n".join(reasons) + f"\n→ Kp {gains.kp * u.BAR:g} 1/bar, Ki {gains.ki * u.BAR:g} "
                              f"1/(bar s), Kd {gains.kd * u.BAR:g} s/bar.  Enter them on page 1 to use them.",
                              fg=TEXT)
        fill_table(self.tune_table, table_df, 4)

    # ------------------------------------------------------------------
    # Page 5: Sensitivity
    # ------------------------------------------------------------------
    def sensitivity_page(self):
        body = self.page_header("sensitivity", "Sensitivity",
                                "S5: the S4 PID run with βeff, τy, μp, C_d and A_max changed by ±20 % (gains kept), "
                                "plus the equal-percentage choke - about one minute.  "
                                "Steady tornado / sweep: one input at a time, choke held at z0.")
        top = card(body, None, fill="x", expand=False)
        button(top, "▶  Run S5", self.run_s5).pack(side="left")
        tk.Label(top, text="Steady ±%", bg=CARD, fg=MUTED).pack(side="left", padx=(16, 4))
        self.change = ttk.Spinbox(top, from_=1, to=50, width=4, font=(FONT, 11))
        self.change.set(10)
        self.change.pack(side="left")
        button(top, "Tornado", self.run_tornado, primary=False).pack(side="left", padx=6)
        tk.Label(top, text="Sweep", bg=CARD, fg=MUTED).pack(side="left", padx=(12, 4))
        self.sweep_param = ttk.Combobox(top, state="readonly", width=15, font=(FONT, 11),
                                        values=list(analysis.PARAMETERS.values()))
        self.sweep_param.current(0)
        self.sweep_param.pack(side="left")
        button(top, "×0.6 … ×1.4", self.run_sweep, primary=False).pack(side="left", padx=6)
        box = card(body, None)
        tabs = ttk.Notebook(box)
        tabs.pack(fill="both", expand=True)
        chart_tab, table_tab = tk.Frame(tabs, bg=CARD), tk.Frame(tabs, bg=CARD)
        tabs.add(chart_tab, text="Chart")
        tabs.add(table_tab, text="Table")
        self.sens_fig, self.sens_canvas = chart(chart_tab, 5)
        self.sens_df = None
        button(table_tab, "Export CSV", lambda: self.save_csv(self.sens_df, "sensitivity.csv"),
               primary=False).pack(anchor="w", pady=4)
        self.sens_table = table(table_tab, height=14)
        message(self.sens_fig, self.sens_canvas, "Run S5 or a steady sensitivity.")

    def run_s5(self):
        config, specs = copy.deepcopy(self.config_data), copy.deepcopy(self.specs)
        self.run_task("S5 sensitivity", lambda progress, cancel: scenarios.run_sensitivity(
            config, "S5", specs, progress, cancel), self.s5_done)

    def s5_done(self, result):
        self.s5 = result
        self.show_s5()

    def show_s5(self):
        table_df, results = self.s5
        redraw(self.sens_fig, self.sens_canvas, plots.s5_chart, table_df, results, self.unit.get())
        self.sens_df = table_df
        fill_table(self.sens_table, table_df, 3)

    def run_tornado(self):
        data = analysis.steady_sensitivity(self.config_data, float(self.change.get()) / 100)
        unit = self.unit.get()
        p = plots.p_conv(unit)
        redraw(self.sens_fig, self.sens_canvas, plots.tornado, data, unit)
        self.sens_df = pd.DataFrame({
            "Parameter": data["parameter"], f"BHP − [{unit}]": p(data["bhp_low"]), f"BHP + [{unit}]": p(data["bhp_high"]),
            f"SPP − [{unit}]": p(data["spp_low"]), f"SPP + [{unit}]": p(data["spp_high"]),
            "ECD − [ppg]": u.ppg(data["ecd_low"]), "ECD + [ppg]": u.ppg(data["ecd_high"])})
        fill_table(self.sens_table, self.sens_df, 2)

    def run_sweep(self):
        name = list(analysis.PARAMETERS)[self.sweep_param.current()]
        data = analysis.sweep(self.config_data, name, np.linspace(0.6, 1.4, 9))
        unit = self.unit.get()
        redraw(self.sens_fig, self.sens_canvas, plots.sweep_chart, data, f"{analysis.PARAMETERS[name]} (× base)", unit)
        p = plots.p_conv(unit)
        self.sens_df = pd.DataFrame({"Factor": data["factor"], f"BHP [{unit}]": p(data["bhp"]),
                                     f"SPP [{unit}]": p(data["spp"]), "ECD [ppg]": u.ppg(data["ecd"])})
        fill_table(self.sens_table, self.sens_df, 2)

    # ------------------------------------------------------------------
    # Page 6: Validation & Evidence
    # ------------------------------------------------------------------
    def validation_page(self):
        body = self.page_header("validation", "Validation & Evidence",
                                "Three levels (proposal 4-2-10). Every row states its data type and source; "
                                "synthetic data are never called field validation.")
        tiles = tk.Frame(body, bg=PAGE)
        tiles.pack(fill="x")
        self.level_badges = []
        for title, text in (("LEVEL 1 · code / equations", "hand calculation, conservation, convergence"),
                            ("LEVEL 2 · steady vs published", "textbook / published example, MAPE < 5 %"),
                            ("LEVEL 3 · transient shape", "Kaasa 2012, Naderi Lordejani 2020")):
            box = tk.Frame(tiles, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
            box.pack(side="left", fill="both", expand=True, padx=(0, 8))
            tk.Label(box, text=title, bg=CARD, fg=MUTED, font=(FONT, 10, "bold")).pack(anchor="w", padx=12, pady=(10, 0))
            b = tk.Label(box, text="not run", font=(FONT, 13, "bold"), padx=8, pady=2)
            b.pack(anchor="w", padx=12, pady=4)
            badge(b, "info", "not run")
            tk.Label(box, text=text, bg=CARD, fg=MUTED, font=(FONT, 11)).pack(anchor="w", padx=12, pady=(0, 10))
            self.level_badges.append(b)
        top = card(body, None, fill="x", expand=False)
        button(top, "▶  Run validation", self.run_validation).pack(side="left")
        button(top, "Level-2 reference…", lambda: self.load_reference(2), primary=False).pack(side="left", padx=8)
        button(top, "Level-3 curve…", lambda: self.load_reference(3), primary=False).pack(side="left")
        button(top, "Export tables", self.export_validation, primary=False).pack(side="left", padx=8)
        self.val_status = tk.Label(body, text="", bg=PAGE, fg=MUTED, font=(FONT, 11), justify="left", anchor="w")
        self.val_status.pack(fill="x")
        autowrap(self.val_status)
        box = card(body, None)
        tabs = ttk.Notebook(box)
        tabs.pack(fill="both", expand=True)
        frames = {n: tk.Frame(tabs, bg=CARD) for n in ("Level 1 table", "Level 1 chart", "Model-form comparison",
                                                       "Level 2", "Level 3", "Sources")}
        for name, frame in frames.items():
            tabs.add(frame, text=name)
        self.val_tables = {k: table(frames[k], height=12) for k in ("Level 1 table", "Model-form comparison",
                                                                   "Level 2", "Level 3")}
        self.val_fig, self.val_canvas = chart(frames["Level 1 chart"], 4.5)
        tk.Label(frames["Sources"], bg=CARD, fg=TEXT, font=(FONT, 11), justify="left", wraplength=1050,
                 text="\n\n".join(f"[{k}]  {v}" for k, v in validation.CITATIONS.items()) +
                      "\n\nBundled reference values are a SYNTHETIC hand calculation (data/hand_calculation.py), "
                      "written independently from the model. Put published data in data/reference/ "
                      "(see the README there).").pack(anchor="w", pady=8)
        self.ref2 = self.ref3 = None

    def load_reference(self, level):
        path = filedialog.askopenfilename(initialdir=ROOT / "data" / "reference", filetypes=[("CSV", "*.csv")])
        if path:
            if level == 2:
                self.ref2 = path
            else:
                self.ref3 = path
            self.run_validation()

    def run_validation(self):
        config, ref2, ref3 = copy.deepcopy(self.config_data), self.ref2, self.ref3
        dyn = self.dyn_result

        def work(progress, cancel):
            l1, sim = validation.level1(config, progress, cancel)
            return {"l1": l1, "form": validation.model_form_table(config), "l2": validation.level2(ref2),
                    "l3": validation.level3(dyn or sim, ref3)}
        self.run_task("Validation", work, self.validation_done)

    def validation_done(self, v):
        self.validation = v
        l1 = v["l1"]
        hand = l1[l1["data_type"] == "synthetic hand calculation"]
        worst = hand["error_percent"].max()
        conservation_ok = l1[l1["data_type"] == "conservation check"]["model"].abs().max() < 1e-6
        convergence = l1[l1["data_type"].str.startswith("convergence")]["model"].iloc[0]
        ok = worst < 1 and conservation_ok and convergence < self.case.solver.convergence_limit
        badge(self.level_badges[0], "ok" if ok else "fail",
              f"{'✓' if ok else '✗'}  max error {worst:.3f} %  (synthetic)")
        table2, status2, value2 = v["l2"]
        badge(self.level_badges[1], "info" if table2 is None else ("ok" if value2 < 5 else "fail"),
              "pending reference data" if table2 is None else f"MAPE {value2:.2f} %")
        table3, status3 = v["l3"]
        badge(self.level_badges[2], "info" if table3 is None else "ok",
              "pending reference data" if table3 is None else "shape compared")
        self.val_status.config(text=f"Level 2: {status2}\nLevel 3: {status3}")
        fill_table(self.val_tables["Level 1 table"], l1, 6)
        fill_table(self.val_tables["Model-form comparison"], v["form"], 3)
        for key, t in (("Level 2", table2), ("Level 3", table3)):
            fill_table(self.val_tables[key], t if t is not None else
                       pd.DataFrame({"status": ["pending reference data - no numbers are invented"]}))
        redraw(self.val_fig, self.val_canvas, plots.validation_chart, hand)

    def export_validation(self):
        if not self.validation:
            messagebox.showinfo("Nothing to export", "Run the validation first.")
            return
        folder = filedialog.askdirectory(initialdir=ROOT)
        if folder:
            self.validation["l1"].to_csv(Path(folder) / "validation_level1.csv", index=False)
            self.validation["form"].to_csv(Path(folder) / "validation_model_comparison.csv", index=False)
            for key, name in (("l2", "validation_level2.csv"), ("l3", "validation_level3.csv")):
                if self.validation[key][0] is not None:
                    self.validation[key][0].to_csv(Path(folder) / name, index=False)
            self.status.config(text=f"Validation tables exported to {folder}", fg=GREEN)


if __name__ == "__main__":
    App().mainloop()
