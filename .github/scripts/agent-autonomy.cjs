"use strict";

const crypto = require("crypto");

const AUTH_MARKER = "agent-merge-authorization:v2";
const VERIFIED_MARKER = "agent-verified:v2";
const MERGE_MARKER = "agent-auto-merge:v2";
const GATE_CONTEXT = "agent-verified-gate";
const AGENT_STATES = new Set([
  "agent:ready",
  "agent:running",
  "agent:pr",
  "agent:needs-human",
  "agent:verified",
]);

function labelNames(labels = []) {
  return labels.map((label) => (typeof label === "string" ? label : label?.name)).filter(Boolean);
}

function exactAgentState(labels, expected) {
  const states = labelNames(labels).filter((label) => AGENT_STATES.has(label));
  return states.length === 1 && states[0] === expected;
}

function implementationClassification(labels) {
  const types = labelNames(labels).filter((label) => label.startsWith("type:"));
  return types.length === 1 && types[0] === "type:implementation" ? "type:implementation" : null;
}

function canonicalIssueSpec({ title, body, labels }) {
  const classification = implementationClassification(labels);
  if (!classification) return null;
  return JSON.stringify({
    title: String(title || ""),
    body: String(body || ""),
    classification,
  });
}

function issueSpecHash(input) {
  const canonical = canonicalIssueSpec(input);
  return canonical ? crypto.createHash("sha256").update(canonical, "utf8").digest("hex") : null;
}

function authorizationMarker({ repo, issueNumber, specHash, actor, runId }) {
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo || "")) throw new Error("INVALID_REPOSITORY");
  if (!Number.isSafeInteger(Number(issueNumber)) || Number(issueNumber) < 1) throw new Error("INVALID_ISSUE");
  if (!/^[0-9a-f]{64}$/.test(specHash || "")) throw new Error("INVALID_SPEC_HASH");
  if (!/^[A-Za-z0-9-]{1,39}$/.test(actor || "")) throw new Error("INVALID_ACTOR");
  if (!Number.isSafeInteger(Number(runId)) || Number(runId) < 1) throw new Error("INVALID_RUN_ID");
  return `<!-- ${AUTH_MARKER} repo=${repo} issue=${Number(issueNumber)} spec=${specHash} actor=${actor} run=${Number(runId)} -->`;
}

function authorizationPrefix({ repo, issueNumber }) {
  return `<!-- ${AUTH_MARKER} repo=${repo} issue=${Number(issueNumber)} `;
}

function parseAuthorization(comments = [], { repo, issueNumber }) {
  const prefix = authorizationPrefix({ repo, issueNumber });
  const matches = comments
    .filter((comment) => comment.user?.login === "github-actions[bot]")
    .flatMap((comment) => String(comment.body || "").split("\n").map((line) => ({ line, comment })))
    .filter(({ line }) => line.startsWith(prefix) && line.endsWith(" -->"));
  if (matches.length === 0) return { ok: false, reason: "AUTHORIZATION_MISSING" };
  if (matches.length !== 1) return { ok: false, reason: "AUTHORIZATION_AMBIGUOUS" };
  const escapedRepo = repo.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`^<!-- ${AUTH_MARKER} repo=${escapedRepo} issue=${Number(issueNumber)} spec=([0-9a-f]{64}) actor=([A-Za-z0-9-]{1,39}) run=([1-9][0-9]*) -->$`).exec(matches[0].line);
  if (!match) return { ok: false, reason: "AUTHORIZATION_MALFORMED" };
  return {
    ok: true,
    specHash: match[1],
    actor: match[2],
    runId: Number(match[3]),
    commentId: matches[0].comment.id,
    marker: matches[0].line,
  };
}

function authorizationDecision({ comments, repo, issueNumber, title, body, labels, state = "open" }) {
  if (state !== "open") return { ok: false, reason: "ISSUE_NOT_OPEN" };
  const specHash = issueSpecHash({ title, body, labels });
  if (!specHash) return { ok: false, reason: "ISSUE_NOT_IMPLEMENTATION" };
  const parsed = parseAuthorization(comments, { repo, issueNumber });
  if (!parsed.ok) return parsed;
  if (parsed.specHash !== specHash) return { ok: false, reason: "AUTHORIZATION_STALE", specHash };
  return { ok: true, specHash, actor: parsed.actor, runId: parsed.runId, commentId: parsed.commentId };
}

function verificationMarker({ repo, issueNumber, prNumber, headSha, specHash, ciRunId }) {
  if (!/^[0-9a-f]{40}$/.test(headSha || "")) throw new Error("INVALID_HEAD_SHA");
  if (!/^[0-9a-f]{64}$/.test(specHash || "")) throw new Error("INVALID_SPEC_HASH");
  if (!Number.isSafeInteger(Number(ciRunId)) || Number(ciRunId) < 1) throw new Error("INVALID_CI_RUN_ID");
  return `<!-- ${VERIFIED_MARKER} repo=${repo} issue=${Number(issueNumber)} pr=${Number(prNumber)} sha=${headSha} spec=${specHash} ci=${Number(ciRunId)} -->`;
}

function exactVerificationEvidence(comments = [], { repo, issueNumber, prNumber, headSha, specHash }) {
  const prefix = `<!-- ${VERIFIED_MARKER} repo=${repo} issue=${Number(issueNumber)} pr=${Number(prNumber)} sha=${headSha} spec=${specHash} ci=`;
  const matches = comments
    .filter((comment) => comment.user?.login === "github-actions[bot]")
    .flatMap((comment) => String(comment.body || "").split("\n"))
    .filter((line) => line.startsWith(prefix) && /^<!-- agent-verified:v2 .* ci=[1-9][0-9]* -->$/.test(line));
  return matches.length === 1;
}

function mergeMarker({ repo, issueNumber, prNumber, headSha, specHash, mergeSha }) {
  if (!/^[0-9a-f]{40}$/.test(headSha || "") || !/^[0-9a-f]{40}$/.test(mergeSha || "")) throw new Error("INVALID_MERGE_SHA");
  return `<!-- ${MERGE_MARKER} repo=${repo} issue=${Number(issueNumber)} pr=${Number(prNumber)} sha=${headSha} spec=${specHash} merge=${mergeSha} -->`;
}

function lifecyclePairDecision(prLabels, issueLabels, expectedState) {
  const all = [...labelNames(prLabels), ...labelNames(issueLabels)];
  if (all.includes("agent:needs-human")) return { ok: false, reason: "NEEDS_HUMAN_PRESENT" };
  return exactAgentState(prLabels, expectedState) && exactAgentState(issueLabels, expectedState)
    ? { ok: true }
    : { ok: false, reason: `NOT_EXACT_${expectedState.replace(":", "_").toUpperCase()}` };
}

function verificationLifecyclePlan(prLabels, issueLabels) {
  const state = (labels) => labelNames(labels).filter((label) => AGENT_STATES.has(label));
  const pr = state(prLabels), issue = state(issueLabels);
  if ([...pr, ...issue].includes("agent:needs-human")) return { ok: false, reason: "NEEDS_HUMAN_PRESENT" };
  if (pr.length !== 1 || issue.length !== 1) return { ok: false, reason: "AMBIGUOUS_AGENT_STATE" };
  const allowed = new Set(["agent:pr", "agent:verified"]);
  if (!allowed.has(pr[0]) || !allowed.has(issue[0])) return { ok: false, reason: "INVALID_VERIFICATION_LIFECYCLE" };
  return { ok: true, prState: pr[0], issueState: issue[0] };
}

function verificationDecision(input) {
  const checks = [
    [input.authorizationCurrent, "AUTHORIZATION_NOT_CURRENT"],
    [input.issueOpen, "ISSUE_NOT_OPEN"],
    [input.issueIsImplementation, "ISSUE_NOT_IMPLEMENTATION"],
    [input.linkageValid, "LINKAGE_INVALID"],
    [input.open, "PR_NOT_OPEN"],
    [input.correctBase, "PR_BASE_INVALID"],
    [!input.draft, "PR_IS_DRAFT"],
    [input.exactHead, "HEAD_SHA_MISMATCH"],
    [input.lifecycleValid, "LIFECYCLE_INVALID"],
    [!input.needsHuman, "NEEDS_HUMAN_PRESENT"],
    [input.currentMain, "PR_BEHIND_MAIN"],
    [input.requiredCiSuccessful, "REQUIRED_CI_MISSING"],
    [input.independentReviewPass, "INDEPENDENT_REVIEW_PASS_MISSING"],
  ];
  for (const [ok, reason] of checks) if (!ok) return { ok: false, reason };
  return { ok: true };
}

function gateDecision(input) {
  const base = verificationDecision({ ...input, lifecycleValid: input.verifiedLifecycle });
  if (!base.ok) return base;
  if (!input.verificationEvidence) return { ok: false, reason: "VERIFICATION_EVIDENCE_MISSING" };
  return { ok: true };
}

function mergeDecision(input) {
  const gate = gateDecision(input);
  if (!gate.ok) return gate;
  if (!input.gateSuccess) return { ok: false, reason: "VERIFIED_GATE_NOT_SUCCESSFUL" };
  if (!input.expectedHeadUnchanged) return { ok: false, reason: "MERGE_HEAD_CHANGED" };
  return { ok: true };
}

const BUILDER_DENIED_PREFIXES = [
  ".github/",
  "backend/alembic/",
  "backend/src/quantlab/api/auth",
  "deploy/",
  "infra/",
];
const BUILDER_DENIED_BASENAMES = new Set([
  "AGENTS.md",
  "ROADMAP.md",
  "package.json",
  "package-lock.json",
  "pyproject.toml",
  "uv.lock",
]);
const BUILDER_DENIED_SEGMENTS = new Set([
  "auth",
  "authentication",
  "authorization",
  "broker",
  "credential",
  "credentials",
  "execution",
  "live",
  "risk",
  "secret",
  "secrets",
  "security",
  "trading",
]);

function safeRelativePath(path) {
  return typeof path === "string" && path.length > 0 && !path.startsWith("/") && !path.includes("\\") &&
    path.split("/").every((part) => part && part !== "." && part !== "..");
}

function builderPathAllowed(path) {
  if (!safeRelativePath(path)) return false;
  if (BUILDER_DENIED_PREFIXES.some((prefix) => path.startsWith(prefix))) return false;
  const basename = path.split("/").at(-1);
  if (BUILDER_DENIED_BASENAMES.has(basename)) return false;
  const tokens = path.toLowerCase().split(/[\/_\-.]+/).filter(Boolean);
  if (tokens.some((token) => BUILDER_DENIED_SEGMENTS.has(token))) return false;
  return true;
}

function validateBuilderPaths(paths, maxFiles = 20) {
  return Array.isArray(paths) && paths.length > 0 && paths.length <= maxFiles &&
    new Set(paths).size === paths.length && paths.every(builderPathAllowed);
}

function validateBuilderModes(rawDiff) {
  const lines = String(rawDiff || "").split("\n").filter(Boolean);
  if (!lines.length) return false;
  return lines.every((line) => {
    const match = /^:(\d{6}) (\d{6}) [0-9a-f]+ [0-9a-f]+ [A-Z][0-9]*\t/.exec(line);
    if (!match) return false;
    const [, oldMode, newMode] = match;
    if (oldMode === newMode) return oldMode === "100644" || oldMode === "100755";
    return (oldMode === "000000" && newMode === "100644") ||
      (oldMode === "100644" && newMode === "000000");
  });
}

function transientCiRetryDecision({ failureClass, runAttempt, maxRetries }) {
  if (failureClass !== "infra-transient") return { action: "NONE" };
  if (!Number.isSafeInteger(Number(runAttempt)) || !Number.isSafeInteger(Number(maxRetries)) || Number(maxRetries) < 1) {
    return { action: "NEEDS_HUMAN", reason: "INVALID_TRANSIENT_RETRY_BUDGET" };
  }
  return Number(runAttempt) < Number(maxRetries)
    ? { action: "RETRY", nextAttempt: Number(runAttempt) + 1 }
    : { action: "NEEDS_HUMAN", reason: "TRANSIENT_RETRY_BUDGET_EXHAUSTED" };
}

module.exports = {
  AUTH_MARKER,
  VERIFIED_MARKER,
  MERGE_MARKER,
  GATE_CONTEXT,
  labelNames,
  exactAgentState,
  implementationClassification,
  canonicalIssueSpec,
  issueSpecHash,
  authorizationMarker,
  authorizationPrefix,
  parseAuthorization,
  authorizationDecision,
  verificationMarker,
  exactVerificationEvidence,
  mergeMarker,
  lifecyclePairDecision,
  verificationLifecyclePlan,
  verificationDecision,
  gateDecision,
  mergeDecision,
  safeRelativePath,
  builderPathAllowed,
  validateBuilderPaths,
  validateBuilderModes,
  transientCiRetryDecision,
};
