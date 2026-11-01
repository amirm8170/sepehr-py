"""
Reverse-acting PID controller for the choke (proposal 4-2-7).

    e(t)    = p_target - BHP_m(t)            BHP too low -> e > 0
    u       = Kp e + Ki integral(e) + Kd de/dt
    z_cmd   = z0 - u                         e > 0 -> u > 0 -> choke CLOSES -> BHP rises

Discrete form, executed every T_s seconds:
  * derivative of the error through a first-order filter, T_f = (Kd/Kp) / N
        D_k = T_f/(T_f+T_s) D_(k-1) + Kd/(T_f+T_s) (e_k - e_(k-1))
  * output saturation:   z_sat = min(max(z_cmd, z_min), z_max)
  * back-calculation anti-windup (Astrom & Hagglund): the integral is
    corrected with the saturation error,
        I_k+1 = I_k + T_s (Ki e_k + (u_sat - u_unsat) / T_t)
    T_t = Ti = Kp/Ki (PI) or sqrt(Ti Td) (PID) unless set in the case file.
"""

import math
from dataclasses import replace


class PID:
    """Discrete reverse-acting PID; update() returns the saturated choke command."""

    def __init__(self, gains, z0, z_min, z_max, sample_time):
        self.kp, self.ki, self.kd = gains.kp, gains.ki, gains.kd
        self.z0, self.z_min, self.z_max, self.ts = z0, z_min, z_max, sample_time
        td = self.kd / self.kp if self.kp > 0 else 0.0
        self.t_filter = td / gains.n_filter if td > 0 else 0.0
        if not math.isnan(gains.tracking_time):
            self.t_track = gains.tracking_time
        elif self.ki > 0:
            ti = self.kp / self.ki
            self.t_track = math.sqrt(ti * td) if td > 0 else max(ti, sample_time)
        else:
            self.t_track = math.inf                    # no integral -> nothing to wind up
        self.integral = 0.0                            # I-term (already multiplied by Ki)
        self.d_term = 0.0
        self.last_error = None
        self.terms = {"p": 0.0, "i": 0.0, "d": 0.0, "u_unsat": 0.0, "z_unsat": z0, "z_cmd": z0}

    def update(self, error):
        """One controller step with the error e = target - measured BHP [Pa]."""
        p_term = self.kp * error
        if self.last_error is not None and self.kd > 0:
            a = self.t_filter / (self.t_filter + self.ts)
            self.d_term = a * self.d_term + self.kd / (self.t_filter + self.ts) * (error - self.last_error)
        self.last_error = error

        u_unsat = p_term + self.integral + self.d_term
        z_unsat = self.z0 - u_unsat
        z_cmd = min(max(z_unsat, self.z_min), self.z_max)
        u_sat = self.z0 - z_cmd

        self.terms = {"p": p_term, "i": self.integral, "d": self.d_term,
                      "u_unsat": u_unsat, "z_unsat": z_unsat, "z_cmd": z_cmd}
        # integral for the next step, with back-calculation anti-windup
        self.integral += self.ts * (self.ki * error + (u_sat - u_unsat) / self.t_track)
        return z_cmd


def controller_gains(gains, kind):
    """Gains for 'P', 'PI' or 'PID' from the tuned PID gains (proposal: tuned in that order)."""
    if kind == "P":
        return replace(gains, ki=0.0, kd=0.0)
    if kind == "PI":
        return replace(gains, kd=0.0)
    return gains
