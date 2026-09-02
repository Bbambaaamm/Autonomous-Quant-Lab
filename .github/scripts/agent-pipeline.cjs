"use strict";

const STATES = ["agent:ready", "agent:running", "agent:pr", "agent:needs-human", "agent:verified"];
const EPIC_LABELS = new Set(["type:epic", "type:roadmap", "type:capability"]);
const ALLOWED = new Set([
  "none->agent:ready",
  "agent:ready->agent:running",
  "agent:running->agent:pr",
  "agent:running->agent:needs-human",
  "agent:pr->agent:needs-human",
  "agent:needs-human->agent:ready",
  "agent:needs-human->agent:running",
]);

function labelNames(labels) {
  return labels.map((label) => (typeof label === "string" ? label : label.name));
}

function currentState(labels) {
  const found = labelNames(labels).filter((label) => STATES.includes(label));
  return found.length === 0 ? "none" : found.length === 1 ? found[0] : "ambiguous";
}

function isImplementation(labels) {
  const names = new Set(labelNames(labels));
  return names.has("type:implementation") && ![...EPIC_LABELS].some((label) => names.has(label));
}

function transitionProgress(labels, previousState, nextState) {
  const states = labelNames(labels).filter((label) => STATES.includes(label));
  const previousLabels = previousState === "none" ? [] : [previousState];
  const allowed = new Set([...previousLabels, nextState]);
  if (states.includes("agent:needs-human") && previousState !== "agent:needs-human" && nextState !== "agent:needs-human") {
    return { ok: false, reason: "NEEDS_HUMAN_PRESENT" };
  }
  if (states.some((state) => !allowed.has(state))) return { ok: false, reason: "CONFLICTING_AGENT_STATE" };
  if (states.length === 0 && previousState !== "none") return { ok: false, reason: "PREVIOUS_STATE_MISSING" };
  if (states.length === 0) return { ok: true, complete: false };
  if (states.every((state) => state === nextState)) return { ok: true, complete: true };
  return { ok: true, complete: false };
}

function validateManualTransition({ labels, previousState, nextState, targetKind }) {
  const state = currentState(labels);
  if (nextState === "agent:verified") return { ok: false, reason: "VERIFIED_IS_AUTOMATED_ONLY" };
  if (targetKind !== "issue") return { ok: false, reason: "MANUAL_TRANSITIONS_REQUIRE_ISSUE" };
  if (!isImplementation(labels)) return { ok: false, reason: "NOT_UNAMBIGUOUS_IMPLEMENTATION" };
  if (!ALLOWED.has(`${previousState}->${nextState}`)) return { ok: false, reason: "INVALID_TRANSITION" };
  const progress = transitionProgress(labels, previousState, nextState);
  if (!progress.ok) return progress;
  return { ok: true, previousState, complete: progress.complete, observedState: state };
}

function parseAgentIssue(body) {
  const matches = [...(body || "").matchAll(/^\s*-?\s*Agent-Issue:\s*#([1-9][0-9]*)\s*$/gim)];
  return matches.length === 1 ? Number(matches[0][1]) : null;
}

function hasDurableLink(comments, { owner, repo, issueNumber, prNumber }) {
  return parseDurableLink(comments, { owner, repo, prNumber }) === issueNumber;
}

function parseDurableLink(comments, { owner, repo, prNumber }) {
  const prefix = `<!-- agent-link:v1 repo=${owner}/${repo} issue=`;
  const suffix = ` pr=${prNumber} -->`;
  const issueNumbers = comments
    .filter((comment) => comment.user?.login === "github-actions[bot]")
    .flatMap((comment) => (comment.body || "").split("\n"))
    .filter((line) => line.startsWith(prefix) && line.endsWith(suffix))
    .map((line) => Number(line.slice(prefix.length, -suffix.length)))
    .filter((number) => Number.isSafeInteger(number) && number > 0);
  const unique = [...new Set(issueNumbers)];
  return unique.length === 1 ? unique[0] : null;
}

function durablePrLinkDecision(comments, { owner, repo, issueNumber }) {
  const prefix = `<!-- agent-link:v1 repo=${owner}/${repo} issue=${issueNumber} pr=`;
  const suffix = " -->";
  const prNumbers = comments
    .filter((comment) => comment.user?.login === "github-actions[bot]")
    .flatMap((comment) => (comment.body || "").split("\n"))
    .filter((line) => line.startsWith(prefix) && line.endsWith(suffix))
    .map((line) => Number(line.slice(prefix.length, -suffix.length)))
    .filter((number) => Number.isSafeInteger(number) && number > 0);
  const unique = [...new Set(prNumbers)];
  if (unique.length > 1) return { ok: false, reason: "AMBIGUOUS_DURABLE_LINK" };
  return { ok: true, prNumber: unique[0] ?? null };
}

function durableIssueLinkDecision(comments, { owner, repo, prNumber }) {
  const prefix = `<!-- agent-link:v1 repo=${owner}/${repo} issue=`;
  const suffix = ` pr=${prNumber} -->`;
  const issueNumbers = comments
    .filter((comment) => comment.user?.login === "github-actions[bot]")
    .flatMap((comment) => (comment.body || "").split("\n"))
    .filter((line) => line.startsWith(prefix) && line.endsWith(suffix))
    .map((line) => Number(line.slice(prefix.length, -suffix.length)))
    .filter((number) => Number.isSafeInteger(number) && number > 0);
  const unique = [...new Set(issueNumbers)];
  if (unique.length > 1) return { ok: false, reason: "AMBIGUOUS_DURABLE_LINK" };
  return { ok: true, issueNumber: unique[0] ?? null };
}

function hasExactShaReviewAcknowledgement(comments, headSha) {
  const marker = `<!-- agent-review-ack:v1 sha=${headSha} -->`;
  return comments.some((comment) => comment.user?.login === "github-actions[bot]" &&
    comment.body?.split("\n").includes(marker));
}

function reviewSatisfied({ reviewDecision, reviews = [], acknowledgements = [], headSha }) {
  const nativeExactShaApproval = reviewDecision === "APPROVED" &&
    reviews.some((review) => review.state === "APPROVED" && review.commit_id === headSha);
  return nativeExactShaApproval || hasExactShaReviewAcknowledgement(acknowledgements, headSha);
}

function parseTrustedMarker(comments, kind, headSha) {
  const prefix = `<!-- ${kind}:v2 sha=${headSha} `;
  const matches = comments.filter((comment) => comment.user?.login === "github-actions[bot]")
    .flatMap((comment) => (comment.body || "").split("\n"))
    .filter((line) => line.startsWith(prefix) && line.endsWith(" -->"));
  return matches.length === 1 ? matches[0] : null;
}

function independentReviewSatisfied(comments, headSha) {
  return parseTrustedMarker(comments, "agent-codex-review", headSha)?.includes(" result=PASS ") === true;
}

function classifyCiFailure({ jobs, headSha, fixableJobs, prohibitedJobs }) {
  const failed = jobs.filter((job) => job.head_sha === headSha && ["failure", "timed_out"].includes(job.conclusion));
  if (failed.length !== 1) return { disposition: "NEEDS_HUMAN", reason: "AMBIGUOUS_FAILURE_SET" };
  const name = failed[0].name;
  if (prohibitedJobs.includes(name) || !fixableJobs.includes(name)) {
    return { disposition: "NEEDS_HUMAN", reason: "UNSUPPORTED_OR_UNSAFE_FAILURE" };
  }
  return { disposition: "FIX", job: name, evidence: `${headSha}:${failed[0].id}:${failed[0].run_attempt}` };
}

function fixAttemptDecision({ comments, sourceSha, evidence, maxAttempts }) {
  const prefix = `<!-- agent-fix:v2 source=${sourceSha} evidence=${evidence} `;
  const exact = comments.filter((comment) => comment.user?.login === "github-actions[bot]")
    .flatMap((comment) => (comment.body || "").split("\n")).filter((line) => line.startsWith(prefix));
  if (exact.length) return { action: "NO_WRITE", reason: "EXACT_EVIDENCE_ALREADY_PROCESSED" };
  const attempts = comments.filter((comment) => comment.user?.login === "github-actions[bot]")
    .flatMap((comment) => (comment.body || "").split("\n")).filter((line) => line.startsWith("<!-- agent-fix:v2 ")).length;
  return attempts >= maxAttempts ? { action: "NEEDS_HUMAN", reason: "FIX_BUDGET_EXHAUSTED" } : { action: "FIX", attempt: attempts + 1 };
}

function validatePatchPaths(paths, config) {
  if (!Array.isArray(paths) || paths.length === 0 || paths.length > config.maxPatchFiles) return false;
  return paths.every((path) => typeof path === "string" && !path.startsWith("/") && !path.includes("..") &&
    !config.deniedPathPrefixes.some((prefix) => path.startsWith(prefix)) &&
    !config.deniedPathBasenames.includes(path.split("/").at(-1)) &&
    !config.deniedPathFragments.some((fragment) => path.toLowerCase().includes(fragment)));
}

function stateMutationPlan(labels, previousState, nextState) {
  const progress = transitionProgress(labels, previousState, nextState);
  if (!progress.ok) return progress;
  const names = new Set(labelNames(labels));
  return {
    ok: true,
    add: names.has(nextState) ? [] : [nextState],
    remove: previousState !== "none" && names.has(previousState) ? [previousState] : [],
    complete: progress.complete,
  };
}

function escalationMutationPlan(labels) {
  const names = new Set(labelNames(labels));
  const states = [...names].filter((label) => STATES.includes(label));
  const permitted = new Set(["agent:pr", "agent:verified", "agent:needs-human"]);
  if (states.some((state) => !permitted.has(state))) return { ok: false, reason: "CONFLICTING_AGENT_STATE" };
  return {
    ok: true,
    add: names.has("agent:needs-human") ? [] : ["agent:needs-human"],
    remove: states.filter((state) => state !== "agent:needs-human"),
  };
}

function invalidationLifecycleDecision({ prLabels, issueLabels = [], issueLoaded, durableLink, markerIssueNumber }) {
  const prStates = labelNames(prLabels).filter((label) => STATES.includes(label));
  const issueStates = labelNames(issueLabels).filter((label) => STATES.includes(label));
  if ([...prStates, ...issueStates].includes("agent:needs-human")) {
    return { action: "NEEDS_HUMAN_NO_WRITE" };
  }
  if (!durableLink.ok) return { action: "ESCALATE_CONFLICT", reason: durableLink.reason };
  if (durableLink.issueNumber === null) {
    return prStates.length === 0
      ? { action: "PRELINK_NO_WRITE" }
      : { action: "ESCALATE_CONFLICT", reason: "LINKED_STATE_WITHOUT_DURABLE_LINK" };
  }
  if (!issueLoaded || markerIssueNumber !== durableLink.issueNumber) {
    return { action: "ESCALATE_CONFLICT", reason: "LINKAGE_MISMATCH" };
  }
  const validLinkedStates = (states) => states.length >= 1 &&
    states.every((state) => state === "agent:pr" || state === "agent:verified");
  if (!validLinkedStates(prStates) || !validLinkedStates(issueStates)) {
    return { action: "ESCALATE_CONFLICT", reason: "INVALID_LINKED_STATE" };
  }
  if (![...prStates, ...issueStates].includes("agent:verified")) {
    return { action: "LINKED_PR_NO_WRITE" };
  }
  return { action: "INVALIDATE_VERIFIED", issueNumber: durableLink.issueNumber };
}

function successfulRequiredJobs(jobs, requiredNames, headSha) {
  const latest = new Map();
  for (const job of jobs) {
    if (job.head_sha !== headSha) continue;
    const old = latest.get(job.name);
    if (!old || job.run_attempt >= old.run_attempt) latest.set(job.name, job);
  }
  return requiredNames.every((name) => latest.get(name)?.conclusion === "success");
}

function authoritativeCiRunCandidates(runs, { workflowName, headSha, prNumber }) {
  return runs
    .filter((run) => run.name === workflowName && run.event === "pull_request" &&
      run.status === "completed" && run.conclusion === "success" && run.head_sha === headSha &&
      run.pull_requests?.length === 1 && run.pull_requests[0].number === prNumber)
    .sort((left, right) => right.id - left.id);
}

function verificationTriggerDecision({ workflowRun, workflowCallInputs = {} }) {
  if (workflowRun) {
    if (workflowRun.status !== "completed" || workflowRun.conclusion !== "success" ||
        workflowRun.pull_requests?.length !== 1) {
      return { ok: false, reason: "INVALID_WORKFLOW_RUN_TRIGGER" };
    }
    const prNumber = workflowRun.pull_requests[0].number;
    if (workflowRun.name === "CI" && workflowRun.event === "pull_request") {
      return { ok: true, kind: "ci", prNumber, headSha: workflowRun.head_sha };
    }
    if (workflowRun.name === "Agent review signal" && workflowRun.event === "pull_request_review") {
      return { ok: true, kind: "review-signal", prNumber, headSha: null };
    }
    if (workflowRun.name === "Agent Codex review" && workflowRun.event === "workflow_run") {
      return { ok: true, kind: "codex-review", prNumber, headSha: workflowRun.head_sha };
    }
    return { ok: false, reason: "UNTRUSTED_WORKFLOW_RUN_TRIGGER" };
  }

  const prNumber = Number(workflowCallInputs.prNumber);
  const headSha = workflowCallInputs.headSha || "";
  if (!Number.isSafeInteger(prNumber) || prNumber < 1 || !/^[0-9a-f]{40}$/.test(headSha)) {
    return { ok: false, reason: "INVALID_WORKFLOW_CALL_INPUTS" };
  }
  return { ok: true, kind: "workflow-call", prNumber, headSha };
}

function verificationDecision(input) {
  if (input.workflowName !== "CI" || input.workflowConclusion !== "success") return { ok: false, reason: "CI_NOT_SUCCESSFUL" };
  if (!input.headSha || input.prHeadSha !== input.headSha) return { ok: false, reason: "HEAD_SHA_MISMATCH" };
  if (!input.open || !input.correctBase) return { ok: false, reason: "PR_NOT_OPEN_AGAINST_DEFAULT" };
  if (input.draft) return { ok: false, reason: "PR_IS_DRAFT" };
  if (!input.reviewSatisfied) return { ok: false, reason: "EXACT_SHA_REVIEW_MISSING" };
  if (!input.independentReviewSatisfied) return { ok: false, reason: "INDEPENDENT_REVIEW_PASS_MISSING" };
  if (!input.issueIsImplementation) return { ok: false, reason: "ISSUE_NOT_IMPLEMENTATION" };
  if (!input.statesReconciliable) return { ok: false, reason: "INVALID_PIPELINE_STATE" };
  if (input.needsHuman) return { ok: false, reason: "NEEDS_HUMAN_PRESENT" };
  if (!input.requiredJobsSuccessful) return { ok: false, reason: "REQUIRED_CI_JOBS_MISSING" };
  return { ok: true };
}

module.exports = {
  STATES,
  currentState,
  authoritativeCiRunCandidates,
  durablePrLinkDecision,
  durableIssueLinkDecision,
  escalationMutationPlan,
  hasDurableLink,
  isImplementation,
  invalidationLifecycleDecision,
  hasExactShaReviewAcknowledgement,
  parseAgentIssue,
  parseDurableLink,
  reviewSatisfied,
  independentReviewSatisfied,
  parseTrustedMarker,
  classifyCiFailure,
  fixAttemptDecision,
  validatePatchPaths,
  stateMutationPlan,
  successfulRequiredJobs,
  transitionProgress,
  validateManualTransition,
  verificationDecision,
  verificationTriggerDecision,
};
