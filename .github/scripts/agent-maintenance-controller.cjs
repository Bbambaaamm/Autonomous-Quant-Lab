"use strict";

const guards = require("./agent-maintenance-guards.cjs");

const allowedPath = (path) => typeof path === "string" && (
  /^\.github\/workflows\/agent-[^/]+\.yml$/.test(path) ||
  /^\.github\/scripts\/agent-[^/]+\.cjs$/.test(path) ||
  path === ".github/agent-pipeline.json" ||
  path === "docs/autonomous-development-pipeline.md" ||
  ["0003-autonomous-development-pipeline-v2.md", "0006-conditional-autonomous-merge.md", "0007-control-plane-remediation.md"]
    .some((name) => path === `docs/adr/${name}`)
);
const allowedFile = (file) => allowedPath(file?.filename) &&
  (file.status !== "renamed" || allowedPath(file.previous_filename));

function requiredCiEvidence(runs, jobsByRun, expected, pipeline) {
  const matching = runs.filter((run) => run.name === "CI" && run.event === "pull_request" &&
    run.head_sha === expected.headSha && run.pull_requests?.length === 1 &&
    run.pull_requests[0].number === expected.prNumber).sort((a, b) => b.id - a.id);
  const run = matching[0];
  const jobs = run ? jobsByRun.get(run.id) : null;
  return {repo: expected.repo, headSha: expected.headSha, prNumber: expected.prNumber,
    event: run?.event, name: run?.name, status: run?.status, conclusion: run?.conclusion,
    isNewest: Boolean(run), requiredJobsSuccessful: Boolean(run && pipeline.successfulRequiredJobs(jobs, expected.requiredJobNames, expected.headSha)),
    runId: run?.id, runAttempt: run?.run_attempt};
}

async function collectSnapshot({github, context, expected, pipeline, autonomy, rulesetAudit}) {
  const repoArgs = context.repo;
  const [{data: pr}, {data: issue}, {data: permission}, {data: main}] = await Promise.all([
    github.rest.pulls.get({...repoArgs, pull_number: expected.prNumber}),
    github.rest.issues.get({...repoArgs, issue_number: expected.issueNumber}),
    github.rest.repos.getCollaboratorPermissionLevel({...repoArgs, username: expected.requester}),
    github.rest.repos.getBranch({...repoArgs, branch: expected.defaultBranch}),
  ]);
  const [issueComments, prComments, files, runs, origin] = await Promise.all([
    github.paginate(github.rest.issues.listComments, {...repoArgs, issue_number: expected.issueNumber, per_page: 100}),
    github.paginate(github.rest.issues.listComments, {...repoArgs, issue_number: expected.prNumber, per_page: 100}),
    github.paginate(github.rest.pulls.listFiles, {...repoArgs, pull_number: expected.prNumber, per_page: 100}),
    github.paginate(github.rest.actions.listWorkflowRunsForRepo, {...repoArgs, event: "pull_request", head_sha: expected.headSha, per_page: 100}),
    github.rest.actions.getWorkflowRun({...repoArgs, run_id: expected.requestRunId}).then(({data}) => data),
  ]);
  const comparison = pr.head?.sha === expected.headSha
    ? await github.rest.repos.compareCommits({...repoArgs, base: main.commit.sha, head: expected.headSha}) : null;
  const jobsByRun = new Map();
  for (const run of runs.filter((item) => item.name === "CI" && item.head_sha === expected.headSha)) {
    jobsByRun.set(run.id, await github.paginate(github.rest.actions.listJobsForWorkflowRun,
      {...repoArgs, run_id: run.id, filter: "all", per_page: 100}));
  }
  const issueLink = pipeline.durablePrLinkDecision(issueComments, {...repoArgs, issueNumber: expected.issueNumber});
  const prLink = pipeline.durableIssueLinkDecision(prComments, {...repoArgs, prNumber: expected.prNumber});
  const fullLink = pipeline.fullLinkageDecision({prBody: pr.body, prComments, issueComments, ...repoArgs,
    issueNumber: expected.issueNumber, prNumber: expected.prNumber});
  return {pr, issue, issueComments, prComments,
    authorization: autonomy.authorizationDecision({comments: issueComments, repo: expected.repo,
      issueNumber: expected.issueNumber, title: issue.title, body: issue.body || "", labels: issue.labels, state: issue.state}),
    requesterPermission: permission.permission, currentMainSha: main.commit.sha,
    behindBy: comparison?.data.behind_by, markerIssueNumber: pipeline.parseAgentIssue(pr.body),
    linksConflict: !issueLink.ok || !prLink.ok || (issueLink.prNumber !== null && issueLink.prNumber !== expected.prNumber) ||
      (prLink.issueNumber !== null && prLink.issueNumber !== expected.issueNumber),
    fullLinkageValid: fullLink.ok, fileScopeValid: Number.isSafeInteger(pr.changed_files) && pr.changed_files > 0 &&
      pr.changed_files <= 3000 && files.length === pr.changed_files && files.every(allowedFile),
    files, ci: requiredCiEvidence(runs, jobsByRun, expected, pipeline), rulesetAudit,
    requestOrigin: {actor: origin.actor?.login, runId: origin.id, runAttempt: origin.run_attempt,
      workflowPath: origin.path, headRepository: origin.head_repository?.full_name, headBranch: origin.head_branch}};
}

function controller(deps) {
  const snapshot = () => collectSnapshot(deps);
  const validate = (state, options) => guards.bindingDecision(state, deps.expected, options);
  const guarded = (operations, options = {}, postOptions = options) => guards.guardedSequence({snapshot,
    validate: (state) => validate(state, options), operations,
    postvalidate: (state) => validate(state, postOptions)});
  return {snapshot, validate, guarded};
}

module.exports = {allowedPath, allowedFile, requiredCiEvidence, collectSnapshot, controller};
