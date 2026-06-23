# manager / triage (v0 placeholder)

Authored in Step 6. The manager runs a bounded ReAct loop: search the day's top
news, rank by relevance to the in-scope beats (tech, world, politics, economics),
and emit at most N_MAX assignments, each pairing a persona with an angle.

Bounds (enforced in code, not by the prompt): max_manager_steps, a tool-call
budget, a circuit breaker, and a forced "emit assignments now" terminal when the
step cap is hit. The manager is the SOLE fan-out point; journalists never spawn
agents.

No output-length cap anywhere.
