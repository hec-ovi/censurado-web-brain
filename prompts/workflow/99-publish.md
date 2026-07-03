# Preview (put it on your site, still the last gate)

`preview` stages the finished piece to your LOCAL site (`localhost:8080`), so the user can
see it exactly as it will render. It is NOT public: going live to the public internet is a
SEPARATE `make deploy` step, never done here. This is still the last editorial gate: do NOT
preview a piece while any evaluation dimension is still failing.

**Interactive (the default).** Show the user the full draft: title, subtitle, description,
body, and the section, slug, topics, and any widgets you propose. Ask: **"Good as-is, or
change anything?"** If they want one part different, change just that part on the local draft
and show it again. Preview only once they approve. Only skip this pause when you were
explicitly told to run unattended.

Preview with the toolkit, which reads the operator token and builds the strict JSON for you:

    python3 cli/censurado.py preview --author <id> --title "..." --subtitle "..." \
      --description "..." --body-file draft.md --topics "tag1,tag2" [--image /media/<sha>.png]

Write a `--subtitle` and a one-line `--description`; preview needs both.

The field contract (the exact request, the strict JSON keys, the response codes, the widget
snapshots, and where the article lands on the portada) is in the write-article skill
(`cli/skills/write-article/SKILL.md`); follow it for the mechanics. Expect `201` and
`{"id":...,"slug":...}` (`200` means an identical article already existed and nothing was written).

On success the command prints the live link for you, so you do not build the URL yourself:

    PREVIEW: http://localhost:8080/a/<slug>-<hash>/  [live now]
    NEWEST:  http://localhost:8080/latest/

Copy that `PREVIEW:` link back to the user; it is where the piece is live-rendered (the tag
reads `still rendering` for a beat if the generate watcher has not rebuilt yet, then the page
resolves). Publishing to production (the public site) is a separate `make deploy` and only
happens on an explicit go-ahead. This article is done.
