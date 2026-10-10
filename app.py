"""
MPD well hydraulics simulator - desktop application (Tkinter + matplotlib).

    python app.py

The window has a navigation bar on the left and one page per task:
  Steady state   - BHP, SPP, ECD and the pressure profile (Stage A)
  Dynamic        - the 10 time scenarios (Stage B)
  PID control    - tune the controller, compare with / without PID
  Sensitivity    - tornado chart and parameter sweep
  Validation     - model vs reference values
  Case file      - all inputs (YAML text), open / save
"""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import matplotlib
import numpy as np
import pandas as pd
import yaml
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from mpd import analysis, plots
from mpd import units as u
from mpd.dynamics import simulate
from mpd.hydraulics import solve_steady
from mpd.well import ROOT, build_case, load_config, make_segments

# ----------------------------------------------------------------------
# Look and feel
# ----------------------------------------------------------------------
SIDEBAR = "#111827"        # dark navigation bar
SIDEBAR_TEXT = "#9ca3af"
SIDEBAR_ACTIVE = "#1f2937"
ACCENT = "#2563eb"         # blue buttons and highlights
PAGE = "#f3f4f6"           # light grey page background
CARD = "#ffffff"
BORDER = "#e5e7eb"
TEXT = "#111827"
MUTED = "#6b7280"
GREEN, RED, AMBER = "#059669", "#dc2626", "#d97706"
FONT = "Helvetica Neue"

matplotlib.rcParams.update({"font.family": "sans-serif", "font.size": 9.5,
                            "axes.titlesize": 11, "axes.edgecolor": "#9ca3af",
                            "figure.facecolor": CARD, "axes.facecolor": CARD})

SCENARIOS = yaml.safe_load(open(ROOT / "config" / "scenarios.yaml", encoding="utf-8"))


# ----------------------------------------------------------------------
# Reusable building blocks
# ----------------------------------------------------------------------
def card(parent, title=None, **pack):
    """A white panel with a thin border and an optional title. Returns the inner frame."""
    outer = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
    outer.pack(**({"fill": "both", "expand": True, "pady": 6} | pack))
    if title:
        tk.Label(outer, text=title, bg=CARD, fg=TEXT, font=(FONT, 13, "bold")).pack(
            anchor="w", padx=16, pady=(12, 0))
    inner = tk.Frame(outer, bg=CARD)
    inner.pack(fill="both", expand=True, padx=16, pady=12)
    return inner


def kpi(parent, title):
    """A small result tile: grey title, big number, coloured note. Returns (value, note) labels."""
    box = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
    box.pack(side="left", fill="both", expand=True, padx=(0, 10))
    tk.Label(box, text=title.upper(), bg=CARD, fg=MUTED, font=(FONT, 10, "bold")).pack(
        anchor="w", padx=14, pady=(12, 2))
    value = tk.Label(box, text="-", bg=CARD, fg=TEXT, font=(FONT, 22, "bold"))
    value.pack(anchor="w", padx=14)
    note = tk.Label(box, text="", bg=CARD, fg=MUTED, font=(FONT, 11))
    note.pack(anchor="w", padx=14, pady=(0, 12))
    return value, note


def chart(parent, height=5.0):
    """A matplotlib figure with the zoom / pan / save toolbar."""
    fig = Figure(figsize=(9, height), layout="constrained")
    canvas = FigureCanvasTkAgg(fig, master=parent)
    bar = NavigationToolbar2Tk(canvas, parent, pack_toolbar=False)
    bar.config(background=CARD)
    bar.pack(side="top", fill="x")
    canvas.get_tk_widget().pack(side="top", fill="both", expand=True)
    return fig, canvas


def redraw(fig, canvas, draw_function, *args):
    """Clear a figure, draw it again and show it."""
    fig.clear()
    draw_function(fig, *args)
    canvas.draw_idle()


def table(parent, height=10):
    """A simple table (Treeview) with a scroll bar."""
    frame = tk.Frame(parent, bg=CARD)
    tree = ttk.Treeview(frame, show="headings", height=height)
    bar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=bar.set)
    tree.pack(side="left", fill="both", expand=True)
    bar.pack(side="right", fill="y")
    frame.pack(fill="both", expand=True, pady=4)
    return tree


def fill_table(tree, df, decimals=3):
    """Show a DataFrame in a table (columns are made wide enough for their text)."""
    def text(v):
        if isinstance(v, (float, np.floating)):
            return "-" if np.isnan(v) else f"{v:,.{decimals}f}"
        return str(v)
    rows = [[text(v) for v in row] for row in df.itertuples(index=False)]
    tree.delete(*tree.get_children())
    tree["columns"] = list(df.columns)
    for i, col in enumerate(df.columns):
        longest = max([len(str(col))] + [len(r[i]) for r in rows])
        tree.heading(col, text=col)
        tree.column(col, width=longest * 9 + 24, minwidth=60, anchor="w" if i == 0 else "e")
    for i, values in enumerate(rows):
        tree.insert("", "end", values=values, tags=("odd" if i % 2 else "even",))
    tree.tag_configure("odd", background="#f9fafb")


def button(parent, text, command, primary=True):
    """A flat button (blue for main actions)."""
    return ttk.Button(parent, text=text, command=command,
                      style="Primary.TButton" if primary else "Secondary.TButton")


def labeled_entry(parent, row, label, width=10):
    """Label + entry box in a grid row. Returns the entry."""
    tk.Label(parent, text=label, bg=CARD, fg=MUTED, font=(FONT, 12)).grid(
        row=row, column=0, sticky="w", pady=5)
    entry = ttk.Entry(parent, width=width, font=(FONT, 12))
    entry.grid(row=row, column=1, sticky="e", pady=5, padx=(10, 0))
    return entry


def set_pill(label, status):
    """Coloured status pill for the pressure window."""
    text, fg, bg = {"safe": ("●  SAFE  -  BHP inside the pressure window", GREEN, "#d1fae5"),
                    "kick risk": ("▲  KICK RISK  -  BHP below pore pressure", RED, "#fee2e2"),
                    "loss risk": ("▲  LOSS RISK  -  BHP above fracture pressure", RED, "#fee2e2"),
                    }[status]
    label.config(text=text, fg=fg, bg=bg)


def metrics_table(metrics, unit):
    """Control metrics as a table with units."""
    p = plots.p_conv(unit)
    return pd.DataFrame([
        ("Min BHP", f"{p(metrics['min_bhp']):,.1f} {unit}"),
        ("Max BHP", f"{p(metrics['max_bhp']):,.1f} {unit}"),
        ("Max above target", f"{p(metrics['max_above_target']):,.2f} {unit}"),
        ("Max below target", f"{p(metrics['max_below_target']):,.2f} {unit}"),
        ("Steady-state error", f"{p(metrics['steady_state_error']):,.2f} {unit}"),
        ("Settling time", "not settled" if np.isnan(metrics["settling_time_s"])
         else f"{metrics['settling_time_s']:.1f} s"),
        ("Time outside window", f"{metrics['time_outside_window_s']:.1f} s")],
        columns=["Metric", "Value"])


def event_table(metrics, unit):
    """Metrics for every disturbance (event) separately."""
    p = plots.p_conv(unit)
    return pd.DataFrame([{
        "Event": e["event"], "Start [s]": e["time_s"],
        "Settling [s]": round(float(e["settling_time_s"]), 1),
        f"Above [{unit}]": round(float(p(e["max_above_target"])), 2),
        f"Below [{unit}]": round(float(p(e["max_below_target"])), 2),
    } for e in metrics["per_event"]])


# ----------------------------------------------------------------------
# Main window
# ----------------------------------------------------------------------
class App(tk.Tk):

    PAGES = [("steady", "Steady state", "◉"), ("dynamic", "Dynamic scenarios", "◷"),
             ("pid", "PID control", "⟲"), ("sensitivity", "Sensitivity", "▤"),
             ("validation", "Validation", "✓"), ("case", "Case file", "✎")]

    def __init__(self):
        super().__init__()
        self.title("MPD Well Hydraulics Simulator")
        width = min(1480, self.winfo_screenwidth() - 40)
        height = min(940, self.winfo_screenheight() - 110)
        self.geometry(f"{width}x{height}+20+20")
        self.minsize(1200, 760)
        self.configure(bg=PAGE)
        self.setup_style()

        self.config_data = load_config(ROOT / "config" / "base_case.yaml")
        self.unit = tk.StringVar(value="bar")
        self.sim = None            # last dynamic simulation
        self.pid_runs = None       # last PID comparison (with, without)

        self.sidebar()
        self.content = tk.Frame(self, bg=PAGE)
        self.content.pack(side="left", fill="both", expand=True)
        self.pages = {}
        for key, title, _ in self.PAGES:
            self.pages[key] = tk.Frame(self.content, bg=PAGE)
            self.pages[key].place(relx=0, rely=0, relwidth=1, relheight=1)
        self.steady_page()
        self.dynamic_page()
        self.pid_page()
        self.sensitivity_page()
        self.validation_page()
        self.case_page()
        self.load_case()
        self.show("steady")

    def setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=(FONT, 12), background=CARD)
        style.configure("Primary.TButton", font=(FONT, 12, "bold"), foreground="white",
                        background=ACCENT, borderwidth=0, padding=(18, 8))
        style.map("Primary.TButton", background=[("active", "#1d4ed8")])
        style.configure("Secondary.TButton", font=(FONT, 12), foreground=TEXT,
                        background="#e5e7eb", borderwidth=0, padding=(14, 8))
        style.map("Secondary.TButton", background=[("active", "#d1d5db")])
        style.configure("TNotebook", background=CARD, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 6), font=(FONT, 12), background="#f3f4f6",
                        borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", ACCENT)])
        style.configure("Treeview", rowheight=26, font=(FONT, 11), borderwidth=0,
                        fieldbackground=CARD)
        style.configure("Treeview.Heading", font=(FONT, 11, "bold"), background="#f3f4f6",
                        borderwidth=0)
        style.configure("TCombobox", padding=4)
        style.configure("TEntry", padding=4)
        style.configure("TRadiobutton", background=CARD, font=(FONT, 12))
        style.configure("TCheckbutton", background=CARD, font=(FONT, 12))
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=BORDER,
                        borderwidth=0, thickness=8)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def sidebar(self):
        bar = tk.Frame(self, bg=SIDEBAR, width=240)
        bar.pack(side="left", fill="y")
        bar.pack_propagate(False)
        logo = tk.Frame(bar, bg=SIDEBAR)
        logo.pack(fill="x", padx=20, pady=(24, 4))
        tk.Label(logo, text="MPD", bg=ACCENT, fg="white", font=(FONT, 14, "bold"),
                 padx=8, pady=4).pack(side="left")
        tk.Label(logo, text="  Hydraulics", bg=SIDEBAR, fg="white",
                 font=(FONT, 17, "bold")).pack(side="left")
        tk.Label(bar, text="Well hydraulics · BHP control", bg=SIDEBAR, fg=SIDEBAR_TEXT,
                 font=(FONT, 11)).pack(anchor="w", padx=22, pady=(0, 26))
        tk.Label(bar, text="MENU", bg=SIDEBAR, fg="#4b5563", font=(FONT, 10, "bold")).pack(
            anchor="w", padx=22, pady=(0, 6))
        self.nav = {}
        for key, title, icon in self.PAGES:
            item = tk.Label(bar, text=f"   {icon}   {title}", bg=SIDEBAR, fg=SIDEBAR_TEXT,
                            font=(FONT, 13), anchor="w", padx=12, pady=10, cursor="hand2")
            item.pack(fill="x", padx=10, pady=1)
            item.bind("<Button-1>", lambda e, k=key: self.show(k))
            self.nav[key] = item

        bottom = tk.Frame(bar, bg=SIDEBAR)
        bottom.pack(side="bottom", fill="x", padx=22, pady=20)
        tk.Label(bottom, text="PRESSURE UNIT", bg=SIDEBAR, fg="#4b5563",
                 font=(FONT, 10, "bold")).pack(anchor="w")
        units = tk.Frame(bottom, bg=SIDEBAR)
        units.pack(anchor="w", pady=(6, 16))
        for text in ("bar", "psi"):
            tk.Radiobutton(units, text=text, value=text, variable=self.unit, command=self.refresh_all,
                           bg=SIDEBAR, fg="white", selectcolor=SIDEBAR, activebackground=SIDEBAR,
                           activeforeground="white", font=(FONT, 12)).pack(side="left", padx=(0, 10))
        self.case_label = tk.Label(bottom, text="", bg=SIDEBAR, fg=SIDEBAR_TEXT, font=(FONT, 10),
                                   wraplength=200, justify="left")
        self.case_label.pack(anchor="w")
        tk.Label(bottom, text="Research / teaching software.\nSynthetic demonstration data.",
                 bg=SIDEBAR, fg="#4b5563", font=(FONT, 10), justify="left").pack(anchor="w", pady=(10, 0))

    def show(self, key):
        """Show one page and highlight its menu item."""
        self.pages[key].tkraise()
        for k, item in self.nav.items():
            item.config(bg=SIDEBAR_ACTIVE if k == key else SIDEBAR,
                        fg="white" if k == key else SIDEBAR_TEXT)

    def page_header(self, key, title, subtitle):
        """Title and subtitle at the top of a page. Returns the page body frame."""
        page = self.pages[key]
        head = tk.Frame(page, bg=PAGE)
        head.pack(fill="x", padx=28, pady=(22, 6))
        tk.Label(head, text=title, bg=PAGE, fg=TEXT, font=(FONT, 24, "bold")).pack(anchor="w")
        tk.Label(head, text=subtitle, bg=PAGE, fg=MUTED, font=(FONT, 12)).pack(anchor="w", pady=(2, 0))
        body = tk.Frame(page, bg=PAGE)
        body.pack(fill="both", expand=True, padx=28, pady=(6, 20))
        return body

    def chart_and_table(self, body, height):
        """A card with two tabs: a chart and a table."""
        box = card(body, None)
        self.val_box = box.master
        tabs = ttk.Notebook(box)
        tabs.pack(fill="both", expand=True)
        chart_tab, table_tab = tk.Frame(tabs, bg=CARD), tk.Frame(tabs, bg=CARD)
        tabs.add(chart_tab, text="Chart")
        tabs.add(table_tab, text="Table")
        fig, canvas = chart(chart_tab, height)
        return fig, canvas, table(table_tab, height=14)

    # ------------------------------------------------------------------
    # Case handling
    # ------------------------------------------------------------------
    def load_case(self):
        """Build the case from self.config_data and refresh the pages."""
        try:
            self.case = build_case(self.config_data)
            self.seg = make_segments(self.case)
        except Exception as error:
            messagebox.showerror("Input error", str(error))
            return False
        self.fill_inputs()
        self.case_text.delete("1.0", "end")
        self.case_text.insert("1.0", yaml.safe_dump(self.config_data, sort_keys=False))
        self.case_label.config(text=f"{self.case.name}\n{len(self.seg)} segments")
        self.calculate_steady()
        return True

    def refresh_all(self):
        """Redraw everything after the pressure unit changed."""
        self.calculate_steady()
        if self.sim:
            self.show_dynamic()
        if self.pid_runs:
            self.show_pid()

    def save_csv(self, df):
        if df is None:
            messagebox.showinfo("Nothing to save", "Run a calculation first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv")
        if path:
            df.to_csv(path, index=False)

    def progress_update(self, fraction, bar):
        bar["value"] = fraction * 100
        self.update()

    # ------------------------------------------------------------------
    # Page 1: steady state
    # ------------------------------------------------------------------
    def steady_page(self):
        body = self.page_header("steady", "Steady-state hydraulics",
                                "Stage A  ·  Bingham-plastic friction in every segment, "
                                "hydrostatic pressure from TVD, BHP / SPP / ECD")
        left = tk.Frame(body, bg=PAGE, width=300)
        left.pack(side="left", fill="y", padx=(0, 16))
        form = card(left, "Operating point", fill="x", expand=False, pady=(0, 6))
        self.inputs = {}
        for i, (key, label) in enumerate([("pump", "Pump rate [gpm]"), ("mw", "Mud weight [ppg]"),
                                          ("pv", "Plastic viscosity [cP]"),
                                          ("yp", "Yield point [lbf/100ft²]"),
                                          ("sbp", "Back pressure [psi]"),
                                          ("opening", "Choke opening [0-1]")]):
            self.inputs[key] = labeled_entry(form, i, label)
            self.inputs[key].bind("<Return>", lambda e: self.apply_inputs())
        self.bp_mode = tk.StringVar(value="choke")
        self.auto_choke = tk.BooleanVar(value=True)
        tk.Label(form, text="Back pressure from", bg=CARD, fg=MUTED, font=(FONT, 12)).grid(
            row=6, column=0, sticky="w", pady=(10, 2))
        modes = tk.Frame(form, bg=CARD)
        modes.grid(row=7, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(modes, text="Choke", value="choke", variable=self.bp_mode).pack(side="left")
        ttk.Radiobutton(modes, text="Fixed value", value="fixed", variable=self.bp_mode).pack(side="left", padx=12)
        ttk.Checkbutton(form, text="Auto choke (reach target BHP)", variable=self.auto_choke).grid(
            row=8, column=0, columnspan=2, sticky="w", pady=8)
        button(form, "Calculate", self.apply_inputs).grid(row=9, column=0, columnspan=2,
                                                           sticky="ew", pady=(8, 0))
        info = card(left, "Pressure accounting", fill="both", expand=True)
        self.accounting = tk.Label(info, text="", bg=CARD, fg=TEXT, font=("Menlo", 11),
                                   justify="left", anchor="nw")
        self.accounting.pack(fill="both", expand=True)

        right = tk.Frame(body, bg=PAGE)
        right.pack(side="left", fill="both", expand=True)
        self.steady_pill = tk.Label(right, text="", font=(FONT, 12, "bold"), padx=12, pady=5)
        self.steady_pill.pack(anchor="w", pady=(0, 8))
        tiles = tk.Frame(right, bg=PAGE)
        tiles.pack(fill="x")
        self.kpi = {k: kpi(tiles, t) for k, t in (
            ("bhp", "Bottomhole pressure"), ("spp", "Standpipe pressure"),
            ("sbp", "Surface back pressure"), ("ecd", "ECD"))}
        gauge = card(right, None, fill="x", expand=False, pady=(10, 4))
        self.gauge_fig = Figure(figsize=(9, 1.5), layout="constrained")
        self.gauge_canvas = FigureCanvasTkAgg(self.gauge_fig, master=gauge)
        self.gauge_canvas.get_tk_widget().pack(fill="x")
        charts = card(right, None, pady=(6, 0))
        tabs = ttk.Notebook(charts)
        tabs.pack(fill="both", expand=True)
        frames = {}
        for name in ("Pressure profile", "SPP breakdown", "Well trajectory", "Segment table"):
            frames[name] = tk.Frame(tabs, bg=CARD)
            tabs.add(frames[name], text=name)
        self.profile_fig, self.profile_canvas = chart(frames["Pressure profile"])
        self.spp_fig, self.spp_canvas = chart(frames["SPP breakdown"])
        self.traj_fig, self.traj_canvas = chart(frames["Well trajectory"])
        self.segment_tree = table(frames["Segment table"], height=14)
        button(frames["Segment table"], "Export CSV",
               lambda: self.save_csv(self.steady["segments"]), primary=False).pack(anchor="w", pady=4)

    def fill_inputs(self):
        """Put the case values into the input boxes."""
        cfg, op = self.config_data, self.config_data["operation"]
        values = {"pump": op["pump_rate_gpm"], "mw": cfg["mud"]["density_ppg"],
                  "pv": cfg["mud"]["pv_cp"], "yp": cfg["mud"]["yp_lbf100ft2"],
                  "sbp": op["back_pressure_psi"],
                  "opening": op["choke_opening"] if op["choke_opening"] != "auto" else 0.5}
        for key, value in values.items():
            self.inputs[key].delete(0, "end")
            self.inputs[key].insert(0, str(value))
        self.bp_mode.set(op["back_pressure_from"])
        self.auto_choke.set(op["choke_opening"] == "auto")
        for key, entry in self.gains.items():
            entry.delete(0, "end")
            entry.insert(0, str(cfg["pid"][key]))

    def apply_inputs(self):
        """Copy the input boxes into the case and recalculate."""
        try:
            v = {k: float(e.get()) for k, e in self.inputs.items()}
        except ValueError:
            messagebox.showerror("Input error", "Please enter numbers only.")
            return
        op, mud = self.config_data["operation"], self.config_data["mud"]
        op["pump_rate_gpm"], op["back_pressure_psi"] = v["pump"], v["sbp"]
        op["back_pressure_from"] = self.bp_mode.get()
        op["choke_opening"] = "auto" if self.auto_choke.get() else v["opening"]
        mud["density_ppg"], mud["pv_cp"], mud["yp_lbf100ft2"] = v["mw"], v["pv"], v["yp"]
        self.load_case()

    def calculate_steady(self):
        r = solve_steady(self.case, self.seg)
        self.steady = r
        unit, p = self.unit.get(), plots.p_conv(self.unit.get())
        target = p(self.case.target)
        self.kpi["bhp"][0].config(text=f"{p(r['bhp']):,.1f} {unit}")
        self.kpi["bhp"][1].config(text=f"target {target:,.1f} {unit}")
        self.kpi["spp"][0].config(text=f"{p(r['spp']):,.1f} {unit}")
        self.kpi["spp"][1].config(text=f"total friction {p(r['dp_total']):,.1f} {unit}")
        self.kpi["sbp"][0].config(text=f"{p(r['sbp']):,.1f} {unit}")
        self.kpi["sbp"][1].config(text=f"choke {r['opening']:.0%} open" if r["opening"] is not None
                                  else "fixed value")
        self.kpi["ecd"][0].config(text=f"{u.ppg(r['ecd']):.3f} ppg")
        self.kpi["ecd"][1].config(text=f"BHP as EMW {u.ppg(r['emw']):.2f} ppg")
        set_pill(self.steady_pill, r["status"])
        if r["opening"] is not None and self.auto_choke.get():
            self.inputs["opening"].delete(0, "end")
            self.inputs["opening"].insert(0, f"{r['opening']:.3f}")
        lines = [("Hydrostatic (ρ g TVD)", r["hydrostatic"]), ("+ Annulus friction", r["dp_annulus"]),
                 ("+ Back pressure", r["sbp"]), ("= BHP", r["bhp"]), ("", None),
                 ("Drill pipe friction", r["dp_drill_pipe"]), ("BHA friction", r["dp_bha"]),
                 ("Bit nozzles", r["dp_bit"]), ("Annulus friction", r["dp_annulus"]),
                 ("Back pressure", r["sbp"]), ("= SPP", r["spp"])]
        self.accounting.config(text="\n".join(
            f"{name:<22}{p(value):>9,.1f}" if value is not None else "" for name, value in lines)
            + f"\n\n[{unit}]")
        redraw(self.gauge_fig, self.gauge_canvas, plots.window_gauge, r, self.case, unit)
        redraw(self.profile_fig, self.profile_canvas, plots.pressure_profile, r, self.case, unit)
        redraw(self.spp_fig, self.spp_canvas, plots.spp_breakdown, r, unit)
        redraw(self.traj_fig, self.traj_canvas, plots.trajectory, self.case)
        seg = r["segments"].rename(columns={"md_top": "MD top", "md_bottom": "MD bottom",
                                            "tvd_bottom": "TVD", "inclination": "Inc",
                                            "string_regime": "Pipe flow", "string_dp": "Pipe dP [Pa]",
                                            "annulus_regime": "Annulus flow",
                                            "annulus_dp": "Annulus dP [Pa]", "pipe": "Pipe"})
        fill_table(self.segment_tree, seg[["MD top", "MD bottom", "TVD", "Inc", "Pipe", "Pipe flow",
                                           "Pipe dP [Pa]", "Annulus flow", "Annulus dP [Pa]"]], 1)

    # ------------------------------------------------------------------
    # Page 2: dynamic scenarios
    # ------------------------------------------------------------------
    def dynamic_page(self):
        body = self.page_header("dynamic", "Dynamic scenarios",
                                "Stage B  ·  pump changes, choke moves, back pressure and pipe "
                                "movement over time")
        top = card(body, None, fill="x", expand=False, pady=(0, 6))
        self.scenario_names = list(SCENARIOS)
        self.scenario_box = ttk.Combobox(top, state="readonly", width=36, font=(FONT, 12),
                                         values=[SCENARIOS[n]["title"] for n in self.scenario_names])
        self.scenario_box.current(0)
        self.scenario_box.grid(row=0, column=0, padx=(0, 16))
        self.scenario_box.bind("<<ComboboxSelected>>", lambda e: self.describe_scenario())
        self.dyn_model = tk.StringVar(value="lumped")
        ttk.Radiobutton(top, text="Lumped model (compressible mud)", value="lumped",
                        variable=self.dyn_model).grid(row=0, column=1)
        ttk.Radiobutton(top, text="Quasi-steady", value="quasi_steady",
                        variable=self.dyn_model).grid(row=0, column=2, padx=12)
        button(top, "▶  Run simulation", self.run_dynamic).grid(row=0, column=3, padx=12)
        self.dyn_progress = ttk.Progressbar(top, length=180)
        self.dyn_progress.grid(row=0, column=4)
        self.scenario_text = tk.Label(top, text="", bg=CARD, fg=MUTED, font=(FONT, 12))
        self.scenario_text.grid(row=1, column=0, columnspan=5, sticky="w", pady=(8, 0))
        self.describe_scenario()

        tiles = tk.Frame(body, bg=PAGE)
        tiles.pack(fill="x", pady=6)
        self.dyn_kpi = {k: kpi(tiles, t) for k, t in (
            ("range", "BHP range"), ("outside", "Time outside window"),
            ("dev", "Largest deviation"), ("result", "Result"))}
        charts = card(body, None, pady=(6, 0))
        tabs = ttk.Notebook(charts)
        tabs.pack(fill="both", expand=True)
        frames = {n: tk.Frame(tabs, bg=CARD) for n in ("Overview", "ECD", "Depth profiles", "Metrics")}
        for name, frame in frames.items():
            tabs.add(frame, text=name)
        self.dyn_fig, self.dyn_canvas = chart(frames["Overview"], 8)
        self.ecd_fig, self.ecd_canvas = chart(frames["ECD"])
        self.prof_fig, self.prof_canvas = chart(frames["Depth profiles"])
        self.dyn_metrics = table(frames["Metrics"], height=8)
        self.dyn_events = table(frames["Metrics"], height=5)
        button(frames["Metrics"], "Export time series CSV",
               lambda: self.save_csv(self.sim["timeseries"] if self.sim else None),
               primary=False).pack(anchor="w", pady=4)

    def describe_scenario(self):
        name = self.scenario_names[self.scenario_box.current()]
        self.scenario_text.config(text=SCENARIOS[name].get("description", ""))

    def run_dynamic(self):
        name = self.scenario_names[self.scenario_box.current()]
        scenario = dict(SCENARIOS[name], model=self.dyn_model.get())
        self.config(cursor="watch")
        self.sim = simulate(self.case, self.seg, scenario,
                            lambda f: self.progress_update(f, self.dyn_progress))
        self.config(cursor="")
        self.dyn_progress["value"] = 0
        self.show_dynamic()

    def show_dynamic(self):
        sim, unit = self.sim, self.unit.get()
        p, m = plots.p_conv(unit), sim["metrics"]
        self.dyn_kpi["range"][0].config(text=f"{p(m['min_bhp']):,.0f} – {p(m['max_bhp']):,.0f}")
        self.dyn_kpi["range"][1].config(text=unit)
        self.dyn_kpi["outside"][0].config(text=f"{m['time_outside_window_s']:.1f} s")
        self.dyn_kpi["dev"][0].config(text=f"{p(max(m['max_above_target'], m['max_below_target'])):,.1f} {unit}")
        self.dyn_kpi["dev"][1].config(text="from the target BHP")
        ok = m["window_held"]
        self.dyn_kpi["result"][0].config(text="Window held" if ok else "Window violated",
                                         fg=GREEN if ok else RED)
        self.dyn_kpi["result"][1].config(text=sim["title"])
        redraw(self.dyn_fig, self.dyn_canvas, plots.dynamic_overview, sim, unit)
        redraw(self.ecd_fig, self.ecd_canvas, plots.ecd_chart, sim, self.case)
        redraw(self.prof_fig, self.prof_canvas, plots.profiles_over_time, sim, unit)
        fill_table(self.dyn_metrics, metrics_table(m, unit))
        fill_table(self.dyn_events, event_table(m, unit), 1)

    # ------------------------------------------------------------------
    # Page 3: PID control
    # ------------------------------------------------------------------
    def pid_page(self):
        body = self.page_header("pid", "PID control of bottomhole pressure",
                                "Target BHP  →  error  →  PID  →  choke  →  well  →  BHP  →  feedback")
        top = card(body, None, fill="x", expand=False, pady=(0, 6))
        self.gains = {}
        for i, (key, label) in enumerate((("kp", "Kp"), ("ki", "Ki"), ("kd", "Kd"))):
            tk.Label(top, text=label, bg=CARD, fg=MUTED, font=(FONT, 12, "bold")).grid(row=0, column=2 * i)
            entry = ttk.Entry(top, width=8, font=(FONT, 12))
            entry.grid(row=0, column=2 * i + 1, padx=(6, 18))
            self.gains[key] = entry
        tk.Label(top, text="(choke opening per bar of error)", bg=CARD, fg=MUTED,
                 font=(FONT, 11)).grid(row=0, column=6, padx=(0, 18))
        self.compare = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Compare without PID", variable=self.compare).grid(row=0, column=7)
        button(top, "▶  Run", self.run_pid).grid(row=0, column=8, padx=14)
        self.pid_progress = ttk.Progressbar(top, length=140)
        self.pid_progress.grid(row=0, column=9)
        tk.Label(top, text=SCENARIOS["pid_control"]["description"], bg=CARD, fg=MUTED,
                 font=(FONT, 12)).grid(row=1, column=0, columnspan=10, sticky="w", pady=(8, 0))

        row = tk.Frame(body, bg=PAGE)
        row.pack(fill="both", expand=True)
        results = card(row, "Results", side="right", fill="y", expand=False)
        results.master.config(width=480)          # fixed width for the results panel
        results.master.pack_propagate(False)
        self.pid_table = table(results, height=8)
        tk.Label(results, text="Per event:  1 = pump stop,  2 = pump start,  3 = target step",
                 bg=CARD, fg=MUTED, font=(FONT, 11)).pack(anchor="w", pady=(10, 0))
        self.pid_events = table(results, height=4)
        charts = card(row, None, side="left", padx=(0, 12))
        tabs = ttk.Notebook(charts)
        tabs.pack(fill="both", expand=True)
        f1, f2 = tk.Frame(tabs, bg=CARD), tk.Frame(tabs, bg=CARD)
        tabs.add(f1, text="BHP and choke")
        tabs.add(f2, text="Error and PID terms")
        self.pid_fig, self.pid_canvas = chart(f1, 8)
        self.pid_terms_fig, self.pid_terms_canvas = chart(f2, 6)

    def run_pid(self):
        try:
            for key, entry in self.gains.items():
                self.config_data["pid"][key] = float(entry.get())
        except ValueError:
            messagebox.showerror("Input error", "Gains must be numbers.")
            return
        self.load_case()
        scenario = SCENARIOS["pid_control"]
        show = lambda f: self.progress_update(f, self.pid_progress)
        with_pid = simulate(self.case, self.seg, scenario, show)
        without = simulate(self.case, self.seg, dict(scenario, pid=False)) if self.compare.get() else None
        self.pid_progress["value"] = 0
        self.pid_runs = (with_pid, without)
        self.show_pid()

    def show_pid(self):
        with_pid, without = self.pid_runs
        unit = self.unit.get()
        redraw(self.pid_fig, self.pid_canvas, plots.dynamic_overview, with_pid, unit,
               without["timeseries"] if without else None)
        redraw(self.pid_terms_fig, self.pid_terms_canvas, plots.pid_chart, with_pid, unit)
        result = metrics_table(with_pid["metrics"], unit).rename(columns={"Value": "With PID"})
        if without:
            result["Without PID"] = metrics_table(without["metrics"], unit)["Value"]
        fill_table(self.pid_table, result)
        fill_table(self.pid_events, event_table(with_pid["metrics"], unit), 1)

    # ------------------------------------------------------------------
    # Page 4: sensitivity
    # ------------------------------------------------------------------
    def sensitivity_page(self):
        body = self.page_header("sensitivity", "Sensitivity analysis",
                                "Change one input at a time and see how BHP, SPP and ECD respond")
        top = card(body, None, fill="x", expand=False, pady=(0, 6))
        tk.Label(top, text="Change ± [%]", bg=CARD, fg=MUTED, font=(FONT, 12)).grid(row=0, column=0)
        self.change = ttk.Spinbox(top, from_=1, to=30, width=5, font=(FONT, 12))
        self.change.set(10)
        self.change.grid(row=0, column=1, padx=8)
        button(top, "Tornado chart", self.run_sensitivity).grid(row=0, column=2, padx=(4, 40))
        tk.Label(top, text="Sweep", bg=CARD, fg=MUTED, font=(FONT, 12)).grid(row=0, column=3)
        self.sweep_param = ttk.Combobox(top, state="readonly", width=20, font=(FONT, 12),
                                        values=list(analysis.PARAMETERS.values()))
        self.sweep_param.current(0)
        self.sweep_param.grid(row=0, column=4, padx=8)
        tk.Label(top, text="from ×", bg=CARD, fg=MUTED).grid(row=0, column=5)
        self.sweep_from = ttk.Entry(top, width=5, font=(FONT, 12))
        self.sweep_from.insert(0, "0.6")
        self.sweep_from.grid(row=0, column=6, padx=4)
        tk.Label(top, text="to ×", bg=CARD, fg=MUTED).grid(row=0, column=7)
        self.sweep_to = ttk.Entry(top, width=5, font=(FONT, 12))
        self.sweep_to.insert(0, "1.4")
        self.sweep_to.grid(row=0, column=8, padx=4)
        button(top, "Run sweep", self.run_sweep).grid(row=0, column=9, padx=8)
        self.sens_fig, self.sens_canvas, self.sens_table = self.chart_and_table(body, 4.6)

    def run_sensitivity(self):
        self.config(cursor="watch")
        self.update()
        data = analysis.sensitivity(self.config_data, float(self.change.get()) / 100)
        self.config(cursor="")
        unit = self.unit.get()
        redraw(self.sens_fig, self.sens_canvas, plots.tornado, data, unit)
        p = plots.p_conv(unit)
        shown = pd.DataFrame({
            "Parameter": data["parameter"],
            f"BHP − [{unit}]": p(data["bhp_low"]), f"BHP + [{unit}]": p(data["bhp_high"]),
            f"SPP − [{unit}]": p(data["spp_low"]), f"SPP + [{unit}]": p(data["spp_high"]),
            "ECD − [ppg]": u.ppg(data["ecd_low"]), "ECD + [ppg]": u.ppg(data["ecd_high"])})
        fill_table(self.sens_table, shown, 2)

    def run_sweep(self):
        name = list(analysis.PARAMETERS)[self.sweep_param.current()]
        factors = np.linspace(float(self.sweep_from.get()), float(self.sweep_to.get()), 11)
        data = analysis.sweep(self.config_data, name, factors)
        data["x"] = data["factor"]
        unit = self.unit.get()
        redraw(self.sens_fig, self.sens_canvas, plots.sweep_chart, data,
               f"{analysis.PARAMETERS[name]} (× base value)", unit)
        p = plots.p_conv(unit)
        fill_table(self.sens_table, pd.DataFrame({
            "Factor": data["factor"], f"BHP [{unit}]": p(data["bhp"]),
            f"SPP [{unit}]": p(data["spp"]), "ECD [ppg]": u.ppg(data["ecd"])}), 2)

    # ------------------------------------------------------------------
    # Page 5: validation
    # ------------------------------------------------------------------
    def validation_page(self):
        body = self.page_header("validation", "Validation",
                                "Error (%) = |model − reference| / |reference| × 100")
        top = card(body, None, fill="x", expand=False, pady=(0, 6))
        button(top, "Compare with bundled reference",
               lambda: self.run_validation(ROOT / "data" / "validation_reference.csv")).pack(side="left")
        button(top, "Load my reference CSV…", self.choose_reference, primary=False).pack(side="left", padx=10)
        self.val_note = tk.Label(body, text="", bg="#fef3c7", fg="#92400e", font=(FONT, 12),
                                 wraplength=1000, justify="left", padx=12, pady=8)
        self.val_fig, self.val_canvas, self.val_table = self.chart_and_table(body, 4.4)

    def choose_reference(self):
        path = filedialog.askopenfilename(filetypes=[("CSV", "*.csv")])
        if path:
            self.run_validation(path)

    def run_validation(self, reference_path):
        config = load_config(ROOT / "config" / "validation_case.yaml")
        case = build_case(config)
        result = solve_steady(case, make_segments(case))
        data = analysis.validate(result, pd.read_csv(reference_path))
        if (data["data_type"] == "synthetic").any():
            self.val_note.config(text="⚠  Synthetic hand-calculation data - demonstration only, not "
                                      "experimental validation. The 9 % annulus difference comes from "
                                      "the exact vs. simplified laminar Bingham formula.")
        else:
            self.val_note.config(text=f"Reference data: {', '.join(data['data_type'].unique())}",
                                 bg="#d1fae5", fg="#065f46")
        self.val_note.pack(fill="x", before=self.val_box)
        redraw(self.val_fig, self.val_canvas, plots.validation_chart, data)
        fill_table(self.val_table, data[["quantity", "unit", "reference", "model",
                                         "absolute_error", "error_percent", "data_type"]], 2)

    # ------------------------------------------------------------------
    # Page 6: case file
    # ------------------------------------------------------------------
    def case_page(self):
        body = self.page_header("case", "Case file",
                                "All inputs of the simulation. The units are written in the names "
                                "(_m, _in, _ppg, _gpm, _psi). Edit the text and press Apply.")
        top = tk.Frame(body, bg=PAGE)
        top.pack(fill="x")
        button(top, "Apply changes", self.apply_case_text).pack(side="right")
        button(top, "Save as…", self.save_case, primary=False).pack(side="right", padx=8)
        button(top, "Open…", self.open_case, primary=False).pack(side="right")
        editor = card(body, None)
        self.case_text = tk.Text(editor, font=("Menlo", 12), undo=True, wrap="none", bg=CARD,
                                 fg=TEXT, relief="flat", insertbackground=TEXT,
                                 highlightthickness=0)
        self.case_text.pack(fill="both", expand=True)

    def apply_case_text(self):
        try:
            config = yaml.safe_load(self.case_text.get("1.0", "end"))
        except yaml.YAMLError as error:
            messagebox.showerror("YAML error", str(error))
            return
        old = self.config_data
        self.config_data = config
        if not self.load_case():
            self.config_data = old
        else:
            messagebox.showinfo("Case updated", "The new inputs are used on all pages.")

    def open_case(self):
        path = filedialog.askopenfilename(initialdir=ROOT / "config", filetypes=[("YAML", "*.yaml")])
        if path:
            self.config_data = load_config(path)
            self.load_case()

    def save_case(self):
        path = filedialog.asksaveasfilename(initialdir=ROOT / "config", defaultextension=".yaml")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(self.config_data, f, sort_keys=False)


if __name__ == "__main__":
    App().mainloop()
