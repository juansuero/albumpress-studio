# Skills for AI harnesses

AlbumPress Studio is agent-first. Codex coordinates the workflow through the bundled skills, while you use the local interface to listen, compare, and approve musical and visual decisions. The agent calls the application instead of reimplementing its business logic.

Every skill follows [the runtime contract](skill-runtime-contract.md), reads the application's current OpenAPI schema, and leaves upload and publication outside the application.

| Skill | Use it for |
| --- | --- |
| `$use-albumpress-studio` | Route a general or multi-step request |
| `$manage-album-project` | Open, create, relink, rescan, or inspect Projects |
| `$choose-separation-candidates` | Discover, calibrate, and process Candidates |
| `$review-instrumental-outputs` | Audition, approve, reject, loop, and select Outputs |
| `$recover-albumpress-job` | Diagnose, resume, stop, or retry jobs safely |
| `$prepare-instrumental-video` | Prepare tails, Proof, MP4 render, and Video Package |
| `$export-instrumental-audio` | Export and validate the full-album audio package |
| `$maintain-project-storage` | Inventory, preview cleanup, and migrate artifacts |
| `$produce-instrumental-album` | Coordinate an end-to-end local album deliverable |

## Harness integration

Codex is the reference integration for this repository. Keep each skill folder intact, including `agents/openai.yaml`. Another harness may discover project skills, accept an explicit path to `SKILL.md`, or require you to copy each folder into its skills directory.

Support outside Codex depends on the harness. It needs filesystem access, an HTTP client, support for `SKILL.md` instructions, and a way to present local audio, images, or video when you need to review them.

Set `ALBUMPRESS_URL` only when the application uses a different loopback port. `STEM_COMPARISON_URL` remains a compatibility alias. Skills must reject non-loopback URLs.

## Expected interaction

Agents inspect state before writing, use the program's endpoints rather than editing manifests, and stop for a human decision when musical semantics or visual approval is involved. A request to prepare or inspect does not authorize sustained processing, cleanup application, upload, or publication.
