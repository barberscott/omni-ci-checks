# Reference queries

Pinned queries with expected results. Every `*.yaml` file **directly in this
directory** is run against the PR's Omni branch and against the base model on
every PR that touches model YAML; a fixture that fails on the branch but not
on the base blocks the PR.

Use these to pin numbers that must never silently change: row counts, grand
totals, KPI values, boundary conditions.

Files in `examples/` are **not** executed — copy one up into this directory
and adapt it to your model to activate it.

## Fixture format

```yaml
name: orders_grand_total          # optional; defaults to the file name
description: >
  Total order count across all time. Pins the base aggregate so join or
  filter changes that fan out rows are caught immediately.
query:
  # The query body, exactly as accepted by `omni query run` — the same JSON
  # shape the workbook's "View query" panel shows, in YAML.
  table: "Order Items"                       # base view/topic label
  fields:
    - order_items.count
  join_paths_from_topic_name: "Order Items"  # topic providing the join tree
  # filters, sorts, pivots, limit ... all supported
expect:
  row_count: 1              # optional: exact number of data rows
  rows:                     # optional: expected cell values, in field order
    - [123456]
tolerance: 0                # optional: numeric tolerance per cell (default 0)
```

Notes:

- `expect.rows` is compared cell-by-cell after normalizing numbers (thousands
  separators are stripped; `"1,234"` equals `1234`).
- For non-deterministic or slowly drifting values, set a `tolerance` or pin
  only `row_count`.
- Sort explicitly whenever you pin more than one row — result order is only
  stable if the query orders it.
- A fixture that errors at execution time (unknown field, broken join) counts
  as a failure, so these also catch accidental field/topic removals.
