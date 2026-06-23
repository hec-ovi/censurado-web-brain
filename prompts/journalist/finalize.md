# journalist / finalize (v0 placeholder)

Authored in Step 5. A dedicated JSON-only call that structures the finished
article into the publish payload (title, body, author, section, optional topics
and slug). Pydantic-validated with a single retry on parse failure (the format
tax: structure in its own call). The body field is never length-constrained;
structured output comes from validate-and-retry, not truncation.

No output-length cap.
