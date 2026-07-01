# Publish (the last gate)

Publishing puts the piece on a public site, so review before you POST. This is the last
gate: do NOT publish while any evaluation dimension is still failing.

**Interactive (the default).** Show the human the full draft: title, subtitle, description,
body, and the section, slug, topics, and any widgets you propose. Ask: **"Publish as-is, or
change anything?"** If they want one part different, change just that part on the local draft
and show it again. Publish only once they approve. Only skip this pause when you were
explicitly told to run unattended.

Publish with the toolkit, which reads the operator token and builds the strict JSON for you:

    python3 cli/censurado.py publish --author <id> --title "..." --subtitle "..." \
      --description "..." --body-file draft.md --topics "tag1,tag2" [--image /media/<sha>.png]

The publish contract (the exact request, the strict JSON keys, the response codes, the
widget snapshots, and where the article lands on the portada) is in `cli/AGENTS.md`; follow
it for the mechanics. Expect `201` and `{"id":...,"slug":...}` (`200` means an identical
article already existed and nothing was written).

Then verify it is live: the generate watcher rebuilds within a few seconds, so a `404` right
after a `201` is just timing, wait and re-check `http://localhost:8080/latest/` and the
author page. This article is done.
