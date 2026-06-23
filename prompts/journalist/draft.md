# journalist / draft (v0 placeholder)

Authored in Step 5. Persona-conditioned prose. The persona is re-injected FRESH
on every draft call (the "helpful assistant" attractor lives in the weights, so a
persona stated once drifts back to neutral over a long generation), and the draft
runs at a warmer temperature to fight voice homogenization across journalists.
Journalists never see each other's drafts.

The body is UNBOUNDED: no max-tokens, no word or sentence cap, no "be brief".
Voice and length are the task; capping either breaks it.
