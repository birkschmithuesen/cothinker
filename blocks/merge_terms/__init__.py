# CoThinker — blocks/merge_terms: the merge-terms block (issue #95)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The `merge-terms` building block: merges near-identical terms by
embedding similarity and announces the merged list.

`MergeTermsBlock` consumes `block_output` events whose payload carries
`"terms"` (the shape a term-capture block emits):

    {"terms": [{"form": str, "mentions": int}, ...]}

Merging is two-stage: an `alias_key` match (plural/spelling, free, no
embedding call) merges two forms for free; everything else that
survives is embedded through an injected client and joined to the
group of its single nearest existing member
(`term_merge.nearest_group`) when that scores at or above `threshold`.
A placeholder term (a speaker/seat label, `term_merge.is_placeholder_term`)
is dropped before either stage: it is not content. Each group's
displayed label is picked by `term_merge.medoid_label`.

It emits `block_output {"merged_terms": [{"label", "members", "mentions"}, ...]}`.

Choosing an embedding provider is instance configuration, out of scope
here: the default client reaches for an `embed` method on whatever
`ctx.llm` already is, the same way `blocks.topics`'s `TitlerFactory`
injects a titler. With no usable client, the embedding stage is skipped
(logged through `ctx.trace`) and only the free alias-key stage runs.

Config (`[[blocks]] id = "merge-terms"`): `threshold` (default
`term_merge.MERGE_COS_THRESHOLD`).
"""
from __future__ import annotations

from typing import Callable, Mapping, Optional, Sequence

from shared.blocks import BLOCK_API_VERSION, BlockContext
from shared.modules import CoreEvent

from . import term_merge
from .term_merge import MERGE_COS_THRESHOLD

EmbedClientFactory = Callable[[object], Optional[Callable[[str], Sequence[float]]]]


def _default_embed_client(llm: object) -> Optional[Callable[[str], Sequence[float]]]:
    return getattr(llm, "embed", None)


class MergeTermsBlock:
    id = "merge-terms"
    api_version = BLOCK_API_VERSION

    def __init__(self, embed_client_factory: EmbedClientFactory = _default_embed_client) -> None:
        self._embed_client_factory = embed_client_factory
        self._ctx: Optional[BlockContext] = None
        self._embed: Optional[Callable[[str], Sequence[float]]] = None
        self._threshold = MERGE_COS_THRESHOLD

    def start(self, ctx: BlockContext, config: Mapping[str, object]) -> None:
        self._ctx = ctx
        self._threshold = float(config.get("threshold", MERGE_COS_THRESHOLD))
        self._embed = self._embed_client_factory(ctx.llm)

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        if ev.kind != "block_output":
            return ()
        terms = ev.payload.get("terms")
        if terms is None:
            return ()
        return (CoreEvent("block_output", {"merged_terms": self._merge(terms)}),)

    def stop(self) -> None:
        pass

    def _merge(self, terms: Sequence[Mapping[str, object]]) -> list:
        if self._embed is None:
            self._trace("merge_terms_no_embed_client", {"terms": len(terms)})
        groups: dict[str, list] = {}
        alias_to_group: dict[str, str] = {}
        order: list[str] = []
        counter = 0
        for term in terms:
            form = str(term.get("form", "")).strip()
            if not form or term_merge.is_placeholder_term(form):
                continue
            mentions = int(term.get("mentions", 1))
            key = term_merge.alias_key(form)
            gid = alias_to_group.get(key)
            vector = None
            if gid is None and self._embed is not None:
                vector = self._embed(term_merge.embed_text(form))
                existing = {g: [m[2] for m in members if m[2]] for g, members in groups.items()}
                gid = term_merge.nearest_group(vector, existing, threshold=self._threshold)
            if gid is None:
                counter += 1
                gid = f"g{counter}"
                groups[gid] = []
                order.append(gid)
            alias_to_group[key] = gid
            groups[gid].append((form, mentions, vector))
        result = []
        for gid in order:
            members = groups[gid]
            label, _score = term_merge.medoid_label(members)
            result.append({"label": label, "members": [m[0] for m in members],
                           "mentions": sum(m[1] for m in members)})
        return result

    def _trace(self, event: str, fields: Mapping[str, object]) -> None:
        trace = self._ctx.trace if self._ctx is not None else None
        if trace is None:
            return
        trace(event, fields)


def make_block() -> MergeTermsBlock:
    return MergeTermsBlock()
