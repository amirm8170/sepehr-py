# Well Hydraulics Simulation Model for Managed Pressure Drilling and Dynamic BHP Control

M.Sc. thesis project, University of Tehran (petroleum engineering, drilling).
The approved proposal is the specification: a one-dimensional, single-phase,
**four-state** dynamic model of a CBHP-MPD well with a physical **choke model**
and a reverse-acting **PID controller**, evaluated with six scenarios (S0 – S5)
and a three-level validation.

> **All bundled data are synthetic demonstration data.** Level-1 validation uses an
> independent *synthetic* hand calculation; levels 2 and 3 are **pending reference data**.
> Research / teaching software – not certified for drilling operations.

Persian presentation guide: `README_FA.md`. Persian layered code walkthrough: `GUIDE_FA.md`.

---

## 1. How to run

```bash
# once: Python 3.12 (uv) - Apple's Python 3.9 / Tk 8.5 crashes on recent macOS
~/.local/share/uv/python/cpython-3.12-*/bin/python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python app.py                              # desktop application (6 pages)
.venv/bin/python main.py                             # steady state + S0 - S4 -> outputs/
.venv/bin/python main.py --scenario all --s5 --validate   # everything (about 2 min)
.venv/bin/python main.py --scenario S3               # one scenario (S0 S1 S2 S2b S3 S4 S5)
.venv/bin/python main.py --tune                      # PID tuning study on S3 (about 1 min)
.venv/bin/python main.py --case data/case_library.csv --variant "OBM 10.5 ppg"
.venv/bin/pytest                                     # 76 tests, about 50 s
.venv/bin/python data/hand_calculation.py            # regenerate the level-1 reference
```

Inputs are never changed automatically: problems are listed with explained
suggestions (`mpd/suggestions.py`) that the user accepts one by one.

## 2. Scope and assumptions (proposal 4-2-2)

- 1-D, single-phase, homogeneous mud; CBHP with a known target BHP.
- Bingham Plastic (ρ, μp, τy); concentric segments of string and annulus.
- Lumped model: string and annulus volumes with compressible fluid and elastic walls,
  one fluid inertance, quasi-steady friction.
- The **choke is the only actuator**. The back-pressure pump (BPP) is a **known input**.
- Not modelled: influx / losses, cuttings, temperature, surge / swab, string movement,
  eccentricity, Power-law / Herschel-Bulkley, multiphase flow, MPC.

## 3. Equations implemented

Pressures are gauge, SI inside, oilfield units only at input / output.

| Item | Equation | File |
|---|---|---|
| Hydrostatic | `P = ρ g h` (TVD) | `hydraulics.py` |
| Critical velocity | pipe `v_c = (97μp + 97√(μp² + 8.2ρd²τy))/(ρd)` ft/min; annulus 6.2 and `Δd = d_h − d_o` | `hydraulics.py` |
| Laminar | pipe `μp v/(1500 d²) + τy/(225 d)`; annulus `μp v/(1000 Δd²) + τy/(200 Δd)` psi/ft, v in ft/s | `hydraulics.py` |
| Turbulent | `ρ^0.75 v^1.75 μp^0.25 / (1800 d^1.25)`; annulus `/(1396 Δd^1.25)` | `hydraulics.py` |
| Smoothing | every loss × `tanh(q / q_ε)`, `q_ε = 10⁻⁴ q_pump` | `hydraulics.py` |
| Bit | `ΔP_bit = ρ q² / (2 C_dn² A_n²)` | `hydraulics.py` |
| Steady | `BHP = p_c + ρ g h + ΔP_a`, `SPP = p_c + ΔP_d + ΔP_bit + (ρ_a−ρ_d) g h + ΔP_a`, `ECD = BHP/(g h)` | `hydraulics.py` |
| Choke | `q_c = C_d A_c(z) √(2 Δp⁺/ρ)`, `Δp⁺ = (Δp + √(Δp² + ε²))/2`, `ε = 10⁻³ p_max`; `A_c = A_max z` (equal-% `A_max R^(z−1)` in S5 only) | `choke.py` |
| Initial opening | `p_c* = p_target − ρ g h − ΔP_a`, z0 from `brentq` | `choke.py` |
| Volumes | `V_d = Σ A_i L_i`, `V_a = Σ A_a,i L_i`, `M = Σ ρL_i/A_i + Σ ρL_i/A_a,i` | `well.py` |
| Bulk modulus | `1/β_eff = 1/β_f + 1/β_w`, thin wall `β_w ≈ 2 E t / D` | `well.py` |
| States (1–4) | `(V_d/β) ṗ_p = q_pump − q`; `(V_a/β) ṗ_c = q + q_bpp − q_c`; `M q̇ = p_p − p_c − (ρ_a−ρ_d) g h − ΔP_d − ΔP_bit − ΔP_a`; `ż = r_max tanh(((z_cmd − z)/τ_a)/r_max)` then `z ∈ [z_min, z_max]` | `dynamics.py` |
| Measurement | `BHP_m(t) = BHP(t − θ_m)`, sampled every `T_s`, zero-order hold; noise optional (off) | `dynamics.py` |
| PID | `e = p_target − BHP_m`, `z_cmd = z0 − (Kp e + Ki ∫e + Kd ė_f)`, filter `T_f = (Kd/Kp)/N`, back-calculation `İ = Ki e + (u_sat − u)/T_t` | `controller.py` |
| Metrics | overshoot, settling (±2 %), max deviation, RMSE, MAPE, % time in `[p_pore+δ, p_frac−δ]` | `metrics.py` |

Solver: `scipy.integrate.solve_ivp`, **Radau**, rtol 1e-6, atol 1e-9, max step 0.2 s,
integrated between controller samples. Every run reports the mass-balance residual
(a 5th bookkeeping variable integrates `q_pump + q_bpp − q_c`) and, on request, the
step-halving check (BHP RMSE change < 0.1 %). RK45 / BDF / LSODA are selectable for comparison.

## 4. Assumptions and decisions (things the proposal leaves open)

| # | Decision | Why | Config key |
|---|---|---|---|
| 1 | **Velocity units of the friction formulas**: v_c (97-constant formula) in **ft/min**, laminar / turbulent gradients with v in **ft/s** | The proposal text says ft/min for all; the 1500/225/1800 and 1000/200/1396 constants are Bourgoyne's ft/s forms – ft/min would give ~60× too much friction. **Needs supervisor confirmation.** | – |
| 2 | Laminar → turbulent over a **10 % band** `[v_c, 1.1 v_c]` (straight line) | A jump at v_c (turbulent is 39 % higher than laminar for the base drill pipe) traps the ODE exactly at v_c; same idea as the proposal's tanh smoothing | `numerics.regime_transition_width` (0 = plain switch) |
| 3 | `BHP = p_c + ρgh + ΔP_a` (proposal prints ΔP_c, read as the annulus loss) | consistent with SPP and the flow path | – |
| 4 | One mud in string and annulus (ρ_a = ρ_d) – the term is written but is 0 | single-phase, homogeneous fluid | – |
| 5 | Inverse choke equation limited to `p_c ≥ p_0 − ε` | the smooth Δp⁺ never gives exactly zero flow | – |
| 6 | β_w = 3.4e10 Pa from `2Et/D` (steel 200 GPa, 5-in pipe); β_f = 1.5e9 Pa | proposal: E must come from a reliable source [confirm] | `compressibility.*` |
| 7 | T_s 0.2 s, θ_m 0.5 s, τ_a 1 s, r_max 0.05 1/s, A_max 3.14 in² (2-in bore), C_d 0.7 | synthetic, typical orders of magnitude [confirm] | `measurement`, `actuator`, `choke` |
| 8 | S2 = **choke-command step** (open loop); target step with PID kept as S2b | without a controller a target step does nothing | `config/scenarios.yaml` |
| 9 | Connection (S3): t1..t4 = 30, 90, 270, 330 s; BPP ramps to **200 gpm** while the rig pump stops | holding the target at zero pump flow then needs z ≈ 0.11 (inside the range) | `S3.bpp_gpm` |
| 10 | Overshoot of a disturbance (target constant) = peak `|BHP − target|` / target | the proposal's "initial distance" is 0 when BHP starts on target | `metrics.py` |
| 11 | "Marked oscillation" in tuning = BHP crosses target (0.1 % band) > 6 times or the choke reverses > 20 times | makes the trial-and-error rule repeatable | `analysis.py` |
| 12 | Step-halving check halves the **integrator max step** (T_s fixed); change floor 0.1 % of target | T_s belongs to the controller, not to the numerics | `solver.*` |

**Conflicts with earlier repository documentation (proposal wins):** the old version used the
exact Buckingham-Reiner / slot friction (now an optional comparison model `exact_bingham`),
a Cv choke curve, a one-state annulus model with hand-coded Euler steps, a back-pressure
actuator, fixed-SBP mode, gel strength, surge/swab in the core model, and an auto-fix that
changed inputs silently. All replaced; surge/swab kept only in `mpd/extensions.py`
(labelled out of scope). Old case files (YAML or CSV) are converted by `well.migrate_config()`.

## 5. PID tuning (proposal 4-2-7, `main.py --tune`)

On S3: Ki = Kd = 0, Kp ∈ {0.005 … 0.32} 1/bar → largest calm Kp = **0.08** (0.16 makes the
choke hunt: 38 reversals). Then Ki ∈ {0.001 … 0.032} → smallest RMSE without marked
oscillation: **Ki = 0.008 1/(bar s)**. PI overshoot 1.13 % < 5 %, so **Kd = 0** – therefore the
PID run equals the PI run in S4 (Kd = 0.05 s/bar would lower RMSE ≈ 13 %; supervisor's choice).

## 6. Results on the synthetic base case

Base case (J-type well, 3500 m MD / 2943 m TVD, 10 ppg WBM, 500 gpm): BHP = target =
383.8 bar (11.10 ppg, window midpoint), SPP 176.1 bar, p_c 14.4 bar, z0 0.454;
losses drill pipe 54.8, BHA 24.3, bit 59.0, annulus 23.7 bar; β_eff 1.44 GPa,
V_d 31.4 m³, V_a 84.9 m³, M 6.6e8 kg/m⁴.

| Scenario | Result |
|---|---|
| S0 | 350 / 500 / 650 gpm: z0 0.30 / 0.45 / 0.75, SPP 109 / 176 / 263 bar; dynamic model stays at each point (drift 0 Pa) |
| S1 | +150 gpm, fixed choke: BHP +15.5 bar, settles at the new steady state (open loop – settling criterion FAIL by design) |
| S2 | choke −0.05: BHP +3.8 bar |
| S3 | connection, fixed choke: BHP down 20.9 bar, only 60 % of the time in the safe window (all criteria FAIL) |
| S4 | P: max dev 6.9 bar, RMSE 2.65 bar · PI = PID: max dev 4.3 bar, RMSE 0.60 bar, overshoot 1.13 %, 100 % in window (all criteria PASS) |
| S5 | ±20 %: max deviation 3.4 – 5.7 bar; β_eff is the most sensitive parameter; C_d and A_max act identically (linear choke: only C_d·A_max matters) |
| Numerics | mass-balance residual ≈ 1e-15 %; step-halving change < 1e-4 % |
| Validation L1 | model vs independent hand calculation: max error 0.093 % (rounded oilfield constants) |
| Validation L2 / L3 | **pending reference data** (`data/reference/README.md`) |

## 7. Files (≈ 4300 lines of code)

```
app.py                Tkinter window: 6 pages, background runs with progress / Cancel
main.py               command line, results to outputs/
mpd/                  see mpd/__init__.py for one line per module
config/base_case.yaml canonical input schema (units in key names) - the base case
config/scenarios.yaml S0 - S5 schedules
config/validation_case.yaml   vertical well for the level-1 check
data/case_library.csv 10 cases in one CSV (several trigger suggestions on purpose)
data/hand_calculation.py      independent hand calculation -> validation_reference.csv
data/reference/       templates for level-2 / level-3 reference data (no numbers)
tests/                76 tests against hand calculations / analytic results
archive/advanced_version.zip  old large version (not used)
```

## 8. Limitations

Lumped model (no pressure waves – see the Godunov approach of Sánchez-Barra et al.),
quasi-steady friction, single phase, no influx / losses / cuttings / gel, choke the only
actuator, synthetic data. Quantitative validation against published data (levels 2 – 3)
is still open.

## References

1. Kaasa G.-O. et al. (2012) SPE Drilling & Completion 27(1) 127-138, doi:10.2118/143097-PA
2. Naderi Lordejani S. et al. (2020) SPE Drilling & Completion 35(4) 598-619, doi:10.2118/201108-PA
3. Sheikhi M. A. et al. (2022) ISA Transactions, doi:10.1016/j.isatra.2021.11.022
4. Sánchez-Barra A. J. et al. (2015) J. Petrol. Explor. Prod. Technol., doi:10.1007/s13202-015-0176-8
5. Bourgoyne A. T. et al. (1986) Applied Drilling Engineering, SPE Textbook Series vol. 2
6. Åström K. J., Hägglund T. (2006) Advanced PID Control, ISA (back-calculation anti-windup)
