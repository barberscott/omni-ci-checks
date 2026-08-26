# Company standards — Omni model review

The authoritative Omni modeling best practices are the full agent skill docs
in [exploreomni/omni-agent-skills](https://github.com/exploreomni/omni-agent-skills)
(`omni-model-builder`, `omni-ai-optimizer`), which the best-practices review
workflow checks out and reads directly. Those are not restated here.

This file is **your company's layer on top of those skills** — edit it freely.
It supplies three things to the review:

1. **Severity rubric** — how findings are graded, and which grades block.
2. **Rule-id taxonomy** — stable kebab-case ids the reviewer uses verbatim
   when labeling findings, so you can group and trend findings over time.
3. **Company overrides & additions** — rules specific to your org. Anything
   in that section takes precedence over the upstream skills when the two
   conflict, and the finding `message` will note that a company override
   applied.

## Severity rubric

- `error` — would fail model validation or definitely breaks behavior.
  **Blocks the PR.**
- `warning` — clear deviation from a documented best practice. Advisory.
- `info` — soft suggestion (AI-readiness, naming, style). Advisory.

## Rule-id taxonomy

Use these ids verbatim when a finding matches. If an issue is real but no
listed id fits, coin a concise new kebab-case id rather than dropping the
finding.

| Rule id | Typical severity | Meaning |
|---|---|---|
| `raw-column-sql` | error | A `sql:` expression references another **field** via a raw column or `${TABLE}.COLUMN` instead of `${field}` / `${view.field}` |
| `unscoped-field-ref` | warning | A derived dimension references a raw column where a `${view.field}` reference to an existing dimension should be used |
| `topic-inline-join` | error | A topic defines a join inline (`on_sql` / `sql_on`) instead of referencing a relationship in the central `relationships.yaml` |
| `missing-display-order` | error | A view referenced by a topic is missing from the topic's `views:` block or lacks an explicit `display_order:` |
| `missing-label` | warning | Dimension or measure without a human-readable `label:` |
| `missing-description` | warning | Dimension or measure without a `description:` |
| `missing-group-label` | warning | Related fields not clustered with a `group_label:` |
| `redundant-case-vs-filtered-measure` | warning | `CASE WHEN … ELSE NULL END` inside an aggregate where a filtered measure expresses the intent |
| `missing-timeframes` | warning | Time-based dimension without explicit `timeframes: [...]` |
| `base-view-label-misplaced` | warning | Base view labeled via `label:` inside `views:` instead of the topic's `base_view_label:` |
| `missing-ai-context` | info | Topic or high-traffic view without `ai_context:` |
| `missing-sample-queries` | info | Topic without `sample_queries:` |
| `missing-ai-fields` | info | Topic without an `ai_fields:` curation |
| `terse-label` | info | Label too terse to be useful in semantic search |
| `inconsistent-label-casing` | info | Mixed label casing within a view |
| `trailing-whitespace` | info | Trailing whitespace or tabs |
| `file-naming-mismatch` | info | File name doesn't follow the `<SCHEMA>/<table>.view.yaml` convention |

## Company overrides & additions

Rules specific to **your** repo/org go here. Each entry states the rule, its
severity, and a kebab-case rule id — prefix company ids with `co-` so they are
easy to separate from upstream ids in trend reports. When an entry here
conflicts with the upstream skills, this section wins.

_None yet._ The block below shows the expected format — it is fenced off so
the reviewer does not treat it as active rules; copy an entry out of the fence
to activate it.

```markdown
- **`co-measure-agg-prefix`** (warning) — measure names must start with their
  aggregation: `total_`, `count_`, `avg_`, `min_`, `max_` (e.g.
  `total_revenue`, not `revenue`).
- **`co-no-scratch-refs`** (error) — topics and shared views must not
  reference tables in the `SCRATCH` schema; promote the table to `ANALYTICS`
  first.
- **`co-currency-format`** (info) — money fields declare a currency
  `format:` and a label that names the currency (e.g. `Revenue (USD)`).
```
