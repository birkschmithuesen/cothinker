# Building a surface from outside the core team

This guide is for designers (and their coding agents) who build a surface: a screen, a room display, a phone
view. It says where each kind of work lives, how a contribution reaches `main`, and how to make sure the result
runs with the core. The binding decisions behind it are ADR 0006 (Surface API), ADR 0012 (External Display),
ADR 0015 (private instance repositories) and ADR 0019 (surface contract).

## 1. Three places, three kinds of work

| Place | What goes there | What never goes there |
|---|---|---|
| **This repository** (public) | Generic layouts under `surfaces/`, design-token defaults, changes to the surface contract, tests, docs. Example data only, under invented names. | Any real deployment's name, logo, colours, texts, people, places, URLs or screenshots with real content. |
| **The deployment's instance repository** (private, one per deployment) | `instance.toml`, design-token overrides (the deployment's look), logos and fonts, texts and content, deployment scripts, and design prototypes that show real names or content. | Core code. A change the core needs is an issue and a pull request here, not a patch in the instance. |
| **Your organisation's notes** (wherever your team keeps minutes, plans, UX research) | Decisions, minutes, UX concepts, handovers, links. | Code of any kind, including prototypes. Notes point to code; they never hold it. |

Rule of thumb: if it runs, it is code and belongs in one of the two repositories. If it names a real deployment,
it belongs in the private one.

## 2. How a contribution reaches `main`

1. **Issue first.** Every piece of work starts as an issue in this repository; it is the task description. A
   missing piece of data is an issue against the surface contract (section 3), not a field you invent.
2. **Branch and pull request.** Name the branch `<issue number>-<short-slug>`. Open the pull request as a draft
   early, mark it ready when done. The body says what changed and why, and ends with `Closes #<n>`.
3. **Commit identity.** Commit with your GitHub login as name and your GitHub no-reply address
   (`<id>+<login>@users.noreply.github.com`, shown under GitHub settings, Emails) as email. Every commit carries
   `Signed-off-by:` with the same name and address (`git commit -s`), see CONTRIBUTING.md section 2. Your first
   pull request also carries the CLA line from CLA.md.
4. **Checks.** CI runs the test suite, the DCO check, the merge guard and the publish check. Run
   `python3 tools/publish_check.py --repo . --public-only` locally before you push.
5. **Merge.** The maintainer merges once every check is green and the review is done. You never push to `main`.

## 3. Building against the contract

The surface contract (ADR 0019) is the only meeting point between a surface and the core:

- **Read** the interactive docs of the contract server (`/docs`) and the committed `surfaces/contract/openapi.json`
  (request/answer) and `surfaces/contract/asyncapi.yaml` (push channels).
- **Run** the real server in fixture mode, which replays an invented session over the real endpoints, and build
  against it. Never build against a self-made simulation or a copy of the page: it drifts from the real data and
  cannot be deployed.
- **Fields marked planned** (`x-status: planned`) are filled in fixture mode and empty in a live session until
  their issue is done. Design for both.
- **Done** means the conformance test passes for your surface against every fixture scenario, and the design gate
  screenshots are attached to the pull request.

## 4. Look versus layout

The layout (zones, what shows when, the logic that switches views) is generic and lives here. The look of one
deployment (colours, fonts, logo, wording) is a set of design-token overrides plus content in its private
instance repository. A layout that only works with one deployment's content is not generic yet; make the
content a parameter.
