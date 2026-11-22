# CoThinker — tests of blocks/graph/session_graph.py (issue #48)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Pure functions: `topics_from_stage()` (buehne.json + protocol -> topics)
and `build_session_graph()` (topics + vault -> the graph tab's payload).
No I/O, no clock -- ported from the stage's own invariants (module
docstring) onto the moved-but-unchanged code.

    python3 -m pytest -q tests/test_session_graph.py
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from blocks.graph import session_graph as sg
from graph.neighbours import Neighbour
from graph.ring_select import Hysteresis
from graph.vault_notes import Note, NoteIndex


def _note(rel: str, *, links: tuple = (), mtime: float = 0.0) -> Note:
    return Note(path=Path(rel), rel=rel, stem=Path(rel).stem, mtime=mtime,
               links=links)


def _vault(*notes: Note) -> NoteIndex:
    return NoteIndex(list(notes))


class FakeEdges:
    """`neighbours(note)` from a fixed table, no disk, no HardEdges."""
    def __init__(self, table: dict) -> None:
        self.table = table

    def neighbours(self, note: Note) -> list:
        return self.table.get(note.rel, [])


def _no_resolve(name: str):
    return None


def _graph(topics, **kw):
    kw.setdefault("session_id", "session")
    kw.setdefault("session_label", "Sitzung")
    kw.setdefault("resolve", _no_resolve)
    return sg.build_session_graph(topics, **kw)


# 1 — protocol_head: the Rohtranskript rule

def test_protocol_head_returns_everything_without_the_heading() -> None:
    assert sg.protocol_head("# Titel\n\n- a\n") == "# Titel\n\n- a\n"


def test_protocol_head_cuts_at_the_heading() -> None:
    text = "# Titel\n\n- a\n\n## Rohtranskript\n\n- geheim\n"
    assert sg.protocol_head(text) == "# Titel\n\n- a\n\n"


def test_protocol_head_excludes_the_heading_line_itself() -> None:
    text = "## Rohtranskript\n- geheim\n"
    assert sg.protocol_head(text) == ""


def test_protocol_head_requires_the_exact_heading() -> None:
    text = "### Rohtranskript\n- not the real heading\n"
    assert sg.protocol_head(text) == text


def test_protocol_head_of_empty_text_is_empty() -> None:
    assert sg.protocol_head("") == ""


def test_protocol_head_of_non_string_is_empty() -> None:
    assert sg.protocol_head(None) == ""


# 2 — _text: foreign JSON

def test_text_keeps_a_string() -> None:
    assert sg._text("abc") == "abc"


def test_text_of_a_non_string_is_empty() -> None:
    assert sg._text(123) == ""


# 3 — _clean_label

def test_clean_label_strips_markdown_emphasis() -> None:
    assert sg._clean_label("*fett* _kursiv_ `code`") == "fett kursiv code"


def test_clean_label_replaces_plain_wikilink_with_its_target() -> None:
    assert sg._clean_label("siehe [[Notiz]] dort") == "siehe Notiz dort"


def test_clean_label_replaces_aliased_wikilink_with_the_alias() -> None:
    assert sg._clean_label("[[Notiz|Anzeigename]]") == "Anzeigename"


def test_clean_label_replaces_markdown_link_with_its_text() -> None:
    assert sg._clean_label("[Text](https://example.org)") == "Text"


def test_clean_label_collapses_whitespace() -> None:
    assert sg._clean_label("a   b\n\nc") == "a b c"


def test_clean_label_truncates_beyond_the_limit() -> None:
    label = sg._clean_label("x" * (sg.LABEL_MAX + 1))
    assert len(label) == sg.LABEL_MAX and label.endswith("…")


def test_clean_label_leaves_the_limit_itself_untouched() -> None:
    text = "x" * sg.LABEL_MAX
    assert sg._clean_label(text) == text


def test_clean_label_of_empty_text_is_empty() -> None:
    assert sg._clean_label("") == ""


# 4 — _first_time

def test_first_time_zero_pads_a_single_digit_hour() -> None:
    assert sg._first_time("treffen um 9:05 uhr") == "09:05"


def test_first_time_absent_is_empty() -> None:
    assert sg._first_time("kapitel 12345") == ""


def test_first_time_takes_the_first_match() -> None:
    assert sg._first_time("von 09:00 bis 10:30") == "09:00"


# 5 — _block_for

def test_block_for_without_blocks_is_empty() -> None:
    assert sg._block_for([], "09:00") == ""


def test_block_for_without_a_time_is_the_last_block() -> None:
    blocks = [{"id": "a", "time": "09:00"}, {"id": "b", "time": "10:00"}]
    assert sg._block_for(blocks, "") == "b"


def test_block_for_before_every_block_is_the_first_block() -> None:
    blocks = [{"id": "a", "time": "09:00"}, {"id": "b", "time": "10:00"}]
    assert sg._block_for(blocks, "08:00") == "a"


def test_block_for_picks_the_latest_block_at_or_before_the_time() -> None:
    blocks = [{"id": "a", "time": "09:00"}, {"id": "b", "time": "10:00"}]
    assert sg._block_for(blocks, "10:30") == "b"


# 6 — topics_from_stage: blocks from buehne.json history

def test_topics_from_stage_of_nothing_is_empty() -> None:
    topics = sg.topics_from_stage({}, "")
    assert topics == {"blocks": [], "entries": [], "links": []}


def test_consecutive_rows_with_the_same_title_stay_one_block() -> None:
    rows = [{"title": "Budget", "set_at": "2026-01-01T09:00:00"},
            {"title": "Budget", "set_at": "2026-01-01T09:05:00"}]
    topics = sg.topics_from_stage({"history": rows}, "")
    assert len(topics["blocks"]) == 1


def test_a_new_title_starts_a_new_block() -> None:
    rows = [{"title": "Budget", "set_at": "2026-01-01T09:00:00"},
            {"title": "Zeitplan", "set_at": "2026-01-01T09:30:00"}]
    topics = sg.topics_from_stage({"history": rows}, "")
    assert len(topics["blocks"]) == 2


def test_block_id_derives_from_title_and_set_at_without_an_explicit_id() -> None:
    row = {"title": "Thema X", "set_at": "2026-01-01T09:15:00"}
    topics = sg.topics_from_stage({"history": [row]}, "")
    expected = "T:" + hashlib.sha1(
        ("Thema X" + "2026-01-01T09:15:00").encode()).hexdigest()[:8]
    assert topics["blocks"][0]["id"] == expected


def test_colliding_explicit_ids_get_a_tick_suffix() -> None:
    rows = [{"title": "A", "id": "dup", "set_at": ""},
            {"title": "B", "id": "dup", "set_at": ""},
            {"title": "C", "id": "dup", "set_at": ""}]
    topics = sg.topics_from_stage({"history": rows}, "")
    ids = [b["id"] for b in topics["blocks"]]
    assert ids == ["T:dup", "T:dup'", "T:dup''"]


def test_without_history_the_main_title_becomes_one_block() -> None:
    buehne = {"main": {"title": "Hauptthema", "set_at": "2026-01-01T10:00:00"}}
    topics = sg.topics_from_stage(buehne, "")
    assert len(topics["blocks"]) == 1
    assert topics["blocks"][0]["title"] == "Hauptthema"
    assert topics["blocks"][0]["time"] == "10:00"


def test_non_dict_history_rows_are_skipped() -> None:
    buehne = {"history": ["not a row", {"title": "A", "set_at": ""}]}
    topics = sg.topics_from_stage(buehne, "")
    assert len(topics["blocks"]) == 1


# 7 — topics_from_stage: entries from the protocol head

def test_entries_come_from_recognised_section_headings() -> None:
    text = "## Aufgaben\n- Etwas erledigen\n"
    topics = sg.topics_from_stage({}, text)
    assert len(topics["entries"]) == 1
    assert topics["entries"][0]["tag"] == "aufgabe"
    assert topics["entries"][0]["label"] == "Etwas erledigen"


def test_an_inline_tag_overrides_the_heading() -> None:
    text = "## Entscheidungen\n- Etwas Neues #idee\n"
    topics = sg.topics_from_stage({}, text)
    assert topics["entries"][0]["tag"] == "idee"


def test_nothing_below_rohtranskript_becomes_an_entry() -> None:
    text = ("## Aufgaben\n- Normale Aufgabe\n\n"
            "## Rohtranskript\n- KANARIE-GEHEIM #idee\n")
    topics = sg.topics_from_stage({}, text)
    labels = [e["label"] for e in topics["entries"]]
    assert all("KANARIE" not in label for label in labels)
    assert len(topics["entries"]) == 1


def test_duplicate_entries_are_deduplicated() -> None:
    text = "## Aufgaben\n- Gleiche Aufgabe\n- Gleiche Aufgabe\n"
    topics = sg.topics_from_stage({}, text)
    assert len(topics["entries"]) == 1


def test_a_wikilink_attaches_to_the_block_nearest_its_own_time() -> None:
    rows = [{"title": "Erstes", "set_at": "2026-01-01T09:00:00"},
            {"title": "Zweites", "set_at": "2026-01-01T10:00:00"}]
    text = "10:05 sieht sich [[Notiz]] an\n"
    topics = sg.topics_from_stage({"history": rows}, text)
    second_block = topics["blocks"][1]["id"]
    assert topics["links"] == [{"block": second_block, "target": "Notiz"}]


# 8 — build_session_graph: the session and its topics

def test_the_session_node_always_exists() -> None:
    graph = _graph({}, session_id="file:protokoll.md", session_label="Sitzung")
    assert graph["seed"] == "file:protokoll.md"
    assert any(n["id"] == "file:protokoll.md" and n["ring"] == 0
              for n in graph["nodes"])


def test_topic_nodes_are_parented_to_the_session() -> None:
    topics = {"blocks": [{"id": "T:a", "title": "Thema", "time": "09:00"}]}
    graph = _graph(topics)
    topic = next(n for n in graph["nodes"] if n["id"] == "T:a")
    assert topic["ring"] == 0 and topic["parent"] == "session"


def test_newer_topics_are_inserted_before_older_ones() -> None:
    topics = {"blocks": [{"id": "T:a", "title": "Erstes", "time": "09:00"},
                         {"id": "T:b", "title": "Zweites", "time": "10:00"}]}
    graph = _graph(topics)
    topic_ids = [n["id"] for n in graph["nodes"] if n["kind"] == "topic"]
    assert topic_ids == ["T:b", "T:a"]


def test_a_recognised_entry_is_parented_to_its_block() -> None:
    topics = {"blocks": [{"id": "T:a", "title": "Thema", "time": "09:00"}],
             "entries": [{"id": "E:1", "tag": "aufgabe", "label": "Tun",
                         "block": "T:a", "time": "09:00"}]}
    graph = _graph(topics)
    entry = next(n for n in graph["nodes"] if n["id"] == "E:1")
    assert entry["parent"] == "T:a"


def test_an_entry_with_an_unrecognised_tag_is_dropped() -> None:
    topics = {"entries": [{"id": "E:1", "tag": "sonstiges", "label": "Egal"}]}
    graph = _graph(topics)
    assert all(n["id"] != "E:1" for n in graph["nodes"])


def test_an_entry_without_a_matching_block_is_owned_by_the_session() -> None:
    topics = {"entries": [{"id": "E:1", "tag": "idee", "label": "Idee",
                         "block": "T:missing"}]}
    graph = _graph(topics, session_id="session")
    entry = next(n for n in graph["nodes"] if n["id"] == "E:1")
    assert entry["parent"] == "session"


# 9 — build_session_graph: ring 1 (wikilinks)

def test_an_unresolved_wikilink_gets_no_node_and_no_edge() -> None:
    topics = {"links": [{"block": "session", "target": "Nirgendwo"}]}
    graph = _graph(topics, resolve=lambda name: None)
    assert graph["nodes"] == [n for n in graph["nodes"] if n["kind"] != "note"]
    assert graph["edges"] == []


def test_a_resolved_wikilink_without_a_vault_note_is_still_a_node() -> None:
    topics = {"links": [{"block": "session", "target": "Projektdatei"}]}
    graph = _graph(topics, resolve=lambda name: "file:projektdatei.md")
    assert any(n["id"] == "file:projektdatei.md" and n["kind"] == "note"
              for n in graph["nodes"])


def test_the_same_linked_note_is_a_single_node_even_when_linked_twice() -> None:
    a = _note("a.md")
    topics = {"links": [{"block": "session", "target": "a"},
                        {"block": "session", "target": "a"}]}
    graph = _graph(topics, resolve=lambda name: "vault:a.md",
                   index=_vault(a), edges=FakeEdges({}))
    matches = [n for n in graph["nodes"] if n["id"] == "vault:a.md"]
    assert len(matches) == 1


def test_ring_1_is_cut_to_its_budget() -> None:
    notes = [_note(f"n{i:02d}.md", mtime=float(i)) for i in range(20)]
    links = [{"block": "session", "target": n.rel} for n in notes]
    resolve_map = {n.rel: f"vault:{n.rel}" for n in notes}
    graph = _graph({"links": links}, resolve=lambda name: resolve_map[name],
                   index=_vault(*notes), edges=FakeEdges({}))
    ring1 = [n for n in graph["nodes"] if n["ring"] == 1]
    assert len(ring1) == sg.RING_BUDGETS[0]


def test_ring_1_keeps_the_most_recently_touched_notes() -> None:
    notes = [_note(f"n{i:02d}.md", mtime=float(i)) for i in range(20)]
    links = [{"block": "session", "target": n.rel} for n in notes]
    resolve_map = {n.rel: f"vault:{n.rel}" for n in notes}
    graph = _graph({"links": links}, resolve=lambda name: resolve_map[name],
                   index=_vault(*notes), edges=FakeEdges({}))
    ring1_ids = {n["id"] for n in graph["nodes"] if n["ring"] == 1}
    assert "vault:n19.md" in ring1_ids and "vault:n00.md" not in ring1_ids


# 10 — build_session_graph: ring 2 (neighbours)

def test_ring_2_comes_from_the_edge_source_of_a_ring_1_note() -> None:
    a, b = _note("a.md"), _note("b.md")
    topics = {"links": [{"block": "session", "target": "a"}]}
    edges = FakeEdges({"a.md": [Neighbour(b, "wikilink", 1.0)]})
    graph = _graph(topics, resolve=lambda name: "vault:a.md",
                   index=_vault(a, b), edges=edges)
    ring2 = next(n for n in graph["nodes"] if n["id"] == "vault:b.md")
    assert ring2["ring"] == 2


def test_a_dropped_ring_2_note_is_kept_once_by_the_grace_window() -> None:
    a, b = _note("a.md"), _note("b.md")
    hysteresis = Hysteresis(rounds=3)
    topics = {"links": [{"block": "session", "target": "a"}]}

    first = _graph(topics, resolve=lambda name: "vault:a.md",
                   index=_vault(a, b),
                   edges=FakeEdges({"a.md": [Neighbour(b, "wikilink", 1.0)]}),
                   hysteresis=hysteresis)
    before = next(n for n in first["nodes"] if n["id"] == "vault:b.md")
    assert before["grace"] is False

    second = _graph(topics, resolve=lambda name: "vault:a.md",
                    index=_vault(a, b), edges=FakeEdges({}),
                    hysteresis=hysteresis)
    after = next(n for n in second["nodes"] if n["id"] == "vault:b.md")
    assert after["grace"] is True


def test_without_a_hysteresis_a_dropped_ring_2_note_is_gone() -> None:
    a, b = _note("a.md"), _note("b.md")
    topics = {"links": [{"block": "session", "target": "a"}]}
    first = _graph(topics, resolve=lambda name: "vault:a.md",
                   index=_vault(a, b),
                   edges=FakeEdges({"a.md": [Neighbour(b, "wikilink", 1.0)]}))
    assert any(n["id"] == "vault:b.md" for n in first["nodes"])
    second = _graph(topics, resolve=lambda name: "vault:a.md",
                    index=_vault(a, b), edges=FakeEdges({}))
    assert all(n["id"] != "vault:b.md" for n in second["nodes"])


# 11 — build_session_graph: visibility and the emergency cap

def test_nodes_beyond_max_visible_are_hidden() -> None:
    entries = [{"id": f"E:{i}", "tag": "idee", "label": f"Idee {i}"}
              for i in range(5)]
    graph = _graph({"entries": entries}, max_visible=3)
    assert sum(1 for n in graph["nodes"] if n["hidden"]) == graph["hidden"]
    assert graph["hidden"] == len(graph["nodes"]) - 3


def test_the_emergency_cap_bounds_the_total_node_count() -> None:
    entries = [{"id": f"E:{i}", "tag": "idee", "label": f"Idee {i}"}
              for i in range(sg.MAX_TOTAL + 50)]
    graph = _graph({"entries": entries})
    assert len(graph["nodes"]) == sg.MAX_TOTAL


# 12 — build_session_graph: edges only between present nodes, hrefs

def test_an_edge_is_only_emitted_between_two_present_nodes() -> None:
    topics = {"blocks": [{"id": "T:a", "title": "Thema", "time": "09:00"}],
             "entries": [{"id": "E:1", "tag": "idee", "label": "Idee",
                         "block": "T:a"}]}
    graph = _graph(topics)
    assert {"e:E:1", "b:T:a"} <= {e["id"] for e in graph["edges"]}
    for edge in graph["edges"]:
        ids = {n["id"] for n in graph["nodes"]}
        assert edge["source"] in ids and edge["target"] in ids


def test_href_is_derived_from_a_file_or_vault_id() -> None:
    assert sg._href("file:unterordner/datei.md") == "file/unterordner/datei.md"
    assert sg._href("vault:notiz.md") == "vault/notiz.md"


def test_href_is_empty_for_ids_without_a_file_or_vault_prefix() -> None:
    assert sg._href("session") == ""
    assert sg._href("T:abc123") == ""
