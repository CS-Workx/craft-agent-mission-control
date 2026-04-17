# Security Policy

## Threat model

Mission Control is designed for **single-user local machines**. The default configuration binds a local HTTP server to `127.0.0.1:9753` and has no authentication. This is intentional:

- The server listens on **localhost only** — it is not accessible from the network.
- The server reads and writes `session.jsonl` files under `~/.craft-agent/workspaces/`.
- The server has **no user accounts, no passwords, no tokens** — anyone who can reach `127.0.0.1:9753` on your machine can see and modify your sessions.
- Session contents may include sensitive information (code, API keys, private chats). Treat the machine running Mission Control as you would treat any environment that has access to that data.

**v2.0.1 closed a CSRF vector** where the previous `Access-Control-Allow-Origin: *` header combined with no auth meant any website the user visited could mutate session files through the running server. Mutating `POST` endpoints now reject cross-origin requests, and workspace / session identifiers in request bodies are validated against strict patterns with a resolved-path check to prevent traversal into paths outside `~/.craft-agent/workspaces/`. If you are still running v2.0.0, upgrade.

**v2.1.0 added input validation** — a 64 KB POST body cap, a strict `^[a-z][a-z0-9-]{0,31}$` status regex, label type/length limits, URL length and control-character checks, and a JSON parse guard that no longer leaks stack traces to the browser. Silent `except Exception: pass` blocks in the workspace scanner have been replaced with `logger.warning` calls so malformed `config.json` failures are visible rather than swallowed.

**Local-only by design.** Mission Control reads `~/.craft-agent/workspaces/` from the local filesystem. Headless SDK deployments, remote workspaces, and team-shared instances are out of scope for v2.x — see [INSTALL.md](INSTALL.md) for details. Shared-instance mode is a v3.0 roadmap item.

### What this means in practice

| Situation | Is it safe? |
|---|---|
| Single-user laptop, local installation | ✅ Safe — the intended use case |
| Shared workstation with multiple user accounts | ⚠️ Any other logged-in user on the same machine can reach the server |
| Running inside a container or VM | ✅ Safe if the network is not port-forwarded |
| Exposing `127.0.0.1:9753` to the public internet | 🚫 **Do not do this.** Mission Control has no auth and is not designed for this |
| Running on a development server shared with teammates | 🚫 **Do not do this** yet — see ROADMAP for shared-instance mode |

### Data handling

- **No telemetry.** Mission Control does not make any outbound HTTP calls to servers you don't control.
- **No external CDN.** All CSS, JS, and fonts are embedded in the served HTML. You can open the dev tools and verify.
- **Local storage only.** All data read/written stays on disk under `~/.craft-agent/`.

## Reporting a vulnerability

If you find a security issue:

1. **Do not open a public issue.**
2. Open a private GitHub security advisory at https://github.com/CS-Workx/craft-agent-mission-control/security/advisories/new
3. Include a minimal reproduction, your OS/Python version, and the Mission Control version from `curl http://localhost:9753/health`.
4. Give us a reasonable window to respond before any public disclosure.

We aim to acknowledge security reports within 7 days.

## Supported versions

| Version | Status |
|---------|--------|
| 2.x | ✅ Actively supported — get security fixes |
| 1.x | ⚠️ Please upgrade to 2.x |

## Hardening tips

If you want to reduce risk further:

- **Don't share your user account.** The first line of defense is macOS user account isolation.
- **Stop the server when you don't need it.** `launchctl unload ~/Library/LaunchAgents/com.craft-agent.mission-control.plist`
- **Rotate any secrets that may have been typed into a session.** Sessions can contain pasted API keys; treat them as sensitive.
- **Audit `install.sh` before running.** Open the script, read it, then run it. The one-liner (`curl | bash`) is convenient but the manual path exists for good reason.

## What Mission Control does not do

- It does **not** transmit data off the machine.
- It does **not** require root/sudo to install or run.
- It does **not** modify any part of Craft Agents itself; it reads and writes `session.jsonl` files only.
- It does **not** open firewall ports or change macOS network settings.

---

Built by [Steff Vanhaverbeke](https://github.com/CoachSteff).
