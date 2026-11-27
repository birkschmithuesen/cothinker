# ADR 0020: Host names and paths of a deployment

- **Status:** accepted, amended by ADR 0021, ADR 0027
- **Date:** 2026-11-24

## Context

Source: ADR 0015 (one private repository per deployment), ADR 0019 (surface contract), the first hosted
deployment with a meeting page, a room display and the contract API side by side.

Every deployment needs public addresses for its surfaces. Without a rule each one invents its own (a path on a
website, a subdomain, a port), links break when a deployment moves, and nobody can tell from a link which
deployment and which surface it is.

## Decision

1. **One slug per deployment.** Lowercase ASCII letters, digits and hyphens, 3–30 characters, starting with a
   letter. It is the name of the deployment's private repository without the `cothinker-` prefix
   (`cothinker-<slug>`) and `[instance] name` in its `instance.toml`. A slug never changes.
2. **One host per deployment:** `https://<slug>.cothinker.app/`. The product website stays on its own domain;
   `cothinker.app` itself carries no deployment.
3. **Fixed paths per surface**, the same in every deployment:

| Path | What |
|---|---|
| `/meeting/` | integrated meeting page (video call plus stage) |
| `/stage/` | the stage on its own |
| `/monitor/` | room display (External Display layout, ADR 0012) |
| `/api/` | surface contract, live mode (ADR 0019): `/api/snapshot`, `/api/events`, `/api/docs` |
| `/api-fixtures/` | surface contract, fixture mode, for building and testing surfaces |

   A surface gets its API base from `?api=` and defaults to `/api/` on its own host.
4. **Access by token in the link** (`?t=<token>`), checked by the front proxy on every path above; without a
   valid token the answer is 404. Tokens are per deployment and can be rotated without changing any path.
5. **DNS:** one wildcard record `*.cothinker.app` to the hosting server (IPv4 and IPv6); certificates per host
   name. A new deployment needs no DNS change.

## Consequences

- A link says which deployment and which surface it is; moving a deployment to another server is one DNS change.
- Surfaces never hard-code a host; tests run them against `/api-fixtures/` or a local fixture server.
- The deployment's front-proxy configuration lives in its private repository; this repository ships a generic
  template with `<slug>` placeholders.

## Rejected options

- Paths under the product website (`cothinker.eu/<slug>/…`): mixes the public website with private deployments and
  ties both to one server.
- One host for all deployments with the slug in the path: cookies, storage and tokens of different deployments
  would share one origin.
