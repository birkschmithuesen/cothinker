#!/usr/bin/env python3
# CoThinker - an image on the stage, on request
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""The call lane may answer with a line `BILD: <absolute path>`; the stage
then shows that image large. Shared by the consumer (which validates the
path before the stage state gets an id) and the stage server (which
validates AGAIN before it serves a byte -- a hand-edited buehne.json must
not turn `/image/<id>` into a file reader).

The allow-list is LEXICAL: a path is allowed when it is listed under the
project folder or one of COTHINKER_EXTRA_DIRS. Symlinks are resolved only
to read the file -- the extra dir of a meeting is a folder of symlinks into
the vault, so "where the link points" is not the question, "where it is
listed" is. `..` components are refused outright.

buehne.json carries only `{id, caption}`: the id is a hash of the path,
the path itself never leaves the consumer/stage processes.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Iterable, Optional

IMAGE_EXTS = {".png": "image/png", ".jpg": "image/jpeg",
              ".jpeg": "image/jpeg", ".svg": "image/svg+xml",
              ".webp": "image/webp"}
# Large enough for a slide export, small enough that a mistaken path to a
# video or a dump never goes out over the stage.
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_CAPTION = 160
_MARKER = re.compile(r"^\s*\**\s*BILD\s*:\s*\**\s*(\S.*?)\s*$", re.MULTILINE)
ID_RE = re.compile(r"[0-9a-f]{16}")


def extract_marker(text: str) -> tuple[str, Optional[str]]:
    """`(text without BILD lines, first path or None)`."""
    found = _MARKER.findall(text or "")
    if not found:
        return text, None
    rest = _MARKER.sub("", text)
    rest = re.sub(r"\n{3,}", "\n\n", rest).strip()
    path = found[0].strip().strip("`\"'<>")
    return rest, path


def image_id(path: str | Path) -> str:
    return hashlib.sha256(os.path.normpath(str(path)).encode()).hexdigest()[:16]


def allowed_dirs(project: str | Path, extra: Iterable[str] = ()) -> list[Path]:
    out = []
    for d in [project, *extra]:
        if d:
            out.append(Path(os.path.abspath(os.path.expanduser(str(d)))))
    return out


def validate(path: str, dirs: Iterable[Path]) -> Optional[Path]:
    """The lexical path when it may be shown, else None."""
    if not path or "\x00" in path:
        return None
    p = Path(path).expanduser()
    if not p.is_absolute() or ".." in p.parts:
        return None
    p = Path(os.path.normpath(str(p)))
    if p.suffix.lower() not in IMAGE_EXTS:
        return None
    if not any(p != d and p.is_relative_to(d) for d in dirs):
        return None
    try:
        if not p.is_file() or p.stat().st_size > MAX_IMAGE_BYTES:
            return None
    except OSError:
        return None
    return p


def find_by_id(wanted: str, dirs: Iterable[Path]) -> Optional[Path]:
    """Walk the allowed dirs (links not followed for directories) for the
    image whose id matches. Re-validated before it is returned."""
    if not ID_RE.fullmatch(wanted or ""):
        return None
    dirs = list(dirs)
    for root in dirs:
        for cur, subdirs, files in os.walk(root):
            subdirs[:] = [s for s in subdirs if not s.startswith(".")]
            for name in files:
                if Path(name).suffix.lower() not in IMAGE_EXTS:
                    continue
                cand = Path(cur) / name
                if image_id(cand) == wanted:
                    return validate(str(cand), dirs)
    return None


def caption_for(path: Path, answer: str) -> str:
    first = next((ln.strip() for ln in (answer or "").splitlines()
                  if ln.strip()), "")
    cap = first or path.stem.replace("-", " ").replace("_", " ")
    cap = re.sub(r"[\x00-\x1f\x7f]+", " ", cap).rstrip(" :")
    return cap[:MAX_CAPTION]
