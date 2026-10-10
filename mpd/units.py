"""
Unit conversions.

Rule of this project: ALL calculations are done in SI units
(m, Pa, kg/m3, m3/s, Pa.s). Oilfield units are only used when reading
the case file and when showing results.
"""

G = 9.80665                 # gravity [m/s2]

FT = 0.3048                 # 1 ft            = 0.3048 m
INCH = 0.0254               # 1 in            = 0.0254 m
PSI = 6894.757              # 1 psi           = 6894.757 Pa
BAR = 1.0e5                 # 1 bar           = 100 000 Pa
GPM = 6.309020e-5           # 1 US gal/min    = 6.309e-5 m3/s
PPG = 119.8264              # 1 lb/gal        = 119.83 kg/m3
CP = 1.0e-3                 # 1 cP            = 0.001 Pa.s
LBF_100FT2 = 0.4788026      # 1 lbf/100 ft2   = 0.4788 Pa (yield point, gel strength)


def psi(pa):
    """Pa -> psi"""
    return pa / PSI


def bar(pa):
    """Pa -> bar"""
    return pa / BAR


def ppg(kg_m3):
    """kg/m3 -> ppg"""
    return kg_m3 / PPG


def gpm(m3_s):
    """m3/s -> gpm"""
    return m3_s / GPM


def emw_to_pressure(ppg_value, tvd_m):
    """Equivalent mud weight [ppg] at depth TVD [m] -> pressure [Pa]:  P = rho * g * TVD"""
    return ppg_value * PPG * G * tvd_m


def pressure_to_emw(pressure_pa, tvd_m):
    """Pressure [Pa] at depth TVD [m] -> equivalent mud weight [ppg]:  EMW = P / (g * TVD)"""
    return pressure_pa / (G * tvd_m) / PPG
