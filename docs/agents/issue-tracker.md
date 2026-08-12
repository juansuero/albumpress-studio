# Issue tracker: Local Markdown

Issues and specs for this repo live as markdown files in `.scratch/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The spec is `.scratch/<feature-slug>/spec.md`
- Implementation issues are one file per ticket at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`
- Tickets are numbered from `01`
- Comments and conversation history append under `## Comments`

## Publishing

When a skill says “publish to the issue tracker”, create the corresponding file under `.scratch/<feature-slug>/`.

## Fetching

When a skill says “fetch the relevant ticket”, read the referenced ticket file.

## Blocking

- `Blocked by: NN, NN` records dependencies.
- A ticket is actionable when all its blockers are resolved.
- Each implementation session claims and completes one ticket.
