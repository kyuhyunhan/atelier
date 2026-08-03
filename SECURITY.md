# Security policy

## Reporting

Please report suspected vulnerabilities via **GitHub's private security
advisory** for this repository (Security → Report a vulnerability) rather than
a public issue. You should get a response within a week.

## What counts as sensitive here

atelier is a *local-first* engine: no server component of ours holds your
data, and the engine repo contains no user content by design. The interesting
surfaces are:

- **The MCP HTTP transport** (`atelier serve --http`) — bearer-token
  authenticated, binds loopback by default. A bypass of the token check or a
  non-loopback default would be a vulnerability.
- **The pre-commit / CI PII guard** — its job is keeping personal content out
  of *public* repos. A bypass that lets staged personal content through
  silently (as opposed to the documented `--no-verify` escape) is a
  vulnerability in spirit even though it is "just" a hook.
- **Vault-boundary writes** — the engine promises to write only inside the
  configured vault. Any path-traversal that lets a tool write outside it
  breaks a hard rule (CLAUDE.md #7).

## Out of scope

- Anything requiring the attacker to already control `~/.atelier/` or the
  vault working copy (that is the user's own trust domain).
- The contents of a user's own vault (private by construction; not our data).
