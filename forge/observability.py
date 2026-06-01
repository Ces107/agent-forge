"""OpenTelemetry GenAI-conventions span emitter for the agent-forge trajectory.

Why this exists
---------------
The 2026 state of the art in agent observability (Langfuse, AgentOps, Arize, OpenLLMetry)
has converged on the **OpenTelemetry GenAI semantic conventions**: every agent invocation,
tool call and model request is a span carrying ``gen_ai.*`` attributes (operation name, agent
name, request model, token usage), so traces are portable across vendors instead of locked into
one SDK's schema.

agent-forge emits the *same* attribute shape from pure stdlib — no SDK dependency, no network —
and writes it as newline-delimited JSON to the run trajectory. Any OTel-aware backend can ingest
it, and the audit trail stays a flat, greppable file you can diff in a PR.

Where this goes beyond the field
--------------------------------
Each span additionally carries a ``forge.provenance`` attribute (the agent identity that authored
the artifact) so observability and provenance are the *same* record, not two disconnected systems.
A span is not just "what happened" but "who is accountable for it", checkable from ``git log``.

Spec reference: OpenTelemetry Semantic Conventions for Generative AI, ``gen_ai.*`` namespace.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

# --- OTel GenAI conventions: closed vocabularies -----------------------------------------------


class GenAiOperation(StrEnum):
    """``gen_ai.operation.name`` — the kind of GenAI operation a span represents."""

    INVOKE_AGENT = "invoke_agent"
    CHAT = "chat"
    EXECUTE_TOOL = "execute_tool"


class SpanKind(StrEnum):
    """OTel span kind. Agent invocations are INTERNAL work units in this pipeline."""

    INTERNAL = "INTERNAL"
    CLIENT = "CLIENT"


class SpanStatus(StrEnum):
    """OTel status code. ERROR marks a stage whose gate halted or whose process exited non-zero."""

    UNSET = "UNSET"
    OK = "OK"
    ERROR = "ERROR"


# --- value objects ------------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenUsage:
    """``gen_ai.usage.*`` token counters. Zero is a valid 'not reported' value."""

    input_tokens: int = 0
    output_tokens: int = 0

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("token usage cannot be negative")


@dataclass(frozen=True)
class AgentSpan:
    """One OTel GenAI span over a single pipeline-stage invocation.

    The attribute names emitted by :meth:`to_otel` are the GenAI semantic-convention keys, so the
    record drops into any OTel GenAI backend unchanged.
    """

    operation: GenAiOperation
    agent_name: str
    model: str
    start: datetime
    end: datetime
    status: SpanStatus = SpanStatus.UNSET
    usage: TokenUsage = field(default_factory=TokenUsage)
    provenance: str = ""
    attributes: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("span end precedes its start")

    @property
    def span_name(self) -> str:
        """OTel GenAI span-name convention: ``{operation} {target}``."""
        return f"{self.operation.value} {self.agent_name}"

    @property
    def duration_ms(self) -> float:
        return (self.end - self.start).total_seconds() * 1000.0

    def to_otel(self) -> dict[str, object]:
        """Render as an OTel-GenAI-shaped record (``gen_ai.*`` + forge extensions)."""
        record: dict[str, object] = {
            "name": self.span_name,
            "kind": SpanKind.INTERNAL.value,
            "start_time": self.start.isoformat(),
            "end_time": self.end.isoformat(),
            "duration_ms": round(self.duration_ms, 3),
            "status": self.status.value,
            "attributes": {
                "gen_ai.operation.name": self.operation.value,
                "gen_ai.provider.name": "anthropic",
                "gen_ai.agent.name": self.agent_name,
                "gen_ai.request.model": self.model,
                "gen_ai.usage.input_tokens": self.usage.input_tokens,
                "gen_ai.usage.output_tokens": self.usage.output_tokens,
                # forge extension: ties the span to the accountable agent identity (provenance).
                "forge.provenance": self.provenance,
                **dict(self.attributes),
            },
        }
        return record


# --- emitter ------------------------------------------------------------------------------------


Clock = Callable[[], datetime]


class TrajectoryEmitter:
    """Appends OTel GenAI spans as NDJSON to a trajectory file.

    The clock is injected so callers (and tests) control time deterministically; nothing here
    reads a wall clock implicitly.
    """

    def __init__(self, path: Path, clock: Clock) -> None:
        self._path = path
        self._clock = clock

    def emit(self, span: AgentSpan) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(span.to_otel(), ensure_ascii=False) + "\n")

    def span_for_stage(
        self,
        *,
        agent_name: str,
        model: str,
        start: datetime,
        provenance: str,
        exit_code: int,
        usage: TokenUsage | None = None,
        attributes: Mapping[str, str] | None = None,
    ) -> AgentSpan:
        """Build a stage span, stamping ``end`` from the injected clock and mapping exit -> status."""
        status = SpanStatus.OK if exit_code == 0 else SpanStatus.ERROR
        return AgentSpan(
            operation=GenAiOperation.INVOKE_AGENT,
            agent_name=agent_name,
            model=model,
            start=start,
            end=self._clock(),
            status=status,
            usage=usage or TokenUsage(),
            provenance=provenance,
            attributes=attributes or {},
        )
