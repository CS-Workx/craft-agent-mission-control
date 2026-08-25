# Workspace Routing — tasking agents across Craft Agents

This is the map an agent reads before tasking work into another Craft Agents workspace. It answers
three questions: **what the workspace framework is**, **how to hand a task to another workspace**,
and **which workspace to send a given task to**.

Pair it with:
- `SKILL.md` — the `mc.py` command reference (the tool that does the tasking).
- `mc.py` — the CLI itself.
- `~/Developer/craft-agents-workspaces/` — the consolidation project record (`HANDOVER.md`,
  `PROGRESS.md`, `REPLICATION.md`); the source of truth for *why* the workspace set looks like it does.

---

## 1. The framework in 60 seconds

Craft Agents is a desktop app. Steff runs **one app, many workspaces** (currently ~17 registered in
`~/.craft-agent/config.json`, consolidated down from 31). Each workspace is a self-contained agent
environment on disk at `~/.craft-agent/workspaces/<slug>/`:

- `config.json` — name, id (UUID), slug.
- `skills/`, `sources/` — the workspace's installed skills and connected data sources. **This is what
  makes a workspace fit for a job:** a workspace can only do well what its skills and sources equip it for.
- `sessions/<id>/session.jsonl` — every session is a **card on that workspace's kanban board**. The
  first line is the header (status, labels, model, cost, tokens). `sessionStatus` and `labels` live there.
- `statuses/config.json`, `labels/config.json` — the board's columns and label tree.
- `automations.json` — event/schedule triggers that **create sessions** (prompt actions), send webhooks,
  etc. 573 existing sessions were born this way (`triggeredBy`).

Key constraints (from the consolidation project — do not relearn the hard way):
- **Agents may set OPEN statuses only.** Every CLOSED status (`done`, `cancelled`, …) is category-blocked
  for agents. Closing a card is Steff's board click. Substitutes for "put it away": `automated` and
  `handed-off` (both OPEN).
- **`automations.json` is machine-local** (gitignored in most workspaces). It does not travel when a
  workspace is folded; edit it in place.
- **Never `rm` a workspace** (retired → `~/.craft-agent/_archive/`). Never git-sync an Obsidian vault.

---

## 2. How to task another workspace (`mc.py`)

The in-session tools (`create_task`, `spawn_session`, `send_agent_message`, `set_session_status` via the
SDK) are **scoped to the workspace the agent runs in** — with one exception: `set_session_status(id,…)`
reaches any workspace by id. To *see* and *task* work across **all** workspaces, use `mc.py` (full
reference in `SKILL.md`). Four tiers, increasing in effect:

| Tier | Command | What it does |
|---|---|---|
| 1 read | `mc list`, `mc queue`, `mc show` | cross-workspace triage; always safe |
| 2 trigger | `mc status`, `mc label` | flips a card's status/labels → fires that workspace's `SessionStatusChange` / `LabelAdd` automations |
| 3 **dispatch** | `mc dispatch <ws> --prompt "…"` | injects a one-shot `SchedulerTick` prompt automation → the scheduler **spawns a real, briefed agent session** in the target workspace |
| aux | `mc open`, `mc new` | opens the app UI via `craftagents://` deeplink (the deeplink **cannot carry a prompt**) |

**Tier 3 is the only way to hand a briefed task to another workspace.** Dispatch is **dry-run until
`--go`** and spawns a live agent — a real side effect — so **confirm intent with Steff before `--go`**.
Each dispatch is one-shot; remove it with `mc cleanup`.

```bash
python3 ~/.agents/skills/mission-control/mc.py queue
python3 ~/.agents/skills/mission-control/mc.py dispatch creator \
  --prompt "Draft this week's LinkedIn carousel from 09-outputs/drafts/notes.md." --at +5m   # preview
python3 ~/.agents/skills/mission-control/mc.py dispatch creator --prompt "…" --at +5m --go   # arm (after OK)
```

---

## 3. Workspace routing table

Pick the workspace whose **skills and sources already fit the task**. Volume = current session count
(a rough signal of how battle-tested the workspace is). Purposes marked *(inferred)* are read from the
workspace's skills/sources, not a written charter — verify by reading its `skills/`/`sources/` if a task
is high-stakes.

| Workspace | Task it for | Signature skills / sources | Automations | Notes |
|---|---|---|---|---|
| **master** | Cross-domain orchestration; overnight multi-step runs; anything needing the full skill/source superset or the Orchestrator/SuperBrain cadence | 217 skills, ~65 sources (superset of everything) | SuperBrain close-day/handoff/compile; Orchestrator morning/evening | The **hub**. Default when a task spans domains or you're unsure. Heaviest, most sources. |
| **creator** | **Content engine**: authoring, blog/article, brand voice, copywriting, social posts, community, audiobooks, video/avatars | author, blog-article, brand-voice, copywriter, community-manager, ai-daily-trending; elevenlabs, heygen, postiz, youtube, linkedin, mastodon, notebooklm, voicebox | AI Daily Trending, AI Weekly Review, AI Deep Insight, SuperBrain close-day | The publishing/marketing brain. Content-engine outputs are **reviewed, not auto-filed**. |
| **developer** | Software dev + web/hosting/deploy + CAD/hardware + SEO/geo-audit | debug, deploy-checklist, design-review, developer, geo-audit, floor-plan, electrical; blender, freecad, kicad, linode, hostinger, godaddy, sites | SuperBrain close-day | Broadest technical workspace; includes physical/CAD and infra. |
| **assistant** | **Personal + business admin**: email/inbox, calendar, CRM, accounting, legal, compliance, business consulting | inbox, business-consultant, compliance-manager, legal, microsoft-365, google-workspace; gmail, google-*, microsoft-graph, zoho-crm, yuki, buffer, craftbot | SuperBrain close-day | Where live email/calendar/CRM/accounting live. Live-data actions need approval. |
| **syntrabizz** | Focused software engineering in the Syntra context (tight code loop) | commit, debug, explain, fix, locate, refactor, review, test; github, context7 | SuperBrain close-day | Lean coding workspace; no infra/CAD noise. |
| **syntravibecoding** | Rapid app building / "vibe coding"; spec → build → deploy | spec-writer, vibe-code, deploy; craftwork, github | — | Prototyping and teaching-oriented build sprints. |
| **strategist** | Strategy & decision frameworks applied to a question | blue-ocean-strategy, cynefin, swot-analysis, systems-thinking, scenario-thinking, score-model | — | Analysis, not delivery. Send a framing question, get structured strategic output. |
| **copilot** | Microsoft Power Platform: Copilot Studio, Power Automate, Power BI, Graph queries | copilot-studio, power-automate, power-bi, graph-query; microsoft-graph | — | Microsoft-ecosystem automation and BI. |
| **n8n** | Building/debugging **n8n** workflows | n8n-build, n8n-debug, n8n-inspect, n8n-template; n8n-mcp, supabase | — | Low volume but purpose-built for n8n. |
| **ollama** | Local LLM / Ollama model ops and status | ollama-status; perplexity, superbrain | Daily Ollama model update | Infra-ops for the local model stack. |
| **the-house-of-ai** | **The House of AI** brand content; AI compliance / EU AI Act angle | compliance-angle, compliance-watch, content-brief, linkedin-post, persona-lens; the-house-of-ai-vault | Monthly Compliance Watch | Brand launching Q4 2026. Regulation-aware AI content. |
| **aetherials** | **Creative fiction / worldbuilding** (the novel); narrative, scenes, illustration | worldbuild, brainstorm, express, image-gen, page-layout, scenario; aetherials-vault, lorken-vault | — | Long-form fiction; not a work/business workspace. |
| **beneo** | Beneo client work | craft-csworkx, github, perplexity | — | Client-scoped; keep client work here. |
| **ali** | **OFF-LIMITS.** Steff's daughter's tutoring vault | tutoring skills; obsidian-ali | — | **Do not task or touch unless Steff explicitly asks.** |
| **workoslive** | The WorkOS Live template/system itself (this repo) | github, workoswiki | Friday weekend overview (draft) | Meta-workspace for the portable Work OS. |
| **workos** | (empty) | — | — | No sessions; ignore unless setting it up. |
| **demo / demo20260821 / demo20260821b** | Throwaway demos & experiments | varies | one has a keyword automation | **Not for real work.** Safe targets for testing `mc.py` (some are unregistered). |

---

## 4. Routing decision guide

Match the *nature* of the task, then confirm the target's skills/sources fit:

- **Write/publish/market anything** (post, article, script, brand asset, newsletter, audio, video) → **creator**.
- **Code, deploy, host, SEO, CAD** → **developer** (broad) or **syntrabizz** (tight code loop) or
  **syntravibecoding** (fast prototype). n8n workflow specifically → **n8n**. Power Platform → **copilot**.
- **Email, calendar, CRM, invoicing, legal, admin, business advice** → **assistant**.
- **Strategy, framework analysis, a hard decision** → **strategist**.
- **AI-regulation / compliance-flavoured brand content** → **the-house-of-ai**.
- **Fiction / worldbuilding** → **aetherials**.
- **Local-model ops** → **ollama**.
- **Spans several of the above, or needs the full toolset, or is an overnight orchestration** → **master**.
- **Client-specific** → that client's workspace (e.g. **beneo**).
- **Just testing the tasking mechanism** → a **demo** workspace, never a live one.

When two fit, prefer the **more specialised** workspace over master — a narrower workspace has less
source/skill noise and cleaner output. Reserve master for genuinely cross-domain or orchestration work.

---

## 5. Guardrails & etiquette

- **Confirm before `mc dispatch … --go`.** It starts a live agent in another workspace. Preview
  (dry-run) first, show Steff the target + time + prompt, then arm.
- **Brief fully.** A dispatched agent starts cold with no conversation context. The prompt must carry
  everything: goal, where to read inputs, where to write outputs, and any "draft only / no external
  actions" guardrails — mirror the style of the existing `automations.json` prompts.
- **Set a sane permission mode.** Default `allow-all` only for trusted internal work; use a stricter
  mode when the task could touch live email/CRM/publishing.
- **Never target `ali`.** Never task a workspace into a CLOSED status (agents are blocked anyway).
- **Clean up.** One-shot dispatches are tagged `[mc-dispatch]` / `mc-…`; `mc dispatched` lists them and
  `mc cleanup` removes them. Don't leave armed automations lying around.
- **Prefer draft-and-review** for anything outward-facing; the content-engine and Orchestrator
  automations are deliberately review-gated, not self-filing. Match that norm.

---

## 6. Keeping this current

Regenerate the workspace map from disk when workspaces are added/folded:

```bash
python3 ~/.agents/skills/mission-control/mc.py list | ...   # or re-survey skills/sources per workspace
```

The registry and consolidation rationale are owned by `~/Developer/craft-agents-workspaces/`
(`HANDOVER.md` first, then `PROGRESS.md`). If this table and that record disagree, that record and the
live disk win — update this file, not the other way around.
