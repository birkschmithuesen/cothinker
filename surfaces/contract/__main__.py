"""Fixture and live mode (ADR 0019 point 2, issue #141):

    python -m surfaces.contract --fixtures meeting
    python -m surfaces.contract --live <session dir>
    python -m surfaces.contract --live-pointer <pointer file>

Both serve the real contract app -- `/docs`, `/snapshot`, `/events`,
`/actions` -- on the same URL paths, over the same models; only the state
source differs. Fixture mode drives it by replaying a fixture scenario
in time-lapse. Live mode reads a running meeting's `buehne.json` and maps
it onto the same models; it is read-only, `gateway.consumer.StageWriter`
stays the single writer. `--live` names a fixed session directory (manual
use). `--live-pointer` instead follows the pointer file the consumer
writes with its current session's path (issue #161): it never guesses a
folder, and switches when the consumer opens a new session, without a
restart of this process.

By default the app answers same-origin requests only, exactly as before
`--cors-origin` existed. Pass `--cors-origin <origin>` (repeatable) to
opt a surface served from another origin (e.g. a local dev server
against `?api=http://127.0.0.1:8000/`) into CORS.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from surfaces.contract.app import create_app
from surfaces.contract.fixtures import load_scenario
from surfaces.contract.live_player import LivePlayer
from surfaces.contract.player import ScenarioPlayer


def build_app(scenario: str, cors_origins: list[str] | None = None) -> FastAPI:
    frames = load_scenario(scenario)
    player = ScenarioPlayer(frames, frames[0].snapshot)
    return create_app(player, cors_origins=cors_origins)


def build_live_app(session_dir: Path | None = None, cors_origins: list[str] | None = None,
                    *, session_pointer: Path | None = None) -> FastAPI:
    player = LivePlayer(session_dir, session_pointer=session_pointer)
    return create_app(player, cors_origins=cors_origins)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m surfaces.contract")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fixtures", metavar="SCENARIO",
                       help="fixture scenario to replay, e.g. 'meeting'")
    mode.add_argument("--live", metavar="SESSION_DIR",
                       help="a fixed session directory to read buehne.json from; "
                            "for manual use -- it never changes while this process runs")
    mode.add_argument("--live-pointer", metavar="POINTER_FILE",
                       help="path to the pointer file the consumer writes its current "
                            "session directory into (gateway.consumer.write_session_pointer); "
                            "re-read on change, so this follows the consumer's session "
                            "instead of guessing a folder, and switches without a restart")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--cors-origin", action="append", dest="cors_origins", metavar="ORIGIN",
                         help="allow this origin via CORS (repeatable); default: same-origin only")
    args = parser.parse_args(argv)

    if args.fixtures:
        app = build_app(args.fixtures, cors_origins=args.cors_origins)
    elif args.live:
        app = build_live_app(Path(args.live), cors_origins=args.cors_origins)
    else:
        app = build_live_app(session_pointer=Path(args.live_pointer), cors_origins=args.cors_origins)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
