# Publish gate

`tools/publish_check.py` is the check run before every push to a public
remote. It is a port of the Kapsel room's `oeffentlich_pruefen.py` (K7),
renamed to English for this repo (repo split, task 6.3).

It scans the **full history** (`git log --all -p`, so content a later
commit removed is still found) plus commit metadata (author, committer,
message) against two kinds of denylist, and the tracked files against
`.secrets.baseline` via `detect-secrets-hook`. It never prints the matched
text, only which pattern list hit, a 12-character short commit SHA, and
the path (or `META` for a commit-metadata hit).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | clean |
| 1 | hit (a denylist pattern or detect-secrets found something) |
| 3 | self-test failed, or an empty list, or the detect-secrets hook is missing |
| 4 | no private list could be loaded (fail-closed) |

## `publish/denylist-public.txt`

Generic, public-safe pattern classes — never a literal operator value
(AGENTS.md; Global Constraints of
`docs/superpowers/plans/2026-11-12-repo-split.md`). Each non-comment line
is either a literal substring (matched lower-cased) or a `re:<regex>`
line. Current classes, one line each:

- `card-id` — this project's card-id shape, `t_[0-9a-f]{8}`.
- `ipv4-public` — any dotted quad that is not loopback, `0.0.0.0`, `255.*`,
  one of the three documentation ranges (`192.0.2.0/24`, `198.51.100.0/24`,
  `203.0.113.0/24`), or RFC 1918 private space.
- `home-path` — a Linux home directory, `/home/<user>/`.
- `mount-path` — this project's volume-mount naming shape,
  `/mnt/<VOL>_<VOL>_<digits>`.
- `email-real` — any email address that is not a documentation placeholder
  (`example.*`, `*.invalid`, `*.test`).
- a PEM private-key header line (`-----BEGIN ... PRIVATE KEY-----`).
- a Telegram-style bot-token shape (`\d{8,10}:[A-Za-z0-9_-]{35}`).

These classes are the same ones named in
`docs/superpowers/specs/2026-11-12-repo-split-inventory/rules.toml`
(`operator_class` entries `card-id`, `ipv4-public`, `home-path`,
`mount-path`, `email-real`), plus the two classes the K7 port adds
(private-key header, bot-token shape).

## Private lists (`--private-list`)

A private list is a **plaintext, never-committed** denylist of literal
values specific to one instance or deployment — people's names, real
hostnames, account strings. It is never checked in; it lives outside the
repo, analogous to the Kapsel room's `~/.config/kapsel/denylist.d/*.txt`.

```
python3 tools/publish_check.py --repo . --private-list ~/.config/cothinker/denylist.d/*.txt
```

`--private-list` takes a glob (quote it so the shell doesn't expand it, or
pass the tool a literal path) and may be given more than once. Each
non-comment line is a literal substring or a `re:<regex>` line, same
format as the public list. With no `--private-list` at all, the tool
looks for `~/.config/cothinker/denylist.d/*.txt` itself; if that glob is
also empty, it fails closed with exit 4 rather than running with the
public list alone.

Before trusting any list, the tool runs a self-test: it builds a throwaway
repo with a canary commit and checks that every loaded list (public,
each private list, the HMAC list) actually fires on it. An empty list, or
a self-test that does not fire for every list, is exit 3 — the tool
refuses to report "clean" when it cannot prove it is looking at anything.

## `--hmac` CI mode

CI cannot hold a private list in plaintext without publishing it in logs
or the repo. `--hmac-generate <denylist-file>` turns a private list's
literal patterns (1-3 tokens each; longer patterns and all `re:` lines are
left out, with a warning, since the HMAC check only covers token windows)
into salted HMAC-SHA256 values, keyed by `COTHINKER_DENYLIST_KEY` (set
that env var to a secret at least 32 characters long before generating).
The generated file holds only hashes and is safe to commit or hand to CI
as a secret.

```
COTHINKER_DENYLIST_KEY=<32+ chars> python3 tools/publish_check.py --hmac-generate ~/.config/cothinker/denylist.d/instance.txt > ci-denylist.hmac
```

In CI, with the same key available as a secret:

```
COTHINKER_DENYLIST_KEY=<32+ chars> python3 tools/publish_check.py --repo . --hmac ci-denylist.hmac
```

In `--hmac` mode, hit paths are hashed (`path#<sha256 prefix>`) instead of
printed in the clear, since CI logs of a public repo are themselves
public.

## Installing the pre-push hook

```
cp publish/hooks/pre-push .git/hooks/pre-push
chmod +x .git/hooks/pre-push
```

The hook runs `python3 tools/publish_check.py --repo .` (which picks up
`--private-list` discovery as described above) before every push; a
non-zero exit blocks the push. Bypass only with `git push --no-verify`,
and only deliberately.

## Other flags

- `--tree <dir>` scans a directory tree instead of git history (used to
  check that the denylist files themselves do not self-match).
- `--skip-detect-secrets` skips the `detect-secrets-hook` pass (used by
  tests and by `--tree` mode, which has no tracked-files concept).
- `--public-only` allows a run with the public list alone — normally
  exit 4 — used only to self-check `publish/denylist-public.txt` against
  itself.
