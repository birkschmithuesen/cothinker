"""Instance-level STT vocabulary (issue #204).

A private instance repo (`docs/instance-format.md`) can commit
`content/stt_vocab.txt` to give the STT engine its own project terms and
names, without the operator typing `--vocab` by hand every start. One
entry per line, in the same `word[:sounds_like,...]` syntax
`speechmatics_rt_backend.parse_vocab` already defines for `--vocab` --
reusing that syntax rather than inventing a second one for the same
concept. Blank lines and lines starting with `#` are ignored.

The file is entirely optional: no instance directory, or an instance
directory without this file, is the normal case and simply yields no
extra entries.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

VOCAB_FILENAME = "stt_vocab.txt"


def load_vocab_file(instance_dir: Optional[Union[str, Path]]) -> list[str]:
    """Raw `word[:sounds_like,...]` lines from `<instance_dir>/content/
    stt_vocab.txt`, or `[]` if `instance_dir` is falsy or the file does
    not exist."""
    if not instance_dir:
        return []
    path = Path(instance_dir) / "content" / VOCAB_FILENAME
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        entries.append(line)
    return entries
