// Emit Omni best-practices findings as a GitHub Checks-API check run with
// inline annotations (file + line markers on the diff / Checks tab).
//
// Usage (from actions/github-script):
//   const annotate = require('./.github/scripts/post-check-annotations.js');
//   await annotate({ github, context, findings, summary, name });
//
// Why a check run (vs. only a PR comment):
//   - Annotations render inline in the diff and on the Checks tab.
//   - Each run creates a fresh check, so annotations always reflect CURRENT
//     state — there is no comment history to prune.
//
// Advisory by design: conclusion is always "neutral", so even failure-level
// annotations never block merge. (Annotation level controls the inline color;
// the check conclusion controls merge gating — they are independent.)

const LEVEL = { error: "failure", warning: "warning", info: "notice" };
// GitHub caps annotations at 50 per checks API request.
const MAX_ANNOTATIONS = 50;

module.exports = async ({ github, context, findings, summary, name }) => {
  const checkName = name || "Omni BP annotations";
  const items = Array.isArray(findings) ? findings : [];

  const headSha =
    context.payload.pull_request?.head?.sha || context.sha;
  if (!headSha) {
    console.log("No head SHA available; skipping check run.");
    return;
  }

  const counts = { error: 0, warning: 0, info: 0 };
  const annotations = [];
  for (const f of items) {
    const sev = LEVEL[f.severity] ? f.severity : "info";
    counts[sev]++;
    if (annotations.length >= MAX_ANNOTATIONS) continue;
    const line = Number.isInteger(f.line) && f.line > 0 ? f.line : 1;
    const endLine =
      Number.isInteger(f.end_line) && f.end_line >= line ? f.end_line : line;
    const sug = (f.suggestion || "").trim();
    annotations.push({
      path: f.file || "(unknown)",
      start_line: line,
      end_line: endLine,
      annotation_level: LEVEL[sev],
      title: f.rule || "finding",
      message: sug ? `${f.message || ""}\n\nSuggestion: ${sug}` : f.message || "",
    });
  }

  const total = items.length;
  const headline =
    total === 0
      ? "No best-practices issues found"
      : `${counts.error} error(s), ${counts.warning} warning(s), ${counts.info} info`;
  const dropped = total - annotations.length;
  const summaryText =
    (summary || "").trim() +
    (dropped > 0 ? `\n\n_${dropped} additional finding(s) not annotated (50-annotation cap)._` : "");

  const { owner, repo } = context.repo;
  const { data } = await github.rest.checks.create({
    owner,
    repo,
    name: checkName,
    head_sha: headSha,
    status: "completed",
    conclusion: "neutral", // advisory — never blocks merge
    output: {
      title: headline,
      summary: summaryText || headline,
      annotations,
    },
  });
  console.log(
    `Created check run ${data.id} (${checkName}) @ ${headSha.slice(0, 7)} ` +
      `with ${annotations.length} annotation(s); conclusion=neutral`,
  );
};
