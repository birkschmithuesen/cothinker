# CoThinker — blocks/merge_terms/term_merge.py: pure term-merge primitives
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Pure, offline-testable building blocks for merging near-identical
terms: no network, no I/O, plain vectors in and out.

1. `alias_key()` -- a deterministic pre-embedding key: an English
   plural-strip lemma plus a small spelling-alias table. Two terms with
   the same `alias_key` merge for free, before any embedding call.
2. `embed_text()` -- the contextual string actually sent to an
   embeddings client, a template rather than the bare term: a single
   bare word often does not carry enough signal for one global cosine
   threshold to separate near-synonyms from unrelated words.
3. `centroid()` / `best_group()` / `nearest_group()` -- group a
   candidate against existing groups by embedding similarity.
   `nearest_group` compares against each group's single nearest member
   rather than its centroid, which drifts as a group grows and can end
   up close to almost anything.

`medoid_label()` picks a group's displayed label: the member with the
highest mention-weighted mean cosine similarity to every other member,
ties broken by more mentions then a shorter surface form. A margin
(`LABEL_SWITCH_MARGIN`) stops the label from flickering on every single
new mention.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Callable, Dict, List, Optional, Sequence, Tuple

MERGE_COS_THRESHOLD = 0.834
LABEL_SWITCH_MARGIN = 0.01

EMBED_TEMPLATE = "term used in this session: {term}"

# Leading article, word-boundary guarded so "a"/"an" never eats the first
# syllable of a longer word.
_LEADING_ARTICLE_RE = re.compile(r"^(der|die|das|den|dem|des|ein|eine|the|a|an)\b\s*")

# Inner word separators -- whitespace, hyphen/dash variants, slash,
# underscore -- all count as one separator, so "note-taking",
# "note taking" and "note_taking" share a normalized form.
_INNER_SEP_RE = re.compile(r"[\s\-‐‑‒–—/_]+")


def _legacy_normalize_term(text: str) -> str:
    """Whitespace-only normalization (hyphen kept). Only `term_id` uses
    this: an id derived from it must stay stable even if
    `normalize_term`'s separator handling changes later."""
    text = unicodedata.normalize("NFC", text or "")
    text = text.casefold()
    text = re.sub(r"\s+", " ", text.strip())
    text = text.strip(".,;:!?„“”\"'")
    text = _LEADING_ARTICLE_RE.sub("", text)
    return text


def normalize_term(text: str) -> str:
    """Case- and separator-insensitive key for the free, exact-match
    dedup stage (hyphen, slash, underscore and whitespace runs all count
    as one space). Does not catch near-synonyms -- that is the embedding
    stage's job."""
    text = unicodedata.normalize("NFC", text or "")
    text = text.casefold()
    text = text.strip().strip(".,;:!?„“”\"'-–—/_")
    text = _INNER_SEP_RE.sub(" ", text).strip()
    text = text.strip(".,;:!?„“”\"'")
    text = _LEADING_ARTICLE_RE.sub("", text)
    return text


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def embed_text(term: str) -> str:
    """The string actually handed to the embeddings client -- never the
    bare term."""
    return EMBED_TEMPLATE.format(term=term)


# --- stage 1: deterministic pre-embedding key ------------------------------

_SPELLING_ALIASES: Dict[str, str] = {
    "theater": "theatre",
    "center": "centre",
    "color": "colour",
    "organization": "organisation",
    "program": "programme",
    "catalog": "catalogue",
}


def _lemma(word: str) -> str:
    """English plural strip. Deliberately simple -- it only has to catch
    the easy, common cases cheaply; anything it gets wrong still has the
    embedding stage as a safety net. Longer, more specific suffixes are
    checked before the generic trailing-s strip so "glasses" strips to
    "glass" (not "glasse") and "houses" strips to "house" (not "hous")."""
    w = word
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith(("sses", "xes", "zes", "ches", "shes")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def alias_key(term: str) -> str:
    """The free, pre-embedding merge key: `normalize_term`, then the
    plural-strip lemma, then the spelling alias table. Two terms with
    the same `alias_key` merge for free -- no embedding call, no
    network."""
    return _alias_of(normalize_term(term))


def _alias_of(norm: str) -> str:
    if not norm:
        return norm
    lemma = _lemma(norm)
    return _SPELLING_ALIASES.get(lemma, lemma)


def term_id(form: str) -> str:
    """A stable id derived from a term's founding alias key -- the same
    form always yields the same id, independent of ordering."""
    digest = hashlib.sha1(_alias_of(_legacy_normalize_term(form)).encode("utf-8")).hexdigest()
    return "m" + digest[:10]


# --- stage 2/3: embedding-based grouping -----------------------------------

def centroid(vectors: Sequence[Sequence[float]]) -> Optional[List[float]]:
    """The mean vector of every given (non-empty) embedding, or None if
    none were usable."""
    vecs = [v for v in vectors if v]
    if not vecs:
        return None
    dim = len(vecs[0])
    return [sum(v[i] for v in vecs) / len(vecs) for i in range(dim)]


def best_group(vector: Sequence[float], groups: Dict[str, Sequence[Sequence[float]]], *,
                threshold: float = MERGE_COS_THRESHOLD,
                cosine_fn: Callable[[Sequence[float], Sequence[float]], float] = cosine
                ) -> Optional[str]:
    """`groups` maps a group id to the embeddings of its current
    members. Compares `vector` against each group's CENTROID, not its
    nearest single member, so one outlier surface form cannot
    single-handedly pull in or push out a whole group. Superseded by
    `nearest_group` below for the merge decision this block actually
    uses: a centroid drifts as a group grows and can end up close to
    almost anything."""
    best_id, best_score = None, threshold
    for gid, vectors in groups.items():
        c = centroid(vectors)
        if c is None:
            continue
        score = cosine_fn(vector, c)
        if score >= threshold and (best_id is None or score > best_score):
            best_id, best_score = gid, score
    return best_id


def nearest_group(vector: Sequence[float], groups: Dict[str, Sequence[Sequence[float]]], *,
                   threshold: float = MERGE_COS_THRESHOLD,
                   cosine_fn: Callable[[Sequence[float], Sequence[float]], float] = cosine
                   ) -> Optional[str]:
    """`vector` joins the group of its single NEAREST existing member --
    the group whose best-matching member scores highest -- never a
    group's centroid. The nearest single member's own embedding never
    drifts as the group grows, unlike a centroid."""
    best_id, best_score = None, threshold
    for gid, vectors in groups.items():
        for v in vectors:
            if not v:
                continue
            score = cosine_fn(vector, v)
            if score >= threshold and (best_id is None or score > best_score):
                best_id, best_score = gid, score
    return best_id


# --- placeholder/identity terms ---------------------------------------------

# No trailing \b: a possessive form ("P3's note") must still match.
_PLACEHOLDER_TERM_RE = re.compile(r"\b[PS]\d+|\bseat\d+", re.IGNORECASE)


def is_placeholder_term(term: Optional[str]) -> bool:
    """True if `term` carries an identity reference (a speaker/seat
    label) rather than content. Such a term must never reach the
    embedding call or become a merged term -- it is not something anyone
    said, it is a label that leaked into extraction."""
    return bool(_PLACEHOLDER_TERM_RE.search(term or ""))


# --- the group label ---------------------------------------------------

Member = Tuple[str, int, Optional[Sequence[float]]]   # (form, mentions, embedding)


def _medoid_scores(members: Sequence[Member],
                    cosine_fn: Callable[[Sequence[float], Sequence[float]], float]
                    ) -> Dict[str, float]:
    scores: Dict[str, float] = {}
    for form, _mentions, vec in members:
        total_w, weighted = 0.0, 0.0
        for other_form, other_mentions, other_vec in members:
            if other_form == form or vec is None or other_vec is None:
                continue
            w = other_mentions or 1
            weighted += cosine_fn(vec, other_vec) * w
            total_w += w
        scores[form] = weighted / total_w if total_w else 0.0
    return scores


def medoid_label(members: Sequence[Member], *, current_label: Optional[str] = None,
                  margin: float = LABEL_SWITCH_MARGIN,
                  cosine_fn: Callable[[Sequence[float], Sequence[float]], float] = cosine
                  ) -> Tuple[str, float]:
    """The group's label: the member with the highest mention-weighted
    mean cosine similarity to every OTHER member -- deterministic, no
    model call, reusing embeddings already computed for the merge
    decision. Ties: more mentions wins, then a shorter surface form
    wins.

    A single member is trivially its own label (score 0.0 -- nothing to
    compare it to yet). With a `current_label`, the candidate only
    replaces it when it beats the current label's own (freshly
    recomputed) score by `margin` -- this is what stops the label from
    flickering on every single new mention as the group grows."""
    if not members:
        raise ValueError("medoid_label needs at least one member")
    if len(members) == 1:
        return members[0][0], 0.0
    scores = _medoid_scores(members, cosine_fn)
    ranked = sorted(members, key=lambda m: (-scores.get(m[0], 0.0), -m[1], len(m[0])))
    candidate_form = ranked[0][0]
    candidate_score = scores.get(candidate_form, 0.0)
    if current_label is not None and current_label in scores:
        if candidate_form == current_label:
            return current_label, scores[current_label]
        cur_score = scores[current_label]
        # `candidate_score >= cur_score` always holds here (candidate is
        # the global best by the sort key above). An exact tie means the
        # switch is already decided by the stated tie-break (more
        # mentions, then a shorter form) -- true synonyms often embed
        # almost identically, so that tie-break, not a score delta, is
        # the realistic deciding signal and must not be blocked by the
        # margin. The margin instead guards the narrow in-between: a
        # candidate that edges ahead on score by less than `margin` is
        # noise, not a deliberate tie-break win.
        if candidate_score == cur_score or candidate_score > cur_score + margin:
            return candidate_form, candidate_score
        return current_label, cur_score
    return candidate_form, candidate_score
