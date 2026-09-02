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
  "agent:needs-human->agent:pr",
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
  const marker = `<!-- agent-link:v1 repo=${owner}/${repo} issue=${issueNumber} pr=${prNumber} -->`;
  return comments.some((comment) => comment.user?.login === "github-actions[bot]" &&
    comment.body?.split("\n").includes(marker));
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

function successfulRequiredJobs(jobs, requiredNames, headSha) {
  const latest = new Map();
  for (const job of jobs) {
    if (job.head_sha !== headSha) continue;
    const old = latest.get(job.name);
    if (!old || job.run_attempt >= old.run_attempt) latest.set(job.name, job);
  }
  return requiredNames.every((name) => latest.get(name)?.conclusion === "success");
}

function verificationDecision(input) {
  if (input.workflowName !== "CI" || input.workflowConclusion !== "success") return { ok: false, reason: "CI_NOT_SUCCESSFUL" };
  if (!input.headSha || input.prHeadSha !== input.headSha) return { ok: false, reason: "HEAD_SHA_MISMATCH" };
  if (!input.open || !input.correctBase) return { ok: false, reason: "PR_NOT_OPEN_AGAINST_DEFAULT" };
  if (input.draft) return { ok: false, reason: "PR_IS_DRAFT" };
  if (!input.reviewSatisfied) return { ok: false, reason: "EXACT_SHA_REVIEW_MISSING" };
  if (!input.issueIsImplementation) return { ok: false, reason: "ISSUE_NOT_IMPLEMENTATION" };
  if (!input.statesReconciliable) return { ok: false, reason: "INVALID_PIPELINE_STATE" };
  if (input.needsHuman) return { ok: false, reason: "NEEDS_HUMAN_PRESENT" };
  if (!input.requiredJobsSuccessful) return { ok: false, reason: "REQUIRED_CI_JOBS_MISSING" };
  return { ok: true };
}

module.exports = {
  STATES,
  currentState,
  hasDurableLink,
  isImplementation,
  hasExactShaReviewAcknowledgement,
  parseAgentIssue,
  parseDurableLink,
  reviewSatisfied,
  stateMutationPlan,
  successfulRequiredJobs,
  transitionProgress,
  validateManualTransition,
  verificationDecision,
};
