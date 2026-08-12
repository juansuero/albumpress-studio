# AlbumPress Studio

**Agent-first, local production for reviewed instrumental albums in MP3 and MP4.**

AlbumPress Studio lets Codex operate a local, CPU-only `audio-separator` workflow. The agent creates Projects, runs Candidate comparisons, recovers jobs, and prepares MP3 or MP4 packages. You use the browser interface to listen, compare, and approve the musical and visual decisions.

Everything stays on your Windows machine. The app has no accounts, telemetry, cloud backend, or automatic upload path.

## Use with Codex or another AI harness

This repository includes nine project-local skills under `.agents/skills/`. Codex is the reference integration and discovers them while working in this repository. Start with a request such as:

```text
Use $use-albumpress-studio to inspect the current state and take the next safe step.
```

Codex uses the router skill to choose the right workflow. The [skill index](docs/agents/skills.md) covers each specialized task. You can also invoke one directly:

```text
Use $choose-separation-candidates to discover the installed Candidates and calibrate one Track.
Use $review-instrumental-outputs to help me audition and select the final Instrumental for each Track.
Use $recover-albumpress-job to explain why the album job stopped and what can be reused.
```

Other AI harnesses can use the same skill folders. They must load `SKILL.md` instructions and access the local filesystem and loopback HTTP API. They must also present local audio, images, and video for human review. Integration details vary by harness.

At runtime, the agent reads `/openapi.json`. It asks for approval before sustained processing, destructive cleanup, musical or visual decisions, export, upload, or publication. See the [runtime contract](docs/agents/skill-runtime-contract.md).

## Setup

Windows 11, Python 3.10+, Node.js, FFmpeg, and FFprobe are required. From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
```

The setup creates `.venv`, installs the CPU `audio-separator` extra, and installs the frontend dependencies. Models remain in the legacy-compatible `%LOCALAPPDATA%\StemComparison\models` application directory.

Optional Second Pressing branding is disabled by default. Set `ALBUMPRESS_BRAND_LIBRARY` to an approved local brand export before enabling that profile.

Album artwork and display fonts are user-owned inputs and are not bundled with the repository. An agent can pass `artworkSourcePath` and `displayFontPath` when configuring video, or you can set `ALBUMPRESS_DEFAULT_ARTWORK` and `ALBUMPRESS_DEFAULT_DISPLAY_FONT` to approved local files.

## Launch

```powershell
powershell -ExecutionPolicy Bypass -File scripts/launch.ps1
```

The app binds to `127.0.0.1` (port `8765` by default). The source folder is read-only; each new Album Project gets a durable Project Folder under the configurable Windows Music `AlbumPress Studio Projects` library by default. The exact Project Folder contains the manifest, Outputs, job state, and final export.

## Workflow

1. Open an Album Project and review naturally ordered Tracks.
2. Discover the live Models and Presets from the installed engine, then save A/B/C and optional D Candidates.
3. Calibrate one Track or acknowledge the uncertainty and skip calibration.
4. Run the sequential full-album queue. Valid Outputs are registered immediately and can be resumed after interruption.
5. Compare Outputs at one shared timestamp, use loops and keyboard shortcuts, and Select a final Candidate per Track.
6. Export the validated Selection set into the `final` folder. Export never runs separation.

## Recovery and data safety

The canonical project state is `project.json`; project-owned paths are relative to the manifest and job status is under `.stem-comparison\jobs`. AlbumPress Studio retains the `.stem-comparison` internal namespace so existing Projects remain readable. Cache reuse requires matching provenance and a registered file fingerprint. Restart reconciliation retains valid Outputs and requeues incomplete work. `Retry` and `Force reprocess` are scoped; temporary cleanup is limited to the application-owned `.stem-comparison\tmp` directory. Shared Models and logs remain in application data and are visible from Projects.

## Opt-in real-engine smoke

The smoke command requires a short user-supplied clip, a Candidate ID from live discovery, and explicit confirmation because it may download a model and consume CPU:

```powershell
.venv\Scripts\python.exe scripts\cpu_smoke.py --clip C:\path\to\short-clip.wav --candidate preset:instrumental_full --confirm
```

Use `/api/catalogue` or the Album screen to confirm the current Candidate ID. The command validates the produced Instrumental with FFprobe and reports the installed engine version and Output path.

## Verification

```powershell
.venv\Scripts\python.exe -m pytest -q
npm run build --prefix frontend
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow and [SECURITY.md](SECURITY.md) for private vulnerability reporting guidance.

## Album Video and manual upload

The Video surface reads the current validated Selections from the permanent Project Folder and copies the approved artwork and font assets into `video/assets`. Configure the Album Landscape identity, palette, Cinematic Finish, and Reduced motion setting, then use the shared Player to review the 1920×1080/30 fps composition. The bounded synthetic render and package are safe smoke checks; they do not process audio or replace Final Instrumentals.

A sustained real-album render is a separate, explicit authorization boundary. It uses only the current Final Instrumentals, keeps `concurrency=2`, and writes a versioned local render. The resulting Video Package contains `album-video.mp4`, `thumbnail.png`, `chapters.txt`, `description.txt`, and `manifest.json`. The manifest records the render snapshot, Track boundaries, provenance, FFprobe validation, and SHA-256 hashes. Recovery preserves the previous valid render/package; cancellation, failure, retry, and backend restart are handled through job state rather than by rerunning separation.

The app prepares the package locally but does not upload it. For a manual upload, inspect the MP4, thumbnail, chapters, description, and manifest first, then use the chosen platform's upload interface and its current settings. You are responsible for having the necessary rights or permissions for the supplied cover artwork, source audio, Final Instrumentals, fonts, and any upload; AlbumPress Studio does not grant copyright, publishing, synchronization, or Content ID clearance.

## License

AlbumPress Studio is available under the [MIT License](LICENSE). This license applies to the project code only; dependencies retain their own terms. Remotion uses a separate license. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). MIT does not grant rights to user-supplied audio, artwork, fonts, model files, separated stems, or generated media.
