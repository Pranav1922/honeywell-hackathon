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
| `test_logsummary.py` | Telemetry and `.err` compaction: that the digest is bounded *and* that the anomalies survive it. |
| `test_llm_agent.py` | The Groq client (retries, timeouts, rate limits, missing key), JSON parsing and recovery, prompt generation, the tool registry, policy validation and bounded self-correction, guard enforcement of model output, and a full closed loop under the supervisor. |

`conftest.py` holds the `BuildingState` factory the agent tests share.

```bash
cd backend && python -m pytest
```

No test reaches the network: the Groq SDK is replaced by a scripted transport,
which is what makes the malformed-response and timeout paths testable at all.
