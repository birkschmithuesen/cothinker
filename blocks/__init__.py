# CoThinker — blocks/: building blocks that plug into the block host
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Building blocks (ADR 0008): each one its own `blocks.<id>` package,
implementing the `Block` contract of `shared/blocks.py`. A block may import
`shared/` and its own package, nothing else under `blocks/`."""
