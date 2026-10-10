# Well Hydraulics Simulation Model for Managed Pressure Drilling (MPD) and Dynamic Bottomhole Pressure Control

Graduate thesis project (University of Tehran), written in Python.

The program has two stages:

1. **Stage A – steady state.** It calculates the pressure everywhere in the well while mud is circulated: friction in the drill pipe, BHA, bit and annulus; hydrostatic pressure; SPP, BHP and ECD.
2. **Stage B – dynamic.** It simulates the well over time (pumps start and stop, choke moves, pipe moves) and controls the bottomhole pressure with a **PID controller** that moves the MPD choke.

> **Disclaimer.** This is research and teaching software. It is not certified for real drilling operations. All data in this project are **synthetic demonstration data**, not field or laboratory measurements.

---

## 1. Model Status and Assumptions

| Category | Content |
|---|---|
| **A. From the thesis proposal** | Bingham Plastic rheology τ = τy + μp·γ̇; well split into segments (MD, TVD, inclination, azimuth); BHP, SPP, ECD, pressure profile; dynamic simulation; PID control of BHP; pore/fracture pressure window |
| **B. Standard equations** | A = πD²/4; annulus A = π/4(Dh² − Dp²); Dh = Dhole − Dpipe; v = Q/A; P = ρ·g·TVD; Re = ρvD/μp; He = ρ·τy·D²/μp²; bit ΔP = ρv²/(2Cd²); minimum-curvature survey method; discrete PID |
| **C. Literature correlations** (modelling choices that need validation) | Buckingham–Reiner (laminar pipe); Bingham slot model (laminar annulus); Hanks 1963 critical Reynolds number; Blasius turbulent friction with μp (Bourgoyne et al. 1986); Burkhardt 1961 swab/surge; simplified MPD model of Kaasa et al. 2012 |
| **D. Assumptions** | One mud, single phase, constant temperature; mud incompressible for friction and hydrostatics; concentric pipe; no tool joints or surface-line losses; no kick or loss flow (only flagged) |
| **E. Simplifications** | Flow along the drill string is incompressible (pump changes reach the bit at once); friction in the dynamic model is taken from the steady model; friction goes linearly to zero below 50 gpm; linear choke Cv curve |
| **F. Future work** | Power-law and Herschel–Bulkley friction; full transient (wave) model; temperature effects; real choke curve; other controllers |

The thesis proposal file itself was not available when this code was written. Choices that the proposal does not fix are marked as modelling choices.

---

## 2. How to run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python app.py                       # the desktop window
.venv/bin/python main.py                      # command line: steady state + 10 scenarios
.venv/bin/python main.py --sensitivity --validate
.venv/bin/pytest                              # 33 tests
```

Python 3.10 or newer, with Tkinter (Tk 8.6 or 9). On new macOS versions Apple's built-in Python 3.9 has an old Tk that crashes. Use Python from python.org, Homebrew or `uv` (this project uses `uv python install 3.12`).

The command-line program saves CSV files and PNG charts in `outputs/`.

---

## 3. Project files (about 2,400 lines of code)

```
app.py               desktop window (Tkinter + matplotlib)
main.py              command-line program
config/
  base_case.yaml     the well, mud and settings (units are in the key names)
  scenarios.yaml     the 10 dynamic scenarios
  validation_case.yaml  simple vertical well for the validation demo
data/
  trajectory_example.csv     survey of the example J-type well (synthetic)
  hand_calculation.py        independent hand calculation (textbook formulas)
  validation_reference.csv   its results = reference values
mpd/
  units.py           unit conversions (everything is calculated in SI)
  well.py            read the case file, survey (MD/TVD), segments
  hydraulics.py      Stage A: Bingham friction, bit, choke, BHP/SPP/ECD
  dynamics.py        Stage B: time simulation and the PID controller
  analysis.py        control metrics, sensitivity, validation
  plots.py           all charts
tests/               tests against independent results
archive/advanced_version.zip   an earlier, larger version of the project
```

---

## 4. The desktop window

| Page | What it shows |
|---|---|
| Steady state | input boxes (pump rate, mud, back pressure, choke), result cards (BHP, SPP, SBP, ECD), pressure-window gauge, pressure accounting, pressure profile vs MD and TVD, SPP breakdown, well trajectory, segment table |
| Dynamic scenarios | choose a scenario, run it, then view BHP / SPP / flow / choke over time, ECD, depth profiles and metrics |
| PID control | change Kp, Ki, Kd and compare the result with and without PID |
| Sensitivity | tornado chart (±%) and a parameter sweep |
| Validation | model vs reference values, error % |
| Case file | all inputs as text; edit, apply, open, save |

Every chart has a toolbar for zoom, pan and save as PNG. Pressures can be shown in bar or psi.

---

## 5. Equations

### Pressure convention (all pressures are gauge pressures)

- **SBP** – surface back pressure = annulus pressure at the surface, just before the choke
- **BHP** – pressure in the annulus at the bottom of the well (TD)

```
BHP = SBP + ρ·g·TVD + annular friction
SPP = SBP + annular friction + bit pressure drop + BHA friction + drill-pipe friction
ECD = ρ + annular friction / (g·TVD)          (conventional, without SBP)
EMW = BHP / (g·TVD)                            (includes SBP; often called "ECD" in MPD)
```

Hydrostatic pressure uses **TVD**. Friction uses the path length (**MD**) of each segment.

### Bingham Plastic friction (in `hydraulics.py`)

1. Reynolds number `Re = ρ|v|D/μp`, Hedstrom number `He = ρ·τy·D²/μp²`
2. Critical Reynolds number (Hanks 1963): `x/(1−x)³ = He/16800`, `Re_c = He/(8x)·(1 − 4x/3 + x⁴/3)`. This gives 2100 when He = 0.
3. **Laminar** (Re < Re_c), exact Bingham solutions:
   - pipe: `8·μp·v/D = τw·(1 − 4/3·r + r⁴/3)`
   - annulus (slot): `12·μp·v/Dh = τw·(1 − 3/2·r + r³/2)`, with `r = τy/τw`
4. **Turbulent**: `f = 0.0791·Re^-0.25`; the annulus uses `De = 0.816·Dh` (Bourgoyne et al. 1986)
5. Pressure gradient `dP/dL = 2·f·ρ·v²/D` (Fanning friction factor). In a segment, `ΔP = (dP/dL)·L`.

### Bit and choke

```
TFA = Σ π·d²/4      v_nozzle = Q/TFA      ΔP_bit = ρ·v²/(2·Cd²)
Choke:  Q = K(u)·sqrt(ΔP/ρ),  K = Cv × 2.4e-5 m²,  Cv = Cv_closed + (Cv_max − Cv_closed)·u
```

### Swab and surge (Burkhardt 1961, simplified)

`v_annulus = (Q + v_pipe·A_pipe)/A_annulus + K_c·v_pipe`, with K_c ≈ 0.45 and v_pipe > 0 when moving down.

### Dynamic model (in `dynamics.py`)

The mud in the annulus is slightly compressible:

```
dp_choke/dt = (β / V_annulus) · (Q_in − Q_choke)
Q_in = pump + back-pressure pump + pipe displacement,   Q_choke from the choke equation
```

- The **quasi-steady** option ignores compressibility: p_choke comes straight from the choke equation.
- The **back-pressure actuator** option makes SBP follow its set-point with a delay τ.

### PID controller

```
error  = (target BHP − BHP) / 1 bar
output = bias + Kp·error + Ki·∫error dt + Kd·d(error)/dt
```

It also has:

- output limits (choke 2–100 % open)
- a rate limit (the choke speed)
- anti-windup
- derivative on the measurement, with a filter
- reverse action: closing the choke raises BHP

### Pressure window

At every time step:

- BHP < pore pressure → **kick risk**
- BHP > fracture pressure → **loss risk**
- otherwise → **safe**

The control metrics are steady-state error, rise time, settling time, overshoot, max/min BHP and time outside the window. They are also given for each event separately.

---

## 6. Scenarios (`config/scenarios.yaml`)

| # | Scenario | Result with the base case |
|---|---|---|
| 1 | Constant pump rate | BHP constant (check) |
| 2 | Pump rate 500 → 650 gpm | BHP rises to 397 bar, safe |
| 3 | Pump rate 500 → 350 gpm | BHP falls to 371 bar, safe |
| 4 | Pump start | starts in kick risk (static mud is too light) |
| 5 | Pump stop, no back-pressure pump | **kick risk**: friction is lost |
| 6 / 7 | Choke opening / closing | BHP falls / rises |
| 8 | Back pressure 150 → 300 psi | BHP +10 bar |
| 9 | Pipe movement | surge +18 bar, swab −9 bar |
| 10 | PID during a connection + target step | BHP stays in the window (largest deviation ≈ 7 bar) |

---

## 7. Validation

`data/hand_calculation.py` calculates the vertical validation well by hand, using the textbook formulas of *Applied Drilling Engineering*. The simulator is then compared with it (Validation page, or `main.py --validate`):

| Quantity | Error |
|---|---|
| drill pipe, BHA, bit | < 0.1 % |
| BHP | 0.55 % |
| SPP | 1.3 % |
| annulus friction | 9 % |

The 9 % comes from the exact laminar Bingham formula versus the simplified textbook formula. **This is a check of the code, not a validation with real data.** For real validation, put field or laboratory values into a CSV file with the same columns and set `data_type` accordingly.

---

## 8. Tests

`pytest` runs 33 tests. Each test compares the code with an independent result:

- unit conversions; the oilfield rule 0.052 × MW × TVD
- areas worked out by hand; minimum curvature on a circle
- Hagen–Poiseuille flow; exact Bingham velocity-profile integration
- Hanks critical Reynolds number; textbook turbulent-pipe and bit formulas; choke Cv definition
- static BHP = ρgh; SPP = sum of losses; U-tube balance at the bit
- PID offset 1/(1+Kp); PI with no offset; settling and rise time of a first-order system
- mass balance in the dynamic model; the PID keeps BHP inside the window

---

## 9. Limitations and calibration

Values to calibrate with real data:

- PV and YP (or the full Fann readings)
- the friction correlation
- the nozzle discharge coefficient Cd
- the choke Cv curve
- the mud bulk modulus β
- the clinging constant K_c
- the PID gains and choke speed

Points to discuss with the supervisor:

- exact vs simplified laminar annulus formula
- how the laminar/turbulent limit is chosen
- whether BHP is referenced at the bit or at TD
- which ECD definition to report
- whether the simplified dynamic model is enough, or a full transient model is needed
- the choke model
- where the real validation data will come from

## References

- Bourgoyne, Millheim, Chenevert, Young (1986). *Applied Drilling Engineering*. SPE.
- Hanks, R.W. (1963). The laminar–turbulent transition for fluids with a yield stress. *AIChE J.* 9(3).
- Burkhardt, J.A. (1961). Wellbore pressure surges produced by pipe movement. *JPT* 13(6).
- Kaasa, G.-O. et al. (2012). Simplified hydraulics model … for an MPD control system. *SPE Drilling & Completion* 27(1).
- Godhavn, J.-M. (2010). Control requirements for automatic managed pressure drilling system. *SPE Drilling & Completion* 25(3).
