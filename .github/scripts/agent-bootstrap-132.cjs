"use strict";

// Deliberately immutable bindings. This module is migration infrastructure for Issue #132,
// not a general control-plane entry point.
const BINDING = Object.freeze({
  repository: "Bbambaaamm/Autonomous-Quant-Lab",
  defaultBranch: "main",
  trustedBaseSha: "94611601bcd3fd683bc54f9f5d6023e4abc5951c",
  issueNumber: 130,
  prNumber: 131,
  headSha: "76694eef519c0057750b170c55fe94673e7960e0",
  specHash: "7b2b5c30a3d55643512159bda237a97259e800ec200cebf92799f98633a88d81",
  rulesetName: "Protect main",
  gateContext: "agent-verified-gate",
  gateIntegrationId: 15368,
});

const BOOTSTRAP_PATHS = new Set([
  ".github/scripts/agent-bootstrap-132.cjs",
  ".github/scripts/agent-bootstrap-132.test.cjs",
  ".github/workflows/agent-bootstrap-132.yml",
  ".github/workflows/ci.yml",
  "docs/adr/0008-one-time-control-plane-bootstrap.md",
]);
const GOVERNANCE_PATHS = [
  "AGENTS.md",
  ".github/agent-pipeline.json",
  "docs/autonomous-development-pipeline.md",
  "docs/adr/0003-autonomous-development-pipeline-v2.md",
  "docs/adr/0006-conditional-autonomous-merge.md",
  "docs/adr/0007-control-plane-remediation.md",
];

function fail(reason) { return { ok: false, reason }; }

function rulesetDecision(ruleset) {
  if (!ruleset || ruleset.name !== BINDING.rulesetName || ruleset.target !== "branch" || ruleset.enforcement !== "active") return fail("RULESET_IDENTITY_CHANGED");
  if ((ruleset.bypass_actors || []).length !== 0) return fail("RULESET_BYPASS_PRESENT");
  const refs = ruleset.conditions?.ref_name;
  if (JSON.stringify(refs?.include) !== JSON.stringify(["refs/heads/main"]) || (refs?.exclude || []).length) return fail("RULESET_TARGET_CHANGED");
  for (const type of ["deletion", "non_fast_forward", "pull_request", "required_status_checks"]) {
    if ((ruleset.rules || []).filter((rule) => rule.type === type).length !== 1) return fail(`RULESET_${type.toUpperCase()}_CHANGED`);
  }
  const status = ruleset.rules.find((rule) => rule.type === "required_status_checks")?.parameters;
  if (status?.strict_required_status_checks_policy !== true) return fail("RULESET_NOT_STRICT");
  const gate = (status.required_status_checks || []).filter((check) => check.context === BINDING.gateContext && check.integration_id === BINDING.gateIntegrationId);
  return gate.length === 1 ? { ok: true } : fail("RULESET_GATE_CHANGED");
}

function ciDecision(run, jobs, requiredJobs) {
  if (!run || run.name !== "CI" || run.event !== "pull_request" || run.status !== "completed" || run.conclusion !== "success" || run.head_sha !== BINDING.headSha || run.pull_requests?.length !== 1 || run.pull_requests[0].number !== BINDING.prNumber) return fail("CI_IDENTITY_CHANGED");
  if (!Number.isSafeInteger(run.run_attempt) || run.run_attempt < 1) return fail("CI_ATTEMPT_INVALID");
  const latest = new Map();
  for (const job of jobs || []) {
    const old = latest.get(job.name);
    if (!old || job.run_attempt > old.run_attempt) latest.set(job.name, job);
  }
  const valid = requiredJobs.every((name) => latest.get(name)?.conclusion === "success" && latest.get(name)?.run_attempt === run.run_attempt);
  return valid ? { ok: true } : fail("CI_JOBS_CHANGED");
}

function bindingDecision(input) {
  if (input.repository !== BINDING.repository || input.defaultBranch !== BINDING.defaultBranch) return fail("REPOSITORY_BINDING_CHANGED");
  if (input.trustedBaseSha !== BINDING.trustedBaseSha || input.mainContainsTrustedBase !== true || input.mainBootstrapPathsOnly !== true) return fail("MAIN_BINDING_CHANGED");
  const { issue, pr } = input;
  if (!issue || issue.number !== BINDING.issueNumber || issue.state !== "open" || input.issueIsImplementation !== true || input.authorizationOk !== true || input.specHash !== BINDING.specHash) return fail("ISSUE_BINDING_CHANGED");
  if (!pr || pr.number !== BINDING.prNumber || pr.state !== "open" || pr.draft || pr.base?.ref !== BINDING.defaultBranch || pr.head?.sha !== BINDING.headSha || input.prIssueNumber !== BINDING.issueNumber) return fail("PR_BINDING_CHANGED");
  if (input.linkageRecoverable !== true) return fail("LINKAGE_CHANGED");
  if (!input.lifecycle || !["needs-human", "pr"].includes(input.lifecycle)) return fail("LIFECYCLE_CHANGED");
  if (input.changedFilesComplete !== true || input.scopeAllowed !== true) return fail("CANDIDATE_SCOPE_CHANGED");
  if (input.candidateDescendsTrustedBase !== true) return fail("CANDIDATE_ANCESTRY_CHANGED");
  return { ok: true };
}

function mainLineageDecision(comparison, commitFiles) {
  if (!comparison || !["ahead", "identical"].includes(comparison.status)) return fail("TRUSTED_BASE_NOT_ON_MAIN");
  if (!Array.isArray(comparison.commits) || comparison.commits.length === 0) return fail("BOOTSTRAP_NOT_ON_MAIN");
  if (comparison.total_commits !== comparison.commits.length || commitFiles.length !== comparison.commits.length) return fail("MAIN_HISTORY_INCOMPLETE");
  const allowed = (file) => file && BOOTSTRAP_PATHS.has(file.filename) && (file.status !== "renamed" || BOOTSTRAP_PATHS.has(file.previous_filename));
  if (!(comparison.files || []).every(allowed) || !commitFiles.every((files) => files.length > 0 && files.every(allowed))) return fail("UNRELATED_MAIN_DRIFT");
  return { ok: true };
}

function linkageRecoveryDecision(input, stage) {
  if (input.bodyIssueNumber !== BINDING.issueNumber || input.issueConflicting || input.prConflicting) return fail("LINKAGE_CONFLICT");
  if (stage === 0) return { ok: true, next: input.issueExact ? (input.prExact ? 2 : 1) : 0 };
  if (stage === 1 && input.issueExact) return { ok: true, next: input.prExact ? 2 : 1 };
  if (stage === 2 && input.issueExact && input.prExact) return { ok: true, next: 2 };
  return fail("LINKAGE_STAGE_INCOMPLETE");
}

function reviewContextDecision(context) {
  if (!context || context.version !== 1 || context.binding?.headSha !== BINDING.headSha || context.binding?.specHash !== BINDING.specHash) return fail("REVIEW_BINDING_MISSING");
  if (context.issue?.number !== BINDING.issueNumber || typeof context.issue.title !== "string" || typeof context.issue.body !== "string" || !context.issue.classification?.includes("type:implementation") || context.authorization?.ok !== true || context.authorization.specHash !== BINDING.specHash || !context.authorization.marker?.includes(BINDING.specHash)) return fail("REVIEW_AUTHORIZATION_MISSING");
  if (!Array.isArray(context.governanceFiles) || JSON.stringify(context.governanceFiles.map((x) => x.path)) !== JSON.stringify(GOVERNANCE_PATHS) || context.governanceFiles.some((x) => !x.content)) return fail("REVIEW_GOVERNANCE_MISSING");
  if (!context.diff || context.diff.base !== BINDING.trustedBaseSha || context.diff.head !== BINDING.headSha || !context.diff.content) return fail("REVIEW_DIFF_MISSING");
  if (!context.ci || context.ci.headSha !== BINDING.headSha || !Number.isSafeInteger(context.ci.runId) || !Number.isSafeInteger(context.ci.attempt) || context.ci.jobs?.length !== 9 || context.ci.jobs.some((job) => !job || job.conclusion !== "success" || job.run_attempt !== context.ci.attempt)) return fail("REVIEW_CI_MISSING");
  if (rulesetDecision(context.ruleset).ok !== true) return fail("REVIEW_RULESET_MISSING");
  return { ok: true };
}

function pairedWriteDecision(before, expectedStage) {
  if (!before?.guardsCurrent) return fail("MUTABLE_GUARD_DRIFT");
  if (before.stage !== expectedStage) return fail("PAIRED_WRITE_STAGE_CHANGED");
  return { ok: true, nextStage: expectedStage + 1 };
}

module.exports = { BINDING, BOOTSTRAP_PATHS, GOVERNANCE_PATHS, bindingDecision, ciDecision, linkageRecoveryDecision, mainLineageDecision, pairedWriteDecision, reviewContextDecision, rulesetDecision };
