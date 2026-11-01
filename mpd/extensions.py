"""
OPTIONAL EXTENSION - NOT PART OF THE PROPOSAL MODEL.

Surge and swab (pipe movement) are listed as OUT OF SCOPE in the proposal
(they need string speed, clearance, eccentricity and open/closed pipe).
This small steady-state estimate is kept from the earlier version of the
program for future work only. It is not used by the scenarios, the
validation or the default results.

Simplified Burkhardt (1961) velocity in the annulus, closed-end pipe:

    v = (Q + v_pipe A_pipe) / A_annulus + K_c v_pipe       (v_pipe > 0 = running in = surge)
"""

import numpy as np

from .hydraulics import annulus_area, friction_gradient, hydraulic_diameter, hydrostatic, pipe_area


def surge_swab_bhp(case, seg, p_c, v_pipe, q=0.0, clinging=0.45):
    """BHP [Pa] at choke pressure p_c with the pipe moving at v_pipe [m/s] (+ down)."""
    a_ann = annulus_area(seg["hole_d"].values, seg["pipe_od"].values)
    a_pipe = pipe_area(seg["pipe_od"].values)
    v = (q + v_pipe * a_pipe) / a_ann + clinging * v_pipe
    grad, _, _ = friction_gradient(case.rho, case.mu_p, case.tau_y, np.abs(v),
                                   hydraulic_diameter(seg["hole_d"].values, seg["pipe_od"].values),
                                   "annulus", case.friction_model, case.transition_width)
    dp_annulus = float((np.sign(v) * grad * seg["length"].values).sum())
    return p_c + float(hydrostatic(case.rho, case.td_tvd)) + dp_annulus
