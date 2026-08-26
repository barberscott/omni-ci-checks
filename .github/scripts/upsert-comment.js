// Upsert a sticky PR comment identified by an HTML marker.
//
// Usage (from actions/github-script):
//   const upsert = require('./.github/scripts/upsert-comment.js');
//   await upsert({ github, context, marker: 'omni-model-validate', body: '...' });
//
// Behavior:
//   - Finds the most recent issue comment containing `<!-- ${marker} -->`.
//   - Updates it if found; otherwise creates a new one.
//   - No-op (logs) if the event has no PR number.

module.exports = async ({ github, context, marker, body }) => {
  if (!marker) throw new Error("marker is required");
  if (typeof body !== "string") throw new Error("body must be a string");

  const prNumber =
    context.payload.pull_request?.number ||
    context.payload.issue?.number ||
    null;

  if (!prNumber) {
    console.log(`No PR number on this event (${context.eventName}); skipping comment.`);
    return;
  }

  const tag = `<!-- ${marker} -->`;
  const fullBody = `${tag}\n${body}`;

  const { owner, repo } = context.repo;
  const comments = await github.paginate(github.rest.issues.listComments, {
    owner,
    repo,
    issue_number: prNumber,
    per_page: 100,
  });

  const existing = comments.find((c) => c.body && c.body.includes(tag));
  if (existing) {
    await github.rest.issues.updateComment({
      owner,
      repo,
      comment_id: existing.id,
      body: fullBody,
    });
    console.log(`Updated sticky comment ${existing.id} (marker=${marker})`);
  } else {
    const { data } = await github.rest.issues.createComment({
      owner,
      repo,
      issue_number: prNumber,
      body: fullBody,
    });
    console.log(`Created sticky comment ${data.id} (marker=${marker})`);
  }
};
