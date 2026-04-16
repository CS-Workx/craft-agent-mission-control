# Contributing to Mission Control

Thanks for your interest in contributing! Mission Control is a small, focused project and contributions of all sizes are welcome.

## Before you start

- Open an issue first for anything non-trivial. A quick `+1 I want to work on this` comment prevents duplicate work.
- Check [ROADMAP.md](ROADMAP.md) to see if your idea aligns with the project's direction.
- For small fixes (typos, docs, obvious bugs) — skip the issue and send a PR.

## Core principles

These shape every review decision. Keep them in mind:

1. **Zero runtime dependencies.** The whole dashboard runs on Python 3 stdlib. Do not add `pip install` requirements. This is what makes the project trivially installable and auditable.
2. **Single-file core.** `dashboard.py` contains the server, the HTML, the CSS, and the JS. Splitting it into a package would add build complexity without real benefit at this scale.
3. **Local-first.** No cloud calls, no telemetry, no external CDN in the rendered HTML.
4. **Progressive enhancement.** Static HTML mode must keep working even when the server features (drag-and-drop, batch ops, etc.) aren't available.

## Development setup

```bash
git clone https://github.com/CS-Workx/craft-agent-mission-control.git
cd craft-agent-mission-control

# run the server against your real data
python3 dashboard.py --serve 9753

# OR produce a static snapshot for quick visual inspection
python3 dashboard.py /tmp/test.html && open /tmp/test.html
```

No build step, no virtualenv needed.

## Testing your change

Before submitting a PR, at minimum:

1. **Syntax check** — `python3 -m py_compile dashboard.py`
2. **Static mode** — `python3 dashboard.py /tmp/test.html` and open the file. The UI should render correctly with your data.
3. **Server mode** — `python3 dashboard.py --serve 9753` and exercise any feature you touched (drag-and-drop, labels, batch ops, archive, etc.).
4. **Cross-browser sanity** — test at least in Safari AND Chrome if your change involves CSS or JS.
5. **Launch Agent** (if you touched the plist or installer) — `bash install.sh --yes` followed by `curl http://localhost:9753/health`.

## Commit & PR conventions

- **Conventional commits** for messages — `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`, `test:`. Example: `feat: add keyboard shortcut for archive paging`.
- **One logical change per PR** — if your work contains both a refactor and a new feature, split it.
- **Update CHANGELOG.md** — add an entry under `[Unreleased]` describing user-visible changes. Format follows [Keep a Changelog](https://keepachangelog.com/).
- **Update README.md / SKILL.md** — if your change is user-facing, documentation must land in the same PR.
- **Keep the PR description specific** — "what and why" in 3-5 sentences beats a wall of screenshots.

## Code style

- **Python:** 4-space indent, f-strings over `%` and `.format()`, type hints where they aid clarity (not required everywhere).
- **HTML/CSS/JS embedded in Python:** 2-space indent for CSS and JS, single quotes for JS strings, double quotes for HTML attributes. Match the existing file.
- **No frameworks** — no React, no Vue, no jQuery. Vanilla JS with template literals is the house style.

## What belongs in the repo

- The single-file dashboard and its tests
- Installation scripts (`install.sh`, `uninstall.sh`, macOS plist)
- Docs (`README.md`, `INSTALL.md`, `SECURITY.md`, `ROADMAP.md`, `CHANGELOG.md`)
- CI config and GitHub templates

## What does not belong

- Personal paths, API keys, credentials, or machine-specific config
- Third-party JS/CSS libraries (see "Zero runtime dependencies")
- Screenshots of real user data — use redacted test data if a screenshot is truly needed

## Reporting bugs

See [SECURITY.md](SECURITY.md) for reporting security issues. For non-security bugs, open a GitHub issue with:

1. What you did
2. What you expected
3. What happened instead
4. Your macOS version, Python version, and Mission Control version (from `curl http://localhost:9753/health`)

## License

By contributing, you agree that your contributions will be licensed under the MIT License, the same license that covers the rest of the project.

---

Built by [Steff Vanhaverbeke](https://github.com/CoachSteff). Thanks for helping make it better.
