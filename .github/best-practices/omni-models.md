# Omni model review — rule-id taxonomy & company overrides

> **Role of this file.** This is **not** the standards source. As of the V3
> review workflow, the authoritative best practices are the **full** Omni
> agent skill docs, checked out live from `exploreomni/omni-agent-skills@main`
> (`omni-model-builder`, `omni-ai-optimizer`) and read directly by Claude.
>
> This file now serves three narrow purposes:
> 1. **Rule-id taxonomy** — the kebab-case `rule` ids below give findings
>    stable names so we can group and trend them over time. Claude uses these
>    verbatim; it may coin a new id only when no listed id fits.
> 2. **Severity rubric** — error / warning / info, as applied below.
> 3. **Company-specific overrides** — see the section at the bottom. Anything
>    here takes precedence over the upstream skills when the two conflict.
>
> The checklist below is a convenience summary that mirrors the skills; treat
> the live skill docs as canonical if they ever diverge.

## Required hygiene (severity: error)

- When a `sql:` expression references **another field**, use `${field}` /
  `${view.field}` — not a raw column. This keeps Omni's reference graph intact
  (column renames propagate; join paths resolve). To reference a **raw database
  column** directly, use the **bare column name** (e.g.
  `sql: CONCAT(FIRST_NAME, ' ', LAST_NAME)`). Do **NOT** use `${TABLE}.COLUMN`
  — that is a LookML-ism Omni only tolerates; it is not recommended syntax
  (see https://docs.omni.co/modeling/dimensions/parameters/sql). A plain column
  dimension usually needs **no `sql:` at all** — it auto-maps to the column by
  name via the schema layer.
- Topic `joins:` must reference relationships defined in the central
  `relationships.yaml`; inline `on_sql` / `sql_on` in a topic body is rejected
  by the API.
- All views referenced by a topic must appear in the topic's `views:` block
  with an explicit `display_order:` (0 for the base view).

## Strongly recommended (severity: warning)

- Every dimension and measure should have a `label:` (human-readable) and a
  `description:` (1–2 sentences explaining what it represents).
- Group related fields with `group_label:` so they cluster in the field picker.
- For dimensions derived from another dimension, prefer `${view.field}` over
  raw column references — e.g., a `${age}` CASE bucket should reference an
  `age` dimension, not `"AGE"` directly.
- Prefer **filtered measures** over `CASE WHEN ... THEN ... ELSE NULL END`
  inside an aggregate. Filtered measures express intent more clearly and are
  faster for Omni to plan.
- Time-based dimensions should declare `timeframes: [...]` explicitly so the
  intended granularities are obvious.
- A topic's base view should have a `base_view_label:` on the topic; do NOT
  use `label:` inside the `views:` block for the base view.

## AI-readiness (severity: info)

- Add `ai_context:` to topics and high-traffic views — a few sentences telling
  Blobby what the view/topic represents, when to use it, and any caveats.
- Add `sample_queries:` to topics — 2–5 example natural-language prompts with
  the expected `fields`/`filters`/`sorts` to anchor Blobby's selections.
- Use `ai_fields:` to whitelist the fields Blobby should default to, so it
  doesn't surface internal/staging fields.
- Prefer descriptive labels over terse ones — Blobby uses labels as part of
  its semantic search; `Order Items: Total Revenue` is more searchable than
  `revenue`.

## Style / consistency (severity: info)

- Consistent label casing within a view (title case is conventional).
- Avoid trailing whitespace and tabs.
- File names should match the conventional view path
  (`<SCHEMA>/<table>.view.yaml`).

## Rule-id naming (for output)

When emitting findings, use these kebab-case `rule` ids so we can group and
trend over time:

- `raw-column-sql`
- `unscoped-field-ref`
- `topic-inline-join`
- `missing-display-order`
- `missing-label`
- `missing-description`
- `missing-group-label`
- `redundant-case-vs-filtered-measure`
- `missing-timeframes`
- `base-view-label-misplaced`
- `missing-ai-context`
- `missing-sample-queries`
- `missing-ai-fields`
- `terse-label`
- `inconsistent-label-casing`
- `trailing-whitespace`
- `file-naming-mismatch`

## Company-specific overrides

> Add rules here that are specific to **this** repo / org and should take
> precedence over the upstream Omni skills. Each entry should state the rule,
> its severity, and a kebab-case `rule` id (prefix company ids with `co-` so
> they're easy to distinguish from upstream ids in trend reports).
>
> _None yet._ When the skills and this section ever conflict, this section
> wins, and the finding `message` should note that a company override applied.
