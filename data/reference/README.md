# Reference data for validation levels 2 and 3

This folder is **empty of reference numbers on purpose**. No published or field
data were available when the program was written, and nothing may be invented.
Until the files below exist, the app and `main.py --validate` show levels 2 and 3
as **"pending reference data"**.

## Level 2 - steady hydraulics vs a published / textbook example
Copy `steady_reference_TEMPLATE.csv` to `steady_reference.csv` and fill it in:

| column | meaning |
|---|---|
| case_file | YAML case file that describes the example well (in this project's schema) |
| quantity | `drill_pipe_loss`, `bha_loss`, `bit_loss`, `annulus_loss`, `bhp`, `spp` or `ecd` |
| unit | `psi`, `bar`, `pa`, `ppg` or `kg_m3` |
| p_c_psi | choke / surface back pressure of the example [psi] |
| reference_value | the published value |
| data_type | e.g. `textbook example`, `published field data` |
| source | full citation with page / table number |

Proposal target: MAPE < 5 % (reported as measured, never adjusted).

## Level 3 - transient response (Kaasa et al. 2012, Naderi Lordejani et al. 2020)
Digitise the published curve into `transient_reference.csv` (see the template):
`time_s, bhp_normalised, source, figure, data_type` with
`bhp_normalised = (BHP - BHP_start) / (BHP_end - BHP_start)`.
Only the response SHAPE is compared (normalised), because the published wells differ
from the synthetic well of this project.
