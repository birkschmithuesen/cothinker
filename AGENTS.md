# Working rules for this repository (humans and coding agents)

1. **Branch + pull request, never a direct push to `main`.** One PR per
   topic (a feature, an ADR, a fix), holding all its commits. Open it as a
   **draft** while work is in progress; mark it ready when it is done.
2. **The GitHub issue is the task description.** A pull request implements
   one issue (`Closes #n` in the PR body); if the scope changes, update the
   issue, not just the PR description. The PR body states what changed and
   why in plain language — no internal tool, task-id, or factory-workflow
   details (those belong to the operator's own tracking, not this public
   history, see rule 5).
3. **Decisions are logged as ADRs.** A choice that excludes an alternative
   (service boundaries, data model, API shape, naming) gets a new ADR in
   `docs/adr/` in its own pull request, as soon as it is made.
   - **Supersede, never rewrite.** An accepted ADR only gets its status line
     changed (`superseded by NNNN`, `amended by NNNN`). Enforced by
     `scripts/check_adr_immutable.py` (CI workflow `adr-immutable`).
   - **Read before you change.** Before changing architecture, read
     `docs/adr/README.md` and the matching ADRs (and whatever supersedes
     them). If a change conflicts with an accepted ADR, name the conflict in
     the PR instead of silently changing the architecture.
4. **The maintainer merges.** A pull request is merged by the maintainer (or
   the maintainer's own agent, under a standing go) only once it is
   reviewed, every required check is green, and it changes no ADR. Any ADR
   change, or anything short of fully green, needs the maintainer's explicit
   go.
5. **Commit identity and sign-off follow `CONTRIBUTING.md` (the DCO).** Every
   commit carries a real `Signed-off-by:` trailer with the actual author's
   identity for a human contributor. No `Co-Authored-By:` trailer naming a
   real mailbox, no other real email address in author/committer metadata,
   and no internal task/ticket ids in commit messages — `tools/publish_check.py`
   enforces this on every pull request.
6. **The publish check must be green before any merge.** `tools/publish_check.py`
   scans the full history and the tracked tree for the patterns in
   `publish/denylist-public.txt`; a merge candidate that is not clean is not
   merged, regardless of who requests it.
7. **Templates stay generic; instances are private.** Nothing in this
   repository names a specific deployment, team, or event — only example
   data under invented names. A concrete installation's real names, data,
   and configuration live in a separate, private repository, never here.
