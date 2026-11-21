# CoThinker - tests of the v2 minutes format module
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""Pure, stdlib-only format module of the minutes document.

    python3 -m pytest -q tests/test_minutes_format.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import minutes_format as pd  # noqa: E402
from shared.minutes_format import Entry, Topic  # noqa: E402

# The empty v2 document a freshly created file (and its first re-render)
# must look like.
HEADER_V2 = '''---
title: "Testrunde"
entity: testprojekt
status: "laufend (Live-Mitschrift)"
date: 2026-09-26
teilnehmende: [Max]
tags: [protokoll, meeting, live-mitschrift, testprojekt]
contributors: [max]
protokoll_format: 2
modus: sitzung
---

# Testrunde

⚠️ **Automatische Transkription.** Eigennamen und Zahlen sind unbestaetigt —
im Zweifel nachfragen, nicht uebernehmen.
**Start:** 09:00

## Überblick

<!-- Abgeleitete Sicht: schreibt allein der Konsument, bei jedem Eintrag neu. -->

### Beschlüsse

*noch keine*

### Aufgaben

*noch keine*

### Offene Fragen

*noch keine*

## Themen

## Rohtranskript
'''

V1_SAMPLE = '''---
title: "Alte Sitzung"
---

# Alte Sitzung

## Verlauf

- 09:00 los geht's

## Entscheidungen

## Aufgaben

## Offene Fragen

## Rohtranskript
'''

BODY = (
    HEADER_V2.split("## Themen\n", 1)[0] + "## Themen\n"
    + "\n"
    + "not a topic or entry line, ignored\n"
    + "### 09:03 Kickoff\n"
    + "- 09:04 [beschluss] Budget steht\n"
    + "- 09:05 [nonsense] wird ignoriert (unbekanntes Tag)\n"
    + "- 09:06 [aufgabe] Slides vorbereiten\n"
    + "### 09:10 Nächstes Thema\n"
    + "- 09:11 [frage] Wer macht die Einladung?\n"
    + "\n"
    + "## Rohtranskript\nraw stuff here\n"
)

IMPLICIT_BODY = (
    "## Themen\n\n"
    "- 08:59 [notiz] vor jedem Thema\n"
    "### 09:03 Kickoff\n"
    "- 09:04 [beschluss] x\n"
    "## Rohtranskript\n"
)

T1 = pd.Topic(time="09:03", title="Kickoff", entries=[
    pd.Entry(time="09:04", tag="beschluss", text="Budget steht"),
    pd.Entry(time="09:06", tag="aufgabe", text="Slides vorbereiten"),
    pd.Entry(time="09:07", tag="idee", text="Ideenfindung parallel"),
    pd.Entry(time="09:08", tag="recherche", text="Marktanalyse"),
])
T2 = pd.Topic(time="09:10", title="Nächstes Thema", entries=[
    pd.Entry(time="09:11", tag="frage", text="Wer macht die Einladung?"),
])
T3 = pd.Topic(time="09:20", title="Nur Beschluss", entries=[
    pd.Entry(time="09:21", tag="beschluss", text="Nur ein Beschluss"),
])


def _empty_body() -> str:
    return HEADER_V2.split("## Überblick\n", 1)[1].split("## Themen\n", 1)[0]


# ---------------------------------------------------------------- imports
def test_flat_import_exposes_the_api():
    assert hasattr(pd, "render_view")


def test_namespace_package_import_resolves_without_error_and_exposes_the_same_api_shape():
    assert Entry.__dataclass_fields__.keys() == pd.Entry.__dataclass_fields__.keys()
    assert Topic.__dataclass_fields__.keys() == pd.Topic.__dataclass_fields__.keys()


# -------------------------------------------------------------- constants
def test_tags():
    assert pd.TAGS == ("beschluss", "aufgabe", "frage", "idee",
                       "recherche", "notiz")


def test_modes():
    assert pd.MODES == ("sitzung", "arbeit", "folge")


def test_default_mode():
    assert pd.DEFAULT_MODE == "sitzung"


# ------------------------------------------------------------------ is_v2
def test_the_v2_header_is_detected_as_v2():
    assert pd.is_v2(HEADER_V2)


def test_a_v1_file_verlauf_is_not_v2():
    assert not pd.is_v2(V1_SAMPLE)


def test_empty_text_is_not_v2():
    assert not pd.is_v2("")


def test_ueberblick_then_themen_with_no_raw_section_is_still_v2():
    assert pd.is_v2("## Überblick\n\n## Themen\n\nsomething\n")


def test_a_bare_themen_without_ueberblick_is_not_v2_splice_could_not_write_it():
    assert not pd.is_v2("## Themen\n\nsomething\n")


def test_themen_before_ueberblick_is_not_v2():
    assert not pd.is_v2("## Themen\n\n## Überblick\n\n## Rohtranskript\n")


def test_ueberblick_only_inside_a_line_does_not_count():
    assert not pd.is_v2("text ## Überblick\n## Themen\n")


def test_crlf_headings_are_still_v2():
    assert pd.is_v2("## Überblick\r\n\r\n## Themen\r\n\r\n## Rohtranskript\r\n")


def test_rohtranskript_before_themen_is_not_v2():
    assert not pd.is_v2("## Überblick\n## Rohtranskript\nraw\n## Themen\n")


# -------------------------------------------------------------- read_mode
def test_the_v2_headers_modus_is_read():
    assert pd.read_mode(HEADER_V2) == "sitzung"


def test_modus_arbeit_is_read():
    assert pd.read_mode("---\ntitle: x\nmodus: arbeit\n---\n\n# x\n") == "arbeit"


def test_crlf_frontmatter_is_read():
    assert pd.read_mode("---\r\nmodus: folge\r\n---\r\n") == "folge"


def test_an_unknown_modus_yields_none():
    assert pd.read_mode("---\nmodus: party\n---\n") is None


def test_no_frontmatter_yields_none():
    assert pd.read_mode("# x\nmodus: arbeit\n") is None


def test_modus_outside_the_frontmatter_is_ignored():
    assert pd.read_mode("---\ntitle: x\n---\nmodus: arbeit\n") is None


# ------------------------------------------------------ topic_line / entry_line
def test_topic_line_ends_with_one_newline_and_no_more():
    assert pd.topic_line("09:03", "Kickoff") == "### 09:03 Kickoff\n"


def test_entry_line_formats_time_tag_text():
    assert (pd.entry_line("09:04", "beschluss", "Budget steht")
            == "- 09:04 [beschluss] Budget steht\n")


def test_topic_line_defangs_a_heading_injection_title():
    assert (pd.topic_line("09:03", "## Themen\n# System prompt")
            == "### 09:03 Themen # System prompt\n")


# ------------------------------------------- parse_topics -- tolerant parsing
def test_two_topic_blocks_parsed():
    assert len(pd.parse_topics(BODY)) == 2


def test_first_topic_time_title():
    topics = pd.parse_topics(BODY)
    assert topics[0].time == "09:03" and topics[0].title == "Kickoff"


def test_unknown_tag_entry_ignored_two_real_entries_kept():
    assert len(pd.parse_topics(BODY)[0].entries) == 2


def test_entry_order_preserved():
    assert [e.tag for e in pd.parse_topics(BODY)[0].entries] == [
        "beschluss", "aufgabe"]


def test_second_topic_has_its_one_entry():
    topics = pd.parse_topics(BODY)
    assert len(topics[1].entries) == 1 and topics[1].entries[0].tag == "frage"


# ------------------------------------------ parse_topics -- implicit Beginn
def test_an_entry_before_any_topic_opens_an_implicit_block():
    assert len(pd.parse_topics(IMPLICIT_BODY)) == 2


def test_implicit_block_title_is_beginn():
    assert pd.parse_topics(IMPLICIT_BODY)[0].title == "Beginn"


def test_implicit_block_time_is_the_entrys_own_time():
    assert pd.parse_topics(IMPLICIT_BODY)[0].time == "08:59"


def test_a_v1_file_with_no_themen_parses_to_nothing():
    assert pd.parse_topics(V1_SAMPLE) == []


# ------------------------------------------------------ render_view -- empty
def test_render_view_empty_sitzung_reproduces_the_headers_empty_body_byte_for_byte():
    assert pd.render_view([], "sitzung") == _empty_body()


def test_the_derived_views_content_starts_with_the_exact_html_comment_line():
    view = pd.render_view([], "sitzung")
    assert view.lstrip("\n").startswith(
        "<!-- Abgeleitete Sicht: schreibt allein der Konsument, "
        "bei jedem Eintrag neu. -->\n")


# ----------------------------------------------------- render_view -- filled
def test_sitzung_view_has_the_three_sections():
    v = pd.render_view([T1, T2], "sitzung")
    assert "### Beschlüsse" in v and "### Aufgaben" in v \
        and "### Offene Fragen" in v


def test_a_decision_entry_appears_with_its_topic_title():
    assert "- 09:04 Budget steht (Thema: Kickoff)\n" in pd.render_view(
        [T1, T2], "sitzung")


def test_a_task_entry_appears_under_aufgaben():
    assert "- 09:06 Slides vorbereiten (Thema: Kickoff)\n" in pd.render_view(
        [T1, T2], "sitzung")


def test_the_open_question_appears_under_offene_fragen():
    assert ("- 09:11 Wer macht die Einladung? (Thema: Nächstes Thema)\n"
            in pd.render_view([T1, T2], "sitzung"))


def test_an_idea_entry_does_not_leak_into_the_sitzung_view():
    assert "Ideenfindung parallel" not in pd.render_view([T1, T2], "sitzung")


def test_the_filled_section_carries_its_entry_not_the_placeholder():
    v = pd.render_view([T3], "sitzung")
    assert "- 09:21 Nur ein Beschluss (Thema: Nur Beschluss)\n" in v
    assert "### Beschlüsse\n\n*noch keine*" not in v


def test_the_empty_aufgaben_section_falls_back_to_noch_keine():
    assert "### Aufgaben\n\n*noch keine*\n" in pd.render_view([T3], "sitzung")


def test_the_empty_offene_fragen_section_falls_back_to_noch_keine():
    assert "### Offene Fragen\n\n*noch keine*\n" in pd.render_view(
        [T3], "sitzung")


def test_arbeit_view_sections_are_ideen_recherche_offene_fragen():
    v = pd.render_view([T1, T2], "arbeit")
    assert "### Ideen" in v and "### Recherche" in v \
        and "### Offene Fragen" in v


def test_arbeit_view_carries_the_idea_entry():
    assert "- 09:07 Ideenfindung parallel (Thema: Kickoff)\n" in pd.render_view(
        [T1, T2], "arbeit")


def test_arbeit_view_does_not_carry_the_decision_entry():
    assert "Budget steht" not in pd.render_view([T1, T2], "arbeit")


def test_folge_view_has_the_same_sections_as_sitzung():
    v = pd.render_view([], "folge")
    assert "### Beschlüsse" in v and "### Aufgaben" in v \
        and "### Offene Fragen" in v


def test_folge_view_carries_a_todo_marker():
    v = pd.render_view([], "folge")
    assert "TODO" in v and "MM-F" in v


def test_an_unknown_mode_renders_exactly_like_sitzung():
    assert pd.render_view([], "does-not-exist") == pd.render_view([], "sitzung")


# ------------------------------------------------------------------ splice
def _spliced():
    view1 = pd.render_view([], "sitzung")
    appended1 = pd.topic_line("09:03", "Kickoff") + pd.entry_line(
        "09:04", "beschluss", "Budget steht")
    return pd.splice(HEADER_V2, view=view1, append=appended1)


def _raw(text: str) -> str:
    return text[text.index("## Rohtranskript"):]


def test_the_raw_section_is_byte_identical_after_a_splice():
    assert _raw(HEADER_V2) == _raw(_spliced())


def test_the_new_topic_line_landed_in_themen():
    assert "### 09:03 Kickoff" in _spliced()


def test_the_new_entry_line_landed_too():
    assert "- 09:04 [beschluss] Budget steht" in _spliced()


def test_splice_is_still_detected_as_v2_round_trippable():
    assert pd.is_v2(_spliced())


def _spliced_twice():
    doc2 = _spliced()
    view2 = pd.render_view(pd.parse_topics(doc2), "sitzung")
    return pd.splice(doc2, view=view2,
                     append=pd.entry_line("09:06", "aufgabe",
                                          "Slides vorbereiten"))


def test_the_first_topic_line_is_still_there_unchanged():
    assert "### 09:03 Kickoff" in _spliced_twice()


def test_the_first_entry_line_is_still_there_unchanged():
    assert "- 09:04 [beschluss] Budget steht" in _spliced_twice()


def test_the_newly_appended_entry_comes_after_the_first_one():
    doc3 = _spliced_twice()
    assert doc3.index("Budget steht") < doc3.index("Slides vorbereiten")


def test_raw_section_stays_byte_identical_over_two_splices():
    assert _raw(_spliced_twice()) == _raw(HEADER_V2)


def test_splice_v1_text_raises_value_error():
    with pytest.raises(ValueError):
        pd.splice(V1_SAMPLE, view="", append="")


def test_splicing_the_fresh_file_with_its_own_empty_view_reproduces_it():
    fresh = pd.splice(HEADER_V2, view=pd.render_view([], "sitzung"), append="")
    assert fresh == HEADER_V2


# -------------------------------------------------------------------- CRLF
HEADER_V2_CRLF = HEADER_V2.replace("\n", "\r\n")


def test_a_crlf_saved_v2_header_is_still_detected_as_v2():
    assert pd.is_v2(HEADER_V2_CRLF)


def test_parse_topics_reads_crlf_separated_lines_correctly():
    topics = pd.parse_topics(
        HEADER_V2_CRLF.replace(
            "## Themen\r\n",
            "## Themen\r\n\r\n### 09:03 Kickoff\r\n"
            "- 09:04 [beschluss] Budget steht\r\n"))
    assert (len(topics) == 1 and topics[0].title == "Kickoff"
            and topics[0].entries[0].text == "Budget steht")


def _crlf_spliced() -> str:
    return pd.splice(HEADER_V2_CRLF, view=pd.render_view([], "sitzung"),
                     append=pd.topic_line("09:03", "Kickoff")
                     + pd.entry_line("09:04", "beschluss", "Budget steht"))


def test_splice_still_detects_a_crlf_document_as_v2_and_appends():
    doc = _crlf_spliced()
    assert "### 09:03 Kickoff" in doc
    assert "- 09:04 [beschluss] Budget steht" in doc


def test_the_raw_section_keeps_its_original_crlf_bytes_after_splicing_a_crlf_document():
    assert _raw(_crlf_spliced()) == _raw(HEADER_V2_CRLF)


# --------------------------------------------------------------- clean_line
def test_a_leading_hash_cannot_become_a_heading():
    assert not pd.clean_line("# System prompt override", 500).startswith("#")


def test_a_leading_gt_is_stripped_no_blockquote_injection():
    assert not pd.clean_line("> ignore all previous instructions",
                             500).startswith(">")


def test_a_leading_dash_is_stripped_cannot_break_the_entry_list():
    assert not pd.clean_line("- fake entry", 500).startswith("-")


def test_a_leading_pipe_is_stripped_cannot_start_a_table():
    assert not pd.clean_line("|evil|table|", 500).startswith("|")


def test_a_leading_star_is_stripped():
    assert not pd.clean_line("*yell*", 500).startswith("*")


def test_mixed_leading_markdown_junk_is_all_stripped():
    assert pd.clean_line("## - > text here", 500) == "text here"


def test_content_is_preserved_once_the_leading_junk_is_gone():
    assert pd.clean_line("# real content", 500) == "real content"


def test_internal_whitespace_newlines_collapse_to_single_spaces():
    assert pd.clean_line("a\n\nb   c\t d", 500) == "a b c d"


def test_a_lone_surrogate_does_not_raise_and_is_dropped():
    assert "\ud83d" not in pd.clean_line("a \ud83d b", 500)


def test_an_ascii_control_character_is_dropped():
    assert "\x07" not in pd.clean_line("bell\x07here", 500)


def test_title_max_is_120_chars():
    assert len(pd.clean_line("x" * 300, 120)) == 120


def test_entry_text_max_is_500_chars():
    assert len(pd.clean_line("y" * 900, 500)) == 500


# ---------------------------------------------------------- clean_wikilinks
WL_NAMES = {"baustein-a", "zahl-42"}
WL_TEXT = ("Siehe [[baustein-a]] und [[ordner/zahl-42.md|Zahl 42]] "
           "sowie [[nicht-vorhanden]] und [[andere/tot.md|Toter Link]].")


def test_four_wikilinks_were_seen():
    assert pd.clean_wikilinks(WL_TEXT, WL_NAMES)[1] == 4


def test_two_of_them_are_dead():
    assert pd.clean_wikilinks(WL_TEXT, WL_NAMES)[2] == 2


def test_an_existing_link_bare_is_kept_verbatim():
    assert "[[baustein-a]]" in pd.clean_wikilinks(WL_TEXT, WL_NAMES)[0]


def test_an_existing_link_with_a_path_label_is_kept_verbatim():
    assert "[[ordner/zahl-42.md|Zahl 42]]" in pd.clean_wikilinks(
        WL_TEXT, WL_NAMES)[0]


def test_a_dead_bare_link_is_replaced_by_its_target_as_plain_text():
    cleaned = pd.clean_wikilinks(WL_TEXT, WL_NAMES)[0]
    assert "nicht-vorhanden" in cleaned and "[[nicht-vorhanden]]" not in cleaned


def test_a_dead_labelled_link_is_replaced_by_its_label_as_plain_text():
    cleaned = pd.clean_wikilinks(WL_TEXT, WL_NAMES)[0]
    assert "Toter Link" in cleaned and "[[andere/tot.md|Toter Link]]" not in cleaned


# ---------------------------------------------------------- project_md_names
@pytest.fixture
def tree(tmp_path):
    (tmp_path / "a.md").write_text("a", encoding="utf-8")
    (tmp_path / "b.md").write_text("b", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.md").write_text("c", encoding="utf-8")
    (tmp_path / "sessions" / "2026-09-26-x").mkdir(parents=True)
    (tmp_path / "sessions" / "2026-09-26-x" / "protokoll.md").write_text(
        "p", encoding="utf-8")
    (tmp_path / "sitzungen").mkdir()
    (tmp_path / "sitzungen" / "d.md").write_text("d", encoding="utf-8")
    (tmp_path / "not-markdown.txt").write_text("n", encoding="utf-8")
    return tmp_path


def test_plain_top_level_and_nested_md_files_are_found(tree):
    assert set(pd.project_md_names(tree)) == {"a", "b", "c"}


def test_names_are_sorted(tree):
    found = pd.project_md_names(tree)
    assert found == sorted(found)


def test_session_vault_meeting_folders_are_excluded(tree):
    found = pd.project_md_names(tree)
    assert "protokoll" not in found and "d" not in found


def test_duplicate_stems_from_different_folders_are_deduplicated(tree):
    (tree / "dup").mkdir()
    (tree / "dup" / "a.md").write_text("dup-a", encoding="utf-8")
    assert pd.project_md_names(tree).count("a") == 1


def test_limit_caps_the_list(tree):
    assert len(pd.project_md_names(tree, limit=2)) == 2


def test_hidden_paths_are_excluded(tree):
    (tree / ".trash").mkdir()
    (tree / ".trash" / "geloescht.md").write_text("t", encoding="utf-8")
    (tree / ".obsidian" / "snippets").mkdir(parents=True)
    (tree / ".obsidian" / "snippets" / "cfg.md").write_text(
        "o", encoding="utf-8")
    (tree / ".versteckt.md").write_text("v", encoding="utf-8")
    hidden = pd.project_md_names(tree)
    assert not {"geloescht", "cfg", ".versteckt"} & set(hidden)
    assert {"a", "b", "c"} <= set(hidden)


def test_a_non_existent_folder_yields_an_empty_list_no_exception(tree):
    assert pd.project_md_names(tree / "does-not-exist") == []
