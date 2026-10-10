"""
mpd - a simple well-hydraulics simulator for Managed Pressure Drilling (MPD).

Modules
-------
units.py       unit conversions (all calculations use SI units)
well.py        read the case file, survey (MD/TVD), split the well into segments
hydraulics.py  Stage A: steady-state hydraulics (Bingham Plastic)
dynamics.py    Stage B: time simulation, choke and PID controller
analysis.py    pressure window, control metrics, sensitivity, validation
plots.py       charts (used by main.py and app.py)
"""
