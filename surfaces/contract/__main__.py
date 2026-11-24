"""Fixture and live mode (ADR 0019 point 2, issue #141):

    python -m surfaces.contract --fixtures meeting
    python -m surfaces.contract --live <session dir>

Both serve the real contract app -- `/docs`, `/snapshot`, `/events`,
`/actions` -- on the same URL paths, over the same models; only the state
source differs. Fixture mode drives it by replaying a fixture scenario
in time-lapse. Live mode reads a running meeting's `buehne.json` from
the given session directory and maps it onto the same models; it is
read-only, `gateway.consumer.StageWriter` stays the single writer.
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


def build_app(scenario: str) -> FastAPI:
    frames = load_scenario(scenario)
    player = ScenarioPlayer(frames, frames[0].snapshot)
    return create_app(player)


def build_live_app(session_dir: Path) -> FastAPI:
    player = LivePlayer(session_dir)
    return create_app(player)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m surfaces.contract")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fixtures", metavar="SCENARIO",
                       help="fixture scenario to replay, e.g. 'meeting'")
    mode.add_argument("--live", metavar="SESSION_DIR",
                       help="session directory to read buehne.json from")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    app = build_app(args.fixtures) if args.fixtures else build_live_app(Path(args.live))
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
