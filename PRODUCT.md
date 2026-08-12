# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

- Python and FastAPI backend with a dedicated, single-concurrency processing worker.
- React, TypeScript, and Vite frontend served locally by the backend in production.
- `audio-separator` as the separation engine, discovered through its installed Python API rather than a frozen model catalogue.
- Remotion in a separate Node/React rendering module for future Video Packages.
- Windows-first, CPU-only operation. The application is launched locally and binds only to the loopback interface.
- Durable Album Projects live in user-visible Project Folders, normally under a configurable Project Library in the Windows Music known folder. Temporary directories are for explicit tests only, never canonical user projects.

## Users

The product supports one local operator working with an AI harness on a Windows PC. The harness operates the application under the operator's authority; the operator reviews audio and video and makes the musical, visual, export, and publication decisions. The MVP does not include accounts, multi-user behavior, or hosted infrastructure.

## Product Purpose

The product turns an album folder into a repeatable listening experiment: generate one or more vocal-removal alternatives for each Track, compare available alternatives at the same musical moment, save one Selection per Track, and export the resulting Final Instrumentals. Success means an operator can evaluate a complete album without manually changing models, locating outputs, renaming files, or remembering which configuration produced each result.

## Positioning

AlbumPress Studio is an agent-first, audition-led workspace. An AI harness coordinates the workflow. The operator listens and approves each musical or visual decision in the browser. The application treats separator choice as a per-Track decision, preserves the provenance of every Output, and does not infer that one Candidate works best for every recording.

## Operating Context

- Source material is organized as an album folder containing WAV, FLAC, MP3, or other supported audio files.
- The primary task is vocal removal while preserving acoustic guitars, resonances, harmonics, pedal steel, fiddle, and other material spectrally close to the voice.
- Evaluations focus on vocal bleed, vocal reverb, breaths and consonants, instrumental loss, and watery, phasey, or metallic artifacts.
- CPU processing may take many hours. Work must be sequential, observable, resumable, and safe to leave running in the background.
- The comparator is used through shared transport controls, synchronized timestamps, Candidate hotkeys, and short listening loops.
- Existing FL Studio instrumentals may later be introduced as a baseline Candidate.
- Video Packages are rendered locally from the selected Final Instrumentals and uploaded to YouTube manually. Rendering a vocal-removed recording does not imply authorization to publish it.

## Capabilities and Constraints

- Discover Tracks from a selected input folder and preserve their natural order.
- Discover installed `audio-separator` Models and Presets dynamically. Validate any recommended Candidates against the installed catalogue before presenting them.
- Begin with a one-Track calibration run before committing an entire album to the selected Candidates.
- Process with concurrency one and expose honest task-level progress, elapsed time, and a rolling estimate rather than fabricated model-internal percentages.
- Cache completed Outputs using input and configuration provenance, validate files before promotion, skip valid work by default, and resume after interruption.
- Keep the canonical manifest, generated audio, Final Instrumentals, artwork, Album Videos, and Video Packages together inside the Project Folder, with the exact location visible and openable from the application.
- Compare Candidates with synchronized play, pause, seeking, switching, and optional loop boundaries. Save a Selection immediately and persistently.
- Export only the current Selections as Final Instrumentals.
- The first Video Package includes a 1920x1080 Album Video, thumbnail, chapter list, and description. Its Album Landscape template uses one project-owned panoramic artwork, configurable album typography and colors, an integrated two-digit Track marker, current-Track progress, and a bounded Off/Subtle/Textured Cinematic Finish. The original cover and full Tracklist are not rendered.
- Public upload automation, waveform editing, automatic scoring, ensembles beyond those exposed by the engine, and multi-album management are outside the first MVP.
- The interface language is English. Copy should be centralized so another language can be added later, but the MVP does not need an internationalization framework.

## Brand Commitments

The application should feel beautiful, simple, and calm enough to remain open in the background during long processing and listening sessions. It must not imitate a dense DAW or use decorative complexity to signal technical sophistication. No product name, logo, fixed palette, typeface, or final visual identity has been selected yet.

## Evidence on Hand

- Real source albums and existing FL Studio instrumentals are available to Juan, but no redistributable test fixture or filesystem path has been committed to the project.
- `audio-separator` documentation and its installed runtime catalogue are authoritative for engine capabilities.
- No logo, brand system, screenshots, testimonials, performance claims, or publication rights are available and none may be fabricated.

## Product Principles

1. Listening evidence outranks generic rankings and model reputation.
2. Long-running work must be resumable, provenance-aware, and never repeated accidentally.
3. Comparison speed and timestamp continuity are core product behavior, not optional polish.
4. Progress and system state must be honest, legible, and calm.
5. Local ownership and maintainability outrank cloud convenience.

## Accessibility & Inclusion

The complete evaluation workflow must be operable by keyboard, including Candidate switching and transport controls. Controls require accessible names, visible focus, sufficient contrast, non-color status cues, and reduced-motion behavior. Dynamic processing and persistence changes must be announced without disrupting listening.
