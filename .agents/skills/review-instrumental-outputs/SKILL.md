---
name: review-instrumental-outputs
description: Review AlbumPress Studio Candidate Outputs at a shared timestamp, manage listening loops, record human semantic decisions, reject invalid Outputs, and persist Final Instrumental Selections. Use when a user wants to compare stems, choose the best Candidate, approve or reject results, or finish Track selections.
---

# Review Instrumental Outputs

Read `docs/agents/skill-runtime-contract.md` and `CONTEXT.md`.

1. Inspect current valid Outputs, preview flags, semantic status, existing Selections, duration mismatches, and media availability.
2. Use the application's Compare surface for synchronous audition. Preserve the shared timestamp when switching Candidates and use loops for difficult passages.
3. Let the human listen. Never infer approval from technical validity, silence analysis, filenames, or an agent's preference.
4. Record approval and Selection only after the user identifies the acceptable Candidate. Reject only the exact Output the user reviewed.
5. Keep previews ineligible for Final Selection. Surface materially shorter Outputs and playback failures.
6. Use bulk approval only when one Candidate contract applies to every listed ready Track and the user explicitly approves the displayed scope.

Report each Track as selected, pending review, rejected, or blocked. Include Candidate slot, Output fingerprint, semantic status, and persisted Selection evidence.
