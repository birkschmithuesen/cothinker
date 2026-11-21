# CoThinker — shared/instance.py: no default instance, fail closed (repo split 3.10)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""With no explicit path and no COTHINKER_INSTANCE_DIR, shared.instance.load()
must raise InstanceError -- there is no default instance (ADR 0001).

python3 -m pytest -q tests/test_no_default_instance.py
"""
from __future__ import annotations

import pytest

from shared import instance as si


def test_load_with_nothing_set_raises_instance_error_naming_the_flag_and_env_var(monkeypatch):
    monkeypatch.delenv(si.ENV_DIR, raising=False)
    with pytest.raises(si.InstanceError) as excinfo:
        si.load()
    assert "--instance" in str(excinfo.value)
    assert si.ENV_DIR in str(excinfo.value)
