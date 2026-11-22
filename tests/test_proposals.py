"""Reading the agent's proposal file. Foreign input, read defensively."""
from __future__ import annotations

import atexit
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gateway"))
import pytest  # noqa: E402
from script_checks import check, collected, done  # noqa: E402
from proposals import read_proposals  # noqa: E402

tmp = Path(tempfile.mkdtemp())
atexit.register(shutil.rmtree, tmp, ignore_errors=True)
f = tmp / "vorschlag.jsonl"

print("a missing file is not an error")
actions, offset, bad = read_proposals(f, 0)
check("no actions", actions == [])
check("offset stays at zero", offset == 0)
check("nothing discarded", bad == 0)

print("\nreads whole lines")
f.write_text('{"action":"mirror","text":"Robo hört mit."}\n'
             '{"action":"decision","text":"kein Pitch","time":"19:43"}\n',
             encoding="utf-8")
actions, offset, bad = read_proposals(f, 0)
check("two actions", len(actions) == 2, str(len(actions)))
check("order is preserved", actions[0]["action"] == "mirror")
check("umlauts survive", actions[0]["text"] == "Robo hört mit.")
check("offset advanced", offset == f.stat().st_size)
check("nothing discarded", bad == 0)

print("\nonly what was appended since the last read")
with f.open("a", encoding="utf-8") as fh:
    fh.write('{"action":"none"}\n')
actions, offset2, bad = read_proposals(f, offset)
check("only the new line", len(actions) == 1 and actions[0]["action"] == "none")
check("offset advanced again", offset2 > offset)

print("\nbroken lines are discarded, the rest survives")
f.write_text('{"action":"mirror","text":"a"}\n'
             'this is not json\n'
             '[1,2,3]\n'
             '"just a string"\n'
             '{"action":"none"}\n', encoding="utf-8")
actions, offset, bad = read_proposals(f, 0)
check("the two good ones came through", len(actions) == 2, str(len(actions)))
check("three were discarded", bad == 3, str(bad))

print("\na half-written last line is not consumed")
f.write_text('{"action":"none"}\n{"action":"mir', encoding="utf-8")
actions, offset, bad = read_proposals(f, 0)
check("only the complete line", len(actions) == 1, str(len(actions)))
check("nothing counted as broken", bad == 0, str(bad))
check("the offset stops before the fragment",
      offset == len('{"action":"none"}\n'.encode("utf-8")), str(offset))
rest_actions, _, _ = read_proposals(f, offset)
check("the fragment is not read either", rest_actions == [])

print("\na truncated file resets the offset")
f.write_text('{"action":"none"}\n', encoding="utf-8")
actions, offset, bad = read_proposals(f, 10_000)
check("read from the start again", len(actions) == 1, str(len(actions)))

print("\ninvalid bytes do not raise")
f.write_bytes(b'{"action":"mirror","text":"\xff\xfe"}\n{"action":"none"}\n')
actions, offset, bad = read_proposals(f, 0)
check("the readable line came through",
      any(a["action"] == "none" for a in actions), str(actions))

print("\nunicode line separators inside a JSON string are not record separators")
f.write_text(
    '{"action":"mirror","text":"a b c\x85d"}\n',
    encoding="utf-8",
)
actions, offset, bad = read_proposals(f, 0)
check("one action came through", len(actions) == 1, str(len(actions)))
check("the separators survive in the value",
      actions and actions[0]["text"] == "a b c\x85d",
      str(actions))
check("nothing discarded", bad == 0, str(bad))

print("\nblank and whitespace-only lines between records are skipped")
f.write_text(
    '{"action":"mirror","text":"a"}\n'
    '\n'
    '   \n'
    '{"action":"none"}\n',
    encoding="utf-8",
)
actions, offset, bad = read_proposals(f, 0)
check("both records came through", len(actions) == 2, str(len(actions)))
check("nothing discarded", bad == 0, str(bad))

print("\nCRLF line endings are parsed correctly")
f.write_bytes(
    b'{"action":"mirror","text":"a"}\r\n'
    b'{"action":"none"}\r\n'
)
actions, offset, bad = read_proposals(f, 0)
check("both records came through", len(actions) == 2, str(len(actions)))
check("nothing discarded", bad == 0, str(bad))

print("\na path that exists but cannot be opened as a file is not an error")
actions, offset, bad = read_proposals(tmp, 0)
check("no actions", actions == [])
check("offset unchanged", offset == 0)
check("nothing discarded", bad == 0)

done()


@pytest.mark.parametrize("name,ok,info", collected())
def test_check(name, ok, info):
    assert ok, info
