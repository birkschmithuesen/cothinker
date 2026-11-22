"""additional_vocab_for_roles: the custom-dictionary entries built from roles."""
from __future__ import annotations

from shared.speechmatics import BYTES_PER_S, additional_vocab_for_roles, sm_config

ROLES = [
    {"short": "Ferryman", "given": "Ada", "object": "oar"},
    {"short": "Lamplighter", "given": "Bo", "object": "wick"},
    {"short": "Cartographer", "given": "Cy", "object": "compass"},
]


def _contents(vocab):
    return [v["content"] for v in vocab]


def test_bytes_per_s_is_s16le_mono_16k():
    assert BYTES_PER_S == 32000


def test_additional_vocab_for_roles_carries_given_short_and_object():
    contents = _contents(additional_vocab_for_roles(ROLES))
    for name in ("Ada", "Bo", "Cy", "Ferryman", "Lamplighter", "Cartographer",
                 "oar", "wick", "compass"):
        assert name in contents


def test_additional_vocab_for_roles_entries_are_plain_dicts():
    vocab = additional_vocab_for_roles(ROLES)
    assert all(set(v) <= {"content", "sounds_like"} for v in vocab)


def test_additional_vocab_for_roles_sounds_like_from_the_role():
    roles = [{"short": "Ferryman", "given": "Ada", "object": "oar",
              "sounds_like": ["Adah", "Ayda"]}]
    by_content = {v["content"]: v for v in additional_vocab_for_roles(roles)}
    assert by_content["Ada"].get("sounds_like") == ["Adah", "Ayda"]
    assert "sounds_like" not in by_content["Ferryman"]
    assert "sounds_like" not in by_content["oar"]


def test_additional_vocab_for_roles_empty_without_roles():
    assert additional_vocab_for_roles([]) == []


def test_additional_vocab_for_roles_collapses_duplicates_in_order():
    roles = [{"short": "Ferryman", "given": "Ada", "object": "oar"},
             {"short": "Ferryman", "given": "Bo", "object": "oar"}]
    assert _contents(additional_vocab_for_roles(roles)) == ["Ada", "Ferryman", "oar", "Bo"]


def test_additional_vocab_for_roles_skips_blank_and_non_string_values():
    roles = [{"short": " ", "given": None, "object": 3, "sounds_like": [""]}]
    assert additional_vocab_for_roles(roles) == []


def test_sm_config_carries_the_roles_vocab():
    vocab = additional_vocab_for_roles(ROLES)
    cfg = sm_config(None, False, "en", 1.0, 0.5, additional_vocab=vocab)
    assert cfg["transcription_config"]["additional_vocab"] == vocab
    assert "Ada" in _contents(cfg["transcription_config"]["additional_vocab"])
