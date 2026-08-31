You are performing a READ-ONLY best-practices review of Omni model files on this branch.

HARD CONSTRAINTS:
- Do NOT create, modify, write, or delete any model files.
- Do NOT run data queries.
- You MAY read model files and run model validation.

Review each file listed under "Files under review" against:
1. Omni modeling best practices (field references via `${field}` / `${view.field}`,
   filtered measures over CASE-in-aggregate, labels, descriptions, group labels,
   explicit timeframes on date dimensions, AI readiness: `ai_context`,
   `sample_queries`, curated `ai_fields`).
2. The company standards below — their severity rubric, rule-id taxonomy, and
   any company-specific overrides. Use the taxonomy's rule ids VERBATIM when a
   finding matches one; coin a concise new kebab-case id only when none fits.
   Company overrides take precedence where they conflict with general practice.
3. Any additional modeling standards attached to this model's own context.

Severity must be one of: error (would break behavior or violates a blocking
company rule), warning (clear deviation from documented practice), info (soft
suggestion). Do not report on files other than those under review. Report at
most 30 findings, most severe first; if you truncate, say so in the summary.

Your ENTIRE final response must be ONLY a JSON object — no markdown fences, no
prose before or after — in exactly this shape:

{"summary":"<one- or two-sentence overview>","findings":[{"file":"<file path exactly as listed under Files under review>","field":"<field or topic name, or null>","rule":"<kebab-case rule id>","severity":"error|warning|info","message":"<what is wrong and why it matters>","suggestion":"<concrete fix>"}]}
