"use strict";

// Pure guards. Inputs must come from freshly fetched, trusted controller data;
// this module does not turn model output or a candidate artifact into authority.
const SHA = /^[0-9a-f]{40}$/;
const fail = (reason) => ({ ok: false, reason });
const pass = () => ({ ok: true });
const names = (labels) => Array.isArray(labels)
  ? labels.map((label) => typeof label === "string" ? label : label?.name)
  : [];

function rulesetDecision(ruleset, expected) {
  if (!expected || typeof expected.defaultBranch !== "string" || !expected.defaultBranch ||
      !Array.isArray(expected.requiredChecks) || expected.requiredChecks.length === 0) {
    return fail("RULESET_EXPECTATION_INVALID");
  }
  if (!ruleset || ruleset.name !== "Protect main" || ruleset.target !== "branch" ||
      ruleset.enforcement !== "active") return fail("RULESET_NOT_ACTIVE");
  // GitHub omits this field without the necessary ruleset access. Missing is
  // UNKNOWN, not an empty bypass list. Never use (bypass_actors || []).
  if (!Object.hasOwn(ruleset, "bypass_actors") || !Array.isArray(ruleset.bypass_actors)) {
    return fail("RULESET_BYPASS_UNKNOWN");
  }
  if (ruleset.bypass_actors.length !== 0) return fail("RULESET_HAS_BYPASS");
  const refs = ruleset.conditions?.ref_name;
  if (!Array.isArray(refs?.include) || refs.include.length !== 1 ||
      refs.include[0] !== `refs/heads/${expected.defaultBranch}` ||
      !Array.isArray(refs.exclude) || refs.exclude.length !== 0) return fail("RULESET_BRANCH_SCOPE_INVALID");
  if (!Array.isArray(ruleset.rules)) return fail("RULESET_RULES_MISSING");
  for (const type of ["pull_request", "deletion", "non_fast_forward", "required_status_checks"]) {
    if (ruleset.rules.filter((rule) => rule?.type === type).length !== 1) return fail("RULESET_RULE_MISSING_OR_DUPLICATE");
  }
  const status = ruleset.rules.find((rule) => rule.type === "required_status_checks").parameters;
  if (status?.strict_required_status_checks_policy !== true) return fail("RULESET_NOT_STRICT");
  const actual = status.required_status_checks;
  if (!Array.isArray(actual) || actual.some((check) => typeof check?.context !== "string" || !check.context)) {
    return fail("RULESET_CHECKS_INVALID");
  }
  if (new Set(actual.map((check) => check.context)).size !== actual.length) return fail("RULESET_CHECKS_AMBIGUOUS");
  const required = expected.requiredChecks;
  if (required.some((check) => typeof check?.context !== "string" || !check.context ||
      !Number.isSafeInteger(check.integration_id) || check.integration_id <= 0) ||
      new Set(required.map((check) => check.context)).size !== required.length) return fail("RULESET_EXPECTATION_INVALID");
  if (!required.some((check) => check.context === "agent-verified-gate" && check.integration_id === 15368)) {
    return fail("RULESET_GATE_EXPECTATION_MISSING");
  }
  for (const check of required) {
    if (!actual.some((value) => value.context === check.context && value.integration_id === check.integration_id)) {
      return fail("RULESET_REQUIRED_CHECK_CHANGED");
    }
  }
  // Additional requirements remain enforced by GitHub; they are never removed.
  return pass();
}

function reviewDecision(review, headSha) {
  if (!SHA.test(headSha || "") || !review || typeof review !== "object" || Array.isArray(review)) {
    return fail("REVIEW_MALFORMED");
  }
  const keys = ["reviewed_sha", "result", "findings", "issue_scope_consistent", "test_or_governance_weakened", "paper_only_live_trading_safe", "summary"];
  if (Object.keys(review).length !== keys.length || keys.some((key) => !Object.hasOwn(review, key)) ||
      typeof review.summary !== "string" || review.summary.length > 4096 || !Array.isArray(review.findings) ||
      review.findings.length > 50 || ["issue_scope_consistent", "test_or_governance_weakened", "paper_only_live_trading_safe"]
        .some((key) => typeof review[key] !== "boolean")) return fail("REVIEW_MALFORMED");
  if (review.reviewed_sha !== headSha) return fail("REVIEW_SHA_MISMATCH");
  if (review.result !== "PASS" || review.findings.length !== 0 || !review.issue_scope_consistent ||
      review.test_or_governance_weakened || !review.paper_only_live_trading_safe) return fail("REVIEW_NOT_PASS");
  return pass();
}

function bindingDecision(snapshot, expected, options = {}) {
  if (!snapshot || !expected || !SHA.test(expected.headSha || "") || !SHA.test(expected.baseSha || "") ||
      !/^[0-9a-f]{64}$/.test(expected.specHash || "") || typeof expected.repo !== "string" ||
      !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(expected.repo) ||
      !Number.isSafeInteger(expected.issueNumber) || expected.issueNumber < 1 ||
      !Number.isSafeInteger(expected.prNumber) || expected.prNumber < 1) return fail("EXPECTED_BINDING_INVALID");
  const { pr, issue } = snapshot;
  if (pr?.number !== expected.prNumber || pr.state !== "open" || pr.draft !== false ||
      pr.head?.sha !== expected.headSha || pr.base?.ref !== expected.defaultBranch ||
      pr.head?.repo?.full_name !== expected.repo || pr.base?.repo?.full_name !== expected.repo ||
      pr.base?.sha !== expected.baseSha) return fail("PR_BINDING_CHANGED");
  if (issue?.number !== expected.issueNumber || issue.state !== "open" || issue.pull_request ||
      !names(issue.labels).includes("type:implementation") ||
      names(issue.labels).some((name) => ["type:epic", "type:roadmap", "type:capability"].includes(name))) {
    return fail("ISSUE_BINDING_CHANGED");
  }
  if (snapshot.authorization?.ok !== true || snapshot.authorization.specHash !== expected.specHash) return fail("AUTHORIZATION_CHANGED");
  if (snapshot.requestOrigin?.actor !== expected.requester ||
      snapshot.requestOrigin?.runId !== expected.requestRunId ||
      snapshot.requestOrigin?.runAttempt !== expected.requestRunAttempt ||
      snapshot.requestOrigin?.workflowPath !== ".github/workflows/agent-control-plane-remediation-request.yml" ||
      snapshot.requestOrigin?.headRepository !== expected.repo ||
      snapshot.requestOrigin?.headBranch !== expected.defaultBranch) return fail("REQUEST_ORIGIN_CHANGED");
  if (!["admin", "maintain", "write"].includes(snapshot.requesterPermission)) return fail("REQUESTER_PERMISSION_REVOKED");
  if (snapshot.currentMainSha !== expected.baseSha || snapshot.behindBy !== 0) return fail("MAIN_CHANGED");
  if (snapshot.markerIssueNumber !== expected.issueNumber || snapshot.linksConflict !== false) return fail("LINKAGE_CONFLICT");
  if (options.allowPartialLinks !== true && snapshot.fullLinkageValid !== true) return fail("LINKAGE_INCOMPLETE");
  const states = options.states || ["agent:needs-human", "agent:pr"];
  if (!Array.isArray(states) || states.length === 0) return fail("LIFECYCLE_EXPECTATION_INVALID");
  for (const [index, labels] of [pr.labels, issue.labels].entries()) {
    if (!Array.isArray(labels) || names(labels).some((name) => typeof name !== "string")) return fail("LIFECYCLE_CHANGED");
    const state = names(labels).filter((name) => name.startsWith("agent:"));
    if (index === 0 && state.length === 0 && options.allowUnmanagedPr === true &&
        options.allowPartialLinks === true && expected.entryState === "agent:needs-human") continue;
    if (state.length !== 1 || !states.includes(state[0])) return fail("LIFECYCLE_CHANGED");
  }
  const ci = snapshot.ci;
  if (!ci || ci.repo !== expected.repo || ci.headSha !== expected.headSha || ci.prNumber !== expected.prNumber ||
      ci.event !== "pull_request" || ci.name !== "CI" || ci.status !== "completed" || ci.conclusion !== "success" ||
      ci.isNewest !== true || ci.requiredJobsSuccessful !== true ||
      !Number.isSafeInteger(ci.runId) || ci.runId < 1 || !Number.isSafeInteger(ci.runAttempt) || ci.runAttempt < 1) {
    return fail("NEWEST_REQUIRED_CI_NOT_GREEN");
  }
  if (snapshot.fileScopeValid !== true) return fail("FILE_SCOPE_CHANGED");
  if (snapshot.rulesetAudit?.repo !== expected.repo || snapshot.rulesetAudit?.requestRunId !== expected.requestRunId ||
      snapshot.rulesetAudit?.requestRunAttempt !== expected.requestRunAttempt || snapshot.rulesetAudit?.defaultBranch !== expected.defaultBranch) {
    return fail("RULESET_AUDIT_PROVENANCE_CHANGED");
  }
  return rulesetDecision(snapshot.rulesetAudit.ruleset, expected);
}

function strictReviewArtifact(review, headSha) {
  const decision = reviewDecision(review, headSha);
  if (!decision.ok) throw new Error(decision.reason);
  return Object.freeze(structuredClone(review));
}

// All operations are trusted functions, never commands or callback names taken
// from Issue text/model output. API failures and missing evidence stop the run.
async function guardedSequence({ snapshot, validate, operations, postvalidate = validate }) {
  if (typeof snapshot !== "function" || typeof validate !== "function" || typeof postvalidate !== "function" ||
      !Array.isArray(operations) || operations.length === 0 || operations.some((op) => typeof op !== "function")) {
    throw new Error("GUARDED_SEQUENCE_INVALID");
  }
  for (const operation of operations) {
    const current = await snapshot();
    const decision = validate(current);
    if (decision?.ok !== true) throw new Error(decision?.reason || "PREWRITE_GUARD_REJECTED");
    await operation(current);
  }
  const final = await snapshot();
  const decision = postvalidate(final);
  if (decision?.ok !== true) throw new Error(decision?.reason || "POSTWRITE_GUARD_REJECTED");
  return final;
}

module.exports = { rulesetDecision, reviewDecision, bindingDecision, guardedSequence, strictReviewArtifact };
