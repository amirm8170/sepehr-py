"""
Auto-fix: put the target and the result (BHP) in the right place, and explain.

"Right place" means:
    pore + margin  <=  BHP  <=  fracture - margin       (safe window)
    BHP = target                                        (when the choke is on "auto")
margin = warning_margin_psi of the case file.

auto_fix() checks the case, changes ONE thing at a time, solves again and
repeats. Every change is written to a report with four parts:
    problem - what is wrong            why  - the physical reason
    done    - what the program changed how  - how to fix it on the rig

Order of the fixes (smallest change first):
    1. target outside the safe window          -> move the target inside
    2. BHP too low / too high with a fixed SBP -> set the SBP that gives the target
    3. choke opening set by hand               -> automatic choke opening
    4. BHP too low, choke cannot build SBP     -> start the back-pressure pump
    5. BHP too high, back-pressure pump on     -> stop the back-pressure pump
    6. BHP too high, mud column too heavy      -> lighter mud
    7. BHP too high because of friction        -> lower pump rate
    8. BHP safe but not equal to the target    -> target = reachable BHP (+ advice)
"""

import copy
import math

import numpy as np

from . import units as u
from .hydraulics import choke_flow, hydrostatic, solve_steady
from .well import build_case, make_segments, read_survey

TOLERANCE = 0.05 * u.BAR          # BHP closer than this to the target counts as "on target"
CHOKE_WORKING_OPENING = 0.1       # choke opening used to size the back-pressure pump


def auto_fix(config):
    """Return (corrected copy of the case file, report = list of dicts)."""
    cfg = copy.deepcopy(config)
    win = cfg["pressure_window"]
    if float(win["pore_ppg"]) >= float(win["fracture_ppg"]):
        return cfg, []                          # impossible window: build_case() reports it
    report = []
    fix_window(cfg, report)
    for _ in range(6):
        case, result = solve(cfg)
        low, high = safe_limits(case)
        bhp = result["bhp"]
        if low <= bhp <= high:
            on_target(cfg, case, result, report)
            break
        item = fix_too_low(cfg, case, result) if bhp < low else fix_too_high(cfg, case, result)
        item["problem"] = (f"{'KICK' if bhp < low else 'LOSS'} RISK - BHP {show(bhp, case)} is "
                           f"{'below pore pressure + margin' if bhp < low else 'above fracture pressure - margin'} "
                           f"({show(low if bhp < low else high, case)}).")
        report.append(item)
    return cfg, report


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def solve(cfg, **overrides):
    """Build and solve a case file. Returns (case, steady-state result)."""
    case = build_case(cfg)
    return case, solve_steady(case, make_segments(case), **overrides)


def safe_limits(case):
    """Lowest and highest safe BHP [Pa]: window minus the warning margin."""
    low, high = case.pore + case.margin, case.frac - case.margin
    return (low, high) if low <= high else ((case.pore + case.frac) / 2,) * 2


def show(p, case):
    """Pressure as text in bar and ppg EMW."""
    return f"{u.bar(p):.1f} bar ({u.pressure_to_emw(p, case.td_tvd):.2f} ppg)"


def lowest_bhp(cfg):
    """BHP with the smallest possible surface pressure (choke fully open, or SBP = 0)."""
    c = copy.deepcopy(cfg)
    if c["operation"]["back_pressure_from"] == "fixed":
        c["operation"]["back_pressure_psi"] = 0.0
    else:
        c["operation"]["choke_opening"] = c["choke"]["max_opening"]
    return solve(c)[1]["bhp"]


def bisect(f, lo, hi, n=40):
    """x between lo and hi where the increasing function f(x) = 0."""
    for _ in range(n):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) < 0 else (lo, mid)
    return lo


def with_value(cfg, section, key, value):
    c = copy.deepcopy(cfg)
    c[section][key] = value
    return c


def needed_sbp(case, result):
    """Surface back pressure that gives BHP = target [Pa]."""
    return case.target - hydrostatic(case.rho, case.td_tvd) - result["dp_annulus"]


def bp_pump_for(cfg, case, result):
    """Back-pressure pump rate [gpm] so the choke holds the needed SBP at ~10 % open."""
    q_choke = choke_flow(needed_sbp(case, result), CHOKE_WORKING_OPENING, case.rho, case.choke)
    return max(5 * math.ceil(u.gpm(q_choke - case.pump_rate) / 5),
               cfg["operation"]["bp_pump_gpm"] + 10)


def lighter_mud(cfg):
    """Mud weight [ppg] (rounded down) that gives BHP = target with the smallest SBP."""
    target = build_case(cfg).target
    mw = cfg["mud"]["density_ppg"]
    best = bisect(lambda x: lowest_bhp(with_value(cfg, "mud", "density_ppg", x)) - target, 0.5 * mw, mw)
    return math.floor(best * 100) / 100


def lower_pump_rate(cfg):
    """Pump rate [gpm] (rounded down) that gives BHP = target with the smallest SBP."""
    target = build_case(cfg).target
    q = cfg["operation"]["pump_rate_gpm"]
    best = bisect(lambda x: lowest_bhp(with_value(cfg, "operation", "pump_rate_gpm", x)) - target, 0.0, q)
    return float(math.floor(best))


# ----------------------------------------------------------------------
# the fixes
# ----------------------------------------------------------------------
def fix_window(cfg, report):
    """1. The target must lie inside the safe window."""
    win = cfg["pressure_window"]
    survey = read_survey(cfg["well"])
    tvd = float(np.interp(cfg["well"]["hole_sections"][-1]["bottom_m"], survey["md"], survey["tvd"]))
    margin = u.pressure_to_emw(float(win.get("warning_margin_psi", 0)) * u.PSI, tvd)
    pore, frac = float(win["pore_ppg"]), float(win["fracture_ppg"])
    low, high = pore + margin, frac - margin
    if low > high:
        low = high = (pore + frac) / 2
    target = float(win["target_ppg"])
    if low <= target <= high:
        return
    new = round(min(max(target, low), high), 3)
    win["target_ppg"] = new
    report.append({
        "problem": f"Target {target:.2f} ppg is outside the safe window {low:.2f} - {high:.2f} ppg.",
        "why": f"Pore pressure is {pore:.2f} ppg and fracture pressure {frac:.2f} ppg; the target must "
               f"stay {margin:.2f} ppg (the warning margin) away from both.",
        "done": f"Target moved to {new:.2f} ppg.",
        "how": f"Choose a target between {low:.2f} and {high:.2f} ppg (the middle, "
               f"{(low + high) / 2:.2f} ppg, gives the most room on both sides)."})


def fix_too_low(cfg, case, result):
    """BHP below the safe window (kick risk)."""
    op = cfg["operation"]
    sbp = needed_sbp(case, result)
    if op["back_pressure_from"] == "fixed":
        op["back_pressure_psi"] = float(math.ceil(u.psi(sbp)))
        return {"why": f"The fixed back pressure ({u.bar(result['sbp']):.1f} bar) is too small to "
                       f"lift BHP to the target.",
                "done": f"Back pressure set to {op['back_pressure_psi']:.0f} psi (gives the target BHP).",
                "how": f"Raise the surface back-pressure set-point to {op['back_pressure_psi']:.0f} psi."}
    if str(op["choke_opening"]) != "auto":
        old = op["choke_opening"]
        op["choke_opening"] = "auto"
        return {"why": f"The choke is set by hand to {float(old):.0%} open and holds too little back pressure.",
                "done": "Choke opening set to automatic (the opening that gives the target BHP).",
                "how": "Close the choke until BHP reaches the target (in MPD the PID controller does this)."}
    old = op["bp_pump_gpm"]
    op["bp_pump_gpm"] = float(bp_pump_for(cfg, case, result))
    return {"why": f"Only {u.gpm(case.pump_rate + case.bp_pump_rate):.0f} gpm flows through the choke; even "
                   f"almost closed it builds {u.bar(result['sbp']):.1f} bar, but {u.bar(sbp):.1f} bar is "
                   f"needed. Without flow a choke cannot hold pressure.",
            "done": f"Back-pressure pump raised from {old:.0f} to {op['bp_pump_gpm']:.0f} gpm "
                    f"(choke works near {CHOKE_WORKING_OPENING:.0%} open).",
            "how": f"Start the MPD back-pressure pump at {op['bp_pump_gpm']:.0f} gpm (always during "
                   f"connections, when the rig pumps are off)."}


def fix_too_high(cfg, case, result):
    """BHP above the safe window (loss risk)."""
    op, mud = cfg["operation"], cfg["mud"]
    sbp = needed_sbp(case, result)
    if op["back_pressure_from"] == "fixed" and op["back_pressure_psi"] > 0:
        old = op["back_pressure_psi"]
        op["back_pressure_psi"] = float(max(math.floor(u.psi(sbp)), 0))
        return {"why": f"The fixed back pressure ({old:.0f} psi) pushes BHP too high.",
                "done": f"Back pressure lowered to {op['back_pressure_psi']:.0f} psi.",
                "how": f"Lower the surface back-pressure set-point to {op['back_pressure_psi']:.0f} psi."}
    if op["back_pressure_from"] == "choke" and str(op["choke_opening"]) != "auto":
        old = op["choke_opening"]
        op["choke_opening"] = "auto"
        return {"why": f"The choke is set by hand to {float(old):.0%} open and holds too much back pressure.",
                "done": "Choke opening set to automatic (the opening that gives the target BHP).",
                "how": "Open the choke until BHP falls to the target (in MPD the PID controller does this)."}
    if op["back_pressure_from"] == "choke" and op["bp_pump_gpm"] > 0:
        old = op["bp_pump_gpm"]
        op["bp_pump_gpm"] = 0.0
        return {"why": f"The back-pressure pump ({old:.0f} gpm) adds flow through the choke and so "
                       f"adds back pressure that is not needed.",
                "done": "Back-pressure pump stopped.",
                "how": "Stop the MPD back-pressure pump while the rig pumps circulate."}
    static = hydrostatic(case.rho, case.td_tvd)
    if static >= case.target:
        old = mud["density_ppg"]
        mud["density_ppg"] = lighter_mud(cfg)
        return {"why": f"The mud column alone gives {show(static, case)} - already at or above the target "
                       f"{show(case.target, case)}. Back pressure can only ADD pressure, so the mud is too heavy.",
                "done": f"Mud weight lowered from {old:.2f} to {mud['density_ppg']:.2f} ppg.",
                "how": f"Cut the mud weight to {mud['density_ppg']:.2f} ppg (dilute with base fluid / remove "
                       f"weighting material) and circulate it round."}
    old = op["pump_rate_gpm"]
    op["pump_rate_gpm"] = lower_pump_rate(cfg)
    return {"why": f"Annular friction ({u.bar(result['dp_annulus']):.1f} bar, ECD "
                   f"{u.ppg(result['ecd']):.2f} ppg) is too large: mud column + friction is above the "
                   f"target even with the choke fully open.",
            "done": f"Pump rate lowered from {old:.0f} to {op['pump_rate_gpm']:.0f} gpm.",
            "how": f"Reduce the pump rate to {op['pump_rate_gpm']:.0f} gpm (check it still cleans the hole), "
                   f"or thin the mud (lower PV / YP) to cut the friction."}


def on_target(cfg, case, result, report):
    """8. BHP is safe; with the auto choke it should also equal the target."""
    op = cfg["operation"]
    if op["back_pressure_from"] != "choke" or str(op["choke_opening"]) != "auto":
        return
    gap = result["bhp"] - case.target
    if abs(gap) <= TOLERANCE:
        return
    win = cfg["pressure_window"]
    old = win["target_ppg"]
    win["target_ppg"] = round(float(u.pressure_to_emw(result["bhp"], case.td_tvd)), 3)
    if gap > 0:
        why = ("Even with the choke fully open, mud column + friction give a higher BHP than the target "
               "(a choke can only add pressure).")
        wanted = with_value(cfg, "pressure_window", "target_ppg", old)
        how = f"To reach {old:.2f} ppg: lower the mud weight to {lighter_mud(wanted):.2f} ppg"
        if hydrostatic(case.rho, case.td_tvd) < case.target:
            how += f", or reduce the pump rate to {lower_pump_rate(wanted):.0f} gpm (check hole cleaning)"
        how += "."
    else:
        why = (f"Even almost closed, the choke builds only {u.bar(result['sbp']):.1f} bar with "
               f"{u.gpm(case.pump_rate + case.bp_pump_rate):.0f} gpm flowing through it.")
        old_case = build_case(with_value(cfg, "pressure_window", "target_ppg", old))
        how = (f"To reach {old:.2f} ppg: start / raise the back-pressure pump to "
               f"{bp_pump_for(cfg, old_case, result):.0f} gpm.")
    report.append({
        "problem": f"BHP {show(result['bhp'], case)} is safe but cannot reach the target {old:.2f} ppg.",
        "why": why,
        "done": f"Target set to the reachable BHP, {win['target_ppg']:.2f} ppg (still inside the safe window).",
        "how": how})
