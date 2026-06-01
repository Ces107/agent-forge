"""Tests for the tamper-evident attestation chain.

These tests are adversarial on purpose: most of them *forge* something (edit an artifact, reorder a
link, mutate a field) and assert the verifier catches it at the exact link. A provenance system is
only worth its name if it fails loudly when lied to.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from forge.attestation import (
    GENESIS,
    ArtifactDigest,
    Attestation,
    ChainVerification,
    StageManifest,
    build_chain,
    canonical_json,
    chain_head,
    filesystem_resolver,
    main,
    parse,
    serialize,
    sha256_hex,
    verify,
)

_FILES = {
    "spec.md": b"AC-1 the books shall close.",
    "src/ledger.py": b"def transfer(): ...",
    "review.md": b"GATE: FAIL then fixed.",
}


def _resolver(files: dict[str, bytes]) -> Callable[[str], bytes]:
    def _read(path: str) -> bytes:
        if path not in files:
            raise FileNotFoundError(path)
        return files[path]

    return _read


def _manifests() -> list[StageManifest]:
    return [
        StageManifest("spec", "spec@agent-forge.bot", "opus", (), ("spec.md",), "SPEC: OK"),
        StageManifest(
            "implementer", "implementer@agent-forge.bot", "sonnet", ("spec.md",), ("src/ledger.py",), ""
        ),
        StageManifest(
            "adversarial-reviewer", "reviewer@agent-forge.bot", "sonnet",
            ("src/ledger.py",), ("review.md",), "GATE: FAIL",
        ),
    ]


def _chain() -> tuple[Attestation, ...]:
    return build_chain(_manifests(), _resolver(_FILES))


# --- primitives --------------------------------------------------------------------------------


def test_canonical_json_is_order_independent() -> None:
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_sha256_hex_matches_hashlib() -> None:
    assert sha256_hex(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_artifact_digest_of_content() -> None:
    d = ArtifactDigest.of("x", b"abc")
    assert d.path == "x"
    assert d.sha256 == sha256_hex(b"abc")


# --- chain construction ------------------------------------------------------------------------


def test_genesis_prev_digest_is_zeroes() -> None:
    assert _chain()[0].prev_digest == GENESIS


def test_each_link_points_at_the_previous_digest() -> None:
    chain = _chain()
    for prev, curr in zip(chain, chain[1:], strict=False):
        assert curr.prev_digest == prev.digest()


def test_digest_is_deterministic() -> None:
    assert _chain()[0].digest() == _chain()[0].digest()


def test_chain_head_is_last_digest() -> None:
    chain = _chain()
    assert chain_head(chain) == chain[-1].digest()


def test_chain_head_of_empty_is_genesis() -> None:
    assert chain_head([]) == GENESIS


def test_serialize_parse_round_trip() -> None:
    records = parse(serialize(_chain()))
    assert len(records) == 3
    assert records[0]["stage"] == "spec"
    assert Attestation.from_record(records[1]).stage == "implementer"


# --- verification: the honest case -------------------------------------------------------------


def test_intact_chain_verifies() -> None:
    result = verify(parse(serialize(_chain())), _resolver(_FILES))
    assert result.ok
    assert result.length == 3
    assert "ATTEST: PASS" in result.render()


# --- verification: forgeries are caught --------------------------------------------------------


def test_tampered_artifact_is_caught_at_its_link() -> None:
    records = parse(serialize(_chain()))
    forged = dict(_FILES)
    forged["src/ledger.py"] = b"def transfer(): steal_money()"  # edited after attestation
    result = verify(records, _resolver(forged))
    assert not result.ok
    assert result.broken_sequence == 1
    assert "digest mismatch" in result.reason


def test_mutated_field_breaks_record_digest() -> None:
    records = list(parse(serialize(_chain())))
    records[2] = {**records[2], "agent": "ceo@human.example"}  # rewrite who did it, keep old digest
    result = verify(records, _resolver(_FILES))
    assert not result.ok
    assert result.broken_sequence == 2
    assert "tampered field" in result.reason


def test_reordering_breaks_the_prev_link() -> None:
    records = list(parse(serialize(_chain())))
    records[1], records[2] = records[2], records[1]
    result = verify(records, _resolver(_FILES))
    assert not result.ok
    assert result.broken_sequence == 1
    assert "prev_digest" in result.reason


def test_deleted_link_breaks_the_chain() -> None:
    records = list(parse(serialize(_chain())))
    del records[1]
    result = verify(records, _resolver(_FILES))
    assert not result.ok
    assert result.broken_sequence == 1


def test_missing_artifact_is_reported() -> None:
    records = parse(serialize(_chain()))
    incomplete = {k: v for k, v in _FILES.items() if k != "review.md"}
    result = verify(records, _resolver(incomplete))
    assert not result.ok
    assert "missing from tree" in result.reason


def test_empty_chain_verifies_vacuously() -> None:
    result = verify([], _resolver(_FILES))
    assert result.ok
    assert result.head == GENESIS


# --- filesystem resolver + CLI -----------------------------------------------------------------


def test_filesystem_resolver_reads_from_root(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"hello")
    assert filesystem_resolver(tmp_path)("a.txt") == b"hello"


def _write_tree(root: Path) -> None:
    (root / "src").mkdir(parents=True)
    for path, content in _FILES.items():
        (root / path).write_bytes(content)


def test_cli_build_then_verify_round_trip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_tree(tmp_path)
    manifest = {
        "stages": [
            {"stage": "spec", "agent": "spec@agent-forge.bot", "model": "opus",
             "outputs": ["spec.md"], "gate_verdict": "SPEC: OK"},
            {"stage": "implementer", "agent": "implementer@agent-forge.bot", "model": "sonnet",
             "inputs": ["spec.md"], "outputs": ["src/ledger.py"]},
        ]
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    chain_path = tmp_path / "chain.ndjson"

    build_code = main(
        ["build", "--manifest", str(manifest_path), "--root", str(tmp_path), "--out", str(chain_path)]
    )
    assert build_code == 0
    assert "ATTEST: BUILT" in capsys.readouterr().out
    assert chain_path.exists()

    verify_code = main(["verify", "--chain", str(chain_path), "--root", str(tmp_path)])
    assert verify_code == 0
    assert "ATTEST: PASS" in capsys.readouterr().out


def test_cli_verify_fails_on_tampered_tree(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_tree(tmp_path)
    chain_path = tmp_path / "chain.ndjson"
    chain_path.write_text(serialize(_chain()), encoding="utf-8")

    (tmp_path / "src" / "ledger.py").write_bytes(b"def transfer(): backdoor()")
    code = main(["verify", "--chain", str(chain_path), "--root", str(tmp_path)])
    assert code == 1
    assert "ATTEST: FAIL" in capsys.readouterr().out


def test_chain_verification_render_pass() -> None:
    assert "ATTEST: PASS" in ChainVerification(True, 2, "deadbeef" * 8).render()
