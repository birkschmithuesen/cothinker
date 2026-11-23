"""Fixture mode (ADR 0019 point 2):

    python -m surfaces.contract --fixtures meeting

Serves the real contract app -- `/docs`, `/snapshot`, `/events`, `/actions`
-- with its state driven by replaying a fixture scenario in time-lapse,
never by a separate simulation.
"""
from __future__ import annotations

import argparse

import uvicorn
from fastapi import FastAPI

from surfaces.contract.app import create_app
from surfaces.contract.fixtures import load_scenario
from surfaces.contract.player import ScenarioPlayer


def build_app(scenario: str) -> FastAPI:
    frames = load_scenario(scenario)
    player = ScenarioPlayer(frames, frames[0].snapshot)
    return create_app(player)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m surfaces.contract")
    parser.add_argument("--fixtures", required=True, metavar="SCENARIO",
                         help="fixture scenario to replay, e.g. 'meeting'")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    app = build_app(args.fixtures)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
