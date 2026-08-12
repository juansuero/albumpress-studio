# Domain Docs

## Before exploring

Read:

- `CONTEXT.md`
- any relevant ADR under `docs/adr/`

If a file does not exist, proceed silently. Domain-modeling creates documentation lazily as terms and decisions are resolved.

## Layout

This is a single-context project:

```
/
├── CONTEXT.md
├── docs/adr/
└── src/
```

## Vocabulary

Use the canonical terms defined in `CONTEXT.md`. Do not replace them with synonyms explicitly marked under `_Avoid_`.

## ADR conflicts

If proposed work contradicts an existing ADR, surface the conflict instead of silently overriding the decision.
