# trajectories/

`forge/orchestrator.py` appends one NDJSON line per stage to `run.ndjson` here on every
`make pipeline` run. The committed run that built the current ledger is documented in the
root `AUTONOMOUS-TRAJECTORY.md`, with its per-spawn audit in `forge/spawn-log.jsonl`.
