# Omni CI Checks

A ready-to-use suite of GitHub Actions checks for [Omni](https://omni.co)
model repositories. Drop it into a repo connected to Omni's
[git integration](https://docs.omni.co/docs/integrations/git) and every pull
request gets validated against your Omni instance — model validation, content
validation, reference queries, AI evals, hygiene rules, and AI best-practices
reviews — with results posted as PR comments and enforced as required checks.

## How it works

Every check runs the same validation twice — once against the PR's **Omni
branch**, once against the **base model** — and reports only **net-new**
issues introduced by the PR. Pre-existing problems in the model never block a
PR; new ones do.

The suite assumes Omni's git integration branch convention: **the git branch
name and the Omni branch name match 1:1**. When Omni opens a PR from a branch
(or you push to a branch that mirrors an Omni branch), the workflows resolve
the Omni branch by name and validate against it.

PRs that touch no model YAML skip the whole suite (the skips count as passing
for required checks).

## The checks

| Check | What it does | Blocks on |
|-------|--------------|-----------|
| **Model validation** | `omni models validate` on branch vs base | Net-new validation errors |
| **Content validation** | Omni's content validator (dashboards/workbooks broken by the change) on branch vs base | Net-new content errors |
| **Reference queries** | Runs pinned queries from `tests/reference-queries/` and compares results to expected values | Net-new failures |
| **AI evals** *(optional)* | Runs an Omni eval prompt set against branch and base via the Eval Runs API | Net-new judged failures |
| **Shared-model hygiene** | Flags hand-authored table-backed base views in the shared model (they must come from the schema layer) | Any violation |
| **Omni agent review** | Omni's built-in modeling agent reviews the PR's Omni branch against your company standards; findings appear as a PR comment + check-run annotations. No extra accounts or keys — it uses the same Omni credentials as the other checks | `error`-severity findings (warnings/info advisory) |
| **Best-practices review** *(optional)* | An outside AI provider you supply a key for reviews the changed YAML against the full [omni-agent-skills](https://github.com/exploreomni/omni-agent-skills) docs; findings appear as a PR comment + check-run annotations. The included implementation uses Claude | `error`-severity findings (warnings/info advisory) |

A **validation summary** job assembles the first five checks into one combined
sticky PR comment. Each reviewer posts its own comment with numbered findings.

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

### `/omni-fix` *(optional)*

Comment `/omni-fix <selectors>` on a PR to have Claude generate corrected YAML
for selected best-practices findings and apply it **through Omni's API**
(never a direct git write — Omni owns the repo contents). Selectors:

```text
/omni-fix 1.1            # one finding by its comment number
/omni-fix 3.3-3.5        # a range
/omni-fix 2.*            # all findings in file section 2
/omni-fix errors         # all error-severity findings (also: warnings, infos)
/omni-fix rule:missing-label
/omni-fix all
```

Only users with write access can trigger it, and it refuses to run while Omni
Checks are in flight on the head commit.

## Getting started

### 1. Get the files into your repo

Either click **Use this template** and connect Omni's git integration to the
new repo, or copy `.github/` and `tests/` into your existing Omni-connected
repo.

### 2. Configure secrets and variables

`Settings → Secrets and variables → Actions`, or via `gh`:

```bash
gh secret set OMNI_TOKEN --body "<omni api token>"
gh variable set OMNI_BASE_URL --body "https://<instance>.omniapp.co"
gh variable set OMNI_MODEL_ID --body "<shared model uuid>"
```

| Name | Kind | Required | Description |
|------|------|----------|-------------|
| `OMNI_TOKEN` | secret | yes | Omni API token with access to the target model. |
| `OMNI_BASE_URL` | variable | yes | Omni API base URL, e.g. `https://myorg.omniapp.co`. |
| `OMNI_MODEL_ID` | variable | yes | The base **SHARED** model UUID this repo is git-linked to. |
| `CLAUDE_CODE_OAUTH_TOKEN` | secret | no | API credential for the bring-your-own-provider review (the included implementation uses Claude; get a token with `claude setup-token`). Enables the best-practices review and `/omni-fix`; both skip cleanly when unset. |
| `OMNI_EVAL_PROMPT_SET_ID` | variable | no | An Omni eval prompt set UUID. Enables the AI evals check; skips when unset. |
| `OMNI_MODEL_DIR` | variable | no | Directory the git integration writes model YAML into. Defaults to `omni` (Omni's default `modelPath` is `omni/<model name>`). |
| `OMNI_SKILLS_SHA` | variable | no | Pin the best-practices review to a specific `omni-agent-skills` commit. Defaults to `main`. |
| `OMNI_AGENT_REVIEW` | variable | no | Set to `false` to disable the Omni agent review (it is on by default whenever model YAML changed). Disable it if your Omni instance has AI features turned off. |

### 3. Add reference queries (recommended)

Pin the numbers that must never silently change — row counts, totals, KPI
values. See [tests/reference-queries/README.md](tests/reference-queries/README.md)
for the fixture format and worked examples.

### 4. Make the checks required (recommended)

In `Settings → Branches`, require the checks you care about (e.g.
`Model validation`, `Content validation`, `Reference queries`,
`Shared-model hygiene`, `Omni agent review`, `Best practices review`) on your
base branch. Skipped
runs (no model YAML changed, or an optional feature unconfigured) report as
passing, so required checks never wedge a PR.

## Customization

- **Company standards** — edit
  [`.github/best-practices/omni-models.md`](.github/best-practices/omni-models.md).
  The substantive best practices come live from the `omni-agent-skills` repo;
  this file is your company's layer on top of them: the severity rubric, the
  stable rule-id taxonomy findings are labeled with, and your
  **company-specific overrides and additions**, which take precedence when
  they conflict with the upstream skills.
- **Model directory** — if your git integration uses a custom `modelPath`,
  set the `OMNI_MODEL_DIR` variable to the directory that contains your model
  folder(s). The scripts map repo paths to Omni filenames assuming
  `<OMNI_MODEL_DIR>/<model name>/<file>`.
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

## License and disclaimer

MIT — see [LICENSE](LICENSE).

This project is provided **as-is**, without warranty of any kind, express or
implied, and without any guarantee of support or maintenance. It is not an
official Omni product. Review the workflows before enabling them — they call
your Omni instance's API with the credentials you configure, and `/omni-fix`
writes model changes to Omni branches.
