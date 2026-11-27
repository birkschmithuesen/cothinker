# CoThinker — blocks/mirroring_coach: the mirroring coach block (ADR 0010)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
from blocks.mirroring_coach.coach import MirroringCoach

__all__ = ["MirroringCoach", "make_block"]


def make_block() -> MirroringCoach:
    return MirroringCoach()
