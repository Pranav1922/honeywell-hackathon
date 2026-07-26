# backend/tests/

Unit tests for the domain modules. `comfort.py` and `energy.py` are pure
functions with no I/O, which is what keeps this suite fast enough to run on
every change.

| File | Covers |
|---|---|
| `test_comfort.py` | PMV sign and magnitude against known reference points, the PPD curve, band evaluation. |
| `test_energy.py` | kWh integration, peak detection, savings arithmetic, and that comfort degradation is flagged rather than counted as a win. |
| `test_toy_sim.py` | Energy balance, set-point tracking, and determinism across identical runs — the property the savings comparison depends on. |
| `test_loop.py` | The loop completes a horizon, persists every timestep, honours cooperative stop, and falls back when a controller raises. |

```bash
cd backend && python -m pytest
```

Written alongside the modules they cover, in Milestone 1.
