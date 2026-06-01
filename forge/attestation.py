"""Tamper-evident attestation chain — provenance you cannot fake with one line of git config.

The problem this fixes
----------------------
agent-forge's whole thesis is "the process is legible and *falsifiable*". But the v0.1 provenance
check (`hooks/audit_provenance.py`) only asserts that a commit's author email ends in
``@agent-forge.bot``. That is a *convention*, and a convention is spoofable in one line::

    git config user.email implementer@agent-forge.bot   # now "the agent wrote it"

A skeptical reviewer sees through that immediately. "Falsifiable" has to mean *a forgery is
detectable*, not *a label is present*.

The fix (supply-chain attestation, applied to agents)
-----------------------------------------------------
This module brings the discipline of in-toto / SLSA build provenance — standard in software
supply-chain security, absent from the agentic-dev field — to the question "which agent produced
which artifact". Every pipeline stage emits an **attestation** that commits to:

- the stage, the accountable agent identity, and the model,
- the SHA-256 of every artifact it read (inputs) and produced (outputs),
- the gate verdict, and
- the digest of the *previous* attestation.

That last field makes the log a **hash chain** (the same immutable, append-only, tamper-evident
structure the inner project's double-entry ledger demonstrates — the meta-layer now *is* a ledger of
agent actions). Verification re-reads the artifacts from the working tree, recomputes every digest,
and walks the chain. If anyone edits an artifact after the fact, fabricates a stage that never ran,
or reorders the history, the recomputed digest stops matching and verification fails **at the exact
broken link**. No shared secret, no key management: integrity is keyless, anchored by committing the
chain head to git.

This is the difference between "trust me, an agent did it" and "here is a tamper-evident proof; try
to forge it and the chain tells you where you lied".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# 64 zero hex chars: the prev_digest of the genesis attestation. Nothing precedes the first stage.
GENESIS = "0" * 64

# Resolves an artifact path to its current bytes. Injected so tests and the CLI share one verifier.
Resolver = Callable[[str], bytes]


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj: object) -> str:
    """Deterministic JSON: sorted keys, no incidental whitespace. Same input -> same bytes -> same digest."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class ArtifactDigest:
    """A content-addressed reference to one artifact: its path and the SHA-256 of its bytes."""

    path: str
    sha256: str

    @classmethod
    def of(cls, path: str, content: bytes) -> ArtifactDigest:
        return cls(path=path, sha256=sha256_hex(content))

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, data: Mapping[str, str]) -> ArtifactDigest:
        return cls(path=data["path"], sha256=data["sha256"])


@dataclass(frozen=True)
class StageManifest:
    """Declared intent for one stage: which agent/model, which paths it read and produced, verdict.

    Paths only — the digests are computed from the real tree at build time, never hand-written.
    """

    stage: str
    agent: str
    model: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    gate_verdict: str


@dataclass(frozen=True)
class Attestation:
    """One link in the chain. Frozen: an attestation is a fact, not a mutable record."""

    sequence: int
    stage: str
    agent: str
    model: str
    inputs: tuple[ArtifactDigest, ...]
    outputs: tuple[ArtifactDigest, ...]
    gate_verdict: str
    prev_digest: str

    def payload(self) -> dict[str, object]:
        """The signed-over content. Excludes the digest itself (which is derived from this)."""
        return {
            "sequence": self.sequence,
            "stage": self.stage,
            "agent": self.agent,
            "model": self.model,
            "inputs": [d.to_dict() for d in self.inputs],
            "outputs": [d.to_dict() for d in self.outputs],
            "gate_verdict": self.gate_verdict,
            "prev_digest": self.prev_digest,
        }

    def digest(self) -> str:
        """This attestation's identity: the SHA-256 of its canonical payload."""
        return sha256_hex(canonical_json(self.payload()).encode("utf-8"))

    def to_record(self) -> dict[str, object]:
        return {**self.payload(), "digest": self.digest()}

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> Attestation:
        inputs = tuple(ArtifactDigest.from_dict(d) for d in _as_dicts(record["inputs"]))
        outputs = tuple(ArtifactDigest.from_dict(d) for d in _as_dicts(record["outputs"]))
        return cls(
            sequence=int(str(record["sequence"])),
            stage=str(record["stage"]),
            agent=str(record["agent"]),
            model=str(record["model"]),
            inputs=inputs,
            outputs=outputs,
            gate_verdict=str(record["gate_verdict"]),
            prev_digest=str(record["prev_digest"]),
        )


def _as_dicts(value: object) -> list[Mapping[str, str]]:
    if not isinstance(value, list):
        raise TypeError("attestation inputs/outputs must be a list of artifact dicts")
    return [v for v in value if isinstance(v, Mapping)]


def _digests(paths: Sequence[str], resolver: Resolver) -> tuple[ArtifactDigest, ...]:
    return tuple(ArtifactDigest.of(path, resolver(path)) for path in paths)


def build_chain(manifests: Sequence[StageManifest], resolver: Resolver) -> tuple[Attestation, ...]:
    """Turn declared stage manifests into a hash-linked attestation chain, digesting the real tree."""
    attestations: list[Attestation] = []
    prev = GENESIS
    for index, manifest in enumerate(manifests):
        attestation = Attestation(
            sequence=index,
            stage=manifest.stage,
            agent=manifest.agent,
            model=manifest.model,
            inputs=_digests(manifest.inputs, resolver),
            outputs=_digests(manifest.outputs, resolver),
            gate_verdict=manifest.gate_verdict,
            prev_digest=prev,
        )
        attestations.append(attestation)
        prev = attestation.digest()
    return tuple(attestations)


def chain_head(attestations: Sequence[Attestation]) -> str:
    """The digest that anchors the whole history. Commit this to git and the past is frozen."""
    return attestations[-1].digest() if attestations else GENESIS


def serialize(attestations: Sequence[Attestation]) -> str:
    return "\n".join(canonical_json(a.to_record()) for a in attestations) + "\n"


def parse(text: str) -> tuple[dict[str, object], ...]:
    return tuple(json.loads(line) for line in text.splitlines() if line.strip())


@dataclass(frozen=True)
class ChainVerification:
    """Verdict of walking a chain. ``ok`` is the gate; ``broken_sequence`` + ``reason`` localise a forgery."""

    ok: bool
    length: int
    head: str
    broken_sequence: int | None = None
    reason: str = ""

    def render(self) -> str:
        if self.ok:
            return f"ATTEST: PASS — {self.length} links intact, head {self.head[:12]}…"
        return f"ATTEST: FAIL — link {self.broken_sequence}: {self.reason}"


def verify(records: Iterable[Mapping[str, object]], resolver: Resolver) -> ChainVerification:
    """Walk the chain, recomputing every digest from first principles. Fail at the first forged link.

    Detects: artifact tampering (a file changed after attestation), record tampering (any field
    altered), and reordering / insertion / deletion (a broken prev_digest link).
    """
    items = list(records)
    prev_digest = GENESIS
    head = GENESIS

    for position, record in enumerate(items):
        attestation = Attestation.from_record(record)
        recomputed = attestation.digest()
        stored = str(record.get("digest", ""))

        if recomputed != stored:
            reason = "record digest does not match its content (tampered field)"
            return ChainVerification(False, len(items), head, position, reason)
        if attestation.prev_digest != prev_digest:
            reason = "broken prev_digest link (reordered, inserted or deleted)"
            return ChainVerification(False, len(items), head, position, reason)

        artifact_break = _verify_artifacts(attestation, resolver)
        if artifact_break is not None:
            return ChainVerification(False, len(items), head, position, artifact_break)

        prev_digest = recomputed
        head = recomputed

    return ChainVerification(True, len(items), head)


def _verify_artifacts(attestation: Attestation, resolver: Resolver) -> str | None:
    for digest in (*attestation.inputs, *attestation.outputs):
        try:
            actual = sha256_hex(resolver(digest.path))
        except FileNotFoundError:
            return f"attested artifact missing from tree: {digest.path}"
        if actual != digest.sha256:
            return f"artifact digest mismatch (tampered after attestation): {digest.path}"
    return None


def filesystem_resolver(root: Path) -> Resolver:
    def _read(path: str) -> bytes:
        return (root / path).read_bytes()

    return _read


def _load_manifest(path: Path) -> tuple[StageManifest, ...]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return tuple(
        StageManifest(
            stage=entry["stage"],
            agent=entry["agent"],
            model=entry["model"],
            inputs=tuple(entry.get("inputs", [])),
            outputs=tuple(entry.get("outputs", [])),
            gate_verdict=entry.get("gate_verdict", ""),
        )
        for entry in data["stages"]
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or verify the agent-provenance attestation chain.")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Digest the tree from a manifest and write the chain.")
    build.add_argument("--manifest", type=Path, required=True)
    build.add_argument("--root", type=Path, default=Path("."))
    build.add_argument("--out", type=Path, required=True)

    check = sub.add_parser("verify", help="Walk a chain and re-derive every digest from the tree.")
    check.add_argument("--chain", type=Path, required=True)
    check.add_argument("--root", type=Path, default=Path("."))

    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "build":
        manifests = _load_manifest(args.manifest)
        chain = build_chain(manifests, filesystem_resolver(args.root))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(serialize(chain), encoding="utf-8")
        print(f"ATTEST: BUILT — {len(chain)} links, head {chain_head(chain)[:12]}… -> {args.out}")
        return 0

    result = verify(parse(args.chain.read_text(encoding="utf-8")), filesystem_resolver(args.root))
    print(result.render())
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
