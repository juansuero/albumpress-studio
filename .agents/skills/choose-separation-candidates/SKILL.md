---
name: choose-separation-candidates
description: Discover installed audio-separator Models and Presets, configure Candidate slots, run bounded calibration, process one Candidate, or start approved sequential album separation. Use when a user asks which separator to try, wants A/B comparisons, needs a calibration, or wants Instrumental Outputs generated.
---

# Choose Separation Candidates

Read `docs/agents/skill-runtime-contract.md` and `CONTEXT.md`.

1. Inspect the current Project, runtime catalogue, calibration status, album status, existing Outputs, and Candidate fingerprints.
2. Refresh the catalogue only when discovery is stale or explicitly requested. Never invent a Candidate ID.
3. Preserve valid configured Candidates. Present unavailable or uncached Models honestly, including expected download or CPU implications available from state.
4. Save slot changes through the application. Do not silently replace a recorded Candidate.
5. Run one bounded calibration before sustained work when the Candidate lacks human semantic validation.
6. Expose the calibration audio to the user and stop for confirmation that it is the intended Instrumental. Technical media validation is insufficient.
7. Start a single-Track Candidate only when requested. Start the sequential album queue only after explicit authorization, reporting Project Folder, Candidate set, estimate, disk preflight, and resume state.
8. Never skip calibration, force reprocess, or retry expensive work merely to make progress.

Report Candidates used, cache/fingerprint evidence, Output scope, job ID and stage, and whether human semantic confirmation or sustained-work authorization remains pending.
