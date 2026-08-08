# mod2 — Extractor 2.0 (soft rewrite, IN PROGRESS)

The 2.0 module layout per `haven-ui/docs/EXTRACTOR_2_0.md` §7 (the master plan;
that doc is the source of truth). This folder will REPLACE `mod/` at release;
the old folder gets archived. **`mod/` stays untouched and shipping until then —
members are live on it.**

| Module | Status | Contents |
|---|---|---|
| `payload/` | ✅ transplanted | `extraction_core.py` verbatim from `mod/` (56/56 smoke tests) |
| `telemetry/` | ✅ real 2.0 code | curated event bus (SESSION/CAPTURE/SYNC/ACCOUNT/HEALTH/UPDATE), coalescing |
| `api_local/` | ✅ real 2.0 code | localhost API 8770-8779: /status /current-system /events (SSE) /session |
| `sync/` | ✅ real 2.0 code | staging push, handshake+key provisioning, heartbeat, command poll — TLS verified |
| `config/` | ✅ real 2.0 code | root-level env file (survives updates; kills the config-layering footgun) |
| `state.py` | ✅ real 2.0 code | thread-safe snapshots + the READINESS GATE (no "Ready" with dead hooks) |
| `capture/` | ⏳ next session | hook + memory-read transplant per the manifest — needs the game running to verify |

`simulate.py` drives fake captures through the REAL pipeline (everything except
`capture/`) — it's the dev harness for the website console and the demo rig.

```
py mod2/simulate.py --api http://127.0.0.1:8005 --interval 20
```

Gitignored: `sim_haven.env` (holds a real vh_live_ key once paired).
