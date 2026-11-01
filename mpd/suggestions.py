"""
Suggestions: what to change when the case cannot hold the target safely.

Nothing is changed automatically. Each suggestion lists
    problem - what is wrong          why     - the physical reason
    how     - what to do on the rig   changes - {parameter path: new value}
and is applied only when the user accepts it (apply_suggestion).

Checks, in order:
  1. target outside the safe window          -> target = window midpoint
  2. choke fully open but BHP still too high -> lighter mud / lower pump rate / larger choke
  3. choke at z_min but BHP still too low    -> back-pressure pump / heavier mud
"""

import copy

from . import units as u
from .hydraulics import hydrostatic, solve_steady
from .well import build_case, config_errors, make_segments

WORKING_OPENING = 0.5     # suggestions aim at a choke near half open (room to act both ways)
BPP_OPENING = 0.2         # ... but a back-pressure pump is sized for a choke ~20 % open (smaller pump)


def set_path(cfg, path, value):
    """cfg['a']['b'] = value for path 'a.b'."""
    *parents, key = path.split(".")
    place = cfg
    for p in parents:
        place = place[p]
    place[key] = value


def get_path(cfg, path):
    place = cfg
    for p in path.split("."):
        place = place[p]
    return place


def apply_suggestion(config, suggestion):
    """Copy of the case file with the changes of one suggestion."""
    cfg = copy.deepcopy(config)
    for path, value in suggestion["changes"].items():
        set_path(cfg, path, value)
    return cfg


def _z0(cfg):
    case = build_case(cfg)
    return solve_steady(case, make_segments(case)).z


def _bisect(cfg, path, lo, hi, increasing, opening=WORKING_OPENING, n=30):
    """Value of `path` in [lo, hi] that gives a starting choke opening `opening`."""
    for _ in range(n):
        mid = (lo + hi) / 2
        c = copy.deepcopy(cfg)
        set_path(c, path, mid)
        too_open = _z0(c) > opening
        if too_open == increasing:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def suggestions(config):
    """List of suggestions for a case file (empty = nothing to fix)."""
    errors = config_errors(config)
    win = config.get("pressure_window", {})
    if errors:
        target_error = [e for e in errors if e.startswith("Target")]
        if target_error and len(errors) == len(target_error):
            mid = (float(win["pore_ppg"]) + float(win["fracture_ppg"])) / 2
            return [{"problem": target_error[0],
                     "why": "The proposal holds BHP inside p_pore + delta ... p_frac - delta; the middle of the "
                            "window leaves the same room to kick and to loss.",
                     "how": f"Set the target BHP to the window midpoint, {mid:.2f} ppg EMW.",
                     "changes": {"pressure_window.target_ppg": "midpoint"}}]
        return []                                   # other input errors must be fixed by hand

    case = build_case(config)
    seg = make_segments(case)
    r = solve_steady(case, seg)
    if r.target_reachable:
        return []
    out = []
    target = f"{u.bar(case.target):.1f} bar ({u.pressure_to_emw(case.target, case.td_tvd):.2f} ppg)"
    if r.z >= case.choke.z_max:                     # BHP too high even with the choke fully open
        problem = (f"BHP {u.bar(r.bhp):.1f} bar is above the target {target} even with the choke fully open.")
        static = float(hydrostatic(case.rho, case.td_tvd))
        mw = config["mud"]["density_ppg"]
        new_mw = round(_bisect(config, "mud.density_ppg", 0.5 * mw, mw, increasing=True), 2)
        out.append({"problem": problem,
                    "why": (f"Mud column {u.bar(static):.1f} bar + annular friction {u.bar(r.dp_annulus):.1f} bar "
                            f"is already too much; a choke can only ADD back pressure."),
                    "how": f"Cut the mud weight to {new_mw:.2f} ppg (choke then works near "
                           f"{WORKING_OPENING:.0%} open).",
                    "changes": {"mud.density_ppg": new_mw}})
        q = config["operation"]["pump_rate_gpm"]
        new_q = float(round(_bisect(config, "operation.pump_rate_gpm", 0.05 * q, q, increasing=True)))
        if static < case.target and new_q >= 0.5 * q:      # larger cuts would not clean the hole
            out.append({"problem": problem,
                        "why": f"Annular friction ({u.bar(r.dp_annulus):.1f} bar) raises BHP above the target.",
                        "how": f"Reduce the pump rate to {new_q:.0f} gpm (check hole cleaning).",
                        "changes": {"operation.pump_rate_gpm": new_q}})
        area = config["choke"]["max_area_in2"]
        if case.choke.p0 + static + r.dp_annulus < case.target:   # a bigger choke can only help then
            out.append({"problem": problem,
                        "why": "The choke is too small for this flow: fully open it still holds too much pressure.",
                        "how": f"Use a larger choke, A_max = {2 * area:.2f} in2.",
                        "changes": {"choke.max_area_in2": round(2 * area, 4)}})
    else:                                           # BHP too low even with the choke almost closed
        q_choke = u.gpm(case.q_pump + case.q_bpp)
        bpp = config["operation"]["bpp_rate_gpm"]
        new_bpp = float(round(_bisect(config, "operation.bpp_rate_gpm", bpp, bpp + 2000, increasing=True,
                                       opening=BPP_OPENING)))
        out.append({"problem": f"BHP {u.bar(r.bhp):.1f} bar is below the target {target} even with the choke "
                               f"at its smallest opening.",
                    "why": f"Only {q_choke:.0f} gpm flows through the choke; without enough flow a choke "
                           f"cannot build back pressure.",
                    "how": f"Run the back-pressure pump at {new_bpp:.0f} gpm (choke then near "
                           f"{BPP_OPENING:.0%} open).",
                    "changes": {"operation.bpp_rate_gpm": new_bpp}})
    return out
