# Official-source ingestion (design)

Status: STALE DRAFT (written 2026-07-08, revisited 2026-07-23). No code shipped. The n8n
orchestrator this draft commits to was discarded a day later (containerized node-graph tools
cannot reach the host CLIs; see the README roadmap), and the `--profile automation` compose
profile described below was never added. The ingestion notes (sources, official-image rule,
unsigned lane) are still the reference if this gets picked up; re-decide the orchestrator then.
The cited research store (`.research/official-source-ingestion/`) is local-only and gitignored.

## What this is
An opt-in, isolated automation that watches OFFICIAL government/institutional broadcasts (Argentine
municipal and provincial press offices: intendencia, provincia, etc.) and turns each official release
(an official image + official text) into an UNSIGNED article draft in the newsroom. No person signs
it; the picture is the one the source provided, not AI-generated. It is the automated feed behind the
"institutional / unsigned" writing lane (see the unsigned workflow in `prompts/workflow/`).

## Channel decision (from the research)
- **Email is primary.** Press offices already run opt-in "gacetilla de prensa" mailing lists: subject,
  body text, attached JPEGs, from an institutional `prensa@municipio.gob.ar` address. Sanctioned,
  reliable, and it carries the official photo as an attachment.
- **Telegram Channel is a clean secondary,** via a Telethon userbot that follows the public channel (a
  plain bot cannot read a channel it does not administer).
- **WhatsApp Channel is human-in-the-loop only.** No official read API for followers; every unofficial
  gateway rides a reverse-engineered protocol with a real ban timeline. Do not automate it.

## Isolation (same rule as the Telegram bridge)
Automation runs as its OWN container behind an opt-in compose profile (e.g. `--profile automation`),
so a plain `docker compose up` never starts it. It talks to the newsroom over the same publish/write
API the CLI uses; it never imports the newsroom code. Secrets (mailbox creds, allowlist) live in a
gitignored `.env` and are never handed to any agent.

## Orchestrator: n8n (chosen direction)
Chosen direction (operator, 2026-07-08): plug this into **n8n** as the orchestrator. n8n owns the
triggers and routing (its Email/IMAP trigger, Telegram trigger, and Webhook), running in its own
opt-in container. The one caveat from the research: the trust gate (sender allowlist + DKIM/DMARC
verification, the crux since we publish "official" content) is awkward to express robustly inside n8n
Function nodes, so keep it as a small dedicated verify step, an HTTP call from n8n to a tiny
"is-this-really-official?" endpoint (or the newsroom's ingest endpoint) that does the DKIM/allowlist
check before a draft is created. So: n8n for triggers + routing, a thin trust-gate service for the
security-critical decision. (A fully bespoke Python worker remains the fallback if n8n proves too
heavy.)

Pipeline:
1. **Ingest.** IMAP IDLE (`aioimaplib`) on a dedicated mailbox; fall back to polling. Gmail API
   `watch`/Pub/Sub only if the box is Gmail-hosted. One plus-tag or plain mailbox per source.
2. **Trust gate (fail closed).** Accept a message only if BOTH: (a) `From` domain is on the allowlist
   of verified official press addresses, AND (b) `dkim=pass` with DMARC alignment to that domain
   (read your own MTA's `Authentication-Results`, or verify with `dkimpy`). Reject and log everything
   else for human review. `From` alone is spoofable, so the DKIM check is mandatory.
3. **Parse.** Extract the text (`text/plain`, else sanitized HTML) and the image attachment(s); dedup
   on `Message-ID` + a content hash.
4. **Create an unsigned draft.** POST to the newsroom to create an institutional article (no human
   byline), attaching the provided official image (uploaded via `/media` first), tagged with the
   source (intendencia / provincia / ...) derived from the plus-address or sender.

## Ingestion contract (what the worker sends)
The worker uploads the image, then submits an article whose author is the institutional byline (not a
persona) and whose metadata carries the source provenance and the "image is provided, not generated"
flag. The exact field shape is pinned by the article contract
(`newsroom/contracts/vendored/v1/article.schema.json`) once the unsigned lane lands; until then this
doc is the reference, not a running service.

## Notifications
Operator-facing status can reuse the Telegram bridge's long-run pattern (typing keepalive + a "still
working" note refreshed with editMessageText). Long polling has no reply deadline.

## Open questions (per-source onboarding)
Which press offices; whether their `*.gob.ar` domains actually sign DKIM / align DMARC; whether their
list software accepts "+tag" subaddresses; image rights and how to credit ("Prensa Municipalidad de
X"); de-dup if the same release arrives by email and Telegram; correction handling (update the draft
vs create a new one). See the research file's Open Questions.
