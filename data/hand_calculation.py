"""
Hand calculation used as reference data for the LEVEL-1 validation (code check).

It is written independently from the simulator, in oilfield units, with the
formulas of the proposal (section 4-2-4): critical velocity -> regime ->
Bourgoyne et al. (1986) laminar or turbulent gradient; bit with the oilfield
nozzle formula; hydrostatic with the 0.052 rule. Well: config/validation_case.yaml,
choke pressure p_c = 100 psi. Result: data/validation_reference.csv

THIS IS SYNTHETIC DEMONSTRATION DATA, NOT FIELD OR LABORATORY DATA.
Run:  python data/hand_calculation.py
"""

import csv
import math
from pathlib import Path

MW, PV, YP = 10.0, 20.0, 15.0          # ppg, cP, lbf/100ft2
Q = 350.0                              # gpm
HOLE = 8.5                             # in
DP_OD, DP_ID, DP_L = 5.0, 4.276, 9500  # in, in, ft
DC_OD, DC_ID, DC_L = 6.5, 2.8125, 500  # in, in, ft
TVD, PC, CD = 10000.0, 100.0, 0.95     # ft, psi, -
NOZZLES = [12, 12, 12]                 # 1/32 in


def critical_velocity(d, c):
    """v_c = (97 PV + 97 sqrt(PV^2 + c MW d^2 YP)) / (MW d)   [ft/min] -> ft/s"""
    return (97 * PV + 97 * math.sqrt(PV ** 2 + c * MW * d ** 2 * YP)) / (MW * d) / 60


def pipe_loss(d, length):
    """Pipe: laminar PV v/(1500 d^2) + YP/(225 d), turbulent MW^.75 v^1.75 PV^.25/(1800 d^1.25) [psi/ft]"""
    v = Q / (2.448 * d ** 2)                                   # ft/s
    if v < critical_velocity(d, 8.2):
        return (PV * v / (1500 * d ** 2) + YP / (225 * d)) * length, "laminar"
    return MW ** 0.75 * v ** 1.75 * PV ** 0.25 / (1800 * d ** 1.25) * length, "turbulent"


def annulus_loss(d_pipe, length):
    """Annulus: laminar PV v/(1000 dd^2) + YP/(200 dd), turbulent .../(1396 dd^1.25) [psi/ft]"""
    v = Q / (2.448 * (HOLE ** 2 - d_pipe ** 2))
    gap = HOLE - d_pipe
    if v < critical_velocity(gap, 6.2):
        return (PV * v / (1000 * gap ** 2) + YP / (200 * gap)) * length, "laminar"
    return MW ** 0.75 * v ** 1.75 * PV ** 0.25 / (1396 * gap ** 1.25) * length, "turbulent"


(dp_pipe, r1), (dp_bha, r2) = pipe_loss(DP_ID, DP_L), pipe_loss(DC_ID, DC_L)
(dpa1, r3), (dpa2, r4) = annulus_loss(DP_OD, DP_L), annulus_loss(DC_OD, DC_L)
dp_ann = dpa1 + dpa2
area = sum(math.pi / 4 * (n / 32) ** 2 for n in NOZZLES)
dp_bit = 8.311e-5 * MW * Q ** 2 / (CD ** 2 * area ** 2)
bhp = PC + 0.052 * MW * TVD + dp_ann
spp = PC + dp_pipe + dp_bha + dp_bit + dp_ann
ecd = bhp / (0.052 * TVD)                 # proposal: ECD = BHP / (g h)

rows = [("drill_pipe_loss", dp_pipe, "psi"), ("bha_loss", dp_bha, "psi"), ("bit_loss", dp_bit, "psi"),
        ("annulus_loss", dp_ann, "psi"), ("bhp", bhp, "psi"), ("spp", spp, "psi"), ("ecd", ecd, "ppg")]
if __name__ == "__main__":
    out = Path(__file__).with_name("validation_reference.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["quantity", "reference_value", "unit", "data_type", "source"])
        for name, value, unit in rows:
            w.writerow([name, round(value, 4), unit, "synthetic hand calculation",
                        "data/hand_calculation.py (Bourgoyne et al. 1986 field formulas)"])
    for name, value, unit in rows:
        print(f"{name:16s} {value:10.2f} {unit}")
    print("regimes:", r1, r2, r3, r4)
