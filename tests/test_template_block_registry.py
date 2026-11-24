# CoThinker — every shipped template's block ids must resolve in the
# block host's REGISTRY (issue #178)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""A live session silently skips a block the template names but
`gateway/block_host_wiring.py::REGISTRY` never registers -- one log line,
no crash (by design, see that module's docstring), which is exactly how
`timer`, `speaker-queue` and `speaking-share` went live as permanent
"null" surface fields (#178) without a single test turning red.

Only templates `shared.templates.load_template` can load WITHOUT naming a
format are checked: those are the ones `block_host_wiring.py` actually
serves (`REFLECT_TEMPLATE`, loaded with no format). A template behind
`[formats]` (`templates/inscribe`) needs a format picked to load at all
and is not this host's business today -- same exclusion
`tests/test_components_have_tests.py` already makes for it (issue #153).

    python3 -m pytest -q tests/test_template_block_registry.py
"""
from __future__ import annotations

from pathlib import Path

import pytest

from gateway import block_host_wiring as bhw
from shared.templates import TemplateError, load_template

REPO = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO / "templates"


def _format_free_template_paths() -> list[Path]:
    paths = []
    for path in sorted(TEMPLATES_DIR.glob("*/template.toml")):
        try:
            load_template(path)
        except TemplateError:
            continue  # needs a format named -- not this host's template
        paths.append(path)
    return paths


FORMAT_FREE_TEMPLATES = _format_free_template_paths()


def test_at_least_one_format_free_template_is_checked():
    """A scanner that silently finds nothing checks nothing -- the Reflect
    template must actually be among the paths below."""
    assert REPO / "templates" / "reflect" / "template.toml" in FORMAT_FREE_TEMPLATES


@pytest.mark.parametrize("template_path", FORMAT_FREE_TEMPLATES,
                         ids=[p.relative_to(REPO).as_posix() for p in FORMAT_FREE_TEMPLATES])
def test_every_block_id_resolves_in_the_registry(template_path):
    template = load_template(template_path)
    missing = sorted({b.id for b in template.blocks} - set(bhw.REGISTRY))
    assert missing == []
