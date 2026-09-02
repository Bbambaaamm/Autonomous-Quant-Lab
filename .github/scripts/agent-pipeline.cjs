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

function validateManualTransition({ labels, nextState, targetKind, linkedIssueNumber }) {
  const state = currentState(labels);
  if (state === "ambiguous") return { ok: false, reason: "MULTIPLE_AGENT_STATES" };
  if (nextState === "agent:verified") return { ok: false, reason: "VERIFIED_IS_AUTOMATED_ONLY" };
  if (targetKind !== "issue") return { ok: false, reason: "MANUAL_TRANSITIONS_REQUIRE_ISSUE" };
  if (!isImplementation(labels)) return { ok: false, reason: "NOT_UNAMBIGUOUS_IMPLEMENTATION" };
  if (linkedIssueNumber !== undefined) return { ok: false, reason: "UNEXPECTED_LINK" };
  if (!ALLOWED.has(`${state}->${nextState}`)) return { ok: false, reason: "INVALID_TRANSITION" };
  return { ok: true, previousState: state };
}

function parseAgentIssue(body) {
  const matches = [...(body || "").matchAll(/^\s*-?\s*Agent-Issue:\s*#([1-9][0-9]*)\s*$/gim)];
  return matches.length === 1 ? Number(matches[0][1]) : null;
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
  if (!input.approved) return { ok: false, reason: "APPROVED_REVIEW_MISSING" };
  if (!input.issueIsImplementation) return { ok: false, reason: "ISSUE_NOT_IMPLEMENTATION" };
  if (input.issueState !== "agent:pr" || input.prState !== "agent:pr") return { ok: false, reason: "INVALID_PIPELINE_STATE" };
  if (input.needsHuman) return { ok: false, reason: "NEEDS_HUMAN_PRESENT" };
  if (!input.requiredJobsSuccessful) return { ok: false, reason: "REQUIRED_CI_JOBS_MISSING" };
  return { ok: true };
}

module.exports = {
  STATES,
  currentState,
  isImplementation,
  parseAgentIssue,
  successfulRequiredJobs,
  validateManualTransition,
  verificationDecision,
};
