"""Tests for the OTel GenAI span emitter."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from forge.observability import (
    AgentSpan,
    GenAiOperation,
    SpanStatus,
    TokenUsage,
    TrajectoryEmitter,
)

_T0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
_T1 = _T0 + timedelta(milliseconds=1500)


def _span(**overrides: object) -> AgentSpan:
    base: dict[str, object] = {
        "operation": GenAiOperation.INVOKE_AGENT,
        "agent_name": "architect",
        "model": "opus",
        "start": _T0,
        "end": _T1,
        "status": SpanStatus.OK,
        "usage": TokenUsage(input_tokens=120, output_tokens=80),
        "provenance": "architect@agent-forge.bot",
    }
    base.update(overrides)
    return AgentSpan(**base)  # type: ignore[arg-type]


def test_span_name_follows_otel_convention() -> None:
    assert _span().span_name == "invoke_agent architect"


def test_duration_is_computed_in_milliseconds() -> None:
    assert _span().duration_ms == pytest.approx(1500.0)


def test_to_otel_emits_gen_ai_attribute_keys() -> None:
    attrs = _span().to_otel()["attributes"]
    assert isinstance(attrs, dict)
    assert attrs["gen_ai.operation.name"] == "invoke_agent"
    assert attrs["gen_ai.agent.name"] == "architect"
    assert attrs["gen_ai.request.model"] == "opus"
    assert attrs["gen_ai.usage.input_tokens"] == 120
    assert attrs["gen_ai.usage.output_tokens"] == 80
    assert attrs["gen_ai.provider.name"] == "anthropic"


def test_provenance_is_carried_as_forge_extension() -> None:
    attrs = _span().to_otel()["attributes"]
    assert attrs["forge.provenance"] == "architect@agent-forge.bot"  # type: ignore[index]


def test_custom_attributes_are_merged() -> None:
    span = _span(attributes={"forge.stage": "ARCHITECT"})
    assert span.to_otel()["attributes"]["forge.stage"] == "ARCHITECT"  # type: ignore[index]


def test_end_before_start_is_rejected() -> None:
    with pytest.raises(ValueError, match="end precedes"):
        _span(end=_T0 - timedelta(seconds=1))


def test_negative_token_usage_is_rejected() -> None:
    with pytest.raises(ValueError, match="negative"):
        TokenUsage(input_tokens=-1)


def test_emitter_appends_ndjson(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "run.ndjson"
    emitter = TrajectoryEmitter(target, clock=lambda: _T1)
    emitter.emit(_span())
    emitter.emit(_span(agent_name="verifier", model="haiku"))

    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["name"] == "invoke_agent architect"
    assert first["status"] == "OK"
    assert json.loads(lines[1])["attributes"]["gen_ai.agent.name"] == "verifier"


def test_span_for_stage_maps_nonzero_exit_to_error_status(tmp_path: Path) -> None:
    emitter = TrajectoryEmitter(tmp_path / "run.ndjson", clock=lambda: _T1)
    span = emitter.span_for_stage(
        agent_name="adversarial-reviewer",
        model="sonnet",
        start=_T0,
        provenance="reviewer@agent-forge.bot",
        exit_code=1,
    )
    assert span.status is SpanStatus.ERROR
    assert span.end == _T1


def test_span_for_stage_maps_zero_exit_to_ok_status(tmp_path: Path) -> None:
    emitter = TrajectoryEmitter(tmp_path / "run.ndjson", clock=lambda: _T1)
    span = emitter.span_for_stage(
        agent_name="verifier",
        model="haiku",
        start=_T0,
        provenance="verifier@agent-forge.bot",
        exit_code=0,
    )
    assert span.status is SpanStatus.OK
