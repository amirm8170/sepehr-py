"""
Hand calculation used as reference data for the validation demo.

It uses the classic oilfield-unit Bingham formulas of Applied Drilling
Engineering (Bourgoyne et al., 1986, chapter 4) - written independently
from the simulator code - for the vertical well in
config/validation_case.yaml. Result: data/validation_reference.csv

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
TVD, SBP, CD = 10000.0, 100.0, 0.95    # ft, psi, -
NOZZLES = [12, 12, 12]                 # 1/32 in


def pipe_loss(d, length):
    """Turbulent pipe flow:  dp/dL = MW^0.75 v^1.75 PV^0.25 / (1800 d^1.25)   [psi/ft]"""
    v = Q / (2.448 * d ** 2)
    return MW ** 0.75 * v ** 1.75 * PV ** 0.25 / (1800 * d ** 1.25) * length


def annulus_loss(d_pipe, length):
    """Laminar annular flow:  dp/dL = PV v / (1000 (d2-d1)^2) + YP / (200 (d2-d1))   [psi/ft]"""
    v = Q / (2.448 * (HOLE ** 2 - d_pipe ** 2))
    gap = HOLE - d_pipe
    return (PV * v / (1000 * gap ** 2) + YP / (200 * gap)) * length


dp_pipe, dp_bha = pipe_loss(DP_ID, DP_L), pipe_loss(DC_ID, DC_L)
dp_ann = annulus_loss(DP_OD, DP_L) + annulus_loss(DC_OD, DC_L)
area = sum(math.pi / 4 * (n / 32) ** 2 for n in NOZZLES)
dp_bit = 8.311e-5 * MW * Q ** 2 / (CD ** 2 * area ** 2)
bhp = SBP + 0.052 * MW * TVD + dp_ann
spp = SBP + dp_pipe + dp_bha + dp_bit + dp_ann
ecd = MW + dp_ann / (0.052 * TVD)

rows = [("drill_pipe_loss", dp_pipe, "psi"), ("bha_loss", dp_bha, "psi"), ("bit_loss", dp_bit, "psi"),
        ("annulus_loss", dp_ann, "psi"), ("bhp", bhp, "psi"), ("spp", spp, "psi"), ("ecd", ecd, "ppg")]
out = Path(__file__).with_name("validation_reference.csv")
with open(out, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["quantity", "reference_value", "unit", "data_type", "source"])
    for name, value, unit in rows:
        w.writerow([name, round(value, 4), unit, "synthetic",
                    "Hand calculation, Applied Drilling Engineering ch. 4"])
for name, value, unit in rows:
    print(f"{name:16s} {value:10.2f} {unit}")
