---
name: translate
description: >-
  Localize the whole Censurado site into a new language, once per language: translate the
  reader-facing site strings and the operator panel strings, and generate that language's own
  editorial anchors (banned lexicon, slop phrases, orthography). Use when the user wants to add
  a language (pt, de, zh, ...) to the portal, or fill in what a half-translated language is missing.
---

# Localize the site into a new language

The site's every human-facing string lives in the backend as DB rows, keyed and grouped into
three catalogs (scopes): `frontend` (what readers see), `panel` (what the operator sees), and
`editorial` (the newsroom's per-language writing anchors). English is the base for the two UI
catalogs; the editorial anchors are authored in Spanish. This skill copies a base language into
a NEW target language, one language at a time. It is a one-time setup per language, not a
per-article step.

## The two-command loop

1. **Pull the job.** `python3 cli/censurado.py translate <lang>` prints a JSON job: for each
   scope, every base row that has NO `<lang>` row yet, with its `source` value and an empty
   `value` for you to fill. A key already present in `<lang>` is never listed (so a re-run only
   shows what is still missing). Save the JSON to a scratch file under `$CENSURADO_WORK`.
2. **Fill every `value`** in that file (see the two ops below). Keep every `key`, `scope`,
   `op` and `source` exactly as printed; only write into `value`.
3. **Apply it.** `python3 cli/censurado.py translate <lang> --apply --file <your-file>`
   (or `--file -` to pipe it on stdin). It upserts ONLY the missing rows and reports how many
   it wrote, skipped, and left blank. Apply never overwrites an existing row, so re-running is
   safe: fill more values and apply again to fill the gaps. The `<lang>` you apply MUST be the
   one you pulled the job for: apply writes rows for the command's `<lang>` and refuses a file
   whose `target_lang` differs, so name each scratch file after its language to avoid mixing them.

## Two ops, do not confuse them

Each scope in the job is tagged with an `op`:

- **`translate`** (`frontend`, `panel`): render the English `source` string faithfully in the
  target language. Same meaning, natural phrasing, same intent. Keep any `{brand}` or other
  `{token}` placeholders and any punctuation markers intact. Section labels (World, Technology,
  Politics, ...) are proper section names, translate them the way that language names those beats.
- **`generate`** (`editorial`): do NOT translate word for word. Author that language's OWN
  anchors, because slop, banned lexicon and orthography are language-specific: a German slop
  phrase is not a translated Spanish one. The `source` shows the Spanish row as a STRUCTURAL
  template. Many editorial values are JSON (a list like `["a","b"]` or an object like
  `{"x":"y"}`); keep the exact JSON shape and write a valid JSON string back into `value`, just
  with the target language's own words. Plain-text rows (an attribution example) are
  written idiomatically for that language.

## Notes

- The target language reaches readers only once the site regenerates and you `publicar`; the
  editorial anchors reach the newsroom immediately (the walk reads them live via
  `python3 cli/censurado.py editorial-rules <lang>`).
- A scope is absent from the job when the target already has a row for every one of that scope's
  keys, OR when `<lang>` is that scope's own base language (nothing localizes a base into itself:
  `es` skips `editorial`, `en` skips `frontend`/`panel`). Being the base for ONE catalog does not
  exempt the others: `translate es` still emits the whole English UI to translate, and
  `translate en` still emits the editorial anchors to generate. The job is empty only when every
  non-base catalog already has a `<lang>` row for every key.
- Auth is automatic. If a pull or apply fails with a stack error, run
  `python3 cli/censurado.py status` and relay the error, do not touch the DB or the code.
