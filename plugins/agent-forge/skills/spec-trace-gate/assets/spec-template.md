# Spec: <feature name>

## Scope
<One paragraph: the single problem this solves. Name what is deliberately out of scope.>

## Interface
<Endpoints, CLI commands, functions or events the outside world uses. Inputs and outputs.>

## Invariants
<Properties that must hold at all times, e.g. "sum of debits equals sum of credits".>

## Acceptance criteria (EARS, machine-traceable)

Each criterion has a stable `AC-N` id. `tasks.md` maps every id to a task and a real test, and the
traceability gate enforces that mapping.

- **AC-1**: When <trigger>, the <system> shall <observable response>.
- **AC-2**: While <state>, the <system> shall <observable response>.
- **AC-3**: If <unwanted condition>, then the <system> shall <observable response>.
- **AC-4**: The <system> shall <always-true property>.

## Out of scope
- <explicit exclusion>
