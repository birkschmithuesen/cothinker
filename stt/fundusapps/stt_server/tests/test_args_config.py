"""Where the server's configuration comes from, and in which order.

`args.py` used to read `.env` and nothing else, so the whole suite died
at collection with `KeyError: 'STT_HOST'` on any machine without one --
even though `conftest.py` had been setting those very names in
`os.environ` for exactly this reason. The environment now fills gaps;
`.env` still wins, because on the recording PC it is the only source and
nothing about that may change.
"""
import sys

# args.py runs argparse at import time; mock argv so the import is safe.
_original_argv = sys.argv.copy()
sys.argv = ["pytest", "vosk"]
from fundusapps.stt_server.args import build_config, config  # noqa: E402
sys.argv = _original_argv


def test_the_dotenv_wins_over_the_environment():
    """The recording PC's .env is hand-maintained and authoritative. An
    environment variable left over in a shell must never quietly override
    the file somebody edited on purpose."""
    merged = build_config({"STT_PORT": "9999"}, {"STT_PORT": "5051"})
    assert merged["STT_PORT"] == "5051"


def test_the_environment_only_fills_gaps_and_only_for_our_prefixes():
    merged = build_config(
        {"STT_HOST": "127.0.0.1", "SST_AUDIO_DEVICES": "dummy",
         "PATH": "/nowhere", "HOME": "/nowhere"},
        {"STT_PORT": "5051"},
    )
    assert merged["STT_HOST"] == "127.0.0.1"
    assert merged["SST_AUDIO_DEVICES"] == "dummy"
    assert merged["STT_PORT"] == "5051"
    # Three prefixes, not the whole shell: _redacted_config() prints the
    # key list on every start, and a full environment there would be both
    # noise and a leak risk.
    assert "PATH" not in merged and "HOME" not in merged


def test_the_environment_alone_is_enough_to_start():
    """This is the collection error that used to stop the suite dead."""
    merged = build_config(
        {"STT_HOST": "127.0.0.1", "STT_PORT": "5051",
         "SST_AUDIO_DEVICES": "dummy", "STT_AUDIO_DEVICES_SR": "44100"},
        {},
    )
    assert merged["STT_HOST"] == "127.0.0.1"
    assert merged["STT_AUDIO_DEVICES_SR"] == "44100"
    # And the module really did build itself that way just now.
    assert config["STT_HOST"]
