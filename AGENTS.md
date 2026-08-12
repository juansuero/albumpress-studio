## Agent skills

### Issue tracker

Los specs y tickets se gestionan como Markdown bajo `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Project-local skills

- For a general request to operate the application or choose a workflow, load `.agents/skills/use-albumpress-studio/SKILL.md`.
- Specialized skills under `.agents/skills/` cover Project management, Candidate processing, Output review, job recovery, video, audio export, and storage maintenance.
- For an end-to-end instrumental album workflow, load `.agents/skills/produce-instrumental-album/SKILL.md`.
- Every operational skill must follow `docs/agents/skill-runtime-contract.md`.

### Domain docs

Este proyecto utiliza un único contexto definido en `CONTEXT.md`, con decisiones arquitectónicas en `docs/adr/`. See `docs/agents/domain.md`.
