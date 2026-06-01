"""agent-forge meta-layer package.

The reusable, observable, falsifiable multi-agent SDLC pipeline. Modules:

- ``orchestrator``  — the Spec -> Architect -> Tasks -> Implement -> Review -> Verify driver.
- ``observability`` — OpenTelemetry GenAI-semantic-conventions span emitter for the trajectory.
- ``traceability``  — the spec <-> task <-> test coverage gate (no orphan AC, task or test).

See ``docs/STATE-OF-THE-ART.md`` for the landscape this layer draws from and goes beyond.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.2.0"
