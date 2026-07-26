# frontend/src/lib/

| Module | Purpose |
|---|---|
| `api.js` | One wrapper per backend endpoint listed in `docs/ARCHITECTURE.md` §6. Same-origin relative paths; the Vite dev proxy forwards `/api` to FastAPI. |
| `format.js` | Display formatting shared across panels: kWh, percentages, temperatures, timestamps, PMV. One number in, one string out. |
| `series.js` | Chart series shaping: downsampling for long horizons, cumulative accumulation, and merging a baseline run against an agent run by step. One array in, one array out. |

`series.js` is where the chart panels' only non-presentational logic lives, so it
can be asserted on directly instead of through rendered SVG. Two rules in it are
load-bearing: energy is accumulated **before** downsampling, or the total is
understated by the stride; and a baseline overlay is matched on **step**, not on
array index, or two runs of unequal length silently misalign.
