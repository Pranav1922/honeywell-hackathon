# frontend/src/lib/

| Module | Purpose |
|---|---|
| `api.js` | One wrapper per backend endpoint listed in `docs/ARCHITECTURE.md` §6. Same-origin relative paths; the Vite dev proxy forwards `/api` to FastAPI. |
| `format.js` | Display formatting shared across panels: kWh, percentages, temperatures, timestamps, PMV. |
