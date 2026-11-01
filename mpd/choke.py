"""
Choke valve and its actuator (proposal 4-2-6).

Choke (orifice equation), p_0 = pressure downstream of the choke:

    q_c   = C_d A_c(z) sqrt(2 dp+ / rho)
    dp    = p_c - p_0
    dp+   = (dp + sqrt(dp^2 + eps^2)) / 2      smooth "positive part" of dp:
                                               ~dp when dp >> eps, ~0 when dp < 0
    A_c(z) = A_max z                           linear (baseline)
    A_c(z) = A_max R^(z - 1)                   equal percentage (sensitivity only)

Actuator: first-order lag with a smooth rate limit, then the opening is
kept between z_min and z_max:

    dz/dt = r_max tanh( ((z_cmd - z) / tau_a) / r_max )
"""

import math

from scipy.optimize import brentq


def choke_area(z, spec):
    """Open area A_c(z) [m2]; the opening is first kept inside [z_min, z_max]."""
    z = min(max(z, spec.z_min), spec.z_max)
    if spec.characteristic == "equal_percentage":
        return spec.a_max * spec.rangeability ** (z - 1.0)
    return spec.a_max * z


def smooth_positive(dp, eps):
    """dp+ = (dp + sqrt(dp^2 + eps^2)) / 2   (differentiable max(dp, 0))"""
    return 0.5 * (dp + math.sqrt(dp * dp + eps * eps))


def choke_flow(p_c, z, rho, spec):
    """Flow through the choke [m3/s] for upstream pressure p_c [Pa] and opening z."""
    dp_plus = smooth_positive(p_c - spec.p0, spec.eps)
    return spec.cd * choke_area(z, spec) * math.sqrt(2.0 * dp_plus / rho)


def choke_pressure(q, z, rho, spec):
    """
    Upstream pressure p_c that pushes flow q through opening z (inverse of choke_flow).

        dp+ = rho/2 (q / (C_d A))^2     and     dp = dp+ - eps^2 / (4 dp+)

    The smooth dp+ never reaches exactly zero flow, so for (almost) no flow
    the result is limited to p_0 - eps (a choke cannot hold a vacuum).
    """
    dp_plus = 0.5 * rho * (max(q, 1e-12) / (spec.cd * choke_area(z, spec))) ** 2
    return max(spec.p0 + dp_plus - spec.eps ** 2 / (4.0 * dp_plus), spec.p0 - spec.eps)


def opening_for(q, p_c, rho, spec):
    """
    Opening z that passes flow q at upstream pressure p_c (brentq, proposal 4-2-4).

    Returns (z, reachable). If no opening in [z_min, z_max] works, the nearest
    limit is returned with reachable = False.
    """
    def excess(z):
        return choke_flow(p_c, z, rho, spec) - q
    if excess(spec.z_max) < 0:          # even fully open the choke passes too little
        return spec.z_max, False
    if excess(spec.z_min) > 0:          # even almost closed it passes too much
        return spec.z_min, False
    return brentq(excess, spec.z_min, spec.z_max, xtol=1e-12), True


def actuator_rate(z_cmd, z, act, spec):
    """
    dz/dt of the choke actuator [1/s].

    Far from the command the speed is r_max; close to it the motion is a
    first-order lag with time constant tau_a. At a limit the choke stops.
    """
    rate = act.max_rate * math.tanh((z_cmd - z) / act.tau / act.max_rate)
    if (z <= spec.z_min and rate < 0) or (z >= spec.z_max and rate > 0):
        return 0.0
    return rate
