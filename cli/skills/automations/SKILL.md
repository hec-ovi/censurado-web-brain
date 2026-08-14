---
name: automations
description: >-
  Run the whole newsroom on the spot, or manage the schedule registry that fires it on its own:
  create, list and retire automations, pick the model lane, and take an edition live in one
  command. Use when the user wants to run an automation now, schedule a recurring edition,
  see what is scheduled, or stop one from firing.
---

# Automations

An automation is one full newsroom batch: every author with a beat and attached sources pitches
candidates from their own feeds, the jefe de redaccion picks the edition and ranks the portada,
and the selected notes are written in parallel as durable workflows.

Two ways it runs. The **registry** holds the recurring ones and the executor container fires them
on their times. `automation` runs one **right now**, with no registry involved.

## Run one now

```
python3 cli/censurado.py automation
python3 cli/censurado.py automation --lane remote --prompt "la agenda economica de la semana"
python3 cli/censurado.py automation --no-deploy --authors lara-arianna,vector-omni
```

| flag | what it does |
|---|---|
| `--lane local` (default) | every stage runs on the local llama.cpp endpoint |
| `--lane remote` | every stage runs on the configured remote endpoint |
| `--prompt "..."` | steers the edition; reaches the candidates and the jefe prompts |
| `--authors a,b` | limit the pitch to these handles (default: every eligible author) |
| `--run-id ID` | reuse an id to resume a batch that stopped partway |
| `--deploy` (default) | publish the edition, then push the snapshot to the public site |
| `--no-deploy` | hold every piece in preview; nothing is published anywhere |

You pick a lane, never a model. Each stage takes the model saved for that lane in the panel's
Models section, so changing the model is a panel edit, not a flag.

**`--deploy` is on by default and it goes to the public internet.** It ships the entire local
snapshot, not only this edition's notes: whatever is published in the local backend goes live
with it. Use `--no-deploy` when the user only wants to see what the newsroom produces.

## The registry

```
python3 cli/censurado.py automations
python3 cli/censurado.py automation-create "Edicion de la manana" --times 07:00
python3 cli/censurado.py automation-create "Repaso semanal" --cadence weekly --weekdays 0 --times 20:00 --mode auto
python3 cli/censurado.py automation-delete edicion-de-la-manana --yes
```

| flag | what it does |
|---|---|
| `--times 07:00,19:00` | required; `HH:MM` fire times on the executor's own wall clock |
| `--cadence daily\|weekly\|monthly` | default daily; weekly needs `--weekdays`, monthly needs `--monthdays` |
| `--weekdays 0,3` | 0 = Sunday through 6 |
| `--monthdays 1,15` | 1 through 31 |
| `--mode preview\|auto` | hold each piece, or publish them to the local portal (default: preview) |
| `--task batch\|topics` | the article edition, or the topic sweep (default: batch) |
| `--prompt "..."` | directive this automation always runs with |
| `--authors a,b` | limit it to these handles |
| `--slug my-slug` | explicit url-safe slug instead of one derived from the name |
| `--disabled` | create it paused |

`automations --all` also lists the retired ones.

`--mode preview` holds each piece for approval; `--mode auto` publishes them to the local portal
in portada order. Neither mode deploys: the registry never reaches the public site, only the
`automation` verb's `--deploy` does.

`automation-create` upserts on slug, so re-running it with the same slug edits that automation in
place and keeps its run history. Every field is replaced by what you pass, so send the whole shape
each time rather than just the part you are changing. `automation-delete` tombstones: the run
history survives and `automation-create` with the same slug brings it back.

## Reading a run

`automations` prints each automation with its recent-run strip: `queued`, `running`, then `ok` or
`failed` with a short detail. That strip is what the panel's Automation tab shows.

The batch's own artifacts land under `automation/pipeline/runs/<run-id>/`: `plan.json` is the
jefe's selection, `result.json` the per-article outcome. A batch exits 0 even when individual
notes were rejected, so read `result.json` for what actually happened, not the exit code.
