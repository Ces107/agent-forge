"""Done Gate (free, MIT): a Claude Code hook that blocks "done" without proof.

The free core of Proof-of-Done (agent-forge). The paid Proof-of-Done adds a test-integrity guard
(deleted, skipped or weakened tests, lowered coverage, softened CI): drop its test_integrity.py next to
this file and that check switches on automatically.

One script, registered for three hook events:

  PostToolUse (Edit|Write|MultiEdit|NotebookEdit|Bash)  records code edits and test runs
  PostToolUseFailure (Bash)                             records failed test runs
  Stop                                                  decides whether the agent may finish

The agent may finish only when, after its last code edit, a recognised test command ran and
passed (exit code 0, no failure markers, exit code not masked by a pipe or `|| true`), and the
change does not weaken tests (see test_integrity.py). Otherwise the Stop hook blocks with a
reason that tells the agent exactly what is missing. Sessions without code edits are never blocked.

State lives in the system temp directory, one small JSON file per session. Nothing is sent
anywhere.

Config. A file in the repository can add checks but never waive them, so everything that relaxes the
gate lives in the user's own file, outside any repository: `~/.claude/proof-of-done.json`

  {"max_blocks": 2, "integrity": true,
   "projects": {"~/code/shop": {"test_commands": ["./scripts/run-tests\\.sh"], "ignore_paths": ["docs/"],
                                "allow": ["assertion-weakened:tests/test_legacy.py"]}}}

Top-level keys apply everywhere; keys under "projects" apply in that directory and below. The project's
`.proof-of-done.json` may only make the gate stricter: a higher `max_blocks` (up to 10) and
`"integrity": true`. Any other key in it is ignored.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import test_integrity  # noqa: E402  (Proof-of-Done upgrade: test tampering guard)
except ImportError:  # free edition
    test_integrity = None

TEST_COMMAND = re.compile(
    r"\b(pytest|py\.test|tox|nox|unittest|npm\s+(run\s+)?test|yarn\s+test|pnpm\s+(run\s+)?test|jest|vitest"
    r"|mocha|node\s+--test|go\s+test|cargo\s+test|mvn\b.*\b(test|verify)|gradle\w*\s+.*\b(test|check)"
    r"|dotnet\s+test|rspec|phpunit|make\s+(test|check|verify))\b"
)

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
DOC_SUFFIXES = {".md", ".mdx", ".rst", ".txt", ".adoc", ".png", ".jpg", ".jpeg", ".gif", ".svg"}
DEFAULT_MAX_BLOCKS = 2
MAX_BLOCKS_CEILING = 10  # the most a project file can raise max_blocks to

FILE_MUTATING_SHELL = re.compile(
    r"\bsed\s+(-[a-zA-Z]*i|--in-place)|\bperl\s+-[a-zA-Z]*i|\bgit\s+apply\b|\bpatch\s+-|"
    r">\s*[\w./-]+\.(py|js|ts|tsx|jsx|go|rs|java|kt|cs|rb|php|c|cc|cpp|h|swift|sql)\b"
)
FAILURE_MARKERS = re.compile(
    r"\b[1-9]\d* (failed|errors?)\b|\bFAILED\b|Tests?:\s+[1-9]\d* failed|\bnot ok \d+|ℹ fail [1-9]|"
    r"^--- FAIL|^FAIL\b|BUILD FAILURE|Failures: [1-9]|Errors: [1-9]|test result: FAILED|"
    r"\b[1-9]\d* failing\b|AssertionError|Traceback \(most recent call last\)|panicked at",
    re.MULTILINE,
)
PASS_MARKERS = re.compile(
    r"\b\d+ passed\b|ℹ pass \d+|Tests:\s+\d+ passed|^ok\s+\S+|^PASS\b|BUILD SUCCESS|test result: ok|"
    r"\b\d+ passing\b|All tests passed",
    re.MULTILINE,
)
NO_TESTS_MARKERS = re.compile(r"collected 0 items|no tests ran|No tests found|ℹ tests 0\b|0 passing|Ran 0 tests")
MASKING = re.compile(r"\|\|\s*(true|exit\s+0|:)\b|;\s*exit\s+0\b|--passWithNoTests|set\s+\+e\b")
PIPE_AFTER = re.compile(r"\|(?!\|)\s*(tail|head|grep|less|more|tee|sort|cut|awk|sed|wc|cat)\b")


# ---------------------------------------------------------------------------------------------
# State and config
# ---------------------------------------------------------------------------------------------


def _state_path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "default")[:80]
    directory = Path(tempfile.gettempdir()) / "proof-of-done"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{safe}.json"


def load_state(session_id: str) -> dict:
    path = _state_path(session_id)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"seq": 0, "edits": [], "runs": [], "blocks": 0}


def save_state(session_id: str, state: dict) -> None:
    _state_path(session_id).write_text(json.dumps(state), encoding="utf-8")


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _covers(root: str, here: Path) -> bool:
    try:
        base = Path(os.path.expanduser(root)).resolve()
    except (OSError, ValueError):
        return False
    return here == base or base in here.parents


def load_config(cwd: str) -> dict:
    """User config (may relax the gate) merged with the project config (may only tighten it)."""
    user = _read_json(Path.home() / ".claude" / "proof-of-done.json")
    config = {key: value for key, value in user.items() if key != "projects"}
    here = Path(cwd).resolve()
    projects = user.get("projects")
    for root, overrides in (projects.items() if isinstance(projects, dict) else []):
        if isinstance(overrides, dict) and _covers(root, here):
            config.update(overrides)

    project = _read_json(here / ".proof-of-done.json")
    config["max_blocks"] = max(
        _as_int(config.get("max_blocks"), DEFAULT_MAX_BLOCKS),
        min(_as_int(project.get("max_blocks"), 0), MAX_BLOCKS_CEILING),
    )
    if project.get("integrity") is True:
        config["integrity"] = True
    return config


# ---------------------------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------------------------


def is_test_command(command: str, config: dict) -> bool:
    patterns = [TEST_COMMAND] + [re.compile(p) for p in config.get("test_commands", [])]
    return any(p.search(command) for p in patterns)


def is_code_path(path: str, cwd: str, config: dict) -> bool:
    if not path:
        return False
    try:
        rel = os.path.relpath(path, cwd).replace("\\", "/")
    except ValueError:
        rel = path.replace("\\", "/")
    if rel.startswith("../"):
        return False  # outside the project
    if Path(rel).suffix.lower() in DOC_SUFFIXES:
        return False
    for pattern in config.get("ignore_paths", []):
        if rel.startswith(pattern.rstrip("*")) or fnmatch.fnmatch(rel, pattern):
            return False
    return True


def response_text(response: object) -> str:
    if isinstance(response, dict):
        parts = [str(response.get(key, "")) for key in ("stdout", "stderr", "output", "error")]
        return "\n".join(p for p in parts if p)
    return str(response or "")


def judge_test_run(command: str, output: str, failed: bool) -> tuple[bool, str]:
    """Return (passed, reason). A run counts only when it demonstrably passed."""
    if failed:
        return False, "the test command exited non-zero"
    if MASKING.search(command):
        return False, "the command ignores failures (`|| true`, `exit 0` or `--passWithNoTests`)"
    if NO_TESTS_MARKERS.search(output):
        return False, "no tests were collected or run"
    if FAILURE_MARKERS.search(output):
        return False, "the output reports failing tests"
    if PIPE_AFTER.search(command) and "pipefail" not in command and not PASS_MARKERS.search(output):
        return False, "the output was piped (exit code masked) and shows no passing summary"
    return True, "passed"


def suggest_test_command(cwd: str, state: dict) -> str:
    for run in reversed(state.get("runs", [])):
        return run["command"]
    root = Path(cwd)
    package = root / "package.json"
    if package.exists():
        try:
            if json.loads(package.read_text(encoding="utf-8")).get("scripts", {}).get("test"):
                return "npm test"
        except ValueError:
            pass
        return "node --test"
    if any((root / name).exists() for name in ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini")):
        return "python -m pytest"
    if (root / "go.mod").exists():
        return "go test ./..."
    if (root / "Cargo.toml").exists():
        return "cargo test"
    if (root / "pom.xml").exists():
        return "mvn -q test"
    if any((root / name).exists() for name in ("build.gradle", "build.gradle.kts")):
        return "./gradlew test"
    return "the project's test command"


# ---------------------------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------------------------


def record_tool(event: dict, state: dict, config: dict, failed: bool) -> None:
    tool = event.get("tool_name", "")
    tool_input = event.get("tool_input") or {}
    cwd = event.get("cwd") or os.getcwd()
    if tool in EDIT_TOOLS and not failed:
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        if is_code_path(path, cwd, config):
            state["seq"] += 1
            state["edits"].append({"seq": state["seq"], "path": os.path.relpath(path, cwd) if path else path})
        return
    if tool != "Bash":
        return
    command = str(tool_input.get("command", ""))
    if is_test_command(command, config):
        output = response_text(
            event.get("tool_response") if not failed else event.get("error") or event.get("tool_response")
        )
        passed, reason = judge_test_run(command, output, failed)
        state["seq"] += 1
        state["runs"].append({"seq": state["seq"], "command": command[:300], "passed": passed, "reason": reason})
    elif FILE_MUTATING_SHELL.search(command) and not failed:
        state["seq"] += 1
        state["edits"].append({"seq": state["seq"], "path": f"(shell) {command[:80]}"})


def evaluate_stop(event: dict, state: dict, config: dict) -> str | None:
    """Return a block reason, or None to let the agent finish."""
    cwd = event.get("cwd") or os.getcwd()
    edits = state.get("edits", [])
    if not edits:
        return None
    last_edit = max(edit["seq"] for edit in edits)
    changed = sorted({edit["path"] for edit in edits})
    runs_after = [run for run in state.get("runs", []) if run["seq"] > last_edit]
    command = suggest_test_command(cwd, state)

    if not runs_after:
        listed = ", ".join(changed[:5]) + (f" and {len(changed) - 5} more" if len(changed) > 5 else "")
        return (
            f"Done Gate: you changed {listed} and no test command has run since your last change. "
            f"Run `{command}` (without piping it into tail/grep), read the result, and only then report. "
            "If this project has no tests, say so explicitly in your final message instead of claiming it works."
        )
    last_run = runs_after[-1]
    if not last_run["passed"]:
        return (
            f"Done Gate: the last test run after your changes did not pass ({last_run['reason']}): "
            f"`{last_run['command']}`. Fix the code, not the tests, re-run, and report the real result."
        )

    if test_integrity is not None and config.get("integrity", True):
        try:
            os.chdir(cwd)
            base = test_integrity.resolve_base("auto")
            diff = test_integrity.git("diff", "--unified=3", "--no-color", "--no-ext-diff", base)
        except (OSError, RuntimeError):
            return None  # not a git repository: evidence check above is all we can do
        allowed = set(config.get("allow", []))
        blocking = [
            f
            for f in test_integrity.analyse(test_integrity.parse_unified_diff(diff))
            if f.severity == test_integrity.BLOCK and f"{f.rule}:{f.path}" not in allowed
        ]
        if blocking:
            details = "; ".join(f"{f.rule} at {f.path}:{f.line} ({f.message})" for f in blocking[:5])
            return (
                f"Done Gate: tests pass, but the change weakens the tests: {details}. Restore the "
                "original tests and fix the code instead. If the user explicitly asked for this, tell them "
                "and they can add `rule:path` to `allow` for this project in ~/.claude/proof-of-done.json."
            )
    return None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0  # never break the session on malformed input
    session_id = str(event.get("session_id", "default"))
    cwd = event.get("cwd") or os.getcwd()
    config = load_config(cwd)
    state = load_state(session_id)
    name = event.get("hook_event_name", "")

    if name in ("PostToolUse", "PostToolUseFailure"):
        record_tool(event, state, config, failed=(name == "PostToolUseFailure"))
        save_state(session_id, state)
        return 0

    if name == "Stop":
        reason = evaluate_stop(event, state, config)
        max_blocks = config["max_blocks"]
        if reason and state.get("blocks", 0) < max_blocks:
            state["blocks"] = state.get("blocks", 0) + 1
            save_state(session_id, state)
            print(json.dumps({"decision": "block", "reason": reason}))
            return 0
        if reason:
            print(
                json.dumps(
                    {"systemMessage": f"Done Gate: finished without proof after {max_blocks} reminders. {reason}"}
                )
            )
        state["blocks"] = 0
        save_state(session_id, state)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
