"""
mpd - well-hydraulics simulator for CBHP Managed Pressure Drilling (thesis model).

Modules (proposal section in brackets)
-------
units.py        unit conversions (all calculations use SI units)
well.py         case file, input checks, survey (MD/TVD), segments, V_d / V_a / M   [4-2-3]
hydraulics.py   steady hydraulics, Bingham Plastic, API RP 13D friction             [4-2-4]
choke.py        choke orifice equation and actuator                                 [4-2-6]
controller.py   reverse-acting PID with filter and anti-windup                      [4-2-7]
dynamics.py     four-state dynamic model, solver, mass balance, convergence         [4-2-5]
metrics.py      overshoot, settling, RMSE, MAPE, time in window, criteria           [4-2-8]
scenarios.py    runs S0 - S5 from config/scenarios.yaml                              [4-2-9]
validation.py   three validation levels                                             [4-2-10]
analysis.py     steady sensitivity and the PID tuning study
suggestions.py  explained suggestions (never applied without the user)
library.py      one CSV file with every input, several cases side by side
extensions.py   OPTIONAL surge / swab estimate - outside the proposal scope
plots.py        charts (used by main.py and app.py)
"""
