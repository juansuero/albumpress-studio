# Release gates

Use the application state and manifests as evidence. A synthetic check proves mechanics only;
it does not prove musical semantics or human approval.

| Gate | Required evidence | Stop condition |
| --- | --- | --- |
| Candidate | Runtime catalogue, fingerprinted snapshot, bounded calibration | No sustained separation without human semantic confirmation |
| Selection | Technically valid Outputs, confirmed Instrumental semantics, persisted Selections | Invalid, inverted, pending or incomplete Output |
| Timeline | Current timeline fingerprint, effective durations, chapters and tail decisions | Changed or low-confidence tail awaiting real-audio review |
| Proof | Current bounded Proof Pack and human visual/auditory approval | Missing or stale proof |
| MP4 | Authorized application job, validated local Video Package, hashes and provenance | No full render, retry, or promotion without explicit authorization |
| MP3 | Authorized Audio Mix Package job, approved timeline, CBR/metadata/chapters/CUE and loudness evidence | No real export or force retry without explicit authorization |
| Handoff | Human approval of each requested package and intact source hashes | Do not mark complete or publish |

Before any sustained operation, report the exact Project Folder, current fingerprints,
requested output, active-job state, disk preflight, and the authorization required. Afterward,
record the job result, output paths, hashes, duration, boundaries, provenance and approval.

For a stale Proof Pack, generate one bounded replacement and stop for approval. For a changed
timeline, changed Final Instrumental, changed font/branding snapshot, or changed renderer
settings, treat the previous proof and package approvals as stale. For an interrupted or failed
expensive job, recover fail-closed and ask before retrying; never retry automatically.

Do not upload to YouTube, obtain OAuth, or alter external publication state.
