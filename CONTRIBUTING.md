# Contributing

Thanks for wanting to work on CoThinker. Two things are required before a
contribution can be merged, on top of any code review:

## 1. The CLA

Read `CLA.md` in this repository and follow the instructions there (the
line to put in your first pull request, what you keep, what you grant).
This document does not repeat that text -- `CLA.md` is the single source
for it.

## 2. The DCO -- a `Signed-off-by:` trailer on every commit

In addition to the CLA, every commit and every pull request must carry a
`Signed-off-by:` trailer, per the Developer Certificate of Origin (DCO).
Add it with `git commit -s`, which appends a line like:

```
Signed-off-by: Jane Doe <jane@example.com>
```

By adding that line you certify, in the standard DCO wording, that you
wrote the patch or otherwise have the right to submit it under the
project's licence. This applies alongside the CLA, not instead of it --
the CLA grants the project the right to also license your contribution
commercially; the DCO is the record of who submitted what and that they
were allowed to.

A CI check enforces this trailer automatically on every pull request
(`.github/workflows/dco.yml`, `scripts/check_dco.py`): a pull request
whose commits are missing the trailer fails CI.

## 3. Commit identity on agent-authored commits -- one allow-listed address

`tools/publish_check.py` (the publish gate, now also running on every pull
request) flags any email address that is not a documentation placeholder
(`publish/denylist-public.txt`'s `email-real` class: everything except
`*.example.*`, `*.invalid`, `*.test`) and does not match a class in
`publish/allow-public.txt`. The DCO check above needs a real
`Signed-off-by:` line on every commit. Both are satisfied together because
the factory's own `Signed-off-by:` identity is a GitHub-issued no-reply
address, and that address *shape* -- not one literal address -- is what
`publish/allow-public.txt` allow-lists; any *other* real operator or
assistant mailbox in that trailer, or in author/committer metadata, or in
a `Co-Authored-By:` trailer, still fails the publish gate on its own
commit.

For the factory's own agent-authored commits in this repository (not a
human contributor's), the fixed identity is:

```
Signed-off-by: Robo <8000653+birkschmithuesen@users.noreply.github.com>
```

matching the author/committer identity used by `git commit -s --author`
or the equivalent `GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL`/`GIT_COMMITTER_NAME`/
`GIT_COMMITTER_EMAIL` environment overrides (`FACTORY.yaml`'s
`commit_identity`). Do not add a `Co-Authored-By:` trailer naming any
OTHER real address, including an assistant's own real mailbox -- a real
vendor domain in that trailer is exactly the kind of address the publish
gate flags, on its own commit, every time. If the authoring tool needs to
be credited, name it in prose in the commit body instead of in a trailer
with an email address.

A human contributor signs off with their own real name and address, as the
DCO intends -- the identity above is only for commits the factory itself
authors in this repository, never for a human's contribution.

## Questions

Open an issue in this repository on GitHub.
