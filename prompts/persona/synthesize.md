# persona / synthesize (v0 placeholder)

Authored in Step 3. Turns a one-line seed description into a full persona draft:
who-i-am (first person), style, public about, positive AND negative few-shots,
preferred sources.

Framing: direct role-play. Tell the model to BE the persona, not to describe one
(local Gemma-class models are weak at holding a persona, so contrast carries the
voice). Emit positive and negative exemplars together; style is learned from
contrast.

Rules for every prompt in this folder:
- Never cap output length. No max-tokens, no "in N words", no "be brief".
- The body and the exemplars finish on their own.
