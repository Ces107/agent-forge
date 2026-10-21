# done-gate

A free Claude Code plugin (MIT) that stops the agent from saying "done", "fixed" or "all tests pass"
without proof. After a code edit, the agent cannot end its turn until a real test command has run and
passed after its last change. It checks the commands that actually ran, not the agent's words.

## Install

```
/plugin marketplace add Ces107/agent-forge
/plugin install done-gate@agent-forge
```

It is active from the next turn. Requires Python 3.9+ on the PATH as `python3` or `python`.

## What it runs

One standard-library Python script, `scripts/done_gate.py`, registered in `hooks/hooks.json` for three
hook events:

| Event | What the script does |
|---|---|
| `PostToolUse` (Edit, Write, MultiEdit, NotebookEdit, Bash) | Records code edits and test runs (pytest, npm/yarn/pnpm test, jest, vitest, go test, cargo test, mvn, gradle, dotnet test, rspec, phpunit, make test and more) |
| `PostToolUseFailure` (Bash) | Records failed test runs |
| `Stop` | Blocks the end of the turn when code changed and no passing test run happened after the last edit, and tells the agent which command to run |

The hook never runs your tests itself; it only reads the hook input Claude Code passes it. A run piped
into `tail` or `grep`, masked with `|| true`, or reporting `0 tests collected` does not count. After two
reminders it lets the turn end with a visible warning, so projects without tests keep working. Sessions
without code edits are never blocked.

## Data and network

No network access and no telemetry. State is one small JSON file per session in the system temp folder
(`proof-of-done/`). Optional per-project config in `.proof-of-done.json` at the project root:

```json
{"test_commands": ["./run-tests\.sh"], "ignore_paths": ["docs/"], "max_blocks": 2}
```

## Disable

- For now: `/plugin disable done-gate@agent-forge` (or toggle it in `/plugin`).
- For good: `/plugin uninstall done-gate@agent-forge`.
- In one project only: set `"max_blocks": 0` in `.proof-of-done.json` (it then never blocks, only warns).

## License

MIT. Source: https://github.com/Ces107/agent-forge/tree/main/plugins/done-gate
