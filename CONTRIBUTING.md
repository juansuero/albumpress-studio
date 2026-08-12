# Contributing

Thanks for helping improve AlbumPress Studio. Keep changes focused, local-first, and safe for source audio.

## Development setup

Use Windows 11 with Python 3.10 or newer, Node.js, FFmpeg, and FFprobe. From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
```

Run the application with `scripts/launch.ps1`. The API binds to `127.0.0.1`; do not change the default to a public interface in an unrelated contribution.

## Agent skills are part of the public interface

Users may operate AlbumPress Studio through the skills under `.agents/skills/`. Treat their instructions and safety gates as user-facing behavior. If a contribution changes an endpoint, workflow, or authorization boundary, update the affected skills, [runtime contract](docs/agents/skill-runtime-contract.md), and skill tests in the same pull request.

Keep Codex as the documented reference integration unless another harness has been tested. Describe untested harness support through concrete requirements instead of a compatibility claim.

## Before opening a pull request

```powershell
.venv\Scripts\python.exe -m pytest -q
npm run test --prefix frontend
npm run build --prefix frontend
```

- Add observable tests for behavior changes.
- Keep source folders read-only and generated artifacts inside application-owned project paths.
- Do not commit albums, separated stems, model caches, logs, local manifests, brand assets, or personal filesystem paths.
- Preserve explicit confirmation gates for sustained CPU work, destructive cleanup, and publication-related actions.
- Explain user-visible changes and manual verification in the pull request.
