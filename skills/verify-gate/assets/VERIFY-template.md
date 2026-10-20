# VERIFY-<NNN>: <change or branch name>

Run at: <date and time> · commit: <short sha or "uncommitted">

| Check | Command | Exit code | Result |
|---|---|---|---|
| tests | `<command>` | <n> | PASS / FAIL |
| coverage | `<command>` | <n> | PASS / FAIL (<value>% vs threshold <t>%) |
| lint | `<command>` | <n> | PASS / FAIL |
| types | `<command>` | <n> | PASS / FAIL |
| security | `<command>` | <n> | PASS / FAIL |
| secrets | `<command or search used>` | <n> | PASS / FAIL |

## Failures (verbatim output, last ~20 lines each)

### <check>
```
<real output>
```

## Skipped or not applicable
- <check>: SKIPPED (not installed): install with `<command>` / N/A (<reason>)

VERIFY: <PASS | FAIL | INCOMPLETE>
