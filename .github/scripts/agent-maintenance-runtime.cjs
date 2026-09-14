"use strict";

// Executed ONLY from the workflow's pinned trusted default-branch checkout.
// No candidate code, command, callback or credential is accepted from artifacts.
const fs = require("node:fs");
const crypto = require("node:crypto");
const p = require("./agent-pipeline.cjs");
const a = require("./agent-autonomy.cjs");
const g = require("./agent-maintenance-guards.cjs");
const c = require("../agent-pipeline.json");
const {collectSnapshot} = require("./agent-maintenance-controller.cjs");
const REQUEST_PATH = ".github/workflows/agent-control-plane-remediation-request.yml";
const FOLLOWER_PATH = ".github/workflows/agent-control-plane-remediation.yml";
const SHA = /^[0-9a-f]{40}$/;
const positive = x => Number.isSafeInteger(x) && x > 0;
const ensure = (ok, reason) => { if (!ok) throw new Error(reason); };
const canonical = x => JSON.stringify(normalize(x));
function normalize(x) {
  if (Array.isArray(x)) return x.map(normalize);
  if (x && typeof x === "object") return Object.fromEntries(Object.keys(x).sort().map(k => [k, normalize(x[k])]));
  return x;
}
const digest = x => crypto.createHash("sha256").update(canonical(x)).digest("hex");
// Reviewed Protect main baseline: 8 CI contexts + gate, NOT the 9 CI job list.
const REQUIRED_CHECKS = ["api", "container-build", "frontend", "integration-postgres", "production-smoke", "quality", "security", "unit-research", "agent-verified-gate"].map(context => ({context, integration_id: 15368}));

function protection(r) {
  const result = g.rulesetDecision(r, {defaultBranch: "main", requiredChecks: REQUIRED_CHECKS});
  ensure(result.ok, result.reason);
  const scans = r.rules.filter(x => x.type === "code_scanning");
  ensure(scans.length === 1 && scans[0].parameters?.code_scanning_tools?.some(x =>
    x.tool === "CodeQL" && ["high_or_higher", "medium_or_higher", "all"].includes(x.security_alerts_threshold) &&
    ["errors", "errors_and_warnings", "all"].includes(x.alerts_threshold)), "CODEQL_PROTECTION_MISSING");
  // Preserve ALL additional protections, not merely the minimum checks above.
  const rules = r.rules.map(x => normalize(x)).sort((x, y) => (canonical(x) < canonical(y) ? -1 : canonical(x) > canonical(y) ? 1 : 0));
  return {id: r.id, name: r.name, target: r.target, enforcement: r.enforcement,
    conditions: r.conditions, bypass_actors: r.bypass_actors, rules};
}

function originValid(run, expected) {
  return run?.id === expected.requestRunId && run.run_attempt === expected.requestRunAttempt &&
    run.actor?.login === expected.requester && run.triggering_actor?.login === expected.requester && run.event === "workflow_dispatch" &&
    run.status === "completed" && run.conclusion === "success" && run.path === REQUEST_PATH &&
    run.head_repository?.full_name === expected.repo && run.repository?.full_name === expected.repo &&
    run.head_branch === expected.defaultBranch && run.head_sha === expected.baseSha;
}

function expectedFrom(request, context) {
  const authorization = request.authorization;
  const expected = {repo: `${context.repo.owner}/${context.repo.repo}`, defaultBranch: context.payload.repository.default_branch,
    issueNumber: request.issueNumber, prNumber: request.prNumber, headSha: request.headSha,
    baseSha: context.sha, specHash: request.specHash, requester: request.actor, authorization,
    requestRunId: request.requestRunId, requestRunAttempt: request.requestRunAttempt, entryState: request.entryState,
    mode: request.mode, canaryOnly: request.canary_only,
    requiredJobNames: c.requiredCiJobs, requiredChecks: REQUIRED_CHECKS};
  ensure(request.repo === expected.repo && expected.defaultBranch === "main" &&
    positive(expected.issueNumber) && positive(expected.prNumber) && positive(expected.requestRunId) &&
    positive(expected.requestRunAttempt) && SHA.test(expected.headSha || "") && SHA.test(expected.baseSha || "") &&
    /^[0-9a-f]{64}$/.test(expected.specHash || "") && /^[A-Za-z0-9-]{1,39}$/.test(expected.requester || "") &&
    authorization && positive(authorization.commentId) && positive(authorization.runId) &&
    /^[A-Za-z0-9-]{1,39}$/.test(authorization.actor || "") && authorization.specHash === expected.specHash &&
    ["agent:pr", "agent:needs-human"].includes(expected.entryState) &&
    ((expected.mode === "production" && expected.canaryOnly === false) ||
      (expected.mode === "canary_only" && expected.canaryOnly === true)) &&
    typeof request.reason === "string" && request.reason.trim().length > 0 && request.reason.length <= 1024,
  "REQUEST_BINDING_INVALID");
  ensure(context.eventName === "workflow_run" && originValid(context.payload.workflow_run, expected), "REQUEST_ORIGIN_INVALID");
  return expected;
}

function producer(context) {
  ensure(positive(Number(context.runId)) && positive(Number(context.runAttempt)) && SHA.test(context.sha || ""), "PRODUCER_IDENTITY_INVALID");
  return {repository: `${context.repo.owner}/${context.repo.repo}`, sourceSha: context.sha,
    runId: Number(context.runId), runAttempt: Number(context.runAttempt), workflow: FOLLOWER_PATH};
}
function validateBundle(bundle, context) {
  ensure(bundle?.version === 2 && canonical(bundle.producer) === canonical(producer(context)), "BUNDLE_PROVENANCE_INVALID");
  const expected = expectedFrom(bundle.request, context);
  ensure(canonical(expected) === canonical(bundle.expected), "BUNDLE_EXPECTATION_INVALID");
  ensure(bundle.evidence?.headSha === expected.headSha && bundle.evidence.baseSha === expected.baseSha &&
    bundle.evidence.repository === expected.repo && positive(bundle.evidence.ci?.runId) && positive(bundle.evidence.ci.runAttempt) &&
    canonical(bundle.evidence.authorization) === canonical(expected.authorization) &&
    ["pr", "issue"].every(side => typeof bundle.evidence.recoveryProgress?.[side] === "boolean"), "BUNDLE_EVIDENCE_INVALID");
  ensure(bundle.digest === digest({producer: bundle.producer, expected, evidence: bundle.evidence}), "BUNDLE_DIGEST_INVALID");
  return expected;
}

// Credential-bearing audit adapter: fixed public-GitHub GETs only, no redirects,
// no candidate checkout, no exception containing headers or response bodies.
function rulesetReader(repo, token, fetcher = fetch) {
  ensure(/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo) && typeof token === "string" && token.length > 0, "RULESET_AUDIT_ACCESS_MISSING");
  const get = async suffix => {
    const response = await fetcher(`https://api.github.com/repos/${repo}/${suffix}`, {
      method: "GET", redirect: "error", signal: AbortSignal.timeout(15000),
      headers: {authorization: `Bearer ${token}`, accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
    });
    ensure(response.ok, "RULESET_AUDIT_HTTP_FAILED");
    const text = await response.text();
    ensure(Buffer.byteLength(text) <= 1048576, "RULESET_AUDIT_OVERSIZE");
    return JSON.parse(text);
  };
  return async () => {
    const rows = [];
    for (let page = 1; page <= 100; page++) {
      const list = await get(`rulesets?per_page=100&page=${page}`);
      ensure(Array.isArray(list), "RULESET_LIST_INVALID"); rows.push(...list);
      if (list.length < 100) break;
      ensure(page < 100, "RULESET_LIST_INCOMPLETE");
    }
    const matches = rows.filter(x => x.name === "Protect main" && x.target === "branch");
    ensure(matches.length === 1 && positive(matches[0].id), "RULESET_AMBIGUOUS");
    const r = await get(`rulesets/${matches[0].id}`);
    ensure(r.id === matches[0].id, "RULESET_ID_CHANGED"); protection(r);
    return r;
  };
}

function runtime({github, context, expected, readRuleset, bundle = null, review = null, mergePull = null, readOnlyCanary = false}) {
  ensure(typeof readRuleset === "function", "FRESH_RULESET_READER_REQUIRED");
  ensure(readOnlyCanary
    ? expected.mode === "canary_only" && expected.canaryOnly === true
    : expected.mode === "production" && expected.canaryOnly === false,
  readOnlyCanary ? "CANARY_MODE_REQUIRED" : "PRODUCTION_MODE_REQUIRED");
  const repoArgs = context.repo;
  const requestStates = expected.entryState === "agent:needs-human" ? ["agent:needs-human", "agent:pr"] : ["agent:pr", "agent:verified"];
  const reviewMarker = `<!-- agent-control-plane-review:v2 sha=${expected.headSha} issue=${expected.issueNumber} pr=${expected.prNumber} spec=${expected.specHash} source=${expected.baseSha} bundle=${bundle?.digest || "none"} -->`;
  const targetUrl = `https://github.com/${expected.repo}/actions/runs/${context.runId}/attempts/${context.runAttempt}`;
  // Recovery is one monotonic operation, including its linkage phase. Seed it
  // from the trusted prepare evidence so a fresh runtime cannot forget that an
  // object was already restored before this process began.
  const recovered = new Set(["pr", "issue"].filter(side => bundle?.evidence?.recoveryProgress?.[side] === true));
  const observeRecovery = s => {
    for (const completed of recovered) ensure(a.exactAgentState(s[completed].labels, "agent:pr") ||
      a.exactAgentState(s[completed].labels, "agent:verified"), "RECOVERY_PROGRESS_REVOKED");
    for (const side of ["pr", "issue"]) if (a.exactAgentState(s[side].labels, "agent:pr") ||
      a.exactAgentState(s[side].labels, "agent:verified")) recovered.add(side);
  };
  const snapshot = async (states = requestStates, partial = false) => {
    const s = await collectSnapshot({github, context, expected, pipeline: p, autonomy: a,
      rulesetAudit: {repo: expected.repo, requestRunId: expected.requestRunId, requestRunAttempt: expected.requestRunAttempt,
        defaultBranch: expected.defaultBranch, ruleset: null}});
    const ruleset = await readRuleset(); // Fresh after metadata collection on EVERY snapshot.
    s.rulesetAudit.ruleset = ruleset;
    const origin = (await github.rest.actions.getWorkflowRun({...repoArgs, run_id: expected.requestRunId})).data;
    ensure(originValid(origin, expected), "REQUEST_ORIGIN_CHANGED");
    // An unmanaged PR is supported only during explicit pre-link recovery; the
    // ordinary gate always requires a unique real state on both objects.
    const unmanaged = partial && expected.entryState === "agent:needs-human" &&
      Array.isArray(s.pr.labels) && !a.labelNames(s.pr.labels).some(x => x.startsWith("agent:"));
    const result = g.bindingDecision(s, expected, {states, allowPartialLinks: partial, allowUnmanagedPr: unmanaged});
    ensure(result.ok, result.reason);
    const ci = (await github.rest.actions.getWorkflowRun({...repoArgs, run_id: s.ci.runId})).data;
    ensure(ci.id === s.ci.runId && ci.run_attempt === s.ci.runAttempt && ci.status === "completed" &&
      ci.conclusion === "success" && ci.path === ".github/workflows/ci.yml" && ci.head_sha === expected.headSha &&
      ci.head_repository?.full_name === expected.repo && ci.repository?.full_name === expected.repo &&
      ci.pull_requests?.length === 1 && ci.pull_requests[0].number === expected.prNumber, "CI_PROVENANCE_CHANGED");
    s.protection = protection(ruleset);
    s.filesHash = digest(s.files.map(x => ({filename: x.filename, status: x.status, previous_filename: x.previous_filename || null})).sort((x, y) => (x.filename < y.filename ? -1 : x.filename > y.filename ? 1 : 0)));
    if (bundle) {
      ensure(digest(s.protection) === bundle.evidence.protectionHash, "PROTECTION_CHANGED_SINCE_REVIEW");
      ensure(s.filesHash === bundle.evidence.filesHash && s.ci.runId === bundle.evidence.ci.runId &&
        s.ci.runAttempt === bundle.evidence.ci.runAttempt, "EVIDENCE_CHANGED_SINCE_REVIEW");
    }
    const decisions = s.prComments.filter(x => x.user?.login === "github-actions[bot]" && String(x.body || "").split("\n").includes(reviewMarker));
    ensure(decisions.length <= 1, "REVIEW_EVIDENCE_AMBIGUOUS"); s.reviewRecorded = decisions.length === 1;
    if (partial) observeRecovery(s);
    return s;
  };
  const requireReview = () => {
    ensure(bundle !== null, "TRUSTED_BUNDLE_REQUIRED"); validateBundle(bundle, context);
    g.strictReviewArtifact(review, expected.headSha);
  };
  const checkedWrite = async (states, partial, operation) => {
    requireReview(); const s = await snapshot(states, partial); await operation(s);
  };
  const setState = async (number, labels, next) => {
    if (a.exactAgentState(labels, next)) return;
    const foreign = a.labelNames(labels).filter(x => !x.startsWith("agent:"));
    await github.rest.issues.setLabels({...repoArgs, issue_number: number, labels: [...foreign, next]});
  };
  const linkMarker = `<!-- agent-link:v1 repo=${expected.repo} issue=${expected.issueNumber} pr=${expected.prNumber} -->`;
  const link = async () => {
    for (const side of ["issue", "pr"]) await checkedWrite(requestStates, true, async s => {
      const missing = side === "issue" ? p.durablePrLinkDecision(s.issueComments, {...repoArgs, issueNumber: expected.issueNumber}).prNumber === null
        : p.durableIssueLinkDecision(s.prComments, {...repoArgs, prNumber: expected.prNumber}).issueNumber === null;
      if (missing) await github.rest.issues.createComment({...repoArgs, issue_number: side === "issue" ? expected.issueNumber : expected.prNumber,
        body: `Authorized maintenance linkage.\n\n${linkMarker}`});
    });
    await snapshot(requestStates, true);
  };
  const recover = async () => {
    // Keep needs-human on the Issue until the previously unmanaged PR has a
    // resumable state. A fresh request can then recover a lost first response.
    for (const side of ["pr", "issue"]) await checkedWrite(requestStates, true, async s => {
      ensure(s.fullLinkageValid, "LINKAGE_INCOMPLETE");
      if (!a.exactAgentState(s[side].labels, "agent:verified")) await setState(side === "issue" ? expected.issueNumber : expected.prNumber, s[side].labels, "agent:pr");
      recovered.add(side);
    });
    await snapshot(expected.entryState === "agent:needs-human" ? ["agent:pr"] : ["agent:pr", "agent:verified"]);
  };
  const gate = async () => {
    const states = ["agent:pr", "agent:verified"];
    for (const side of ["issue", "pr"]) await checkedWrite(states, false, s => setState(side === "issue" ? expected.issueNumber : expected.prNumber, s[side].labels, "agent:verified"));
    await checkedWrite(["agent:verified"], false, async s => {
      if (!s.reviewRecorded) await github.rest.issues.createComment({...repoArgs, issue_number: expected.prNumber,
        body: `Independent maintenance review: PASS for ${expected.headSha}. Source ${expected.baseSha}; CI ${s.ci.runId}/${s.ci.runAttempt}.\n\n${reviewMarker}`});
    });
    await checkedWrite(["agent:verified"], false, async s => {
      ensure(s.reviewRecorded, "REVIEW_EVIDENCE_MISSING");
      await github.rest.repos.createCommitStatus({...repoArgs, sha: expected.headSha, state: "success", context: a.GATE_CONTEXT,
        target_url: targetUrl, description: "Exact independently reviewed maintenance"});
    });
    await snapshot(["agent:verified"]);
  };
  const merge = async () => {
    ensure(typeof mergePull === "function", "ISOLATED_MERGE_CLIENT_REQUIRED");
    requireReview();
    const statuses = await github.paginate(github.rest.repos.listCommitStatusesForRef, {...repoArgs, ref: expected.headSha, per_page: 100});
    const status = statuses.find(x => x.context === a.GATE_CONTEXT);
    ensure(positive(status?.id) && status?.state === "success" && status.creator?.login === "github-actions[bot]" && status.target_url === targetUrl, "TRUSTED_GATE_MISSING");
    // Repeat the full snapshot immediately before the ONLY merge mutation.
    const s = await snapshot(["agent:verified"]); ensure(s.reviewRecorded, "REVIEW_EVIDENCE_MISSING");
    const latest = (await github.paginate(github.rest.repos.listCommitStatusesForRef, {...repoArgs, ref: expected.headSha, per_page: 100})).find(x => x.context === a.GATE_CONTEXT);
    ensure(latest?.id === status.id && latest?.state === "success", "GATE_CHANGED");
    const result = await mergePull({pull_number: expected.prNumber, sha: expected.headSha, merge_method: "merge"});
    ensure(result?.merged === true && SHA.test(result.sha || ""), "MERGE_REJECTED");
    const post = (await github.rest.pulls.get({...repoArgs, pull_number: expected.prNumber})).data;
    ensure(post.state === "closed" && post.merged === true && post.head.sha === expected.headSha && post.merge_commit_sha === result.sha, "MERGE_POSTCONDITION_FAILED");
    return result.sha;
  };
  return {snapshot, link, recover, gate, merge};
}

async function executePhase(r, phase, core = {setOutput() {}}) {
  if (phase === "recover") { await r.link(); await r.recover(); }
  if (phase === "gate") await r.gate();
  if (phase === "merge") core.setOutput("merge_sha", await r.merge());
}

function readOnlyGithub(github) {
  const deny = async () => { throw new Error("READ_ONLY_API_WRITE_DENIED"); };
  const pulls = {get: github.rest.pulls.get, listFiles: github.rest.pulls.listFiles};
  const issues = {get: github.rest.issues.get, listComments: github.rest.issues.listComments,
    createComment: deny, setLabels: deny};
  const repos = {getCollaboratorPermissionLevel: github.rest.repos.getCollaboratorPermissionLevel,
    getBranch: github.rest.repos.getBranch, compareCommits: github.rest.repos.compareCommits,
    listCommitStatusesForRef: github.rest.repos.listCommitStatusesForRef, createCommitStatus: deny};
  const actions = {listWorkflowRunsForRepo: github.rest.actions.listWorkflowRunsForRepo,
    listJobsForWorkflowRun: github.rest.actions.listJobsForWorkflowRun, getWorkflowRun: github.rest.actions.getWorkflowRun,
    dispatchWorkflow: deny};
  const allowed = new Set([pulls.listFiles, issues.listComments, repos.listCommitStatusesForRef,
    actions.listWorkflowRunsForRepo, actions.listJobsForWorkflowRun]);
  return {rest: {pulls, issues, repos, actions}, paginate(method, args) {
    ensure(allowed.has(method), "READ_ONLY_API_METHOD_DENIED"); return github.paginate(method, args);
  }};
}

async function run({phase, github, context, core, auditToken, mergeToken, directory, fetcher = fetch}) {
  ensure(["route", "prepare", "canary", "recover", "gate", "merge"].includes(phase), "UNKNOWN_MAINTENANCE_PHASE");
  const request = JSON.parse(fs.readFileSync(`${directory}/request.json`, "utf8"));
  const expected = expectedFrom(request, context);
  if (phase === "route") {
    core.setOutput("production_route", expected.mode === "production" ? "true" : "false");
    core.setOutput("canary_route", expected.mode === "canary_only" ? "true" : "false");
    return {mode: expected.mode, requestRunId: expected.requestRunId, requestRunAttempt: expected.requestRunAttempt};
  }
  if (phase === "canary") ensure(expected.mode === "canary_only" && expected.canaryOnly === true, "CANARY_MODE_REQUIRED");
  else ensure(expected.mode === "production" && expected.canaryOnly === false, "PRODUCTION_MODE_REQUIRED");
  const readRuleset = rulesetReader(expected.repo, auditToken, fetcher);
  if (phase === "prepare") {
    const s = await runtime({github, context, expected, readRuleset}).snapshot(undefined, true);
    const evidence = {repository: expected.repo, headSha: expected.headSha, baseSha: expected.baseSha,
      ci: s.ci, filesHash: s.filesHash, protectionHash: digest(s.protection), protection: s.protection,
      files: s.files.map(x => ({filename: x.filename, status: x.status, previous_filename: x.previous_filename || null})),
      authorization: {commentId: s.authorization.commentId, specHash: s.authorization.specHash,
        actor: s.authorization.actor, runId: s.authorization.runId},
      recoveryProgress: Object.fromEntries(["pr", "issue"].map(side => [side,
        a.exactAgentState(s[side].labels, "agent:pr") || a.exactAgentState(s[side].labels, "agent:verified")])),
      title: p.redactDiagnostic(s.issue.title, 256), body: p.redactDiagnostic(s.issue.body || "", 16384)};
    const bundle = {version: 2, producer: producer(context), request, expected, evidence};
    bundle.digest = digest({producer: bundle.producer, expected, evidence});
    const text = JSON.stringify(bundle); ensure(Buffer.byteLength(text) <= 131072, "EVIDENCE_BUNDLE_OVERSIZE");
    fs.writeFileSync(`${directory}/evidence.json`, text);
    core.setOutput("head_sha", expected.headSha); core.setOutput("base_sha", expected.baseSha);
    return bundle;
  }
  if (phase === "canary") {
    const s = await runtime({github: readOnlyGithub(github), context, expected, readRuleset, readOnlyCanary: true}).snapshot(undefined, true);
    const report = {version: 2, result: "SNAPSHOT_COLLECTED", mode: "read-only-snapshot", operationalAcceptance: "PENDING",
      repository: expected.repo, issueNumber: expected.issueNumber,
      prNumber: expected.prNumber, headSha: expected.headSha, baseSha: expected.baseSha,
      authorization: expected.authorization, requestRunId: expected.requestRunId,
      requestRunAttempt: expected.requestRunAttempt, ci: {runId: s.ci.runId, runAttempt: s.ci.runAttempt},
      protectionHash: digest(s.protection), filesHash: s.filesHash};
    fs.writeFileSync(`${directory}/canary-report.json`, JSON.stringify(report));
    core.setOutput("result", "SNAPSHOT_COLLECTED"); return report;
  }
  const bundle = JSON.parse(fs.readFileSync(`${directory}/evidence.json`, "utf8"));
  validateBundle(bundle, context);
  ensure(canonical(bundle.request) === canonical(request), "REQUEST_ARTIFACT_CHANGED");
  const review = JSON.parse(fs.readFileSync(`${directory}/review.json`, "utf8"));
  const mergePull = phase === "merge" ? async args => {
    ensure(typeof mergeToken === "string" && mergeToken.length > 0, "MERGE_CREDENTIAL_MISSING");
    const response = await fetcher(`https://api.github.com/repos/${expected.repo}/pulls/${args.pull_number}/merge`, {
      method: "PUT", redirect: "error", signal: AbortSignal.timeout(15000),
      headers: {authorization: `Bearer ${mergeToken}`, accept: "application/vnd.github+json", "content-type": "application/json"},
      body: JSON.stringify({sha: args.sha, merge_method: "merge"}),
    });
    ensure(response.ok, "MERGE_HTTP_FAILED"); return response.json();
  } : null;
  const r = runtime({github, context, expected, readRuleset, bundle, review, mergePull});
  await executePhase(r, phase, core);
}
module.exports = {REQUIRED_CHECKS, protection, originValid, expectedFrom, producer, validateBundle, rulesetReader, runtime, executePhase, readOnlyGithub, run, digest};
