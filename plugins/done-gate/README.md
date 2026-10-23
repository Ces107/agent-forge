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
(`proof-of-done/`).

## Config

A file in the repository can add checks but never waive them. Anything that relaxes the gate (extra
test commands, ignored paths, allowed findings, fewer reminders) goes in your own file outside the
repository, `~/.claude/proof-of-done.json`:

```json
{"max_blocks": 2,
 "projects": {"~/code/shop": {"test_commands": ["./run-tests\\.sh"], "ignore_paths": ["docs/"]}}}
```

Top-level keys apply everywhere; keys under `projects` apply in that directory and below.

The project's `.proof-of-done.json` can only make the gate stricter: a higher `max_blocks` (up to 10) and
`"integrity": true`. Any other key in it is ignored.

## Disable

- For now: `/plugin disable done-gate@agent-forge` (or toggle it in `/plugin`).
- For good: `/plugin uninstall done-gate@agent-forge`.
- In one project only: set `"max_blocks": 0` for that project under `projects` in
  `~/.claude/proof-of-done.json` (it then never blocks, only warns).

## License

MIT. Source: https://github.com/Ces107/agent-forge/tree/main/plugins/done-gate
