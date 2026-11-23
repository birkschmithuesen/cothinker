# Contributing

Thanks for wanting to work on CoThinker. See `AGENTS.md` for the working
rules this repository follows (branching, issues, ADRs, who merges, commit
identity, the publish check, and what stays out of this template repo).

Two things are required before a contribution can be merged, on top of any
code review:

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

## 4. The merge guard -- internal work-item ids and publish-check hits

`tools/merge_guard.py` refuses a merge if any new commit carries an internal
work-item id (a `card-id`, the pattern from `publish/denylist-public.txt`) in
its message, or if `tools/publish_check.py` finds a hit on the candidate.
This is not advisory documentation -- it is wired into two real, independent
gates:

- a `merge-guard` job in `.github/workflows/publish-check.yml`, which runs on
  every pull request against `main` and fails the PR's required check (so
  `gh pr checks <pr>` and the GitHub UI both go red); and
- the factory's own landing path, `land.py` (`~/.hermes/profiles/birk/scripts/
  land.py`), which calls `tools/merge_guard.py` before it rebases or merges a
  branch for any repo whose `FACTORY.yaml` sets `workflow: github-pr` (this
  repo has it) -- a refusal there aborts with the same exit code as a failed
  test suite or changelog gate, before anything is pushed to `main`.

Either gate catching a hit means the branch does not land until the
work-item id or publish-check hit is removed from the commit history.

## 5. Building a surface

Designers who build a surface (a screen, a room display, a phone view) follow
[`docs/contributing-surfaces.md`](docs/contributing-surfaces.md): where each kind
of work lives, how it reaches `main`, and how to build against the surface
contract.

## Questions

Open an issue in this repository on GitHub.
