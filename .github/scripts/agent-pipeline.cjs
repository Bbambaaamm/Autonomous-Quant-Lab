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

function trustedCommentLines(comments) {
  return comments
    .filter((comment) => comment.user?.login === "github-actions[bot]")
    .flatMap((comment) => (comment.body || "").split("\n"));
}

function retiredLinkSet(comments, { owner, repo }) {
  const prefix = `<!-- agent-link-retired:v1 repo=${owner}/${repo} issue=`;
  const matches = trustedCommentLines(comments)
    .filter((line) => line.startsWith(prefix) && line.endsWith(" -->"))
    .map((line) => /^<!-- agent-link-retired:v1 repo=[^ ]+ issue=([1-9][0-9]*) pr=([1-9][0-9]*) -->$/.exec(line))
    .filter(Boolean)
    .map((match) => `${Number(match[1])}:${Number(match[2])}`);
  return new Set(matches);
}

function hasDurableLink(comments, { owner, repo, issueNumber, prNumber }) {
  return parseDurableLink(comments, { owner, repo, prNumber }) === issueNumber;
}

function parseDurableLink(comments, { owner, repo, prNumber }) {
  const prefix = `<!-- agent-link:v1 repo=${owner}/${repo} issue=`;
  const suffix = ` pr=${prNumber} -->`;
  const retired = retiredLinkSet(comments, { owner, repo });
  const issueNumbers = trustedCommentLines(comments)
    .filter((line) => line.startsWith(prefix) && line.endsWith(suffix))
    .map((line) => Number(line.slice(prefix.length, -suffix.length)))
    .filter((number) => Number.isSafeInteger(number) && number > 0)
    .filter((number) => !retired.has(`${number}:${prNumber}`));
  const unique = [...new Set(issueNumbers)];
  return unique.length === 1 ? unique[0] : null;
}

function durablePrLinkDecision(comments, { owner, repo, issueNumber }) {
  const prefix = `<!-- agent-link:v1 repo=${owner}/${repo} issue=${issueNumber} pr=`;
  const suffix = " -->";
  const retired = retiredLinkSet(comments, { owner, repo });
  const prNumbers = trustedCommentLines(comments)
    .filter((line) => line.startsWith(prefix) && line.endsWith(suffix))
    .map((line) => Number(line.slice(prefix.length, -suffix.length)))
    .filter((number) => Number.isSafeInteger(number) && number > 0)
    .filter((number) => !retired.has(`${issueNumber}:${number}`));
  const unique = [...new Set(prNumbers)];
  if (unique.length > 1) return { ok: false, reason: "AMBIGUOUS_DURABLE_LINK" };
  return { ok: true, prNumber: unique[0] ?? null };
}

function durableIssueLinkDecision(comments, { owner, repo, prNumber }) {
  const prefix = `<!-- agent-link:v1 repo=${owner}/${repo} issue=`;
  const suffix = ` pr=${prNumber} -->`;
  const retired = retiredLinkSet(comments, { owner, repo });
  const issueNumbers = trustedCommentLines(comments)
    .filter((line) => line.startsWith(prefix) && line.endsWith(suffix))
    .map((line) => Number(line.slice(prefix.length, -suffix.length)))
    .filter((number) => Number.isSafeInteger(number) && number > 0)
    .filter((number) => !retired.has(`${number}:${prNumber}`));
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

const FAILURE_CLASSES = ["lint-format", "typecheck", "unit-test", "api-test", "integration-postgres",
  "frontend-test-build", "security", "container-build", "production-smoke", "dependency-lock",
  "infra-transient", "multiple-failures", "unknown"];

function lifecycleAtAgentPr(prLabels, issueLabels) {
  const states = (labels) => labelNames(labels).filter((label) => STATES.includes(label));
  const pr = states(prLabels), issue = states(issueLabels);
  if (pr.includes("agent:needs-human") || issue.includes("agent:needs-human")) {
    return { ok: false, reason: "NEEDS_HUMAN_PRESENT" };
  }
  return pr.length === 1 && pr[0] === "agent:pr" && issue.length === 1 && issue[0] === "agent:pr"
    ? { ok: true } : { ok: false, reason: "NOT_EXACT_AGENT_PR" };
}

function fullLinkageDecision({ prBody, prComments, issueComments, owner, repo, issueNumber, prNumber }) {
  if (parseAgentIssue(prBody) !== issueNumber) return { ok: false, reason: "BODY_MARKER_MISMATCH" };
  const prSide = durableIssueLinkDecision(prComments, { owner, repo, prNumber });
  const issueSide = durablePrLinkDecision(issueComments, { owner, repo, issueNumber });
  if (!prSide.ok || !issueSide.ok) return { ok: false, reason: "AMBIGUOUS_DURABLE_LINK" };
  return prSide.issueNumber === issueNumber && issueSide.prNumber === prNumber
    ? { ok: true } : { ok: false, reason: "DURABLE_LINK_MISMATCH" };
}

function failedStepNames(job) {
  return (job.steps || []).filter((step) => ["failure", "timed_out"].includes(step.conclusion))
    .map((step) => step.name.toLowerCase());
}

function normalizedFailureClass(job, logExcerpt = "") {
  const name = job.name.toLowerCase();
  const steps = failedStepNames(job).join(" ");
  const diagnostic = String(logExcerpt || "").toLowerCase();
  const metadata = `${name} ${steps} ${diagnostic}`;
  const transientEvidence = /network timeout|audit endpoint returned an error|eai_again|econnreset|etimedout|socket hang up|temporary failure|connection reset|502 bad gateway|503 service unavailable|504 gateway timeout/;
  if (job.conclusion === "timed_out" || transientEvidence.test(diagnostic)) return "infra-transient";
  if (/dependenc|lock|npm ci|uv lock|uv sync/.test(metadata)) return "dependency-lock";
  if (/security|audit|bandit|pip-audit/.test(metadata)) return "security";
  if (/integration-postgres|postgres/.test(metadata)) return "integration-postgres";
  if (/container-build|docker/.test(metadata)) return "container-build";
  if (/production-smoke|smoke/.test(metadata)) return "production-smoke";
  if (/frontend|npm test|next build/.test(metadata)) return "frontend-test-build";
  if (/mypy|typecheck|type check/.test(metadata)) return "typecheck";
  if (/ruff|lint|format/.test(metadata)) return "lint-format";
  if (/api/.test(metadata)) return "api-test";
  if (/unit|pytest/.test(metadata)) return "unit-test";
  return "unknown";
}

function fixerInvocationDecision({ eventName, mode, prNumber, headSha, reviewBlock, ciRunId }) {
  // Explicitly bound reusable modes are authoritative.  A called workflow retains
  // the caller's event name, so eventName cannot identify workflow_call here.
  const validBinding = Number.isSafeInteger(Number(prNumber)) && Number(prNumber) > 0 &&
    /^[0-9a-f]{40}$/.test(headSha || "");
  if (mode === "review-block") return validBinding && String(reviewBlock || "").trim()
    ? { ok: true, kind: "review-block" } : { ok: false, reason: "INVALID_EXPLICIT_BINDING" };
  if (mode === "failed-ci") return validBinding && Number(ciRunId) > 0
    ? { ok: true, kind: "failed-ci" } : { ok: false, reason: "INVALID_EXPLICIT_BINDING" };
  if (eventName === "workflow_run") return { ok: mode === "ci-workflow-run", kind: "ci-workflow-run" };
  return { ok: false, reason: "INVALID_INVOCATION_MODE" };
}

function authoritativeCiIdentity(run, { prNumber, headSha, conclusion }) {
  return !!run && run.name === "CI" && run.event === "pull_request" && run.status === "completed" &&
    run.conclusion === conclusion && run.head_sha === headSha && run.pull_requests?.length === 1 &&
    run.pull_requests[0].number === Number(prNumber);
}

function redactDiagnostic(value, maxBytes = 8192) {
  return String(value || "")
    .replace(/(authorization|token|secret|password|api[_-]?key)\s*[:=]\s*\S+/gi, "$1=[REDACTED]")
    .replace(/\b(bearer|basic)\s+[A-Za-z0-9+/._=-]+/gi, "$1 [REDACTED]")
    .replace(/\b[A-Z][A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|KEY)=[^\s]+/g, "[REDACTED_ENV]")
    .replace(/gh[pousr]_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]+/g, "[REDACTED]")
    .replace(/AKIA[0-9A-Z]{16}/g, "[REDACTED]")
    .replace(/([?&](?:token|key|secret|signature)=)[^&\s]+/gi, "$1[REDACTED]")
    .slice(-maxBytes);
}

function extractFailureDiagnostic(value, maxBytes = 4096) {
  const input = String(value || "");
  const meaningful = input.split(/\r?\n/).filter((line) =>
    /error|fail|assert|exception|traceback|fatal|denied|mismatch|expected|actual|timed out/i.test(line));
  return redactDiagnostic(meaningful.length ? meaningful.slice(-80).join("\n") : input.slice(-maxBytes), maxBytes);
}

function classifyCiFailure({ jobs, runHeadSha, expectedHeadSha, sourceRunId, runAttempt, logExcerpt, config = {} }) {
  if (!/^[0-9a-f]{40}$/.test(runHeadSha || "") || runHeadSha !== expectedHeadSha) {
    return { disposition: "NO_WRITE", failureClass: "unknown", reason: "WORKFLOW_RUN_SHA_MISMATCH" };
  }
  const authoritative = new Set(config.requiredCiJobs || []);
  const failedAll = jobs.filter((job) => ["failure", "timed_out"].includes(job.conclusion));
  if (failedAll.some((job) => !authoritative.has(job.name))) {
    return { disposition: "NEEDS_HUMAN", failureClass: "unknown", reason: "NON_AUTHORITATIVE_FAILED_JOB" };
  }
  const failed = failedAll.filter((job) => authoritative.has(job.name));
  if (failed.length !== 1) return { disposition: "NEEDS_HUMAN", failureClass: failed.length > 1 ? "multiple-failures" : "unknown", reason: "FAILURE_SET_NOT_SINGLE" };
  const job = failed[0], failureClass = normalizedFailureClass(job, logExcerpt);
  const eligible = new Set(config.failureClassPolicy?.eligible || []);
  const denied = new Set(config.failureClassPolicy?.denied || []);
  if (!eligible.has(failureClass) && !denied.has(failureClass)) return { disposition: "NEEDS_HUMAN", failureClass: "unknown", reason: "UNCONFIGURED_FAILURE_CLASS" };
  const excerpt = extractFailureDiagnostic(logExcerpt, 4096);
  const protectedPatterns = config.protectedDiagnosticPatterns || [];
  if (protectedPatterns.some((pattern) => new RegExp(pattern, "i").test(excerpt))) {
    return { disposition: "NEEDS_HUMAN", failureClass, reason: "PROTECTED_TEST_OR_INVARIANT" };
  }
  if (eligible.has(failureClass) && (!Number.isSafeInteger(sourceRunId) || sourceRunId < 1 ||
      !Number.isSafeInteger(runAttempt) || runAttempt < 1 || !excerpt.trim())) {
    return { disposition: "NEEDS_HUMAN", failureClass, reason: "SAFE_DIAGNOSTIC_UNAVAILABLE" };
  }
  const crypto = require("crypto");
  const diagnosticObject = { failureClass, job: job.name, conclusion: job.conclusion,
    failedSteps: failedStepNames(job), jobId: job.id, sourceRunId, runAttempt,
    excerpt };
  const diagnosticWithoutChecksum = JSON.stringify(diagnosticObject);
  diagnosticObject.checksum = crypto.createHash("sha256").update(diagnosticWithoutChecksum).digest("hex");
  return {
    disposition: denied.has(failureClass) ? "NEEDS_HUMAN" : "FIX",
    failureClass,
    job: job.name,
    jobId: job.id,
    evidence: `${runHeadSha}:${sourceRunId}:${job.id}:${runAttempt}:${diagnosticObject.checksum}`,
    diagnostic: JSON.stringify(diagnosticObject),
    reason: denied.has(failureClass) ? "PROHIBITED_OR_UNKNOWN_FAILURE_CLASS" : undefined,
  };
}

function fixScopeDecision(paths, baselinePaths, config) {
  const baseline = new Set(baselinePaths || []);
  const additions = config.fixScope?.testAdditionPrefixes || [];
  const additionPatterns = (config.fixScope?.testAdditionPatterns || []).map((pattern) => new RegExp(pattern));
  if (!Array.isArray(paths) || !paths.length) return { ok: false, reason: "EMPTY_FIX_SCOPE" };
  return paths.every((path) => baseline.has(path) || additions.some((prefix) => path.startsWith(prefix)) ||
      additionPatterns.some((pattern) => pattern.test(path)))
    ? { ok: true, paths: [...paths].sort() } : { ok: false, reason: "OUTSIDE_AUTHORIZED_FIX_SCOPE" };
}

function parseJsonStringArray(value) {
  if (Array.isArray(value)) return value.filter((item) => typeof item === "string");
  if (typeof value !== "string" || !value.trim()) return [];
  const parsed = JSON.parse(value);
  if (!Array.isArray(parsed)) throw new Error("INVALID_JSON_STRING_ARRAY");
  return parsed.filter((item) => typeof item === "string");
}

function stableByteCompare(left, right) {
  return Buffer.compare(Buffer.from(left, "utf8"), Buffer.from(right, "utf8"));
}

function parseTrackedIndexEntries(rawTrackedIndex) {
  return String(rawTrackedIndex || "")
    .split("\0")
    .filter(Boolean)
    .map((entry) => {
      const match = /^([0-9]{6}) [0-9a-f]{40} ([0-3])\t(.+)$/.exec(entry);
      if (!match) throw new Error("INVALID_TRACKED_INDEX_ENTRY");
      return { mode: match[1], stage: Number(match[2]), path: match[3] };
    });
}

function diagnosticExcerpt(value) {
  if (!value) return "";
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      return typeof parsed?.excerpt === "string" ? parsed.excerpt : value;
    } catch {
      return value;
    }
  }
  if (typeof value === "object" && value !== null && typeof value.excerpt === "string") return value.excerpt;
  return String(value);
}

function parseDiagnosticMetadata(value) {
  if (typeof value === "object" && value !== null) return value;
  if (typeof value !== "string" || !value.trim()) return {};
  try {
    const parsed = JSON.parse(value);
    return typeof parsed === "object" && parsed !== null ? parsed : {};
  } catch {
    return {};
  }
}

function diagnosticWorkingDirectoryPrefixes(metadata = {}) {
  const backendJobs = new Set(["quality", "unit-research", "api"]);
  if (backendJobs.has(metadata.job)) return ["backend"];
  if (metadata.job === "frontend" || metadata.failureClass === "frontend-test-build") return ["frontend"];
  return [];
}

function diagnosticMentionsPath(text, filePath) {
  const escaped = filePath.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`(^|[^A-Za-z0-9_./-])${escaped}(?=$|[^A-Za-z0-9_./-])`).test(text);
}

function diagnosticPathAliases(canonicalPath, metadata = {}) {
  const aliases = [canonicalPath];
  for (const prefix of diagnosticWorkingDirectoryPrefixes(metadata)) {
    const root = `${prefix}/`;
    if (canonicalPath.startsWith(root)) aliases.push(canonicalPath.slice(root.length));
  }
  return [...new Set(aliases)];
}

function diagnosticMentionsEligiblePath(canonicalPath, diagnostic = "") {
  const metadata = parseDiagnosticMetadata(diagnostic);
  const excerpt = diagnosticExcerpt(diagnostic);
  if (!excerpt) return false;
  return diagnosticPathAliases(canonicalPath, metadata)
    .some((alias) => diagnosticMentionsPath(excerpt, alias));
}

function prioritizedEligiblePaths({ eligiblePaths = [], fixScopePaths = [], diagnostic = "" }) {
  const eligible = [...new Set(parseJsonStringArray(eligiblePaths))];
  const eligibleSet = new Set(eligible);
  const fixScope = new Set(parseJsonStringArray(fixScopePaths).filter((filePath) => eligibleSet.has(filePath)));
  const diagnosticPaths = new Set(eligible
    .filter((filePath) => !fixScope.has(filePath) && diagnosticMentionsEligiblePath(filePath, diagnostic)));
  const toSorted = (items) => [...items].sort(stableByteCompare);
  return [
    ...toSorted(fixScope),
    ...toSorted(diagnosticPaths),
    ...toSorted(eligible.filter((filePath) => !fixScope.has(filePath) && !diagnosticPaths.has(filePath))),
  ];
}

function prioritizedEligiblePathGroups({ eligiblePaths = [], fixScopePaths = [], diagnostic = "" }) {
  const ordered = prioritizedEligiblePaths({ eligiblePaths, fixScopePaths, diagnostic });
  const eligible = new Set(parseJsonStringArray(eligiblePaths));
  const fixScope = new Set(parseJsonStringArray(fixScopePaths).filter((filePath) => eligible.has(filePath)));
  const diagnosticSet = new Set(
    [...eligible].filter((filePath) => !fixScope.has(filePath) && diagnosticMentionsEligiblePath(filePath, diagnostic))
  );
  const generic = ordered.filter((filePath) => !fixScope.has(filePath) && !diagnosticSet.has(filePath));
  return {
    fixScope: ordered.filter((filePath) => fixScope.has(filePath)),
    diagnostic: ordered.filter((filePath) => diagnosticSet.has(filePath)),
    generic,
    ordered,
  };
}

function trackedEligibleRegularPaths({ trackedEntries = [], config }) {
  const allowedModes = new Set(["100644", "100755"]);
  return [...new Set((trackedEntries || [])
    .filter((entry) => entry?.stage === 0 && typeof entry.path === "string" && allowedModes.has(entry.mode))
    .map((entry) => entry.path)
    .filter((filePath) => validatePatchPaths([filePath], config)))]
    .sort(stableByteCompare);
}

function trackedPriorityMaterializationPlan({ trackedEntries = [], fixScopePaths = [], diagnostic = "", config }) {
  const policyPaths = [...new Set((trackedEntries || [])
    .filter((entry) => entry?.stage === 0 && typeof entry.path === "string")
    .map((entry) => entry.path)
    .filter((filePath) => validatePatchPaths([filePath], config)))];
  const regularPaths = trackedEligibleRegularPaths({ trackedEntries, config });
  const allGroups = prioritizedEligiblePathGroups({ eligiblePaths: policyPaths, fixScopePaths, diagnostic });
  const regularGroups = prioritizedEligiblePathGroups({ eligiblePaths: regularPaths, fixScopePaths, diagnostic });
  const requiredPriority = [...allGroups.fixScope, ...allGroups.diagnostic];
  const safePriority = new Set([...regularGroups.fixScope, ...regularGroups.diagnostic]);
  const rejected = requiredPriority.filter((filePath) => !safePriority.has(filePath));
  if (rejected.length) return { ok: false, reason: `PRIORITY_SOURCE_CONTEXT_UNSAFE_TRACKED_ENTRY:${rejected[0]}` };
  return {
    ok: true,
    priorityPaths: [...regularGroups.fixScope, ...regularGroups.diagnostic],
    eligibleRegularPaths: regularPaths,
  };
}

function buildBoundedSourceContext({ files, fixScopePaths = [], diagnostic = "", sourceBudgetBytes }) {
  if (!Number.isSafeInteger(sourceBudgetBytes) || sourceBudgetBytes < 256) throw new Error("INVALID_SOURCE_CONTEXT_BUDGET");
  const normalized = [...new Map((files || [])
    .filter((file) => typeof file?.path === "string" && typeof file?.content === "string")
    .map((file) => [file.path, { path: file.path, content: file.content }])).values()];
  const eligible = normalized.map((file) => file.path);
  const fixScope = new Set(parseJsonStringArray(fixScopePaths).filter((filePath) => eligible.includes(filePath)));
  const diagnosticPaths = new Set(eligible
    .filter((filePath) => !fixScope.has(filePath) && diagnosticMentionsEligiblePath(filePath, diagnostic)));
  const pathOrder = prioritizedEligiblePaths({
    eligiblePaths: eligible,
    fixScopePaths,
    diagnostic,
  });
  const rank = new Map(pathOrder.map((filePath, index) => [filePath, index]));
  const prioritized = normalized
    .map((file) => ({ ...file, rank: rank.get(file.path) ?? Number.MAX_SAFE_INTEGER }))
    .sort((left, right) => left.rank - right.rank);
  const prioritySet = new Set([...fixScope, ...diagnosticPaths]);
  const selected = [];
  let out = JSON.stringify({ format: "source-context-v1", files: selected });
  if (Buffer.byteLength(out) > sourceBudgetBytes) throw new Error("SOURCE_CONTEXT_TOO_LARGE");
  for (const file of prioritized) {
    const candidate = [...selected, { path: file.path, content: file.content }];
    const encoded = JSON.stringify({ format: "source-context-v1", files: candidate });
    if (Buffer.byteLength(encoded) <= sourceBudgetBytes) {
      selected.push({ path: file.path, content: file.content });
      out = encoded;
      continue;
    }
    if (prioritySet.has(file.path)) throw new Error("PRIORITY_SOURCE_CONTEXT_TOO_LARGE");
    break;
  }
  return { json: out, files: selected };
}

function classificationMarker(record, config) {
  const keys = config.classificationSchema || [];
  if (keys.some((key) => record[key] === undefined)) throw new Error("INCOMPLETE_CLASSIFICATION_RECORD");
  const encoded = Buffer.from(JSON.stringify(record)).toString("base64url");
  return `<!-- ${config.classificationMarker} evidence=${record.sha}:${record.ciRunId}:${record.ciRunAttempt} record=${encoded} -->`;
}

function validationCommands(failureClass) {
  const commands = {
    "lint-format": ["cd backend && uv run ruff check .", "cd backend && uv run ruff format --check ."],
    typecheck: ["cd backend && uv run mypy src/quantlab"],
    "unit-test": ["cd backend && uv run pytest -q tests/test_research.py tests/test_research_engine.py tests/test_phase6.py tests/test_alpaca_corporate_actions.py tests/test_xnys_calendar.py tests/test_paper_only_architecture.py tests/test_phase6_runtime.py tests/test_phase6_audit_fixes.py tests/test_phase6_experiment_audit.py tests/test_phase7.py tests/test_pre_pilot_review_remediation.py"],
    "api-test": ["cd backend && uv run pytest -q tests/test_vertical_slice.py tests/test_phase7_api.py tests/test_phase8_api.py tests/test_phase9_security.py"],
    "frontend-test-build": ["cd frontend && npm ci", "cd frontend && npm run lint", "cd frontend && npm run typecheck", "cd frontend && npm test", "cd frontend && npm run build"],
  };
  return commands[failureClass] ? [...commands[failureClass]] : null;
}

function trustedArtifactDecision({ patchBytes, actualChecksum, metadataChecksum, actualPaths, metadataPaths, config }) {
  if (patchBytes < 1 || patchBytes > config.maxPatchBytes || actualChecksum !== metadataChecksum) return { ok: false, reason: "ARTIFACT_MISMATCH" };
  if (JSON.stringify([...actualPaths].sort()) !== JSON.stringify([...metadataPaths].sort()) || !validatePatchPaths(actualPaths, config)) {
    return { ok: false, reason: "PATH_SET_MISMATCH" };
  }
  return { ok: true };
}

function fixAttemptDecision({ comments, commits = [], sourceSha, evidence, maxAttempts }) {
  const prefix = `<!-- agent-fix:v2 source=${sourceSha} evidence=${evidence} `;
  const exact = comments.filter((comment) => comment.user?.login === "github-actions[bot]")
    .flatMap((comment) => (comment.body || "").split("\n")).filter((line) => line.startsWith(prefix));
  if (exact.length) return { action: "NO_WRITE", reason: "EXACT_EVIDENCE_ALREADY_PROCESSED" };
  const commentAttempts = comments.filter((comment) => comment.user?.login === "github-actions[bot]")
    .flatMap((comment) => (comment.body || "").split("\n")).filter((line) => line.startsWith("<!-- agent-fix:v2 ")).length;
  const commitAttempts = commits.filter((commit) => commit.author?.login === "github-actions[bot]" &&
    /(^|\n)Agent-Fix-Attempt: [1-9][0-9]*($|\n)/.test(commit.commit?.message || "")).length;
  const attempts = Math.max(commentAttempts, commitAttempts);
  return attempts >= maxAttempts ? { action: "NEEDS_HUMAN", reason: "FIX_BUDGET_EXHAUSTED" } : { action: "FIX", attempt: attempts + 1 };
}

function validatePatchPaths(paths, config) {
  if (!Array.isArray(paths) || paths.length === 0 || paths.length > config.maxPatchFiles) return false;
  return paths.every((path) => typeof path === "string" && !path.startsWith("/") && !path.includes("..") &&
    !(config.protectedPaths || []).includes(path) &&
    !config.deniedPathPrefixes.some((prefix) => path.startsWith(prefix)) &&
    !config.deniedPathBasenames.includes(path.split("/").at(-1)) &&
    !config.deniedPathFragments.some((fragment) => path.toLowerCase().includes(fragment)));
}

function validatePatchModes(rawDiff) {
  const allowedModes = new Set(["100644", "100755"]);
  const lines = String(rawDiff || "").split("\n").filter(Boolean);
  if (!lines.length) return false;
  return lines.every((line) => {
    const match = /^:(\d{6}) (\d{6}) [0-9a-f]+ [0-9a-f]+ [A-Z][0-9]*\t/.exec(line);
    if (!match) return false;
    const [, oldMode, newMode] = match;
    if (oldMode === newMode) return allowedModes.has(oldMode);
    // New/deleted generated entries are narrow regular, non-executable files only.
    return (oldMode === "000000" && newMode === "100644") ||
      (oldMode === "100644" && newMode === "000000");
  });
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

function reviewerBlockPairPlan(prLabels, issueLabels) {
  const exactEscalationState = (labels) => {
    const states = labelNames(labels).filter((label) => STATES.includes(label));
    return states.length === 1 && (states[0] === "agent:pr" || states[0] === "agent:needs-human");
  };
  if (!exactEscalationState(prLabels) || !exactEscalationState(issueLabels)) {
    return { ok: false, reason: "INVALID_REVIEW_BLOCK_PAIR" };
  }
  return {
    ok: true,
    pr: escalationMutationPlan(prLabels),
    issue: escalationMutationPlan(issueLabels),
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
    // listJobsForWorkflowRun jobs have no head_sha; callers bind the containing run to headSha.
    if (job.head_sha !== undefined && job.head_sha !== headSha) continue;
    const old = latest.get(job.name);
    if (!old || job.run_attempt >= old.run_attempt) latest.set(job.name, job);
  }
  return requiredNames.every((name) => latest.get(name)?.conclusion === "success");
}

function newestAuthoritativeCiRun(runs, { workflowName, headSha, prNumber }) {
  return runs
    .filter((run) => run.name === workflowName && run.event === "pull_request" &&
      run.status === "completed" && run.head_sha === headSha &&
      run.pull_requests?.length === 1 && run.pull_requests[0].number === prNumber)
    .sort((left, right) => right.id - left.id)[0] ?? null;
}

function authoritativeCiRunCandidates(runs, binding) {
  const newest = newestAuthoritativeCiRun(runs, binding);
  return newest?.conclusion === "success" ? [newest] : [];
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
  FAILURE_CLASSES,
  labelNames,
  currentState,
  authoritativeCiRunCandidates,
  newestAuthoritativeCiRun,
  durablePrLinkDecision,
  durableIssueLinkDecision,
  escalationMutationPlan,
  reviewerBlockPairPlan,
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
  normalizedFailureClass,
  fixerInvocationDecision,
  authoritativeCiIdentity,
  redactDiagnostic,
  extractFailureDiagnostic,
  validationCommands,
  parseTrackedIndexEntries,
  trackedEligibleRegularPaths,
  trackedPriorityMaterializationPlan,
  diagnosticWorkingDirectoryPrefixes,
  diagnosticPathAliases,
  diagnosticMentionsEligiblePath,
  lifecycleAtAgentPr,
  fullLinkageDecision,
  trustedArtifactDecision,
  prioritizedEligiblePathGroups,
  prioritizedEligiblePaths,
  buildBoundedSourceContext,
  fixScopeDecision,
  classificationMarker,
  fixAttemptDecision,
  validatePatchPaths,
  validatePatchModes,
  stateMutationPlan,
  successfulRequiredJobs,
  transitionProgress,
  validateManualTransition,
  verificationDecision,
  verificationTriggerDecision,
};
