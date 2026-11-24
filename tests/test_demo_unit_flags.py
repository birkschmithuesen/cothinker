"""Issue #158: demo/ must start from a clean public-main checkout.

Two checks, run against every demo systemd unit that starts a Python
program:

1. The unit's entry point must import and run to `--help` without raising
   (a fresh clone's ImportError on a missing module is exactly the bug this
   issue is about).
2. Every `--flag` in the unit's ExecStart must be one the target program's
   argparse actually defines (a unit whose flags drifted from the CLI is
   the other half of the bug).

(2) is implemented by parsing each target's `add_argument(...)` calls with
`ast` rather than by inspecting real `--help` output: `fundusapps.stt_server`
pulls in flask, python-dotenv, vosk and sounddevice at import time (see
`stt/requirements-vserver.txt`), none of which this suite installs -- parsing
the source needs none of that and gives the identical answer everywhere.
"""
import ast
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SYSTEMD_DIR = ROOT / "demo" / "systemd"


def _execstart_tokens(unit_path: Path) -> list:
    """Shell-tokenized ExecStart command, descending into a `/bin/sh -c
    '...'` wrapper. shlex (not a regex over the raw text) is what keeps a
    flag nested inside a quoted value -- e.g. consumer.service's
    `--worker-command "... --hermes-home ... --no-history"` -- from being
    mistaken for a flag passed to the unit's own program."""
    text = unit_path.read_text()
    line = next(l for l in text.splitlines() if l.startswith("ExecStart="))
    tokens = shlex.split(line[len("ExecStart="):])
    if tokens[:2] == ["/bin/sh", "-c"]:
        tokens = shlex.split(tokens[2])
    return tokens


def _flags_in(*paths: Path) -> set:
    """Every `--flag`/`-f` string passed as a literal to `add_argument(...)`
    anywhere in the given source files -- across the main parser, any
    parent parser and every subparser, since a unit's flags can land on
    any of them."""
    flags = set()
    for path in paths:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_argument"):
                for arg in node.args:
                    if (isinstance(arg, ast.Constant)
                            and isinstance(arg.value, str)
                            and arg.value.startswith("-")):
                        flags.add(arg.value)
    return flags


# Each demo unit with a Python entry point, mapped to the source file(s)
# whose `add_argument(...)` calls define its valid flags, and to how to
# run it for the import/`--help` check (cwd matches the unit's own
# WorkingDirectory, so `-m` resolves packages exactly as systemd would).
UNITS = {
    "cothinker-demo-tap.service": {
        "sources": [ROOT / "stt/fundusapps/bbb_tap/__main__.py"],
        "argv": [sys.executable, "-m", "fundusapps.bbb_tap", "--help"],
        "cwd": ROOT / "stt",
    },
    "cothinker-demo-stt.service": {
        "sources": [ROOT / "stt/fundusapps/stt_server/args.py"],
        "argv": [sys.executable, "-m", "fundusapps.stt_server", "--help"],
        "cwd": ROOT / "stt",
    },
    "cothinker-demo-consumer.service": {
        "sources": [ROOT / "gateway/consumer.py"],
        "argv": [sys.executable, "gateway/consumer.py", "--help"],
        "cwd": ROOT,
    },
    "cothinker-demo-stage.service": {
        "sources": [ROOT / "stage/stage.py"],
        "argv": [sys.executable, "stage/stage.py", "--help"],
        "cwd": ROOT,
    },
    "cothinker-demo-contract.service": {
        "sources": [ROOT / "surfaces/contract/__main__.py"],
        "argv": [sys.executable, "-m", "surfaces.contract", "--help"],
        "cwd": ROOT,
    },
    "cothinker-demo-contract-fixtures.service": {
        "sources": [ROOT / "surfaces/contract/__main__.py"],
        "argv": [sys.executable, "-m", "surfaces.contract", "--help"],
        "cwd": ROOT,
    },
}

# Not in UNITS, and not expected to be: chrome.service starts an external
# binary (no Python, no argparse); session.service's ExecStart carries no
# flags at all. Both are still checked below, just not via UNITS.
NON_PYTHON_OR_FLAGLESS_UNITS = {
    "cothinker-demo-chrome.service",
    "cothinker-demo-session.service",
}


def test_every_demo_systemd_unit_is_accounted_for():
    """A new demo unit must be added to UNITS (or the exclusion set above)
    -- otherwise it silently escapes both checks below."""
    all_units = {p.name for p in SYSTEMD_DIR.glob("cothinker-demo-*.service")}
    assert all_units == set(UNITS) | NON_PYTHON_OR_FLAGLESS_UNITS


@pytest.mark.parametrize("unit_name", sorted(UNITS))
def test_execstart_flags_are_defined_by_the_targets_argparse(unit_name):
    spec = UNITS[unit_name]
    used = {t for t in _execstart_tokens(SYSTEMD_DIR / unit_name)
            if t.startswith("--")}
    defined = _flags_in(*spec["sources"])
    unknown = used - defined
    assert not unknown, (
        f"{unit_name} passes {sorted(unknown)}, which "
        f"{[str(p.relative_to(ROOT)) for p in spec['sources']]}'s argparse "
        f"does not define"
    )


@pytest.mark.parametrize("unit_name", sorted(UNITS))
def test_demo_unit_entry_point_imports_and_runs_to_help(unit_name):
    spec = UNITS[unit_name]
    result = subprocess.run(spec["argv"], cwd=spec["cwd"],
                             capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (
        f"{unit_name}'s entry point ({' '.join(spec['argv'])}) failed:\n"
        f"{result.stderr}"
    )
    assert "usage" in result.stdout.lower()


def test_chrome_unit_starts_an_external_binary_not_python():
    text = (SYSTEMD_DIR / "cothinker-demo-chrome.service").read_text()
    assert "ExecStart=/usr/bin/google-chrome" in text


def test_session_unit_has_no_cli_flags_to_validate():
    tokens = _execstart_tokens(SYSTEMD_DIR / "cothinker-demo-session.service")
    assert not any(t.startswith("--") for t in tokens)


def test_session_server_module_imports():
    # Plain stdlib (http.server); the real server start is guarded behind
    # `if __name__ == "__main__":`, so importing it is side-effect-free.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "cothinker_demo_session_server", ROOT / "demo/session_server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


def test_shared_speechmatics_imports_without_the_private_workshop_package():
    # workshop/ (a private fork module) is not shipped in this public repo.
    import shared.speechmatics  # noqa: F401
