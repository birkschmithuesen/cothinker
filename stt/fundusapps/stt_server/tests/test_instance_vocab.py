"""Instance-level STT vocabulary (issue #204).

Live trial: project terms and the agent's own name were misheard. The
speechmatics-rt backend already has a per-invocation custom dictionary
(`--vocab`, `DEFAULT_VOCAB`); this file covers the missing piece -- a
vocabulary file an instance repo can commit (`content/stt_vocab.txt`,
one `word[:sounds_like,...]` entry per line, same syntax as `--vocab`)
that is read automatically at STT start, merged with the wake name so
the name can never be dropped by a missing/empty/silent instance file.
"""
from types import SimpleNamespace

from fundusapps.stt_server.backends.speechmatics_rt_backend import (
    DEFAULT_VOCAB, SpeechmaticsRtBackend, merge_vocab, parse_vocab,
)
from fundusapps.stt_server.events import EventBus
from fundusapps.stt_server.instance_vocab import VOCAB_FILENAME, load_vocab_file


def test_load_vocab_file_without_an_instance_dir_is_empty():
    assert load_vocab_file(None) == []


def test_load_vocab_file_instance_dir_without_a_file_is_empty(tmp_path):
    assert load_vocab_file(tmp_path) == []


def test_load_vocab_file_reads_entries_and_skips_blanks_and_comments(tmp_path):
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    (content_dir / VOCAB_FILENAME).write_text(
        "# project terms, one per line\n"
        "CoThinker:Co Thinker\n"
        "\n"
        "Fundus\n"
    )
    assert load_vocab_file(tmp_path) == ["CoThinker:Co Thinker", "Fundus"]


def test_merge_vocab_keeps_the_wake_name_when_the_instance_file_is_absent():
    assert merge_vocab(()) == DEFAULT_VOCAB
    assert merge_vocab(None) == DEFAULT_VOCAB


def test_merge_vocab_keeps_the_wake_name_when_entries_do_not_mention_it():
    merged = merge_vocab(["CoThinker:Co Thinker"])
    parsed = parse_vocab(merged)
    assert {"content": "CoThinker", "sounds_like": ["Co Thinker"]} in parsed
    assert {"content": "Robo", "sounds_like": ["Robbo", "Roboh"]} in parsed


def test_merge_vocab_lets_the_instance_override_the_wake_names_own_hints():
    merged = merge_vocab(["Robo:Robo"])
    parsed = parse_vocab(merged)
    robo_entries = [e for e in parsed if e["content"] == "Robo"]
    assert robo_entries == [{"content": "Robo", "sounds_like": ["Robo"]}]


def test_from_args_loads_the_instance_vocabulary_file_at_start(monkeypatch, tmp_path):
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    (content_dir / VOCAB_FILENAME).write_text("CoThinker:Co Thinker\n")
    monkeypatch.setenv("SM_TEST_KEY", "k")
    args = SimpleNamespace(api_key_env="SM_TEST_KEY", language="de", max_delay=0.7,
                           eou_silence=0.5, max_utt=20.0, url="", vocab=None,
                           instance_dir=str(tmp_path))
    be = SpeechmaticsRtBackend.from_args(EventBus(), args)
    try:
        contents = {v["content"] for v in be._additional_vocab}
        assert contents == {"Robo", "CoThinker"}
    finally:
        be.close()


def test_from_args_without_an_instance_dir_still_has_the_wake_name(monkeypatch):
    monkeypatch.setenv("SM_TEST_KEY", "k")
    args = SimpleNamespace(api_key_env="SM_TEST_KEY", language="de", max_delay=0.7,
                           eou_silence=0.5, max_utt=20.0, url="", vocab=None,
                           instance_dir=None)
    be = SpeechmaticsRtBackend.from_args(EventBus(), args)
    try:
        assert {v["content"] for v in be._additional_vocab} == {"Robo"}
    finally:
        be.close()


def test_from_args_cli_vocab_still_overrides_the_instance_file(monkeypatch, tmp_path):
    """A typed --vocab replaces the default exactly as before (issue #204
    only adds the instance file as a new SOURCE of the default, it does
    not change what --vocab does when the operator types it)."""
    content_dir = tmp_path / "content"
    content_dir.mkdir()
    (content_dir / VOCAB_FILENAME).write_text("CoThinker:Co Thinker\n")
    monkeypatch.setenv("SM_TEST_KEY", "k")
    args = SimpleNamespace(api_key_env="SM_TEST_KEY", language="de", max_delay=0.7,
                           eou_silence=0.5, max_utt=20.0, url="", vocab=["Wiki"],
                           instance_dir=str(tmp_path))
    be = SpeechmaticsRtBackend.from_args(EventBus(), args)
    try:
        assert be._additional_vocab == [{"content": "Wiki"}]
    finally:
        be.close()


def test_instance_dir_cli_flag_is_parsed():
    import sys as _sys
    orig = _sys.argv.copy()
    _sys.argv = ["pytest", "vosk"]
    try:
        from fundusapps.stt_server import args as a
    finally:
        _sys.argv = orig
    ns = a.parser.parse_args(["--instance-dir", "/tmp/some-instance", "speechmatics-rt"])
    assert ns.instance_dir == "/tmp/some-instance"
