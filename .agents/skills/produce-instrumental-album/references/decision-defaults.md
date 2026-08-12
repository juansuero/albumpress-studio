# Decision defaults

Use these defaults only when the Project and application do not already specify a value.
Ask the user when a choice changes scope, provenance, or an irreversible/expensive action.

## Ask only when unresolved

- Which Album Project or source folder to open.
- Whether the requested local outputs are `MP4`, `MP3`, or both.
- Artwork, branding profile, thumbnail choice, or a required display font when more than
  one valid option exists or an asset is missing.
- A low-confidence or manually overridden tail when the real-audio audition can change the
  effective timeline.

Do not ask separately about fps, codec, padding, texture, concurrency, metadata fields, or
minor layout choices when the Project has an approved default. Show the effective defaults in
the preflight and let the user override them as one explicit scope decision.

## Safe defaults

- Query the installed separator catalogue at runtime. Prefer the Project's recorded,
  fingerprint-valid Candidate; otherwise calibrate one bounded Candidate before album work.
- Keep concurrency and model reuse at the application's approved defaults. Do not choose a
  faster or slower backend silently.
- Preserve source files and Final Instrumentals. Use cache fingerprints and resume state;
  never force regeneration to make the workflow look fresh.
- Use the Project's artwork, font, branding snapshot, timeline, codec and metadata defaults.
  Validate and snapshot a user-provided local font instead of installing or substituting it.
- Prepare only the requested output type. If both are requested, share the approved timeline
  snapshot but run independent application jobs and approvals.
- Keep publication out of scope: a local Video Package or Audio Mix Package is not an upload.
