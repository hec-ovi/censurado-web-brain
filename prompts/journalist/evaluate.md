# journalist / evaluate (v0 placeholder)

Authored in Step 5. The separate-endpoint evaluator returns
{verdict: PASS | REVISE, feedback, failing_sections}. PASS means done. It MUST
resolve to a different endpoint than the drafter (a model cannot reliably grade
its own work), or degrade to a rules-grounded check when only the local model is
available.

No output-length cap.
