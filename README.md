# Omni CI Checks

GitHub Actions checks for [Omni](https://omni.co) model repositories that
**[OmniFlow](https://github.com/exploreomni/OmniFlow) does not run**. Use the
two together: OmniFlow is Omni's own CI action and owns model validation,
content validation, semantic diff, downstream contracts, and the dbt
sequencing policies. This repo adds the checks that need a live query, an AI
reviewer, or a write-back fixer, and stays out of OmniFlow's way.

| Need | Where it lives |
|------|----------------|
| Model validation, content validation, semantic lint | OmniFlow |
| Semantic diff, breaking-change detection, downstream contract search | OmniFlow |
| dbt impact analysis, breaking-change hold, post-deploy dbt sync | OmniFlow |
| AI eval regression check | OmniFlow (opt-in) |
| **Reference queries** with pinned results | this repo |
| **Shared-model hygiene** (provenance of table-backed views) | this repo |
| **Omni agent review** against company standards | this repo |
| **Best-practices review** by an outside AI provider | this repo (opt-in) |
| **`/omni-fix`** comment command that applies fixes through Omni's API | this repo |

Install OmniFlow first, following
[its installation guide](https://github.com/exploreomni/OmniFlow/blob/main/docs/INSTALLATION.md).
Then drop this repo's `.github/` and `tests/` alongside it. The checks reuse
OmniFlow's `.omni/flow.json` for the model identity and its `OMNI_API_KEY`
secret for read and query access, so there is nothing to configure twice.
Neither depends on the other at runtime.

## How it works

Where a check has a base to compare against, it runs twice, once against the
PR's **Omni branch** and once against the **base model**, and reports only
**net-new** issues introduced by the PR. Pre-existing problems never block a
PR; new ones do.

The suite assumes Omni's git integration branch convention: **the git branch
name and the Omni branch name match 1:1**. When Omni opens a PR from a branch,
the workflows resolve the Omni branch by name and check against it. This is
the same convention OmniFlow uses.

PRs that touch no model YAML skip the whole suite (the skips count as passing
for required checks).

## The checks

| Check | What it does | Blocks on |
|-------|--------------|-----------|
| **Reference queries** | Runs pinned queries from `tests/reference-queries/` on branch and base and compares results to expected values | Net-new failures |
| **Shared-model hygiene** | Flags hand-authored table-backed base views in the shared model (they must come from the schema layer). Detection asks Omni for each changed view in extension mode, which the git file alone cannot reveal | Any violation |
| **Omni agent review** | Omni's built-in modeling agent reviews the PR's Omni branch against your company standards; findings appear as a PR comment + check-run annotations. No extra accounts or keys — it uses the same Omni credentials as the other checks | `error`-severity findings (warnings/info advisory) |
| **Best-practices review** *(optional)* | An outside AI provider you supply a key for reviews the changed YAML against the full [omni-agent-skills](https://github.com/exploreomni/omni-agent-skills) docs; findings appear as a PR comment + check-run annotations with line numbers. The included implementation uses Claude | `error`-severity findings (warnings/info advisory) |

A **validation summary** job assembles the first two checks into one combined
sticky PR comment. Each reviewer posts its own comment with numbered findings.

### Why these are not in OmniFlow

OmniFlow's core validation deliberately executes no warehouse queries and
writes no YAML. Reference queries run real queries by design, and `/omni-fix`
writes to Omni branches by design. The two reviewers depend on an AI agent
(Omni's or an outside provider's) rather than deterministic validation. Those
are reasonable things to keep out of a required check that ships as an
official action, and reasonable things to want as an optional layer beside it.

### The two review approaches

Both reviewers check the PR against the same company standards file
(`.github/best-practices/omni-models.md`) and produce the same kind of
result: a PR comment with numbered findings, inline annotations, a check that
fails on `error`-severity findings, and input for `/omni-fix`. They differ in
where the reviewing intelligence comes from:

**1. Built into Omni — the Omni agent review (on by default).** Omni ships
its own modeling agent, and this check simply asks it to review the PR. There
is nothing to sign up for and no new key to manage: it authenticates with the
same Omni credentials the rest of the suite already uses, and each review
draws on your Omni instance's AI credit allowance (see Requirements below).
Because the agent works inside Omni, it reviews each changed file as Omni
actually resolves it on the PR's branch — it sees the whole view, the
database schema behind it, and any standards written into the model itself
(`ai_context`), not just the lines the PR touched. The flip side: findings
describe files, not line numbers, and can include pre-existing gaps in a file
the PR merely edited. The review cannot change anything — the agent is
instructed to only read, and the check independently compares the branch's
content before and after the review and fails hard if anything changed.
Disable with the `OMNI_AGENT_REVIEW=false` repository variable (for example,
if your instance has AI features turned off).

**2. Bring your own AI provider — the best-practices review (opt-in).** If
you prefer an outside AI service to do the reviewing, create an account with
a provider, generate an API key, and store it as a repository secret. This
reviewer reads the changed files exactly as they appear in the git diff and
judges them against the complete, current Omni modeling guides from the
public `omni-agent-skills` repository — so its findings carry precise line
numbers and its knowledge updates as those guides do. The implementation
included here uses Anthropic's Claude (the `CLAUDE_CODE_OAUTH_TOKEN` secret
and the `anthropics/claude-code-action` workflow step); treat it as a worked
example of the approach rather than the only option — swapping in a
different provider is a one-step change, described under
[Customization](#customization).

Run both, or either. When both post findings, `/omni-fix` selectors resolve
against the best-practices comment first, falling back to the agent review's
comment when it is the only one present.

### `/omni-fix`

Comment `/omni-fix <selectors>` on a PR to have an AI engine generate
corrected YAML for selected best-practices findings and apply it **through
Omni's API** (never a direct git write — Omni owns the repo contents). Two
generation engines are available, chosen automatically: **Claude** when the
`CLAUDE_CODE_OAUTH_TOKEN` secret is configured, otherwise **Omni's own
modeling agent** via the Agentic Jobs API — which needs no extra secrets and
draws on the same Omni credentials and AI credit allowance as the agent
review. Set the `OMNI_FIX_ENGINE` repository variable to `claude` or
`omni-agent` to pin one explicitly.

Either engine only *generates* — Claude runs with no write tools, and the
Omni agent is generate-only by prompt and by a staged-YAML fingerprint guard
that aborts the run if the branch changed during drafting. The workflow then
applies the output deterministically: write via the YAML endpoint, re-validate
(any net-new **error** rolls everything back), and commit through Omni.
Selectors:

```text
/omni-fix 1.1            # one finding by its comment number
/omni-fix 3.3-3.5        # a range
/omni-fix 2.*            # all findings in file section 2
/omni-fix errors         # all error-severity findings (also: warnings, infos)
/omni-fix rule:missing-label
/omni-fix all
```

Only users with write access can trigger it, and it refuses to run while Omni
Checks are in flight on the head commit. It does not wait for OmniFlow; run it
after OmniFlow has reported so the re-validation inside the fixer and
OmniFlow's own check agree on the branch state.

## Getting started

### 1. Get the files into your repo

Either click **Use this template** and connect Omni's git integration to the
new repo, or copy `.github/` and `tests/` into your existing Omni-connected
repo. If OmniFlow is already installed, nothing here collides with it: the
workflow names, comment markers, secrets, and variables are all distinct.

### 2. Point the checks at your model

The model identity comes from OmniFlow's `.omni/flow.json` on the base
branch, so if OmniFlow is installed there is nothing to add. Without OmniFlow,
create the file from
[OmniFlow's example](https://github.com/exploreomni/OmniFlow/blob/main/.omni/flow.example.json):

```json
{
  "version": 1,
  "models": [
    {
      "base_url": "https://myorg.omniapp.co",
      "model_id": "<shared model uuid>",
      "model_path": "omni/my_model",
      "base_branch": "main"
    }
  ]
}
```

`model_path` is the directory the git integration writes model YAML into
(Omni's default `modelPath` is `omni/<model name>`). The checks read the file
from the base branch on every PR, so a pull request cannot redirect the token
by editing it. Several models may be registered; a PR must touch only one.

### 3. Configure secrets and variables

`Settings → Secrets and variables → Actions`, or via `gh`:

```bash
gh secret set OMNI_API_KEY --body "<read/query token>"
```

```bash
gh secret set OMNI_FIX_API_KEY --body "<write-capable token, only if you use /omni-fix>"
```

| Name | Kind | Required | Description |
|------|------|----------|-------------|
| `OMNI_API_KEY` | secret | yes | The same secret OmniFlow uses: a personal access token for a dedicated least-privilege Omni user. On top of what OmniFlow needs, that user must be able to run queries (reference queries) and start AI jobs (Omni agent review). |
| `OMNI_FIX_API_KEY` | secret | for `/omni-fix` | A separate token whose user can write model YAML and commit through Omni. Only the fixer workflow receives it, mirroring OmniFlow's split between its validation key and its repair key. |
| `CLAUDE_CODE_OAUTH_TOKEN` | secret | no | API credential for the bring-your-own-provider review (the included implementation uses Claude; get a token with `claude setup-token`). Enables the best-practices review and the Claude `/omni-fix` engine; the review skips cleanly when unset and `/omni-fix` falls back to the Omni agent engine. |
| `OMNI_FIX_ENGINE` | variable | no | Pin the `/omni-fix` generation engine: `claude` or `omni-agent`. Default: automatic — Claude when its token is configured, the Omni agent otherwise. |
| `OMNI_SKILLS_SHA` | variable | no | Pin the best-practices review to a specific `omni-agent-skills` commit. Defaults to `main`. |
| `OMNI_AGENT_REVIEW` | variable | no | Set to `false` to disable the Omni agent review (it is on by default whenever model YAML changed). Disable it if your Omni instance has AI features turned off. |

These knobs stay as repository variables rather than going into
`.omniflow.yml`, because OmniFlow rejects unknown keys in its policy file.

### 4. Add reference queries (recommended)

Pin the numbers that must never silently change — row counts, totals, KPI
values. See [tests/reference-queries/README.md](tests/reference-queries/README.md)
for the fixture format and worked examples.

### 5. Make the checks required (recommended)

In `Settings → Branches`, require the checks you care about (e.g.
`Reference queries`, `Shared-model hygiene`, `Omni agent review`,
`Best practices review`) on your base branch, alongside OmniFlow's check.
Skipped runs (no model YAML changed, or an optional feature unconfigured)
report as passing, so required checks never wedge a PR.

## Customization

- **Company standards** — edit
  [`.github/best-practices/omni-models.md`](.github/best-practices/omni-models.md).
  The substantive best practices come live from the `omni-agent-skills` repo;
  this file is your company's layer on top of them: the severity rubric, the
  stable rule-id taxonomy findings are labeled with, and your
  **company-specific overrides and additions**, which take precedence when
  they conflict with the upstream skills.
- **Model directory** — the scripts map repo paths to Omni filenames assuming
  `<parent of model_path>/<model name>/<file>`, which is Omni's default
  `modelPath` shape. If your git integration uses a different layout, adjust
  `git_path_to_omni_filename()` in the scripts.
- **Using a different AI provider for the best-practices review** — the
  Claude step is the only provider-specific piece; everything downstream
  (comment, annotations, blocking, `/omni-fix`) reads one file: a findings
  JSON matching [`.github/schemas/best-practices.json`](.github/schemas/best-practices.json):

  ```json
  {"summary": "...",
   "findings": [{"file": "omni/.../orders.view.yaml", "line": 12,
                 "severity": "warning", "rule": "missing-label",
                 "message": "...", "suggestion": "..."}]}
  ```

  In the `best-practices-review` job, replace the `Run Claude review` and
  `Capture Claude output` steps with anything that writes
  `/tmp/bp/findings.json` in that shape — typically a small script that sends
  your provider the same inputs the job already assembles (the review prompt
  from `.github/prompts/best-practices-review.md`, the standards file, and
  the changed YAML) and parses the reply. Keep `file` values repo-relative so
  the inline annotations land on the right files. The Omni agent review needs
  no provider at all and is unaffected.
- **Dropping a check** — delete the job from
  `.github/workflows/omni-checks.yml` (and remove it from the
  `validation-summary` job's `needs:` list). To drop the fixer, delete
  `.github/workflows/omni-fix.yml`.

## Layout

```text
.github/
  workflows/
    omni-checks.yml          The PR check suite
    omni-fix.yml             The /omni-fix comment command
  actions/setup-omni-cli/    Composite action: install the Omni CLI, write a profile
  best-practices/
    omni-models.md           Company standards: severity rubric, rule-id taxonomy, overrides
  prompts/                   Prompts for the review and fix agents
  schemas/                   JSON schema for review findings
  scripts/                   The check/diff/format/apply machinery
.omni/
  flow.json                  OmniFlow's model identity file (shared, not duplicated)
tests/
  reference-queries/         Your pinned-query fixtures (examples/ inside)
```

## Requirements

- An Omni model repo with git integration configured (the PR webhook is what
  creates/syncs Omni branches for PRs).
- The Omni branch for a PR must exist before the checks run — with the
  standard integration setup this is automatic.
- For the **Omni agent review**: AI features must be enabled on your Omni
  instance, and each review consumes a small amount of the instance's Omni AI
  credits (a one-file review takes well under a minute). Administrators can
  cap spending in Omni under AI credit controls. If AI is disabled on the
  instance, set the `OMNI_AGENT_REVIEW` variable to `false`.
- Workflow permissions: the workflows request `contents: read`,
  `pull-requests: write`, `checks: write`, and `id-token: write` (the last is
  used by `anthropics/claude-code-action`).

## History

Earlier versions of this repo also ran model validation, content validation,
and an AI eval regression check, and carried their own `OMNI_BASE_URL`,
`OMNI_MODEL_ID`, `OMNI_MODEL_DIR` variables and `OMNI_TOKEN` secret. OmniFlow
now covers all three checks with tests and a release process behind it, so
those jobs were removed rather than maintained in parallel, and the
configuration moved to OmniFlow's shapes. The last commit that carried the
old suite is tagged `v0-full-suite` if you need it.

## License and disclaimer

MIT — see [LICENSE](LICENSE).

This project is provided **as-is**, without warranty of any kind, express or
implied, and without any guarantee of support or maintenance. It is not an
official Omni product and is not affiliated with OmniFlow. Review the
workflows before enabling them — they call your Omni instance's API with the
credentials you configure, and `/omni-fix` writes model changes to Omni
branches.
