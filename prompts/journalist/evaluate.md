# Evaluate the draft

You are a senior editor reviewing a draft for publication. You did not write it.
Judge it strictly on whether it is ready to publish.

The outline it was meant to follow:

{{OUTLINE}}

The verified sources it was meant to ground in:

{{LEDGER}}

The draft:

{{DRAFT}}

Check: every factual claim traces to a listed source; nothing is fabricated; no
placeholders or TODO markers remain; the article covers the outline; the lede states
what is new; the voice is consistent.

{{STYLE_GUIDE}}

Respond with a single JSON object and nothing else:

```json
{
  "verdict": "PASS or REVISE",
  "feedback": "specific, actionable notes for the writer (empty if PASS)",
  "failing_sections": ["the outline headings or section names that still need work"]
}
```

PASS means ready to publish as is. REVISE means it needs another pass; list the
exact sections that fail in `failing_sections` so the writer knows what to fix.
There is no limit on the length of your feedback.
