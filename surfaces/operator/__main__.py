"""Fixture and demo-kit mode (issue #159):

    python -m surfaces.operator --fixtures --token-file <path>
    python -m surfaces.operator --demo-kit <demo.sh path> --projects-file <path> --token-file <path>

Mirrors `surfaces/contract/__main__.py`'s `--fixtures`/`--live` split.
The operator token is read from a file, never passed inline: `--token-
file` or the `OPERATOR_TOKEN_FILE` environment variable, whichever is
given; the flag wins if both are. `--projects-file` is a JSON file of
`{"id": ..., "label": ...}` objects -- the allowed working folders, from
deployment config, never a free path.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from surfaces.operator.app import create_app
from surfaces.operator.auth import load_token
from surfaces.operator.demo_backend import DemoKitOperatorBackend
from surfaces.operator.fixtures import FixtureOperatorBackend
from surfaces.operator.models import Project

TOKEN_FILE_ENV_VAR = "OPERATOR_TOKEN_FILE"


def build_fixture_app(token: str, cors_origins: list[str] | None = None) -> FastAPI:
    backend = FixtureOperatorBackend()
    return create_app(backend, token, cors_origins=cors_origins)


def load_projects(path: str | Path) -> list[Project]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Project.model_validate(entry) for entry in data]


def build_demo_kit_app(
    demo_sh: str | Path, projects_file: str | Path, token: str, cors_origins: list[str] | None = None,
) -> FastAPI:
    backend = DemoKitOperatorBackend(demo_sh=demo_sh, projects=load_projects(projects_file))
    return create_app(backend, token, cors_origins=cors_origins)


def _resolve_token(args: argparse.Namespace) -> str | None:
    path = args.token_file or os.environ.get(TOKEN_FILE_ENV_VAR)
    if not path:
        return None
    return load_token(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m surfaces.operator")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fixtures", action="store_true", help="serve the scripted test-mode backend")
    mode.add_argument("--demo-kit", metavar="DEMO_SH", help="path to demo/demo.sh, driven as start|stop|status")
    parser.add_argument("--projects-file", metavar="PATH",
                         help="JSON file of allowed {id, label} working folders (required with --demo-kit)")
    parser.add_argument("--token-file", metavar="PATH",
                         help=f"file holding the operator token (default: ${TOKEN_FILE_ENV_VAR})")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--cors-origin", action="append", dest="cors_origins", metavar="ORIGIN",
                         help="allow this origin via CORS (repeatable); default: same-origin only")
    args = parser.parse_args(argv)

    token = _resolve_token(args)
    if not token:
        print(f"surfaces.operator: no operator token -- pass --token-file or set {TOKEN_FILE_ENV_VAR}",
              file=sys.stderr)
        return 2

    if args.demo_kit:
        if not args.projects_file:
            print("surfaces.operator: --demo-kit requires --projects-file", file=sys.stderr)
            return 2
        app = build_demo_kit_app(args.demo_kit, args.projects_file, token, cors_origins=args.cors_origins)
    else:
        app = build_fixture_app(token, cors_origins=args.cors_origins)

    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
