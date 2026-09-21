"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync, spawnSync } = require("node:child_process");
const test = require("node:test");
const pipeline = require("./agent-pipeline.cjs");
const agentConfig = require("../agent-pipeline.json");
const CI_BINDING = { workflowId: agentConfig.v2.authoritativeCiWorkflowId, workflowPath: agentConfig.v2.authoritativeCiWorkflowPath };
const CI_META = { workflow_id: agentConfig.v2.authoritativeCiWorkflowId, path: agentConfig.v2.authoritativeCiWorkflowPath, run_attempt: 1, created_at: "2025-12-31T23:59:00Z", run_started_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" };
const classifierConfig = {...agentConfig.v2, requiredCiJobs: agentConfig.requiredCiJobs};

test("ready vyžaduje jednoznačný implementační ticket", () => {
  assert.equal(pipeline.validateManualTransition({ labels: ["type:implementation"], previousState: "none", nextState: "agent:ready", targetKind: "issue" }).ok, true);
  assert.equal(pipeline.validateManualTransition({ labels: [], previousState: "none", nextState: "agent:ready", targetKind: "issue" }).reason, "NOT_UNAMBIGUOUS_IMPLEMENTATION");
  assert.equal(pipeline.validateManualTransition({ labels: ["type:implementation", "type:epic"], previousState: "none", nextState: "agent:ready", targetKind: "issue" }).reason, "NOT_UNAMBIGUOUS_IMPLEMENTATION");
});

test("neplatné a nejednoznačné přechody selžou zavřeně", () => {
  assert.equal(pipeline.validateManualTransition({ labels: ["type:implementation", "agent:ready"], previousState: "agent:ready", nextState: "agent:pr", targetKind: "issue" }).reason, "INVALID_TRANSITION");
  assert.equal(pipeline.validateManualTransition({ labels: ["type:implementation", "agent:ready", "agent:running"], previousState: "agent:running", nextState: "agent:needs-human", targetKind: "issue" }).reason, "CONFLICTING_AGENT_STATE");
  assert.equal(pipeline.validateManualTransition({ labels: ["type:implementation", "agent:pr"], previousState: "agent:pr", nextState: "agent:verified", targetKind: "issue" }).reason, "VERIFIED_IS_AUTOMATED_ONLY");
  assert.equal(pipeline.validateManualTransition({ labels: ["type:implementation", "agent:needs-human"], previousState: "agent:needs-human", nextState: "agent:pr", targetKind: "issue" }).reason, "INVALID_TRANSITION");
});

test("recovery vede přes needs-human → running → nový unlabeled PR → pr", () => {
  assert.equal(pipeline.validateManualTransition({ labels: ["type:implementation", "agent:needs-human"], previousState: "agent:needs-human", nextState: "agent:running", targetKind: "issue" }).ok, true);
  assert.equal(pipeline.validateManualTransition({ labels: ["type:implementation", "agent:running"], previousState: "agent:running", nextState: "agent:pr", targetKind: "issue" }).ok, true);
  assert.deepEqual(pipeline.stateMutationPlan([], "none", "agent:pr"), { ok: true, add: ["agent:pr"], remove: [], complete: false });
});

test("regrese scénáře Issue #84 / PR #85: linked recovery zachová cizí labely a je idempotentní", () => {
  const issueNumber = 101;
  const prNumber = 202;
  const comments = [{ user: { login: "github-actions[bot]" }, body: `audit\n<!-- agent-link:v1 repo=owner/repo issue=${issueNumber} pr=${prNumber} -->` }];
  assert.deepEqual(pipeline.durablePrLinkDecision(comments, { owner: "owner", repo: "repo", issueNumber }),
    { ok: true, prNumber });
  const recovered = pipeline.stateMutationPlan(["agent:needs-human", "priority:high"], "agent:needs-human", "agent:running");
  assert.deepEqual(recovered, { ok: true, add: ["agent:running"], remove: ["agent:needs-human"], complete: false });
  assert.equal(recovered.remove.includes("priority:high"), false);
  assert.deepEqual(pipeline.stateMutationPlan(["agent:running", "priority:high"], "agent:needs-human", "agent:running"),
    { ok: true, add: [], remove: [], complete: true });
  assert.deepEqual(pipeline.stateMutationPlan(["agent:running", "priority:high"], "agent:running", "agent:pr"),
    { ok: true, add: ["agent:pr"], remove: ["agent:running"], complete: false });
});

test("missing, ambiguous a stale durable linkage fail-closed", () => {
  assert.deepEqual(pipeline.durablePrLinkDecision([], { owner: "owner", repo: "repo", issueNumber: 84 }),
    { ok: true, prNumber: null });
  const ambiguous = [
    { user: { login: "github-actions[bot]" }, body: "<!-- agent-link:v1 repo=owner/repo issue=84 pr=85 -->" },
    { user: { login: "github-actions[bot]" }, body: "<!-- agent-link:v1 repo=owner/repo issue=84 pr=86 -->" },
  ];
  assert.equal(pipeline.durablePrLinkDecision(ambiguous, { owner: "owner", repo: "repo", issueNumber: 84 }).reason,
    "AMBIGUOUS_DURABLE_LINK");
  const stale = [{ user: { login: "github-actions[bot]" }, body: "<!-- agent-link:v1 repo=owner/repo issue=84 pr=85 -->" }];
  assert.equal(pipeline.hasDurableLink(stale, { owner: "owner", repo: "repo", issueNumber: 84, prNumber: 86 }), false);
  const ambiguousPr = [
    ...stale,
    { user: { login: "github-actions[bot]" }, body: "<!-- agent-link:v1 repo=owner/repo issue=83 pr=85 -->" },
  ];
  assert.equal(pipeline.hasDurableLink(ambiguousPr, { owner: "owner", repo: "repo", issueNumber: 84, prNumber: 85 }), false);
});

test("PR linkage je právě jeden samostatný Agent-Issue marker", () => {
  assert.equal(pipeline.parseAgentIssue("x\n- Agent-Issue: #82\ny"), 82);
  assert.equal(pipeline.parseAgentIssue("Agent-Issue: #82\nAgent-Issue: #83"), null);
  assert.equal(pipeline.parseAgentIssue("Closes #82"), null);
});

test("verified vyžaduje přesný SHA, ready PR, review, stav a kompletní CI", () => {
  const valid = { workflowName: "CI", workflowConclusion: "success", headSha: "abc", prHeadSha: "abc", open: true, correctBase: true, draft: false, reviewSatisfied: true, independentReviewSatisfied: true, issueIsImplementation: true, statesReconciliable: true, needsHuman: false, requiredJobsSuccessful: true };
  assert.deepEqual(pipeline.verificationDecision(valid), { ok: true });
  for (const change of [{ draft: true }, { prHeadSha: "old" }, { reviewSatisfied: false }, { correctBase: false },
    { issueIsImplementation: false }, { statesReconciliable: false }, { needsHuman: true }, { requiredJobsSuccessful: false }]) {
    assert.equal(pipeline.verificationDecision({ ...valid, ...change }).ok, false);
  }
});

test("review event po dřívějším green CI znovu vybere pouze exact-head autoritativní run", () => {
  const runs = [
    { ...CI_META, id: 10, name: "CI", event: "pull_request", status: "completed", conclusion: "success", head_sha: "old", pull_requests: [{ number: 85 }] },
    { ...CI_META, run_started_at: "2026-01-01T00:00:01Z", updated_at: "2026-01-01T00:00:10Z", id: 11, name: "CI", event: "pull_request", status: "completed", conclusion: "failure", head_sha: "new", pull_requests: [{ number: 85 }] },
    { ...CI_META, run_started_at: "2026-01-01T00:00:02Z", updated_at: "2026-01-01T00:00:10Z", id: 12, name: "Other", event: "pull_request", status: "completed", conclusion: "success", head_sha: "new", pull_requests: [{ number: 85 }] },
    { ...CI_META, run_started_at: "2026-01-01T00:00:03Z", updated_at: "2026-01-01T00:00:10Z", id: 13, name: "CI", event: "pull_request", status: "completed", conclusion: "success", head_sha: "new", pull_requests: [{ number: 85 }] },
  ];
  assert.deepEqual(pipeline.authoritativeCiRunCandidates(runs, { ...CI_BINDING, headSha: "new", prNumber: 85 }).map((run) => run.id), [13]);
  assert.deepEqual(pipeline.authoritativeCiRunCandidates(runs, { ...CI_BINDING, headSha: "stale", prNumber: 85 }), []);
});

test("workflow_dispatch caller routuje reusable verifier výhradně podle explicitních inputs", () => {
  const headSha = "a".repeat(40);
  const trigger = pipeline.verificationTriggerDecision({
    // Caller event je záměrně irelevantní: reusable workflow jej může zdědit.
    callerEventName: "workflow_dispatch",
    workflowRun: undefined,
    workflowCallInputs: { prNumber: "202", headSha },
  });
  assert.deepEqual(trigger, { ok: true, kind: "workflow-call", prNumber: 202, headSha });

  const runs = [{ ...CI_META,
    id: 20, name: "CI", event: "pull_request", status: "completed", conclusion: "success",
    head_sha: headSha, pull_requests: [{ number: 202 }],
  }];
  assert.equal(pipeline.authoritativeCiRunCandidates(runs, {
    ...CI_BINDING, headSha: trigger.headSha, prNumber: trigger.prNumber,
  }).length, 1);
  const acknowledgements = [{
    user: { login: "github-actions[bot]" },
    body: `<!-- agent-review-ack:v1 sha=${headSha} -->`,
  }];
  assert.equal(pipeline.reviewSatisfied({ acknowledgements, headSha }), true);
  assert.deepEqual(pipeline.verificationDecision({
    workflowName: "CI", workflowConclusion: "success", headSha, prHeadSha: headSha,
    open: true, correctBase: true, draft: false, reviewSatisfied: true, independentReviewSatisfied: true,
    issueIsImplementation: true, statesReconciliable: true, needsHuman: false,
    requiredJobsSuccessful: true,
  }), { ok: true });

  const stale = "b".repeat(40);
  assert.equal(pipeline.authoritativeCiRunCandidates(runs, {
    ...CI_BINDING, headSha: stale, prNumber: trigger.prNumber,
  }).length, 0);
  assert.equal(pipeline.verificationDecision({
    workflowName: "CI", workflowConclusion: "success", headSha, prHeadSha: stale,
    open: true, correctBase: true, draft: false, reviewSatisfied: true, independentReviewSatisfied: true,
    issueIsImplementation: true, statesReconciliable: true, needsHuman: false,
    requiredJobsSuccessful: true,
  }).reason, "HEAD_SHA_MISMATCH");
});

test("trigger routing rozlišuje CI, review signal a vadné reusable inputs fail-closed", () => {
  const base = { status: "completed", conclusion: "success", pull_requests: [{ number: 202 }] };
  assert.equal(pipeline.verificationTriggerDecision({
    workflowRun: { ...base, name: "CI", event: "pull_request", head_sha: "a".repeat(40) },
  }).kind, "ci");
  assert.equal(pipeline.verificationTriggerDecision({
    workflowRun: { ...base, name: "Agent review signal", event: "pull_request_review" },
  }).kind, "review-signal");
  assert.equal(pipeline.verificationTriggerDecision({
    workflowRun: { ...base, name: "Agent Codex review", event: "workflow_run", head_sha: "a".repeat(40) },
  }).reason, "UNTRUSTED_WORKFLOW_RUN_TRIGGER");
  assert.equal(pipeline.verificationTriggerDecision({
    workflowCallInputs: { prNumber: "202", headSha: "stale" },
  }).reason, "INVALID_WORKFLOW_CALL_INPUTS");
  assert.equal(pipeline.verificationTriggerDecision({
    workflowRun: { ...base, name: "Other", event: "pull_request" },
  }).reason, "UNTRUSTED_WORKFLOW_RUN_TRIGGER");
});

test("exact-SHA review acknowledgement je explicitní a nový commit jej invaliduje", () => {
  const comments = [{ user: { login: "github-actions[bot]" }, body: "Review acknowledged\n<!-- agent-review-ack:v1 sha=abc -->" }];
  assert.equal(pipeline.reviewSatisfied({ reviewDecision: null, acknowledgements: comments, headSha: "abc" }), true);
  assert.equal(pipeline.reviewSatisfied({ reviewDecision: null, acknowledgements: comments, headSha: "new" }), false);
  assert.equal(pipeline.reviewSatisfied({ reviewDecision: "APPROVED", reviews: [{ state: "APPROVED", commit_id: "abc" }], acknowledgements: [], headSha: "abc" }), true);
  assert.equal(pipeline.reviewSatisfied({ reviewDecision: "APPROVED", reviews: [{ state: "APPROVED", commit_id: "abc" }], acknowledgements: [], headSha: "new" }), false);
});

test("durable linkage musí odpovídat repository, PR a mutable markeru", () => {
  const comments = [{ user: { login: "github-actions[bot]" }, body: "audit\n<!-- agent-link:v1 repo=owner/repo issue=82 pr=83 -->" }];
  assert.equal(pipeline.parseDurableLink(comments, { owner: "owner", repo: "repo", prNumber: 83 }), 82);
  assert.equal(pipeline.hasDurableLink(comments, { owner: "owner", repo: "repo", issueNumber: 82, prNumber: 83 }), true);
  assert.notEqual(pipeline.parseAgentIssue("- Agent-Issue: #84"), pipeline.parseDurableLink(comments, { owner: "owner", repo: "repo", prNumber: 83 }));
  assert.equal(pipeline.hasDurableLink(comments, { owner: "owner", repo: "other", issueNumber: 82, prNumber: 83 }), false);
});

test("dismissal review před finálním čtením zastaví verifikaci", () => {
  assert.equal(pipeline.reviewSatisfied({ reviewDecision: "REVIEW_REQUIRED", acknowledgements: [], headSha: "abc" }), false);
});

test("párový přechod je idempotentní a dokončí obě varianty partial write", () => {
  const previous = "agent:pr";
  const next = "agent:verified";
  assert.deepEqual(pipeline.stateMutationPlan([previous, "priority:high"], previous, next), { ok: true, add: [next], remove: [previous], complete: false });
  assert.deepEqual(pipeline.stateMutationPlan([previous, next, "priority:high"], previous, next), { ok: true, add: [], remove: [previous], complete: false });
  assert.deepEqual(pipeline.stateMutationPlan([next, "priority:high"], previous, next), { ok: true, add: [], remove: [], complete: true });
});

test("reconciliation zachová cizí label a odmítne konflikt i needs-human", () => {
  const labels = ["agent:pr", "priority:high"];
  const plan = pipeline.stateMutationPlan(labels, "agent:pr", "agent:verified");
  assert.equal(plan.remove.includes("priority:high"), false);
  assert.equal(pipeline.stateMutationPlan(["agent:running"], "agent:pr", "agent:verified").reason, "CONFLICTING_AGENT_STATE");
  assert.equal(pipeline.stateMutationPlan(["agent:pr", "agent:needs-human"], "agent:pr", "agent:verified").reason, "NEEDS_HUMAN_PRESENT");
});

test("stale Issue změněné na needs-human nesmí projít finálním reconciliation", () => {
  assert.equal(pipeline.verificationDecision({ workflowName: "CI", workflowConclusion: "success", headSha: "abc", prHeadSha: "abc", open: true, correctBase: true, draft: false, reviewSatisfied: true, independentReviewSatisfied: true, issueIsImplementation: true, statesReconciliable: true, needsHuman: false, requiredJobsSuccessful: true }).ok, true);
  assert.equal(pipeline.stateMutationPlan(["agent:needs-human"], "agent:pr", "agent:verified").ok, false);
});

test("CI joby musí všechny uspět na ověřovaném SHA", () => {
  const jobs = [{ name: "quality", conclusion: "success", head_sha: "abc", run_attempt: 1 }];
  assert.equal(pipeline.successfulRequiredJobs(jobs, ["quality"], "abc"), true);
  assert.equal(pipeline.successfulRequiredJobs(jobs, ["quality", "api"], "abc"), false);
  assert.equal(pipeline.successfulRequiredJobs(jobs, ["quality"], "other"), false);
});

test("GitHub jobs payload bez head_sha je SHA-bound přes již ověřený workflow run", () => {
  const jobs = [{ id: 7, name: "quality", conclusion: "success", run_attempt: 1,
    steps: [{ name: "ruff check", conclusion: "success" }] }];
  assert.equal(pipeline.successfulRequiredJobs(jobs, ["quality"], "a".repeat(40)), true);
});

test("re-run failed jobs skládá nejnovější výsledky ze všech attempts", () => {
  const jobs = [
    { name: "quality", conclusion: "success", head_sha: "abc", run_attempt: 1 },
    { name: "api", conclusion: "failure", head_sha: "abc", run_attempt: 1 },
    { name: "api", conclusion: "success", head_sha: "abc", run_attempt: 2 },
  ];
  assert.equal(pipeline.successfulRequiredJobs(jobs, ["quality", "api"], "abc"), true);
});

test("nový SHA invaliduje verified stav na agent:pr a staré review evidence", () => {
  assert.deepEqual(pipeline.stateMutationPlan(["agent:verified"], "agent:verified", "agent:pr"),
    { ok: true, add: ["agent:pr"], remove: ["agent:verified"], complete: false });
  const acknowledgements = [{ user: { login: "github-actions[bot]" }, body: "<!-- agent-review-ack:v1 sha=sha-a -->" }];
  const reviews = [{ state: "APPROVED", commit_id: "sha-a" }];
  assert.equal(pipeline.reviewSatisfied({ reviewDecision: "APPROVED", reviews, acknowledgements, headSha: "sha-b" }), false);
});

test("invalidation escalation odstraní verified a zachová cizí label", () => {
  assert.deepEqual(pipeline.escalationMutationPlan(["agent:verified", "priority:high"]), {
    ok: true, add: ["agent:needs-human"], remove: ["agent:verified"],
  });
  assert.deepEqual(pipeline.escalationMutationPlan(["agent:verified", "agent:needs-human", "priority:high"]), {
    ok: true, add: [], remove: ["agent:verified"],
  });
});

test("Defect D: pre-link PR zůstává při každém synchronize bez zápisu", () => {
  const input = {
    prLabels: ["priority:high"], issueLoaded: false,
    durableLink: { ok: true, issueNumber: null }, markerIssueNumber: 101,
  };
  assert.deepEqual(pipeline.invalidationLifecycleDecision(input), { action: "PRELINK_NO_WRITE" });
  assert.deepEqual(pipeline.invalidationLifecycleDecision(input), { action: "PRELINK_NO_WRITE" });
});

test("Defect D: linked agent:pr bez verified nepotřebuje invalidaci", () => {
  assert.deepEqual(pipeline.invalidationLifecycleDecision({
    prLabels: ["agent:pr", "priority:high"], issueLabels: ["type:implementation", "agent:pr"],
    issueLoaded: true, durableLink: { ok: true, issueNumber: 101 }, markerIssueNumber: 101,
  }), { action: "LINKED_PR_NO_WRITE" });
});

test("Defect D: linked verified se idempotentně invaliduje na agent:pr a zachová cizí labely", () => {
  const decision = pipeline.invalidationLifecycleDecision({
    prLabels: ["agent:verified", "priority:high"],
    issueLabels: ["type:implementation", "agent:verified", "owner:quant"], issueLoaded: true,
    durableLink: { ok: true, issueNumber: 101 }, markerIssueNumber: 101,
  });
  assert.deepEqual(decision, { action: "INVALIDATE_VERIFIED", issueNumber: 101 });
  for (const labels of [["agent:verified", "priority:high"], ["type:implementation", "agent:verified", "owner:quant"]]) {
    const plan = pipeline.stateMutationPlan(labels, "agent:verified", "agent:pr");
    assert.equal(plan.ok, true);
    assert.deepEqual(plan.add, ["agent:pr"]);
    assert.deepEqual(plan.remove, ["agent:verified"]);
    assert.equal(plan.remove.some((label) => !label.startsWith("agent:")), false);
  }
});

test("Defect D: ambiguous link a marker mismatch eskalují fail-closed", () => {
  assert.deepEqual(pipeline.invalidationLifecycleDecision({
    prLabels: [], issueLoaded: false, durableLink: { ok: false, reason: "AMBIGUOUS_DURABLE_LINK" },
    markerIssueNumber: 101,
  }), { action: "ESCALATE_CONFLICT", reason: "AMBIGUOUS_DURABLE_LINK" });
  assert.deepEqual(pipeline.invalidationLifecycleDecision({
    prLabels: ["agent:pr"], issueLabels: ["agent:pr"], issueLoaded: true,
    durableLink: { ok: true, issueNumber: 101 }, markerIssueNumber: 102,
  }), { action: "ESCALATE_CONFLICT", reason: "LINKAGE_MISMATCH" });
});

test("Defect D: agent:needs-human má prioritu a workflow neprovádí recovery", () => {
  assert.deepEqual(pipeline.invalidationLifecycleDecision({
    prLabels: ["agent:needs-human", "priority:high"], issueLabels: ["agent:verified"], issueLoaded: true,
    durableLink: { ok: true, issueNumber: 101 }, markerIssueNumber: 101,
  }), { action: "NEEDS_HUMAN_NO_WRITE" });
});

test("konfigurované required joby přesně odpovídají autoritativnímu CI", () => {
  const config = require("../agent-pipeline.json");
  const ci = fs.readFileSync(`${__dirname}/../workflows/ci.yml`, "utf8");
  const jobBlock = ci.split(/^jobs:\s*$/m)[1];
  const jobNames = [...jobBlock.matchAll(/^  ([a-z][a-z0-9-]*):\s*$/gm)].map((match) => match[1]);
  assert.deepEqual(config.requiredCiJobs, jobNames);
});

test("review acknowledgement i native approval dávají verifieru trusted re-evaluation příležitost", () => {
  const verify = fs.readFileSync(`${__dirname}/../workflows/agent-verify.yml`, "utf8");
  const acknowledgement = fs.readFileSync(`${__dirname}/../workflows/agent-review-acknowledgement.yml`, "utf8");
  const signal = fs.readFileSync(`${__dirname}/../workflows/agent-review-signal.yml`, "utf8");
  assert.match(signal, /pull_request_review:\s*\n\s+types: \[submitted\]/);
  assert.match(signal, /permissions: \{\}/);
  assert.match(verify, /workflows: \[CI, Agent review signal\]/);
  assert.match(verify, /workflow_call:/);
  assert.match(verify, /listWorkflowRunsForRepo/);
  assert.match(acknowledgement, /uses: \.\/\.github\/workflows\/agent-verify\.yml/);
  assert.doesNotMatch(verify, /pull_request_target/);
  assert.doesNotMatch(verify, /context\.eventName/);
});

test("dokumentace popisuje jediný state label, ne jediný label na objektu", () => {
  const docs = fs.readFileSync(`${__dirname}/../../docs/autonomous-development-pipeline.md`, "utf8");
  assert.match(docs, /only `agent:\*` state label/);
  assert.match(docs, /unrelated labels/);
});

test("v2 classifier je jednoznačný a unsafe failures fail-closed", () => {
  const sha = "a".repeat(40);
  // Real listJobsForWorkflowRun shape deliberately has no head_sha.
  const job = (name, id = 1, steps = []) => ({ name, id, run_attempt: 1, conclusion: "failure", steps });
  const classified = pipeline.classifyCiFailure({ jobs: [job("quality", 1, [{ name: "ruff check", conclusion: "failure" }])], runHeadSha: sha, expectedHeadSha: sha, sourceRunId: 42, runAttempt: 1, config: classifierConfig, logExcerpt: "AssertionError: expected 1" });
  assert.equal(classified.disposition, "FIX");
  assert.equal(classified.failureClass, "lint-format");
  assert.equal(pipeline.classifyCiFailure({ jobs: [job("security")], runHeadSha: sha, expectedHeadSha: sha, sourceRunId: 42, runAttempt: 1, config: classifierConfig, logExcerpt: "AssertionError: expected 1" }).disposition, "NEEDS_HUMAN");
  assert.equal(pipeline.classifyCiFailure({ jobs: [job("quality"), job("api", 2)], runHeadSha: sha, expectedHeadSha: sha, sourceRunId: 42, runAttempt: 1, config: classifierConfig, logExcerpt: "AssertionError: expected 1" }).failureClass, "multiple-failures");
  assert.equal(pipeline.classifyCiFailure({ jobs: [job("api")], runHeadSha: sha, expectedHeadSha: "b".repeat(40), sourceRunId: 42, runAttempt: 1, config: classifierConfig, logExcerpt: "failure" }).disposition, "NO_WRITE");
});

test("v2 classes, diagnostics and trusted command map are deterministic", () => {
  assert.deepEqual(pipeline.FAILURE_CLASSES, ["lint-format", "typecheck", "unit-test", "api-test", "integration-postgres", "frontend-test-build", "security", "container-build", "production-smoke", "dependency-lock", "infra-transient", "multiple-failures", "unknown"]);
  assert.equal(pipeline.normalizedFailureClass({ name: "quality", steps: [{ name: "mypy", conclusion: "failure" }] }), "typecheck");
  assert.match(pipeline.redactDiagnostic("token=ghp_abcdefgh secret=hello"), /\[REDACTED\]/);
  assert.match(pipeline.validationCommands("api-test")[0], /test_vertical_slice\.py/);
  assert.equal(pipeline.validationCommands("security"), null);
  assert.equal(pipeline.validationCommands("dependency-lock"), null);
  const dependency = pipeline.classifyCiFailure({ jobs: [{ name: "dependency-lock", id: 7, run_attempt: 2, conclusion: "failure", steps: [] }], runHeadSha: "a".repeat(40), expectedHeadSha: "a".repeat(40), sourceRunId: 99, runAttempt: 2, config: classifierConfig, logExcerpt: "uv lock failed" });
  assert.equal(dependency.disposition, "NEEDS_HUMAN");
  const missingLog = pipeline.classifyCiFailure({ jobs: [{ name: "quality", id: 8, conclusion: "failure", steps: [{name:"mypy",conclusion:"failure"}] }], runHeadSha: "a".repeat(40), expectedHeadSha: "a".repeat(40), sourceRunId: 99, runAttempt: 2, config: classifierConfig, logExcerpt: "" });
  assert.equal(missingLog.reason, "SAFE_DIAGNOSTIC_UNAVAILABLE");
  const diagnostic = pipeline.classifyCiFailure({ jobs: [{ name: "quality", id: 9, run_attempt: 3, conclusion: "failure", steps: [{name:"ruff",conclusion:"failure"}] }], runHeadSha: "a".repeat(40), expectedHeadSha: "a".repeat(40), sourceRunId: 100, runAttempt: 3, config: classifierConfig, logExcerpt: "token=topsecret\nAssertionError" });
  assert.match(diagnostic.diagnostic, /sourceRunId/);
  assert.match(diagnostic.diagnostic, /checksum/);
  assert.doesNotMatch(diagnostic.diagnostic, /topsecret/);
});

test("v2 lifecycle and two-sided durable linkage fail closed", () => {
  const marker = "<!-- agent-link:v1 repo=o/r issue=88 pr=99 -->";
  const bot = [{ user: { login: "github-actions[bot]" }, body: marker }];
  assert.deepEqual(pipeline.lifecycleAtAgentPr(["agent:pr"], ["agent:pr"]), { ok: true });
  assert.equal(pipeline.lifecycleAtAgentPr(["agent:pr", "agent:needs-human"], ["agent:pr"]).reason, "NEEDS_HUMAN_PRESENT");
  assert.equal(pipeline.lifecycleAtAgentPr(["agent:running"], ["agent:pr"]).reason, "NOT_EXACT_AGENT_PR");
  assert.deepEqual(pipeline.fullLinkageDecision({ prBody: "Agent-Issue: #88", prComments: bot, issueComments: bot, owner: "o", repo: "r", issueNumber: 88, prNumber: 99 }), { ok: true });
  assert.equal(pipeline.fullLinkageDecision({ prBody: "Agent-Issue: #88", prComments: bot, issueComments: [], owner: "o", repo: "r", issueNumber: 88, prNumber: 99 }).ok, false);
});

test("validated artifact binds checksum, byte bound, and exact allowed paths", () => {
  const c = require("../agent-pipeline.json").v2;
  assert.deepEqual(pipeline.trustedArtifactDecision({ patchBytes: 20, actualChecksum: "x", metadataChecksum: "x", actualPaths: ["backend/a.py"], metadataPaths: ["backend/a.py"], config: c }), { ok: true });
  assert.equal(pipeline.trustedArtifactDecision({ patchBytes: 20, actualChecksum: "x", metadataChecksum: "y", actualPaths: ["backend/a.py"], metadataPaths: ["backend/a.py"], config: c }).ok, false);
  assert.equal(pipeline.trustedArtifactDecision({ patchBytes: 20, actualChecksum: "x", metadataChecksum: "x", actualPaths: ["docs/ROADMAP.md"], metadataPaths: ["docs/ROADMAP.md"], config: c }).ok, false);
  assert.deepEqual(pipeline.trustedArtifactDecision({ patchBytes: 20, actualChecksum: "x", metadataChecksum: "x", actualPaths: ["docs/ROADMAP.md"], metadataPaths: ["backend/a.py"], config: c }), { ok: false, reason: "PATH_SET_MISMATCH" });
});

test("v2 fixer budget, exact evidence a idempotence jsou bounded", () => {
  const bot = (body) => ({ user: { login: "github-actions[bot]" }, body });
  assert.deepEqual(pipeline.fixAttemptDecision({ comments: [], sourceSha: "a", evidence: "e", maxAttempts: 2 }), { action: "FIX", attempt: 1 });
  assert.equal(pipeline.fixAttemptDecision({ comments: [bot("<!-- agent-fix:v2 source=a evidence=e attempt=1 result=b -->")], sourceSha: "a", evidence: "e", maxAttempts: 2 }).reason, "EXACT_EVIDENCE_ALREADY_PROCESSED");
  const used = [bot("<!-- agent-fix:v2 source=a evidence=1 attempt=1 result=b -->"), bot("<!-- agent-fix:v2 source=b evidence=2 attempt=2 result=c -->")];
  assert.equal(pipeline.fixAttemptDecision({ comments: used, sourceSha: "c", evidence: "3", maxAttempts: 2 }).reason, "FIX_BUDGET_EXHAUSTED");
});

test("v2 denylist blokuje governance, dependency a execution cesty", () => {
  const config = require("../agent-pipeline.json").v2;
  assert.equal(pipeline.validatePatchPaths(["backend/src/quantlab/safe.py"], config), true);
  for (const path of [".github/workflows/ci.yml", "AGENTS.md", "frontend/package.json", "backend/uv.lock", "backend/live_broker.py", "../escape"])
    assert.equal(pipeline.validatePatchPaths([path], config), false, path);
});

test("v3 independent review requires one exact CI-bound PASS", () => {
  const binding={repo:"o/r",issueNumber:142,prNumber:143,headSha:"a".repeat(40),specHash:"b".repeat(64),ciRunId:99,ciRunAttempt:2};
  const marker=(result="PASS")=>`<!-- agent-codex-review:v3 repo=o/r issue=142 pr=143 sha=${binding.headSha} spec=${binding.specHash} ci=99 attempt=2 result=${result} -->`;
  const bot=body=>({user:{login:"github-actions[bot]"},body});
  assert.equal(pipeline.independentReviewSatisfied([bot(marker())],binding),true);
  assert.equal(pipeline.independentReviewSatisfied([bot(marker()),bot(marker())],binding),false,"duplicate PASS is ambiguous");
  assert.equal(pipeline.independentReviewSatisfied([bot(marker()),bot(marker("BLOCK"))],binding),false,"PASS+BLOCK conflicts");
  assert.equal(pipeline.independentReviewSatisfied([bot(`<!-- agent-codex-review:v2 sha=${binding.headSha} result=PASS -->`)],binding),false,"legacy evidence is rejected");
  assert.equal(pipeline.independentReviewSatisfied([bot(marker())],{...binding,ciRunAttempt:3}),false,"rerun needs fresh review");
  const base = { workflowName: "CI", workflowConclusion: "success", headSha: "abc", prHeadSha: "abc", open: true, correctBase: true, draft: false, issueIsImplementation: true, statesReconciliable: true, needsHuman: false, requiredJobsSuccessful: true };
  assert.equal(pipeline.verificationDecision({ ...base, reviewSatisfied: false, independentReviewSatisfied: true }).reason, "EXACT_SHA_REVIEW_MISSING");
  assert.equal(pipeline.verificationDecision({ ...base, reviewSatisfied: true, independentReviewSatisfied: false }).reason, "INDEPENDENT_REVIEW_PASS_MISSING");
});

test("v2 workflow wiring odděluje secret, validation a write trust domains", () => {
  const fixer = fs.readFileSync(".github/workflows/agent-ci-fixer.yml", "utf8");
  const reviewer = fs.readFileSync(".github/workflows/agent-codex-review.yml", "utf8");
  const blockEscalation = fs.readFileSync(".github/workflows/agent-review-block-escalation.yml", "utf8");
  assert.match(fixer, /openai\/codex-action@[0-9a-f]{40}/);
  assert.match(reviewer, /openai\/codex-action@[0-9a-f]{40}/);
  for (const workflow of [fixer, reviewer]) {
    assert.match(workflow, /openai\/codex-action@86365089eb2b84e0a8fb0717b304f8bdcb13b20e/);
    assert.doesNotMatch(workflow, /openai\/codex-action@(main|v[0-9]+)/);
  }
  assert.match(fixer, /maxFixCommits/);
  assert.match(fixer, /persist-credentials: false/);
  assert.match(fixer, /concurrency:/);
  assert.match(reviewer, /permission-profile: ':read-only'/);
  assert.match(fixer, /permission-profile: ":read-only"/);
  assert.match(fixer, /allow-bot-users: "github-actions\[bot\]"/);
  assert.match(reviewer, /allow-bot-users: "github-actions\[bot\]"/);
  assert.doesNotMatch(fixer + reviewer, /allow-bots:/);
  assert.match(fixer, /workflow_call:/);
  assert.match(reviewer, /route-block:[\s\S]*uses: \.\/\.github\/workflows\/agent-review-block-escalation\.yml[\s\S]*pr_number:[\s\S]*head_sha:/);
  assert.doesNotMatch(reviewer, /uses: \.\/\.github\/workflows\/agent-ci-fixer\.yml/);
  const independentReviewer = reviewer.slice(reviewer.indexOf("independent-review:"), reviewer.indexOf("trusted-record:"));
assert.doesNotMatch(independentReviewer + blockEscalation, /contents: write|AGENT_PUBLISH_TOKEN/);
assert.match(reviewer, /verify-after-pass:[\s\S]*permissions: \{actions: read, contents: write, issues: write, pull-requests: write, statuses: write\}/);
  assert.match(fixer, /sourceRunId:run\.id,runAttempt:run\.run_attempt,logExcerpt/);
  assert.match(fixer, /prompt-file: \.codex-input\/prompt\.md/);
  assert.match(reviewer, /prompt-file: \.codex-input\/review-prompt\.md/);
  assert.doesNotMatch(fixer, /prompt:[\s\S]{0,500}\$RUNNER_TEMP\/diagnostic\.json/);
  assert.doesNotMatch(reviewer, /prompt:[\s\S]{0,500}\$RUNNER_TEMP\/authorized-scope\.json/);
  assert.match(reviewer, /output-schema:/);
  assert.match(reviewer, /uses: \.\/\.github\/workflows\/agent-verify\.yml/);
  assert.match(reviewer, /workflow_dispatch:/);
  assert.match(fixer, /validated-checksum/);
  assert.doesNotMatch(fixer.slice(fixer.indexOf("validate-patch:"), fixer.indexOf("trusted-publish:")), /OPENAI_API_KEY|contents: write/);
});

test("v2 crash-safe budget reconciles a pushed fixer commit without final comment", () => {
  const commits = [{ author: { login: "github-actions[bot]" }, commit: { message: "Automatická oprava\n\nAgent-Fix-Attempt: 1" } }];
  assert.deepEqual(pipeline.fixAttemptDecision({ comments: [], commits, sourceSha: "b", evidence: "next", maxAttempts: 2 }), { action: "FIX", attempt: 2 });
  commits.push({ author: { login: "github-actions[bot]" }, commit: { message: "fix\n\nAgent-Fix-Attempt: 2" } });
  assert.equal(pipeline.fixAttemptDecision({ comments: [], commits, sourceSha: "c", evidence: "next", maxAttempts: 2 }).reason, "FIX_BUDGET_EXHAUSTED");
});

test("v2 protected tests and concrete risk/security modules fail closed", () => {
  const config = require("../agent-pipeline.json").v2, sha = "a".repeat(40);
  for (const excerpt of ["FAILED tests/test_paper_only_architecture.py", "ERROR tests/test_phase9_security.py RBAC"])
    assert.equal(pipeline.classifyCiFailure({ jobs: [{name:"api",id:1,conclusion:"failure",steps:[]}], runHeadSha:sha, expectedHeadSha:sha, sourceRunId:1, runAttempt:1, logExcerpt:excerpt, config:{...config,requiredCiJobs:agentConfig.requiredCiJobs} }).reason, "PROTECTED_TEST_OR_INVARIANT");
  for (const path of ["backend/src/quantlab/trading.py", "backend/src/quantlab/phase4.py", "backend/src/quantlab/security.py"])
    assert.equal(pipeline.validatePatchPaths([path], config), false, path);
});

test("v2 diagnostics select bounded relevant tail rather than setup prefix", () => {
  const setup = "setup output\n".repeat(1000), excerpt = pipeline.extractFailureDiagnostic(`${setup}AssertionError: expected safe value\ntoken=secret`, 256);
  assert.match(excerpt, /AssertionError/);
  assert.ok(Buffer.byteLength(excerpt) <= 256);
  assert.doesNotMatch(excerpt, /setup output/);
});

test("v2 third-audit workflow wiring is fail-closed and injection safe", () => {
  const fixer=fs.readFileSync(".github/workflows/agent-ci-fixer.yml","utf8");
  const reviewer=fs.readFileSync(".github/workflows/agent-codex-review.yml","utf8");
  const transition=fs.readFileSync(".github/workflows/agent-state-transition.yml","utf8");
  assert.match(transition,/ci\.conclusion==="success"[\s\S]*agent-codex-review\.yml[\s\S]*agent-ci-fixer\.yml/);
  assert.match(transition,/POST_LINK_CI_IN_PROGRESS_NO_WRITE/);
  assert.match(transition,/POST_LINK_CI_AMBIGUOUS_NO_WRITE/);
  assert.match(fixer,/workflow_dispatch:[\s\S]*ci_run_id/);
  assert.match(fixer,/git diff --cached --binary/);
  assert.match(fixer,/git diff --cached --name-only/);
  assert.match(fixer,/git check-ref-format --branch "\$HEAD_REF"/);
  assert.match(fixer,/push origin "HEAD:\$\{HEAD_REF\}"/);
  assert.doesNotMatch(fixer,/git push[^\n]*\$\{\{[^\n]*head\.ref/);
  assert.match(fixer,/pr\.head\.repo\?\.full_name!==process\.env\.EXPECTED_REPO/);
  assert.doesNotMatch(fixer,/token: '\$\{\{ secrets\.AGENT_PUBLISH_TOKEN \}\}'/);
  assert.match(fixer,/Agent-Fix-Attempt:/);
  assert.match(fixer,/output-schema:[\s\S]*"BLOCK"/);
  assert.match(fixer,/fail-closed-finalizer:[\s\S]*if: always\(\)/);
  assert.match(reviewer,/trusted-governance\.json/);
  assert.match(reviewer,/base_sha:pr\.base\.sha/);
  assert.match(reviewer,/git diff --quiet "\$BASE_SHA" "\$HEAD_SHA" -- AGENTS\.md/);
  assert.equal((fixer.match(/pull-requests: write/g)||[]).length,4);
  assert.equal((reviewer.match(/pull-requests: write/g)||[]).length,5);
});

test("Issue #94 grants PR metadata writes only to trusted fixer writers", () => {
  const fixer=fs.readFileSync(".github/workflows/agent-ci-fixer.yml","utf8");
  const jobNames=[...fixer.matchAll(/^  ([a-z][a-z0-9-]+):\n(?=    )/gm)].map(match=>({name:match[1],index:match.index}));
  const jobs=Object.fromEntries(jobNames.map((job,index)=>[
    job.name,
    fixer.slice(job.index,jobNames[index+1]?.index ?? fixer.length),
  ]));
  const metadataCalls=/github\.rest\.issues\.(?:createComment|addLabels|removeLabel)\(/;
  const metadataWriters=Object.entries(jobs).filter(([,body])=>metadataCalls.test(body)).map(([name])=>name).sort();
  assert.deepEqual(metadataWriters,["escalate","fail-closed-finalizer","record-classification","trusted-publish"]);
  for(const name of metadataWriters) assert.match(jobs[name],/permissions: \{[^\n]*pull-requests: write[^\n]*\}/,`${name} must be able to mutate PR metadata`);

  assert.equal(fixer.match(/^permissions:\n  contents: read$/gm)?.length,1,"workflow permissions remain read-only");
  assert.match(jobs["record-classification"],/classificationMarker[\s\S]*record\.sha[\s\S]*record\.ciRunId[\s\S]*record\.ciRunAttempt[\s\S]*issues\.createComment/);
  for(const name of ["escalate","fail-closed-finalizer"]) {
    assert.match(jobs[name],/for\(const number of \[prNumber,issueNumber\]\)/);
    assert.match(jobs[name],/escalationMutationPlan\(item\.labels\)[\s\S]*plan\.add[\s\S]*plan\.remove/);
    assert.doesNotMatch(jobs[name],/contents: write/);
  }
  assert.match(jobs["trusted-publish"],/agent-fix:v2[\s\S]*issues\.createComment|issues\.createComment[\s\S]*agent-fix:v2/);
  for(const name of ["classify","prepare-generation-context","generate-patch","validate-patch","seal-patch"]) {
    assert.doesNotMatch(jobs[name],/issues: write|pull-requests: write|contents: write/,`${name} trust boundary broadened`);
  }
  assert.doesNotMatch(jobs["generate-patch"],/permissions:[^\n]*(?:issues|pull-requests): write/);
  assert.doesNotMatch(jobs["validate-patch"],/permissions:[^\n]*(?:issues|pull-requests): write/);
});

test("v2 index mode policy rejects symlinks, gitlinks, and mode transitions", () => {
  const raw = (oldMode, newMode) => `:${oldMode} ${newMode} ${"0".repeat(40)} ${"1".repeat(40)} M\tfile`;
  assert.equal(pipeline.validatePatchModes(raw("100644", "100644")), true);
  assert.equal(pipeline.validatePatchModes(raw("100755", "100755")), true);
  assert.equal(pipeline.validatePatchModes(raw("000000", "100644")), true);
  assert.equal(pipeline.validatePatchModes(raw("000000", "120000")), false);
  assert.equal(pipeline.validatePatchModes(raw("000000", "160000")), false);
  assert.equal(pipeline.validatePatchModes(raw("100644", "100755")), false);
});

test("v2 cached artifact preserves a tracked edit and new regression file exactly", () => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),"agent-patch-")), source=path.join(root,"source"), published=path.join(root,"published");
  fs.mkdirSync(source); execFileSync("git",["init","-q"],{cwd:source});
  execFileSync("git",["config","user.email","test@example.invalid"],{cwd:source}); execFileSync("git",["config","user.name","test"],{cwd:source});
  fs.writeFileSync(path.join(source,"code.txt"),"before\n"); execFileSync("git",["add","."],{cwd:source}); execFileSync("git",["commit","-qm","base"],{cwd:source});
  fs.writeFileSync(path.join(source,"code.txt"),"after\n"); fs.writeFileSync(path.join(source,"regression.test.txt"),"covered\n");
  execFileSync("git",["add","-A"],{cwd:source}); const patch=execFileSync("git",["diff","--cached","--binary"],{cwd:source});
  execFileSync("git",["clone","-q",source,published]);
  execFileSync("git",["apply","--index","-"],{cwd:published,input:patch});
  assert.equal(execFileSync("git",["diff","--cached","--binary"],{cwd:published}).equals(patch),true);
  assert.deepEqual(execFileSync("git",["diff","--cached","--name-only"],{cwd:published,encoding:"utf8"}).trim().split("\n"),["code.txt","regression.test.txt"]);
  fs.rmSync(root,{recursive:true,force:true});
});

test("v2 fourth-audit wiring validates before checks and closes dispatch and push TOCTOU", () => {
  const fixer=fs.readFileSync(".github/workflows/agent-ci-fixer.yml","utf8");
  const validation=fixer.slice(fixer.indexOf("validate-patch:"),fixer.indexOf("trusted-publish:"));
  assert.ok(validation.indexOf("cmp \"$RUNNER_TEMP/declared-paths\"") < validation.indexOf("case \"$(jq -r .failure_class"));
  assert.ok(validation.indexOf("validatePatchPaths(x,c.v2)") < validation.indexOf("case \"$(jq -r .failure_class"));
  assert.match(fixer,/authoritativeCiIdentity\(run,\{prNumber,headSha:requestedSha,conclusion:"failure"\}\)/);
  assert.match(fixer,/cmp "\$RUNNER_TEMP\/validated\.patch" "\$RUNNER_TEMP\/publisher\.patch"/);
  assert.match(fixer,/ls-remote --refs origin "refs\/heads\/\$HEAD_REF"[\s\S]*= "\$SHA"[\s\S]*push origin[\s\S]*ls-remote --refs origin/);
  assert.match(fixer,/prepare-generation-context:[\s\S]*source-context\.json/);
  const generation=fixer.slice(fixer.indexOf("generate-patch:"),fixer.indexOf("validate-patch:"));
  assert.doesNotMatch(generation,/actions\/checkout|git |npm |pytest|ruff|mypy/);
  assert.match(generation,/test -n "\$OPENAI_API_KEY"/);
  assert.match(fixer,/test -n "\$AGENT_PUBLISH_TOKEN"/);
});

test("v2 policy taxonomy is exhaustive, disjoint, and action identity is configured", () => {
  const policy=agentConfig.v2.failureClassPolicy;
  assert.deepEqual([...new Set([...policy.eligible,...policy.denied])].sort(),[...pipeline.FAILURE_CLASSES].sort());
  assert.equal(policy.eligible.some(x=>policy.denied.includes(x)),false);
  assert.match(agentConfig.v2.codexAction.revision,/^[0-9a-f]{40}$/);
  assert.equal(agentConfig.v2.codexAction.generationProfile,":read-only");
  assert.equal(agentConfig.v2.reviewerRequired,true);
});

test("v2 authoritative classifier rejects optional failures and uses workflow run attempt", () => {
  const sha="a".repeat(40), job={name:"optional-lint",id:1,conclusion:"failure",steps:[{name:"ruff",conclusion:"failure"}]};
  assert.equal(pipeline.classifyCiFailure({jobs:[job],runHeadSha:sha,expectedHeadSha:sha,sourceRunId:7,runAttempt:3,logExcerpt:"error",config:classifierConfig}).reason,"NON_AUTHORITATIVE_FAILED_JOB");
  const quality={...job,name:"quality"};
  const result=pipeline.classifyCiFailure({jobs:[quality],runHeadSha:sha,expectedHeadSha:sha,sourceRunId:7,runAttempt:3,logExcerpt:"error: lint",config:classifierConfig});
  assert.match(result.evidence,/:3:/);
});

test("v2 fix scope and durable classification records are deterministic", () => {
  assert.equal(pipeline.fixScopeDecision(["backend/src/a.py"],["backend/src/a.py"],agentConfig.v2).ok,true);
  assert.equal(pipeline.fixScopeDecision(["backend/src/unrelated.py"],["backend/src/a.py"],agentConfig.v2).ok,false);
  assert.equal(pipeline.fixScopeDecision(["backend/tests/test_new.py"],[],agentConfig.v2).ok,true);
  const record={repository:"o/r",issue:88,pr:99,sha:"a".repeat(40),ciRunId:7,ciRunAttempt:2,failedJobs:["quality"],failureClass:"lint-format",autoFixEligible:true,budgetState:"available",workflow:"Agent CI classifier and fixer",workflowRunId:8};
  const marker=pipeline.classificationMarker(record,agentConfig.v2);
  assert.match(marker,/agent-ci-classification:v2 evidence=.*:7:2 record=/);
  assert.equal(marker,pipeline.classificationMarker(record,agentConfig.v2));
});

test("Issue #99 source context ordering, truncation, and fail-closed priority are deterministic", () => {
  const files = [
    { path: "backend/src/aa_generic.py", content: "g".repeat(256) },
    { path: "frontend/lib/zz_changed.ts", content: "f".repeat(256) },
    { path: "frontend/lib/mm_diagnostic.ts", content: "d".repeat(256) },
    { path: "backend/src/zz_generic.py", content: "z".repeat(256) },
  ];
  const fixScope = ["frontend/lib/zz_changed.ts"];
  const diagnostic = JSON.stringify({ excerpt: "FAILED in frontend/lib/mm_diagnostic.ts line 10" });
  const wide = pipeline.buildBoundedSourceContext({ files, fixScopePaths: fixScope, diagnostic, sourceBudgetBytes: 50_000 });
  assert.deepEqual(wide.files.map((file) => file.path), [
    "frontend/lib/zz_changed.ts",
    "frontend/lib/mm_diagnostic.ts",
    "backend/src/aa_generic.py",
    "backend/src/zz_generic.py",
  ]);
  const firstThree = wide.files.slice(0, 3);
  const truncatedBudget = Buffer.byteLength(JSON.stringify({ format: "source-context-v1", files: firstThree }));
  const truncated = pipeline.buildBoundedSourceContext({ files, fixScopePaths: fixScope, diagnostic, sourceBudgetBytes: truncatedBudget });
  assert.deepEqual(truncated.files.map((file) => file.path), [
    "frontend/lib/zz_changed.ts",
    "frontend/lib/mm_diagnostic.ts",
    "backend/src/aa_generic.py",
  ]);
  assert.equal(truncated.files.some((file) => file.path === "frontend/lib/zz_changed.ts"), true);
  assert.equal(truncated.files.some((file) => file.path === "frontend/lib/mm_diagnostic.ts"), true);
  assert.equal(truncated.files.some((file) => file.path === "backend/src/zz_generic.py"), false);
  assert.equal(truncated.json, pipeline.buildBoundedSourceContext({ files, fixScopePaths: fixScope, diagnostic, sourceBudgetBytes: truncatedBudget }).json);
  const fixOnlyBudget = Buffer.byteLength(JSON.stringify({ format: "source-context-v1", files: [wide.files[0]] }));
  assert.throws(() => pipeline.buildBoundedSourceContext({
    files, fixScopePaths: fixScope, diagnostic, sourceBudgetBytes: fixOnlyBudget - 1,
  }), /PRIORITY_SOURCE_CONTEXT_TOO_LARGE/);
});

test("Issue #99 priority path ordering is locale-independent and bytewise deterministic", () => {
  const eligible = ["frontend/lib/a.ts", "frontend/lib/A.ts", "frontend/lib/á.ts", "frontend/lib/b.ts"];
  const fixScope = ["frontend/lib/á.ts", "frontend/lib/A.ts"];
  const diagnostic = JSON.stringify({ excerpt: "AssertionError at frontend/lib/a.ts" });
  assert.deepEqual(pipeline.prioritizedEligiblePaths({ eligiblePaths: eligible, fixScopePaths: fixScope, diagnostic }), [
    "frontend/lib/A.ts",
    "frontend/lib/á.ts",
    "frontend/lib/a.ts",
    "frontend/lib/b.ts",
  ]);
  const files = eligible.map((filePath) => ({ path: filePath, content: "x" }));
  const left = pipeline.buildBoundedSourceContext({ files, fixScopePaths: fixScope, diagnostic, sourceBudgetBytes: 5000 }).json;
  const right = pipeline.buildBoundedSourceContext({ files, fixScopePaths: fixScope, diagnostic, sourceBudgetBytes: 5000 }).json;
  assert.equal(left, right);
});

test("Issue #99 diagnostic relevance supports trusted cwd-relative aliases without fuzzy suffix matches", () => {
  const files = [
    { path: "backend/tests/test_example.py", content: "a".repeat(64) },
    { path: "backend/src/quantlab/example.py", content: "b".repeat(64) },
    { path: "frontend/src/example.ts", content: "c".repeat(64) },
    { path: "backend/tests/unrelated.py", content: "d".repeat(64) },
  ];
  assert.equal(pipeline.diagnosticMentionsEligiblePath(
    "backend/tests/test_example.py",
    JSON.stringify({ job: "unit-research", failureClass: "unit-test", excerpt: "FAILED tests/test_example.py::test_x" })
  ), true);
  assert.equal(pipeline.diagnosticMentionsEligiblePath(
    "backend/src/quantlab/example.py",
    JSON.stringify({ job: "quality", failureClass: "lint-format", excerpt: "src/quantlab/example.py:1:1: F401" })
  ), true);
  assert.equal(pipeline.diagnosticMentionsEligiblePath(
    "frontend/src/example.ts",
    JSON.stringify({ job: "frontend", failureClass: "frontend-test-build", excerpt: "src/example.ts:12:3" })
  ), true);
  assert.equal(pipeline.diagnosticMentionsEligiblePath(
    "backend/tests/test_example.py",
    JSON.stringify({ job: "integration-postgres", failureClass: "integration-postgres", excerpt: "tests/test_example.py" })
  ), false);
  assert.equal(pipeline.diagnosticMentionsEligiblePath(
    "backend/tests/test_example.py",
    JSON.stringify({ job: "unit-research", failureClass: "unit-test", excerpt: "FAILED test_example.py only" })
  ), false);
  assert.equal(pipeline.diagnosticMentionsEligiblePath(
    "backend/src/quantlab/example.py",
    JSON.stringify({ job: "quality", failureClass: "lint-format", excerpt: "backend/src/quantlab/example.py:1:1" })
  ), true);

  const fixScope = ["backend/src/quantlab/priority.py"];
  const contextFiles = [
    { path: "backend/src/quantlab/priority.py", content: "p".repeat(64) },
    ...files,
    { path: "backend/src/quantlab/zzz_generic.py", content: "z".repeat(64) },
  ];
  const wide = pipeline.buildBoundedSourceContext({
    files: contextFiles,
    fixScopePaths: fixScope,
    diagnostic: JSON.stringify({ job: "unit-research", failureClass: "unit-test", excerpt: "FAILED tests/test_example.py::test_x" }),
    sourceBudgetBytes: 10_000,
  });
  const firstTwo = wide.files.slice(0, 2);
  const tightBudget = Buffer.byteLength(JSON.stringify({ format: "source-context-v1", files: firstTwo }));
  const tight = pipeline.buildBoundedSourceContext({
    files: contextFiles,
    fixScopePaths: fixScope,
    diagnostic: JSON.stringify({ job: "unit-research", failureClass: "unit-test", excerpt: "FAILED tests/test_example.py::test_x" }),
    sourceBudgetBytes: tightBudget,
  });
  assert.deepEqual(tight.files.map((file) => file.path), [
    "backend/src/quantlab/priority.py",
    "backend/tests/test_example.py",
  ]);
  const again = pipeline.buildBoundedSourceContext({
    files: contextFiles,
    fixScopePaths: fixScope,
    diagnostic: JSON.stringify({ job: "unit-research", failureClass: "unit-test", excerpt: "FAILED tests/test_example.py::test_x" }),
    sourceBudgetBytes: tightBudget,
  });
  assert.equal(tight.json, again.json);
  const overflowFile = { path: "backend/tests/test_example.py", content: "x".repeat(200) };
  const overflowBudget = Buffer.byteLength(JSON.stringify({ format: "source-context-v1", files: [overflowFile] })) - 1;
  assert.throws(() => pipeline.buildBoundedSourceContext({
    files: [overflowFile, { path: "backend/src/quantlab/generic.py", content: "g".repeat(8) }],
    fixScopePaths: [],
    diagnostic: JSON.stringify({ job: "unit-research", failureClass: "unit-test", excerpt: "FAILED tests/test_example.py::test_x" }),
    sourceBudgetBytes: overflowBudget,
  }), /PRIORITY_SOURCE_CONTEXT_TOO_LARGE/);
});

test("Issue #99 tracked-index plan rejects priority symlinks and excludes generic symlinks", () => {
  const tracked = pipeline.parseTrackedIndexEntries(
    "120000 1111111111111111111111111111111111111111 0\tfrontend/lib/zz_changed.ts\0" +
    "100644 2222222222222222222222222222222222222222 0\tfrontend/lib/mm_diagnostic.ts\0" +
    "120000 3333333333333333333333333333333333333333 0\tfrontend/src/generic-link.ts\0" +
    "100644 4444444444444444444444444444444444444444 0\tfrontend/src/regular.ts\0"
  );
  assert.deepEqual(pipeline.trackedEligibleRegularPaths({ trackedEntries: tracked, config: agentConfig.v2 }), [
    "frontend/lib/mm_diagnostic.ts",
    "frontend/src/regular.ts",
  ]);
  const failPlan = pipeline.trackedPriorityMaterializationPlan({
    trackedEntries: tracked,
    fixScopePaths: ["frontend/lib/zz_changed.ts"],
    diagnostic: JSON.stringify({ excerpt: "FAILED frontend/lib/mm_diagnostic.ts" }),
    config: agentConfig.v2,
  });
  assert.equal(failPlan.ok, false);
  assert.match(failPlan.reason, /^PRIORITY_SOURCE_CONTEXT_UNSAFE_TRACKED_ENTRY:frontend\/lib\/zz_changed\.ts$/);

  const okPlan = pipeline.trackedPriorityMaterializationPlan({
    trackedEntries: tracked,
    fixScopePaths: ["frontend/lib/mm_diagnostic.ts"],
    diagnostic: JSON.stringify({ excerpt: "FAILED frontend/src/regular.ts" }),
    config: agentConfig.v2,
  });
  assert.equal(okPlan.ok, true);
  assert.deepEqual(okPlan.priorityPaths, ["frontend/lib/mm_diagnostic.ts", "frontend/src/regular.ts"]);
  assert.equal(okPlan.eligibleRegularPaths.includes("frontend/src/generic-link.ts"), false);
});

test("v2 write job executes only trusted policy and treats candidate checkout as data", () => {
  const fixer=fs.readFileSync(".github/workflows/agent-ci-fixer.yml","utf8");
  const publish=fixer.slice(fixer.indexOf("trusted-publish:"),fixer.indexOf("fail-closed-finalizer:"));
  assert.match(publish,/path: \.trusted-policy/);
  assert.match(publish,/path: candidate, persist-credentials: false/);
  assert.doesNotMatch(publish,/require\(['"]\.\/\.github/);
  assert.match(publish,/\.trusted-policy\/\.github\/scripts\/agent-pipeline\.cjs/);
  assert.match(fixer,/record-classification:[\s\S]*agent-ci-classification:v2|record-classification:[\s\S]*classificationMarker/);
  assert.match(fixer,/authorized_scope/);
});

test("v2 fixer uses explicit trusted invocation modes", () => {
  const sha="a".repeat(40);
  assert.deepEqual(pipeline.fixerInvocationDecision({eventName:"workflow_run",mode:"ci-workflow-run"}),{ok:true,kind:"ci-workflow-run"});
  assert.equal(pipeline.fixerInvocationDecision({eventName:"workflow_call",mode:"review-block",prNumber:7,headSha:sha,reviewBlock:"unsafe"}).ok,true);
  assert.equal(pipeline.fixerInvocationDecision({eventName:"workflow_run",mode:"review-block",prNumber:7,headSha:sha,reviewBlock:"unsafe"}).kind,"review-block");
  assert.equal(pipeline.fixerInvocationDecision({eventName:"workflow_dispatch",mode:"review-block",prNumber:7,headSha:sha,reviewBlock:"unsafe"}).kind,"review-block");
  assert.equal(pipeline.fixerInvocationDecision({eventName:"workflow_call",mode:"ci-workflow-run",prNumber:7,headSha:sha,reviewBlock:"unsafe"}).ok,false);
  assert.equal(pipeline.fixerInvocationDecision({eventName:"workflow_dispatch",mode:"failed-ci",prNumber:7,headSha:sha,ciRunId:9}).ok,true);
});

test("v2 authoritative CI identity is exact for automatic and dispatched routing", () => {
  const sha="b".repeat(40), base={...CI_META,name:"CI",event:"pull_request",status:"completed",conclusion:"success",head_sha:sha,pull_requests:[{number:8}]};
  assert.equal(pipeline.authoritativeCiIdentity(base,{...CI_BINDING,prNumber:8,headSha:sha,conclusion:"success"}),true);
  for (const bad of [{workflow_id:999},{path:".github/workflows/evil.yml"},{event:"workflow_dispatch"},{status:"in_progress"},{conclusion:"failure"},{head_sha:"c".repeat(40)},{pull_requests:[]},{pull_requests:[{number:9}]}])
    assert.equal(pipeline.authoritativeCiIdentity({...base,...bad},{...CI_BINDING,prNumber:8,headSha:sha,conclusion:"success"}),false);
});

test("v2 timeout evidence takes precedence over source-looking job names", () => {
  for (const name of ["unit-research","api","frontend"]) assert.equal(pipeline.normalizedFailureClass({name,conclusion:"timed_out",steps:[{name:"pytest failure",conclusion:"failure"}]}),"infra-transient");
});

test("v2 fifth-audit wiring pins validation toolchain, seals last, and finalizes reviewer", () => {
  const fixer=fs.readFileSync(".github/workflows/agent-ci-fixer.yml","utf8"), reviewer=fs.readFileSync(".github/workflows/agent-codex-review.yml","utf8");
  assert.doesNotMatch(fixer,/context\.eventName===['"]workflow_call/);
  assert.match(fixer,/invocation_mode:[\s\S]*review-block/);
  assert.match(fixer,/actions\/setup-python@[0-9a-f]{40}[\s\S]*python-version: '3\.12'/);
  assert.match(fixer,/setup-uv@[0-9a-f]{40}[\s\S]*version: '0\.12\.3'/);
  assert.match(fixer,/setup-node@[0-9a-f]{40}[\s\S]*node-version: '24'/);
  assert.match(fixer,/npm install --global npm@11\.17\.0/);
  const validate=fixer.slice(fixer.indexOf("validate-patch:"),fixer.indexOf("seal-patch:"));
  const seal=fixer.slice(fixer.indexOf("seal-patch:"),fixer.indexOf("trusted-publish:"));
  assert.match(validate,/node --test/);
  assert.doesNotMatch(validate,/validated-checksum|upload-artifact/);
  assert.match(seal,/runs-on: ubuntu-latest[\s\S]*generated-patch-[\s\S]*Independently seal generated patch on fresh runner/);
  assert.match(seal,/validatePatchPaths[\s\S]*fixScopeDecision[\s\S]*validatePatchModes[\s\S]*validated-checksum/);
  assert.match(fixer,/BEGIN TRUSTED GOVERNANCE[\s\S]*trusted-AGENTS\.md/);
  assert.match(reviewer,/newest exact authoritative CI is not green/);
  assert.match(reviewer,/Fail closed when reviewer credential is absent/);
  assert.match(reviewer,/fail-closed-finalizer:[\s\S]*uses: \.\/\.github\/workflows\/agent-review-block-escalation\.yml[\s\S]*no PASS was synthesized/);
});

test("v2 reusable caller permissions and governance linkage are fail closed", () => {
  const reviewer=fs.readFileSync(".github/workflows/agent-codex-review.yml","utf8");
  const block=fs.readFileSync(".github/workflows/agent-review-block-escalation.yml","utf8");
  assert.match(reviewer,/verify-after-pass:[\s\S]*permissions: \{actions: read, contents: write, issues: write, pull-requests: write, statuses: write\}/);
  assert.match(reviewer,/route-block:[\s\S]*permissions: \{contents: read, issues: write, pull-requests: write\}/);
  assert.match(block,/permissions: \{contents: read, issues: write, pull-requests: write\}/);
  assert.doesNotMatch(block,/contents: write|secrets: inherit|AGENT_PUBLISH_TOKEN/);
  assert.match(block,/pr\.head\.sha!==expectedSha[\s\S]*pr\.state!=="open"[\s\S]*pr\.base\.ref!==context\.payload\.repository\.default_branch/);
  assert.match(block,/issue\.pull_request\|\|issue\.state!=="open"\|\|!p\.isImplementation/);
  assert.match(block,/reviewerBlockPairPlan[\s\S]*fullLinkageDecision[\s\S]*REVIEW_BLOCK_NO_WRITE/);
  assert.match(block,/setLabels[\s\S]*readValidatedPair\(pair\.issueNumber\)[\s\S]*setLabels/);
  const escalation=reviewer.slice(reviewer.indexOf("governance-escalation:"),reviewer.indexOf("independent-review:"));
  assert.match(escalation,/listComments[\s\S]*fullLinkageDecision[\s\S]*STALE_GOVERNANCE_NO_WRITE/);
});

test("Issue #94 Fixer metadata writers have compatible least-privilege grants", () => {
  const fixer=fs.readFileSync(".github/workflows/agent-ci-fixer.yml","utf8");
  const job=(workflow,name)=>{
    const match=workflow.match(new RegExp(`^  ${name}:\\n([\\s\\S]*?)(?=^  [a-zA-Z0-9_-]+:\\n|(?![\\s\\S]))`,"m"));
    assert.ok(match,`missing job ${name}`);
    return match[0];
  };
  for(const name of ["record-classification","escalate","fail-closed-finalizer"]){
    const section=job(fixer,name);
    assert.match(section,/permissions: \{contents: read, issues: write, pull-requests: write\}/,name);
    assert.doesNotMatch(section,/contents: write|OPENAI_API_KEY/,name);
  }
  const publish=job(fixer,"trusted-publish");
  assert.match(publish,/permissions: \{contents: write, issues: write, pull-requests: write\}/);
  assert.doesNotMatch(publish,/OPENAI_API_KEY/);
  for(const name of ["prepare-generation-context","generate-patch","validate-patch","seal-patch"]){
    const section=job(fixer,name);
    assert.match(section,/permissions: \{contents: read\}/,name);
    assert.doesNotMatch(section,/issues: write|pull-requests: write|contents: write/,name);
  }
  assert.match(job(fixer,"generate-patch"),/OPENAI_API_KEY/);
});

test("Issue #96 Reviewer metadata writers have compatible least-privilege grants", () => {
  const reviewer=fs.readFileSync(".github/workflows/agent-codex-review.yml","utf8");
  const block=fs.readFileSync(".github/workflows/agent-review-block-escalation.yml","utf8");
  const job=(workflow,name)=>{
    const match=workflow.match(new RegExp(`^  ${name}:\\n([\\s\\S]*?)(?=^  [a-zA-Z0-9_-]+:\\n|(?![\\s\\S]))`,"m"));
    assert.ok(match,`missing job ${name}`);
    return match[0];
  };
  for(const name of ["governance-escalation","trusted-record","route-block","fail-closed-finalizer"]){
    const section=job(reviewer,name);
    assert.match(section,name==="trusted-record"?/permissions: \{actions: read, contents: read, issues: write, pull-requests: write\}/:/permissions: \{contents: read, issues: write, pull-requests: write\}/,name);
    assert.doesNotMatch(section,/contents: write|OPENAI_API_KEY/,name);
  }
  const model=job(reviewer,"independent-review");
  assert.match(model,/permissions: \{actions: read, checks: read, contents: read, issues: read, pull-requests: read\}/);
  assert.doesNotMatch(model,/issues: write|pull-requests: write|contents: write/);
  assert.match(model,/OPENAI_API_KEY/);
  assert.match(job(reviewer,"trusted-record"),/result\.reviewed_sha!==process\.env\.SHA[\s\S]*createComment[\s\S]*agent-codex-review:v3 repo=\$\{repo\}[\s\S]*attempt=\$\{writeCiRun\.run_attempt\}/);
  const governance=job(reviewer,"governance-escalation");
  assert.match(governance,/fullLinkageDecision[\s\S]*reviewerBlockPairPlan[\s\S]*issues\.addLabels[\s\S]*issues\.removeLabel/);
  assert.doesNotMatch(governance,/issues\.setLabels/);
  assert.equal((governance.match(/pair=await readValidatedPair\(/g)||[]).length,4);
  assert.match(governance,/readValidatedPair\(\{number,previous:label\}\)/);
  assert.match(governance,/escalate\(prNumber[\s\S]*readValidatedPair\(\)[\s\S]*escalate\(issueNumber[\s\S]*readValidatedPair\(\)[\s\S]*createComment/);
  for(const name of ["route-block","fail-closed-finalizer"])
    assert.match(job(reviewer,name),/uses: \.\/\.github\/workflows\/agent-review-block-escalation\.yml/);
  const escalate=job(block,"escalate");
  assert.match(escalate,/permissions: \{contents: read, issues: write, pull-requests: write\}/);
  assert.doesNotMatch(block,/contents: write|OPENAI_API_KEY|secrets:/);
  assert.match(escalate,/reviewerBlockPairPlan[\s\S]*fullLinkageDecision[\s\S]*setLabels[\s\S]*readValidatedPair\(pair\.issueNumber\)[\s\S]*setLabels[\s\S]*createComment/);
});

test("Issue #90 Reviewer BLOCK escalation transitions only an exact valid linked pair", () => {
  const issueNumber=90, prNumber=123, sha="a".repeat(40);
  const marker={user:{login:"github-actions[bot]"},body:`<!-- agent-link:v1 repo=o/r issue=${issueNumber} pr=${prNumber} -->`};
  const valid={prBody:`- Agent-Issue: #${issueNumber}`,prComments:[marker],issueComments:[marker],owner:"o",repo:"r",issueNumber,prNumber};
  assert.deepEqual(pipeline.lifecycleAtAgentPr(["agent:pr"],["type:implementation","agent:pr"]),{ok:true});
  assert.deepEqual(pipeline.fullLinkageDecision(valid),{ok:true});
  assert.deepEqual(pipeline.escalationMutationPlan(["agent:pr","priority:high"]),
    {ok:true,add:["agent:needs-human"],remove:["agent:pr"]});
  for(const conflict of [
    {prLabels:["agent:pr"],issueLabels:["type:implementation","agent:needs-human"]},
    {prLabels:["agent:ready"],issueLabels:["type:implementation","agent:pr"]},
  ]) assert.equal(pipeline.lifecycleAtAgentPr(conflict.prLabels,conflict.issueLabels).ok,false);
  assert.equal(pipeline.fullLinkageDecision({...valid,prBody:"- Agent-Issue: #91"}).ok,false);
  assert.equal(pipeline.fullLinkageDecision({...valid,issueComments:[]}).ok,false);
  // The workflow additionally compares the requested SHA and base immediately before this decision.
  assert.equal(sha.length,40);
});

test("Issue #90 Reviewer BLOCK retry reconciles either partial write and preserves foreign labels", () => {
  for (const [prLabels, issueLabels] of [
    [["agent:needs-human", "priority:high"], ["type:implementation", "agent:pr", "team:quant"]],
    [["agent:pr", "priority:high"], ["type:implementation", "agent:needs-human", "team:quant"]],
  ]) {
    const plan = pipeline.reviewerBlockPairPlan(prLabels, issueLabels);
    assert.equal(plan.ok, true);
    const apply = (labels) => [
      ...pipeline.labelNames(labels).filter((name) => !pipeline.STATES.includes(name)),
      "agent:needs-human",
    ];
    assert.deepEqual(apply(prLabels), ["priority:high", "agent:needs-human"]);
    assert.deepEqual(apply(issueLabels), ["type:implementation", "team:quant", "agent:needs-human"]);
  }
  for (const conflict of [
    [["agent:ready"], ["type:implementation", "agent:pr"]],
    [["agent:pr", "agent:needs-human"], ["type:implementation", "agent:pr"]],
    [["agent:pr"], ["type:implementation", "agent:verified"]],
  ]) assert.deepEqual(pipeline.reviewerBlockPairPlan(...conflict),
    {ok:false,reason:"INVALID_REVIEW_BLOCK_PAIR"});
});

test("v2 classify job guard admits reusable review-block despite inherited caller events", () => {
  const fixer=fs.readFileSync(".github/workflows/agent-ci-fixer.yml","utf8");
  const classify=fixer.slice(fixer.indexOf("  classify:"),fixer.indexOf("    runs-on:",fixer.indexOf("  classify:")));
  assert.match(classify,/if: inputs\.invocation_mode == 'review-block' \|\|/);
  assert.match(classify,/github\.event_name == 'workflow_dispatch'[\s\S]*inputs\.invocation_mode == 'failed-ci'/);
  assert.match(classify,/github\.event_name == 'workflow_run'[\s\S]*inputs\.invocation_mode == ''[\s\S]*conclusion == 'failure'/);
  assert.doesNotMatch(classify,/github\.event_name == 'workflow_call'/);

  // Exercise the job-level eligibility contract rather than only the inner routing helper.
  const classifyRuns=({eventName,conclusion,mode}) => mode==="review-block" ||
    (eventName==="workflow_dispatch" && mode==="failed-ci") ||
    (eventName==="workflow_run" && !mode && conclusion==="failure");
  assert.equal(classifyRuns({eventName:"workflow_run",conclusion:"success",mode:"review-block"}),true);
  assert.equal(classifyRuns({eventName:"workflow_dispatch",conclusion:undefined,mode:"review-block"}),true);
  assert.equal(classifyRuns({eventName:"workflow_run",conclusion:"success",mode:""}),false);
  assert.match(fixer,/prepare-generation-context:[\s\S]*FIX_SCOPE: '\$\{\{ needs\.classify\.outputs\.fix_scope \}\}'[\s\S]*DIAGNOSTIC: '\$\{\{ needs\.classify\.outputs\.diagnostic \}\}'/);
  assert.match(fixer,/git',\['ls-files','--stage','-z'\]/);
  assert.match(fixer,/parseTrackedIndexEntries\(/);
  assert.match(fixer,/trackedPriorityMaterializationPlan\(\{trackedEntries,fixScopePaths:process\.env\.FIX_SCOPE,diagnostic:process\.env\.DIAGNOSTIC,config:c\.v2\}\)/);
  assert.match(fixer,/fs\.lstatSync/);
  assert.doesNotMatch(fixer,/fs\.statSync/);
  assert.match(fixer,/buildBoundedSourceContext\(\{files:\[\.\.\.filesByPath\.values\(\)\],fixScopePaths:process\.env\.FIX_SCOPE,diagnostic:process\.env\.DIAGNOSTIC,sourceBudgetBytes:SOURCE_CONTEXT_MAX_BYTES\}\)/);
  assert.match(fixer,/SOURCE_CONTEXT_MAX_BYTES=917504/);
  assert.match(fixer,/test "\$\(stat -c%s \.codex-input\/prompt\.md\)" -lt 1048576/);
});


test("Issue #130 trusted recorder re-fetches CI and all mutable guards before its only write", () => {
  const workflow=fs.readFileSync(".github/workflows/agent-codex-review.yml","utf8");
  const record=workflow.slice(workflow.indexOf("  trusted-record:"),workflow.indexOf("  verify-after-pass:"));
  const finalReads=record.indexOf("These are the final reads before the sole write boundary");
  const write=record.indexOf("issues.createComment",finalReads);
  assert.ok(finalReads>record.indexOf("listJobsForWorkflowRun"));
  for(const guard of ["repos.get(context.repo)","repos.getBranch","pulls.get","issues.get","authorizationDecision","lifecycleAtAgentPr","fullLinkageDecision","successfulRequiredJobs"])
    assert.ok(record.indexOf(guard,finalReads)>finalReads&&record.indexOf(guard,finalReads)<write,`${guard} must be checked in the final snapshot`);
  assert.equal(record.indexOf("issues.createComment"),write,"the trusted marker is the first write");
});

const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
const controlWorkflow=()=>fs.readFileSync(".github/workflows/agent-control-plane-remediation.yml","utf8");
const reviewerWorkflow=()=>fs.readFileSync(".github/workflows/agent-codex-review.yml","utf8");

// Maintenance mutation-boundary regressions live in agent-maintenance-runtime.test.cjs.

async function executeIssue128FinalWrite(change={}){
  const workflow=reviewerWorkflow(),record=workflow.slice(workflow.indexOf("  trusted-record:"),workflow.indexOf("  verify-after-pass:"));
  const lines=record.split("\n"),start=lines.findIndex(x=>x.includes("const writeJobs=await")),end=lines.findIndex((x,i)=>i>start&&x.includes("await github.rest.issues.createComment"));
  assert.ok(start>=0&&end>start,"production final authority boundary must exist");
  const source=lines.slice(start,end+1).map(x=>x.trim()).join("\n"),head="a".repeat(40),base="b".repeat(40),writes=[],failures=[];
  const ci={id:10,...CI_META,workflow_id:CI_BINDING.workflowId,path:CI_BINDING.workflowPath,head_repository:{full_name:"o/r"},run_attempt:1,name:"CI",event:"pull_request",head_sha:head,pull_requests:[{number:135}],status:"completed",conclusion:"success"};
  const reviewer={id:90,workflow_id:900,path:".github/workflows/agent-codex-review.yml",head_sha:base,run_attempt:4};let drift=false,newerCiStarted=false,runListReads=0,authorityDrift=false;
  const actions={listJobsForWorkflowRun(){},listWorkflowRunsForRepo(){},getWorkflowRun:async({run_id})=>({data:run_id===90?{...reviewer,run_attempt:change.reviewerAttempt||reviewer.run_attempt}:{...ci}}),getWorkflow:async({workflow_id})=>({data:workflow_id===900?{id:900,name:"Agent Codex review",path:reviewer.path}:{id:CI_BINDING.workflowId,name:"CI",path:ci.path}})};
  const github={rest:{actions,repos:{get:async()=>({data:{full_name:"o/r",default_branch:"main"}}),getBranch:async()=>({data:{commit:{sha:authorityDrift||change.mainDrift?"d".repeat(40):base}}})},pulls:{get:async()=>({data:{state:"open",draft:false,base:{ref:change.baseDrift?"other":"main",sha:base},head:{sha:drift||change.headDrift?"c".repeat(40):head,repo:{full_name:"o/r"}},body:"Agent-Issue: #128",labels:["agent:pr"]}})},issues:{listComments(){},get:async()=>({data:{state:"open",title:"t",body:"b",labels:["type:implementation","agent:pr"]}}),createComment:async x=>writes.push(x)}},paginate:async(route)=>{if(route===actions.listJobsForWorkflowRun)return [{name:"required",status:"completed",conclusion:"success"}];if(route===actions.listWorkflowRunsForRepo){runListReads++;if(change.authorityDriftsDuringNewestCiPagination&&runListReads===2)authorityDrift=true;return change.newerCi||newerCiStarted?[{...ci,id:11,run_attempt:2,status:"in_progress",conclusion:null},ci]:[ci];}if(change.newerCiDuringCommentPagination)newerCiStarted=true;if(change.driftDuringPagination)drift=true;return [];}};
  const p={newestAuthoritativeCiRun:pipeline.newestAuthoritativeCiRun,parseAgentIssue:()=>128,isImplementation:()=>true,lifecycleAtAgentPr:()=>({ok:!change.lifecycleDrift}),fullLinkageDecision:()=>({ok:!change.linkageDrift}),authoritativeCiIdentity:run=>run.status==="completed"&&run.conclusion==="success",successfulRequiredJobs:()=>true,parseTrustedMarker:()=>false},a={authorizationDecision:()=>({ok:!change.authorizationDrift,specHash:"spec"})},c={requiredCiJobs:["required"]};
  const evidence={repository:"o/r",issueNumber:128,prNumber:135,headSha:head,baseSha:base,sourceSha:base,specHash:"spec",reviewer:{runId:90,runAttempt:4,workflowId:900,path:reviewer.path,sourceSha:base},ci:{runId:10,runAttempt:1,workflowId:CI_BINDING.workflowId,path:ci.path}},sealedBindings={repository:"o/r",issueNumber:128,prNumber:135,headSha:head,baseSha:base,sourceSha:base,specHash:"spec",reviewerRunId:90,reviewerRunAttempt:4,ciRunId:10,ciRunAttempt:1},seal={schema:"agent-review-seal-v1",bindings:sealedBindings,files:{proof:"digest"}};
  const env={SHA:head,BASE:base,SOURCE:base,SPEC:"spec",REVIEW_RUN:"90",REVIEW_ATTEMPT:"4",CI_RUN:"10",CI_ATTEMPT:"1",GITHUB_WORKFLOW_SHA:base,GITHUB_RUN_ATTEMPT:change.runtimeAttempt===undefined?"4":change.runtimeAttempt};if(change.missingRuntimeAttempt)delete env.GITHUB_RUN_ATTEMPT;
  await new AsyncFunction("github","context","process","prNumber","issueNumber","repo","p","a","c","evidence","seal","sealedBindings","hash","result","core","safeSummary",source)(github,{repo:{owner:"o",repo:"r"},runId:90,workflow:"Agent Codex review",payload:{repository:{default_branch:"main"}}},{env},135,128,"o/r",p,a,c,evidence,seal,sealedBindings,()=>"digest",{reviewed_sha:head,result:"PASS"},{setFailed:x=>failures.push(x),notice:x=>failures.push(x)},"safe");
  return {writes,failures};
}

test("Issue #128 final write re-reads every mutable authority and fails closed on intervening drift",async()=>{
  assert.equal((await executeIssue128FinalWrite()).writes.length,1);
  for(const attack of [{authorizationDrift:true},{lifecycleDrift:true},{linkageDrift:true},{headDrift:true},{baseDrift:true},{mainDrift:"d".repeat(40)},{driftDuringPagination:true},{newerCi:true},{reviewerAttempt:5}]){
    const result=await executeIssue128FinalWrite(attack);assert.deepEqual(result.writes,[],`must reject ${JSON.stringify(attack)}`);assert.deepEqual(result.failures,["NO_WRITE: final authority changed at review record boundary"]);
  }
  for(const runtimeAttempt of ["","not-a-number","3","5"]){const result=await executeIssue128FinalWrite({runtimeAttempt});assert.deepEqual(result.writes,[]);}
  assert.deepEqual((await executeIssue128FinalWrite({missingRuntimeAttempt:true})).writes,[]);
});

test("Issue #128 final write rejects a newer exact-head CI run started during comment pagination",async()=>{
  assert.match(reviewerWorkflow(),/const boundaryRuns=await github\.paginate\(github\.rest\.actions\.listWorkflowRunsForRepo,\{\.\.\.context\.repo,per_page:100\}\)/);
  const result=await executeIssue128FinalWrite({newerCiDuringCommentPagination:true});
  assert.deepEqual(result.writes,[]);
  assert.deepEqual(result.failures,["NO_WRITE: final authority changed at review record boundary"]);
});

test("Issue #128 final write snapshots mutable authority after newest-CI pagination",async()=>{
  const workflow=reviewerWorkflow(),record=workflow.slice(workflow.indexOf("  trusted-record:"),workflow.indexOf("  verify-after-pass:"));
  assert.ok(record.indexOf("const boundaryRuns=await github.paginate")<record.indexOf("const {data:writeRepository}=await github.rest.repos.get"));
  const result=await executeIssue128FinalWrite({authorityDriftsDuringNewestCiPagination:true});
  assert.deepEqual(result.writes,[]);
  assert.deepEqual(result.failures,["NO_WRITE: final authority changed at review record boundary"]);
});

test("Issue #130 real governance controller stops the paired write when authority drifts",async()=>{
  const workflow=reviewerWorkflow(),section=workflow.slice(workflow.indexOf("  governance-escalation:"),workflow.indexOf("  collect-review-evidence:"));
  const script=section.slice(section.indexOf("          script: |")+20).split("\n").filter(x=>x.startsWith("            ")).map(x=>x.slice(12)).join("\n");
  const base="b".repeat(40),head="a".repeat(40),writes=[];let issueReads=0,prReads=0;
  const p={STATES:["agent:pr","agent:needs-human"],labelNames:value=>value,parseAgentIssue:()=>130,isImplementation:()=>true,reviewerBlockPairPlan:()=>({ok:true}),fullLinkageDecision:()=>({ok:true}),escalationMutationPlan:()=>({ok:true,add:["agent:needs-human"],remove:["agent:pr"]})},a={authorizationDecision:()=>({ok:true,specHash:"spec"})};
  const github={
    rest:{
      repos:{get:async()=>({data:{full_name:"o/r",default_branch:"main"}}),getBranch:async()=>({data:{commit:{sha:base}}})},
      pulls:{get:async()=>({data:{state:"open",draft:false,base:{ref:"main",sha:base},head:{sha:head,repo:{full_name:"o/r"}},body:"Agent-Issue: #130",labels:++prReads>1?["agent:pr","agent:needs-human"]:["agent:pr"]}})},
      issues:{listComments(){},get:async({issue_number})=>{if(issue_number===130)issueReads++;return {data:{state:issue_number===130&&issueReads>1?"closed":"open",title:"t",body:"b",labels:["agent:pr"]}}},addLabels:async x=>writes.push(x),removeLabel:async x=>writes.push(x),createComment:async x=>writes.push(x)}
    },
    paginate:async()=>[]
  };
  const requireMock=x=>x.includes("pipeline")?p:a,core={notice(){}},context={repo:{owner:"o",repo:"r"},payload:{repository:{default_branch:"main"}}},processMock={env:{PR:"131",ISSUE:"130",SHA:head,BASE:base,SPEC:"spec"}};
  await new AsyncFunction("require","github","context","core","process",script)(requireMock,github,context,core,processMock);
  assert.equal(writes.length,1,"only the PR label write may occur before the injected drift");
  assert.equal(writes[0].issue_number,131);
});

test("Issue #130 governance escalation preserves a non-agent label added after the final read",async()=>{
  const workflow=reviewerWorkflow(),section=workflow.slice(workflow.indexOf("  governance-escalation:"),workflow.indexOf("  collect-review-evidence:"));
  const script=section.slice(section.indexOf("          script: |")+20).split("\n").filter(x=>x.startsWith("            ")).map(x=>x.slice(12)).join("\n");
  const base="b".repeat(40),head="a".repeat(40),writes=[],states=new Map([[131,new Set(["agent:pr"])],[130,new Set(["type:implementation","agent:pr"])] ]);let injected=false;
  const labels=number=>[...states.get(number)];
  const p={...pipeline,parseAgentIssue:()=>130,fullLinkageDecision:()=>({ok:true})},a={authorizationDecision:()=>({ok:true,specHash:"spec"})};
  const github={rest:{repos:{get:async()=>({data:{full_name:"o/r",default_branch:"main"}}),getBranch:async()=>({data:{commit:{sha:base}}})},pulls:{get:async()=>({data:{state:"open",draft:false,base:{ref:"main",sha:base},head:{sha:head,repo:{full_name:"o/r"}},body:"Agent-Issue: #130",labels:labels(131)}})},issues:{listComments(){},get:async({issue_number})=>({data:{state:"open",title:"t",body:"b",labels:labels(issue_number)}}),addLabels:async args=>{if(args.issue_number===131&&!injected){states.get(131).add("triage:concurrent");injected=true;}for(const label of args.labels)states.get(args.issue_number).add(label);writes.push(args);},removeLabel:async args=>{states.get(args.issue_number).delete(args.name);writes.push(args);},createComment:async()=>{}}},paginate:async()=>[]};
  const requireMock=x=>x.includes("pipeline")?p:a,core={notice(){}},context={repo:{owner:"o",repo:"r"},payload:{repository:{default_branch:"main"}}},processMock={env:{PR:"131",ISSUE:"130",SHA:head,BASE:base,SPEC:"spec"}};
  await new AsyncFunction("require","github","context","core","process",script)(requireMock,github,context,core,processMock);
  assert.ok(states.get(131).has("triage:concurrent"));
  assert.deepEqual(labels(131).filter(x=>x.startsWith("agent:")),["agent:needs-human"]);
  assert.deepEqual(labels(130).filter(x=>x.startsWith("agent:")),["agent:needs-human"]);
  assert.deepEqual(writes.slice(0,4).map(write=>[write.issue_number,write.labels?.[0]||write.name]),[
    [131,"agent:needs-human"],[131,"agent:pr"],[130,"agent:needs-human"],[130,"agent:pr"],
  ],"each object must pass through the targeted add/remove transition");
  assert.ok(writes.every(write=>!("labels" in write)||write.labels.every(label=>label.startsWith("agent:"))),"writes target lifecycle labels only");
});

test("Issue #130 governance escalation propagates prepared base authority",()=>{
  const prepare=reviewerWorkflow().slice(reviewerWorkflow().indexOf("  prepare:"),reviewerWorkflow().indexOf("  governance-escalation:"));
  assert.match(prepare,/GOVERNANCE_CHANGE[\s\S]*base_sha:pr\.base\.sha/);
});

test("newest exact-head CI selection is unfiltered and active or cancelled runs shadow green",()=>{
  const sha="c".repeat(40),base={...CI_META,name:"CI",event:"pull_request",head_sha:sha,pull_requests:[{number:142}],run_attempt:1};
  for(const latest of [{status:"queued",conclusion:null},{status:"in_progress",conclusion:null},{status:"completed",conclusion:"cancelled"},{status:"completed",conclusion:"failure"}]){
    const green={...base,id:10,status:"completed",conclusion:"success"},newer={...base,...latest,id:11,run_started_at:"2026-01-02T00:00:00Z",updated_at:"2026-01-02T00:00:10Z"};
    assert.equal(pipeline.newestAuthoritativeCiRun([green,newer],{...CI_BINDING,headSha:sha,prNumber:142})?.id,latest.status==="completed"?11:undefined);
    assert.deepEqual(pipeline.authoritativeCiRunCandidates([green,newer],{...CI_BINDING,headSha:sha,prNumber:142}),[]);
  }
});

test("authoritative config and docs declare strict v3 exact-CI review",()=>{
  const config=require("../agent-pipeline.json"),docs=fs.readFileSync("docs/autonomous-development-pipeline.md","utf8");
  assert.equal(config.v2.reviewMarker,"agent-codex-review:v3");
  assert.deepEqual(config.v2.reviewEvidenceBinding,["repository","issue","pr","sha","spec","ciRunId","ciRunAttempt","result"]);
  assert.match(docs,/authoritative review format is `agent-codex-review:v3`/);
  assert.match(docs,/Legacy v2 and SHA-only review markers are audit history only/);
});

test("attempt recency shadows run id and malformed recency fails closed",()=>{
  const sha="e".repeat(40),base={...CI_META,name:"CI",event:"pull_request",head_sha:sha,pull_requests:[{number:143}]};
  const oldSuccess={...base,id:200,run_attempt:1,run_started_at:"2026-09-17T20:00:00Z",updated_at:"2026-09-17T20:30:00Z",status:"completed",conclusion:"success"};
  for(const state of [{status:"queued",conclusion:null},{status:"in_progress",conclusion:null},{status:"completed",conclusion:"failure"},{status:"completed",conclusion:"cancelled"}]){
    const rerun={...base,...state,id:100,run_attempt:2,run_started_at:"2026-09-17T21:00:00Z",updated_at:"2026-09-17T21:30:00Z"};
    assert.equal(pipeline.newestAuthoritativeCiRun([oldSuccess,rerun],{...CI_BINDING,headSha:sha,prNumber:143}),state.status==="completed"?rerun:null);
  }
  assert.equal(pipeline.newestAuthoritativeCiRun([oldSuccess,{...oldSuccess,id:100,run_attempt:2,updated_at:"not-a-date"}],{...CI_BINDING,headSha:sha,prNumber:143}),null);
  const tied={...oldSuccess,id:100,run_attempt:2};
  assert.equal(pipeline.newestAuthoritativeCiRun([oldSuccess,tied],{...CI_BINDING,headSha:sha,prNumber:143}),null);
});

test("authoritative workflow identity rejects CI namesakes and path drift",()=>{
  const sha="f".repeat(40),real={...CI_META,id:10,name:"CI",event:"pull_request",status:"completed",head_sha:sha,pull_requests:[{number:143}]},evil={...real,id:99,workflow_id:999,path:".github/workflows/evil.yml"};
  assert.equal(pipeline.newestAuthoritativeCiRun([real,evil],{...CI_BINDING,headSha:sha,prNumber:143}),real);
  assert.equal(pipeline.newestAuthoritativeCiRun([{...real,path:".github/workflows/renamed.yml"}],{...CI_BINDING,headSha:sha,prNumber:143}),null);
});

test("unchanged CI fixer calls use trusted defaults and reject workflow impostors", () => {
  const sha = "a".repeat(40);
  const real = {...CI_META, id: 10, name: "CI", event: "pull_request", status: "completed", conclusion: "failure", head_sha: sha, pull_requests: [{number: 143}]};
  const fixer = fs.readFileSync(".github/workflows/agent-ci-fixer.yml", "utf8");
  const calls = fixer.match(/p\.authoritativeCiIdentity\(run,\{[^}]+\}\)/g);
  assert.equal(calls.length, 2, "classification and transient retry both retain their original call shape");
  for (const call of calls) {
    assert.doesNotMatch(call, /workflowId|workflowPath/);
    const invoke = new Function("p", "run", "prNumber", "requestedSha", "sha", `return ${call};`);
    assert.equal(invoke(pipeline, real, 143, sha, sha), true);
    for (const drift of [{workflow_id: 999}, {path: ".github/workflows/evil.yml"}, {workflow_id: undefined}, {path: undefined}]) {
      assert.equal(invoke(pipeline, {...real, ...drift}, 143, sha, sha), false);
    }
  }
  const binding = {headSha: sha, prNumber: 143};
  assert.equal(pipeline.newestAuthoritativeCiRun([real], binding), real);
  assert.equal(pipeline.newestAuthoritativeCiRun([{...real, workflow_id: 999}], binding), null);
});

test("caller-supplied CI identity cannot replace configured authority", () => {
  const sha = "b".repeat(40);
  const real = {...CI_META, id: 10, name: "CI", event: "pull_request", status: "completed", conclusion: "success", head_sha: sha, pull_requests: [{number: 143}]};
  const binding = {headSha: sha, prNumber: 143, conclusion: "success"};
  for (const explicit of [{}, CI_BINDING, {workflowId: CI_BINDING.workflowId}, {workflowPath: CI_BINDING.workflowPath}]) {
    assert.equal(pipeline.authoritativeCiIdentity(real, {...binding, ...explicit}), true);
    assert.equal(pipeline.newestAuthoritativeCiRun([real], {...binding, ...explicit}), real);
  }
  for (const explicit of [
    {...CI_BINDING, workflowId: 999},
    {...CI_BINDING, workflowPath: ".github/workflows/evil.yml"},
    {workflowId: 999, workflowPath: ".github/workflows/evil.yml"},
    {workflowId: null}, {workflowId: 0}, {workflowId: NaN}, {workflowPath: null}, {workflowPath: ""},
  ]) {
    const impostor = {...real, workflow_id: explicit.workflowId ?? real.workflow_id, path: explicit.workflowPath ?? real.path};
    assert.equal(pipeline.authoritativeCiIdentity(impostor, {...binding, ...explicit}), false);
    assert.equal(pipeline.newestAuthoritativeCiRun([impostor], {...binding, ...explicit}), null);
    assert.deepEqual(pipeline.authoritativeCiRunCandidates([impostor], {...binding, ...explicit}), []);
  }
});

test("every dynamic CI metadata guard rejects drift from trusted configuration", async () => {
  let guards = 0;
  const valid = {id: CI_BINDING.workflowId, path: CI_BINDING.workflowPath, state: "active"};
  for (const file of ["agent-codex-review", "agent-verify", "agent-verified-gate", "agent-auto-merge"]) {
    const workflow = fs.readFileSync(`.github/workflows/${file}.yml`, "utf8");
    const sources = [...workflow.matchAll(/const \{data:ciWorkflow\}=await github\.rest\.actions\.getWorkflow\(\{\.\.\.context\.repo,workflow_id:c\.v2\.authoritativeCiWorkflowPath\}\);[\s\S]*?const ciBinding=\{[^}]+\};/g)];
    assert.equal(sources.length, file === "agent-codex-review" ? 2 : 1, file);
    for (const [source] of sources) {
      guards++;
      for (const drift of [{}, {id: 999}, {path: ".github/workflows/evil.yml"}, {state: "disabled_manually"}, {id: null}, {id: "331418792"}]) {
        const failures = [];
        const github = {rest: {actions: {getWorkflow: async args => {
          assert.equal(args.workflow_id, CI_BINDING.workflowPath);
          return {data: {...valid, ...drift}};
        }}}};
        const core = {setFailed: reason => { failures.push(reason); }};
        const binding = await new AsyncFunction("github", "context", "c", "core", `${source}\nreturn ciBinding;`)(github, {repo: {owner: "o", repo: "r"}}, agentConfig, core);
        if (Object.keys(drift).length) {
          assert.deepEqual(failures, ["AUTHORITATIVE_CI_WORKFLOW_INVALID"], file);
          assert.equal(binding, undefined);
        } else {
          assert.deepEqual(failures, []);
          assert.deepEqual(binding, CI_BINDING);
        }
      }
    }
  }
  assert.equal(guards, 5);
});

test("serialized gate executions revalidate and publish one exact-attempt marker", async () => {
  const autonomy = require("./agent-autonomy.cjs");
  const workflow = fs.readFileSync(".github/workflows/agent-verified-gate.yml", "utf8");
  assert.match(workflow, /concurrency:\n  group: agent-verified-gate-\$\{\{ github\.repository \}\}-\$\{\{ inputs\.pr_number \}\}-\$\{\{ inputs\.head_sha \}\}-\$\{\{ inputs\.spec_hash \}\}\n  cancel-in-progress: false/);
  const section = workflow.slice(workflow.indexOf("          script: |") + "          script: |".length, workflow.indexOf("\n  merge:"));
  const source = section.split("\n").filter(line => line.startsWith("            ")).map(line => line.slice(12)).join("\n");
  const headSha = "c".repeat(40), specHash = "d".repeat(64), repo = "o/r", issueNumber = 142, prNumber = 143;
  const binding = {repo, issueNumber, prNumber, headSha, specHash, ciRunId: 10, ciRunAttempt: 1};
  const trusted = body => ({user: {login: "github-actions[bot]"}, body});
  const comments = [
    trusted(`<!-- agent-codex-review:v3 repo=${repo} issue=${issueNumber} pr=${prNumber} sha=${headSha} spec=${specHash} ci=10 attempt=1 result=PASS -->`),
    trusted(autonomy.verificationMarker(binding)),
  ];
  let prReads = 0, markerWrites = 0, needsHuman = false;
  const statuses = [], failures = [];
  const run = {...CI_META, id: 10, name: "CI", event: "pull_request", head_sha: headSha, pull_requests: [{number: prNumber}], status: "completed", conclusion: "success"};
  const github = {rest: {
    pulls: {get: async () => { prReads++; return {data: {state: "open", draft: false, head: {sha: headSha}, base: {ref: "main"}, body: `Agent-Issue: #${issueNumber}`, labels: needsHuman ? ["agent:verified", "agent:needs-human"] : ["agent:verified"]}}; }},
    issues: {
      get: async () => ({data: {state: "open", title: "t", body: "b", labels: ["type:implementation", "agent:verified"]}}),
      listComments() {},
      createComment: async args => { markerWrites++; comments.push(trusted(args.body)); },
    },
    repos: {
      getBranch: async () => ({data: {commit: {sha: "e".repeat(40)}}}),
      compareCommits: async () => ({data: {behind_by: 0}}),
      createCommitStatus: async args => statuses.push(args),
    },
    actions: {
      getWorkflow: async () => ({data: {id: CI_BINDING.workflowId, path: CI_BINDING.workflowPath, state: "active"}}),
      listWorkflowRunsForRepo() {}, listJobsForWorkflowRun() {},
    },
  }, paginate: async (method, args) => {
    if (method === github.rest.issues.listComments) return args.issue_number === prNumber ? [...comments] : [];
    if (method === github.rest.actions.listWorkflowRunsForRepo) return [run];
    if (method === github.rest.actions.listJobsForWorkflowRun) return agentConfig.requiredCiJobs.map(name => ({name, run_attempt: 1, status: "completed", conclusion: "success"}));
    throw new Error("Unexpected paginated API");
  }};
  const requireMock = name => name.endsWith("agent-pipeline.cjs") ? {...pipeline, fullLinkageDecision: () => ({ok: true})}
    : name.endsWith("agent-autonomy.cjs") ? {...autonomy, authorizationDecision: () => ({ok: true, specHash})} : agentConfig;
  const execute = () => new AsyncFunction("require", "github", "context", "core", "process", source)(
    requireMock, github, {repo: {owner: "o", repo: "r"}, payload: {repository: {default_branch: "main"}}},
    {setFailed: reason => failures.push(reason), setOutput() {}}, {env: {PR: String(prNumber), HEAD: headSha, SPEC: specHash}},
  );
  // The workflow concurrency group admits the second execution only after the first.
  await execute();
  await execute();
  assert.deepEqual(failures, []);
  assert.equal(prReads, 12, "each admitted execution revalidates before marker and status writes");
  assert.equal(markerWrites, 1, "the second execution observes the first marker");
  assert.equal(statuses.filter(status => status.state === "success").length, 2);
  assert.equal(autonomy.exactGateEvidence(comments, binding), true);
  needsHuman = true;
  await execute();
  assert.deepEqual(failures, ["GATE_REJECTED:LIFECYCLE_INVALID"]);
  assert.equal(markerWrites, 1);
  assert.equal(statuses.at(-1).state, "failure");
});

test("overlapping completion cannot hide the attempt that started later", () => {
  const binding = {...CI_BINDING, headSha: "e".repeat(40), prNumber: 143};
  const base = {...CI_META, event: "pull_request", head_sha: binding.headSha, pull_requests: [{number: 143}], status: "completed"};
  const slowSuccess = {...base, id: 200, run_started_at: "2026-01-01T10:00:00Z", updated_at: "2026-01-01T14:00:00Z", conclusion: "success"};
  const laterFailure = {...base, id: 201, run_started_at: "2026-01-01T11:00:00Z", updated_at: "2026-01-01T12:00:00Z", conclusion: "failure"};
  for (const runs of [[slowSuccess, laterFailure], [laterFailure, slowSuccess]]) {
    assert.equal(pipeline.newestAuthoritativeCiRun(runs, binding), laterFailure);
    assert.deepEqual(pipeline.authoritativeCiRunCandidates(runs, binding), []);
  }
  // A later rerun of the older run is newest even with the lower run ID.
  const rerun = {...slowSuccess, run_attempt: 2, run_started_at: "2026-01-01T15:00:00Z", updated_at: "2026-01-01T16:00:00Z"};
  assert.equal(pipeline.newestAuthoritativeCiRun([laterFailure, rerun], binding), rerun);
  for (const drift of [
    {run_started_at: undefined}, {run_started_at: "bad"}, {created_at: undefined},
    {created_at: "2026-01-02T00:00:00Z"}, {updated_at: "2025-12-31T00:00:00Z"},
    {run_attempt: 2, run_started_at: base.created_at},
    {status: "queued"}, {status: "in_progress"},
  ]) {
    assert.equal(pipeline.newestAuthoritativeCiRun([laterFailure, {...rerun, ...drift}], binding), null);
  }
  assert.equal(pipeline.newestAuthoritativeCiRun([rerun, {...rerun, id: 300}], binding), null, "equal start times are ambiguous");
  assert.equal(pipeline.newestAuthoritativeCiRun([rerun, {...rerun, run_attempt: 3}], binding), null, "duplicate IDs are ambiguous");
});

function exactCiWorkflowScript(file, job) {
  const workflow = fs.readFileSync(".github/workflows/" + file + ".yml", "utf8");
  const section = workflow.slice(workflow.indexOf("  " + job + ":"));
  const match = section.match(/          script: \|\n((?:            [^\n]*(?:\n|$)|\n)+)/);
  assert.ok(match, "production script " + file + "/" + job);
  return match[1].split("\n").map(line => line.slice(12)).join("\n").replace(/\$\{\{[^}]+\}\}/g, "");
}

function exactCiWorkflowFixture() {
  const a = require("./agent-autonomy.cjs"), headSha = "a".repeat(40), baseSha = "b".repeat(40);
  const trusted = body => ({user: {login: "github-actions[bot]"}, body});
  const link = trusted("<!-- agent-link:v1 repo=o/r issue=142 pr=143 -->");
  const issue = {title: "Exact CI", body: "Authorized scope", state: "open", labels: ["type:implementation", "agent:pr", "priority:high"]};
  const specHash = a.issueSpecHash(issue);
  const auth = () => trusted(a.authorizationMarker({repo: "o/r", issueNumber: 142, specHash: a.issueSpecHash(issue), actor: "maintainer", runId: 7}));
  const review = (result = "PASS") => trusted("<!-- agent-codex-review:v3 repo=o/r issue=142 pr=143 sha=" + headSha + " spec=" + specHash + " ci=10 attempt=1 result=" + result + " -->");
  const state = {
    issue, pr: {state: "open", draft: false, body: "Agent-Issue: #142", labels: ["agent:pr", "priority:high"], head: {sha: headSha, repo: {full_name: "o/r"}}, base: {ref: "main", sha: baseSha}},
    run: {...CI_META, head_repository:{full_name:"o/r"}, id: 10, name: "CI", event: "pull_request", head_sha: headSha, pull_requests: [{number: 143}], status: "completed", conclusion: "success", html_url: "https://example.test/ci/10"},
    jobs: agentConfig.requiredCiJobs.map(name => ({name, run_attempt: 1, conclusion: "success"})),
    prComments: [link, review()], issueComments: [link, auth()],
    behind: 0, defaultBranch: "main", writes: [], failures: [], outputs: {}, notices: [], auth, review, headSha, baseSha, specHash,
    onWrite() {}, onPaginate() {}, beforeLabelWrite() {},
  };
  const data = value => ({data: structuredClone(value)});
  const github = {rest: {
    pulls: {get: async () => data(state.pr), listFiles() {}},
    issues: {
      get: async args => { assert.equal(args.issue_number, 142); return data(state.issue); }, listComments() {},
      setLabels: async args => {
        (args.issue_number === 143 ? state.pr : state.issue).labels = [...args.labels];
        state.writes.push({kind: "labels", ...args}); state.onWrite(state);
      },
      addLabels: async args => {
        state.beforeLabelWrite(state);
        const item = args.issue_number === 143 ? state.pr : state.issue;
        item.labels = [...new Set([...item.labels, ...args.labels])];
        state.writes.push({kind: "add", ...args}); state.onWrite(state);
      },
      removeLabel: async args => {
        state.beforeLabelWrite(state);
        const item = args.issue_number === 143 ? state.pr : state.issue;
        item.labels = item.labels.filter(label => label !== args.name);
        state.writes.push({kind: "remove", ...args}); state.onWrite(state);
      },
      createComment: async args => {
        state.prComments.push(trusted(args.body)); state.writes.push({kind: "comment", ...args}); state.onWrite(state);
      },
    },
    repos: {
      get: async () => data({full_name: "o/r", default_branch: state.defaultBranch}),
      getBranch: async () => data({commit: {sha: baseSha}}),
      compareCommits: async () => data({behind_by: state.behind}),
    },
    actions: {
      getWorkflow: async args => data(args.workflow_id===900 ? {id:900,name:"Agent Codex review",path:".github/workflows/agent-codex-review.yml",state:"active"} : {id:CI_BINDING.workflowId,name:"CI",path:CI_BINDING.workflowPath,state:"active"}),
      getWorkflowRun: async args => data(args.run_id===90 ? {id:90,run_attempt:4,workflow_id:900,path:".github/workflows/agent-codex-review.yml",head_sha:baseSha} : state.run), listWorkflowRunsForRepo() {}, listJobsForWorkflowRun() {},
    },
  }};
  github.paginate = async (route, args) => {
    let value;
    if (route === github.rest.actions.listWorkflowRunsForRepo) value = [state.run];
    else if (route === github.rest.actions.listJobsForWorkflowRun) value = state.jobs;
    else if (route === github.rest.pulls.listFiles) value = [{filename: ".github/scripts/agent-pipeline.cjs"}];
    else if (route === github.rest.issues.listComments) value = args.issue_number === 143 ? state.prComments : state.issueComments;
    else throw new Error("Unexpected paginated route");
    const snapshot = structuredClone(value);
    state.onPaginate(state, route, args);
    return snapshot;
  };
  const core = {notice: message => state.notices.push(message), setFailed: message => state.failures.push(message), setOutput: (name, value) => { state.outputs[name] = value; }};
  const context = {repo: {owner: "o", repo: "r"}, eventName: "workflow_dispatch", runId:90, workflow:"Agent Codex review", payload: {repository: {default_branch: "main"}, workflow_run: structuredClone(state.run)}};
  const modelResult = {reviewed_sha: headSha, reviewed_ci_run_id: 10, reviewed_ci_run_attempt: 1, result: "PASS", findings: [], issue_scope_consistent: true, test_or_governance_weakened: false, paper_only_live_trading_safe: true, summary: "No findings."};
  const files = {};
  const bindings={repository:"o/r",issueNumber:142,prNumber:143,headSha,baseSha,sourceSha:baseSha,specHash,reviewerRunId:90,reviewerRunAttempt:4,ciRunId:10,ciRunAttempt:1};
  const evidence={...bindings,reviewer:{runId:90,runAttempt:4,workflowId:900,path:".github/workflows/agent-codex-review.yml",sourceSha:baseSha},ci:{runId:10,runAttempt:1,workflowId:CI_BINDING.workflowId,path:CI_BINDING.workflowPath}};
  const seal={schema:"agent-review-seal-v1",bindings,files:{"proof":"c6451ff698b457ed6251b37b42831f495b6efc179a539cd074799b0aa0bb2bb9"}};
  seal.files.proof=require("crypto").createHash("sha256").update("trusted governance").digest("hex");
  const requireMock = name => name === "child_process" ? {execFileSync:()=>baseSha} : name === "fs" ? {readFileSync: file => file.endsWith("review.json") ? JSON.stringify(modelResult) : file.endsWith("seal.json") ? JSON.stringify(seal) : file.endsWith("ci-evidence.json") ? JSON.stringify(evidence) : "trusted governance", writeFileSync: (file, content) => { files[file] = JSON.parse(content); }} : name.endsWith("agent-pipeline.cjs") ? pipeline : name.endsWith("agent-autonomy.cjs") ? a : name.endsWith("agent-pipeline.json") ? agentConfig : require(name);
  const processMock = {env: {PR: "143", ISSUE: "142", SHA: headSha, BASE: baseSha, SPEC: specHash, CI_RUN_ID: "10", CI_RUN_ATTEMPT: "1", CI_RUN:"10", CI_ATTEMPT:"1", SOURCE:baseSha, REVIEW_RUN:"90", REVIEW_ATTEMPT:"4", GITHUB_RUN_ATTEMPT:"4", GITHUB_WORKFLOW_SHA:baseSha, REQUEST_PR: "143", REQUEST_SHA: headSha, RUNNER_TEMP: "/review"}};
  const execute = async (file, job) => new AsyncFunction("require", "github", "context", "core", "process", exactCiWorkflowScript(file, job))(requireMock, github, context, core, processMock);
  return {state, github, execute, modelResult, files};
}

test("review preparation emits its exact CI binding in outputs and scope", async () => {
  const f = exactCiWorkflowFixture();
  await f.execute("agent-codex-review", "prepare");
  assert.deepEqual(f.state.failures, []);
  assert.equal(f.state.outputs.eligible, "true");
  assert.equal(f.state.outputs.ci_run_id, 10);
  assert.equal(f.state.outputs.ci_run_attempt, 1);
  assert.equal(f.files["/review/authorized-scope.json"].ciRunId, 10);
  assert.equal(f.files["/review/authorized-scope.json"].ciRunAttempt, 1);
  const workflow = reviewerWorkflow();
  const schema = JSON.parse(workflow.match(/output-schema: >-\n\s+(\{[^\n]+\})/)[1]);
  for (const field of ["reviewed_ci_run_id", "reviewed_ci_run_attempt"]) {
    assert.ok(schema.required.includes(field));
    assert.equal(schema.properties[field].type, "integer");
  }
});

test("review recorder never rebinds old model output to a replacement CI run or attempt", async () => {
  const control = exactCiWorkflowFixture();
  control.state.prComments.pop();
  await control.execute("agent-codex-review", "trusted-record");
  assert.deepEqual(control.state.failures, []);
  assert.equal(control.state.outputs.pass, "true");
  assert.match(control.state.writes[0].body, /ci=10 attempt=1 result=PASS/);
  for (const drift of [{id: 11}, {run_attempt: 2}]) {
    const f = exactCiWorkflowFixture();
    f.state.prComments.pop();
    Object.assign(f.state.run, drift);
    await f.execute("agent-codex-review", "trusted-record");
    assert.deepEqual(f.state.writes, []);
    assert.deepEqual(f.state.failures, ["NO_WRITE: prepared CI binding changed during review"]);
  }
  for (const drift of [{reviewed_ci_run_id: 11}, {reviewed_ci_run_attempt: 2}, {reviewed_ci_run_id: undefined}]) {
    const f = exactCiWorkflowFixture();
    f.state.prComments.pop();
    Object.assign(f.modelResult, drift);
    await f.execute("agent-codex-review", "trusted-record");
    assert.deepEqual(f.state.writes, []);
    assert.deepEqual(f.state.failures, ["NO_WRITE: reviewer output differs from prepared CI binding"]);
  }
});

test("verifier validates the complete authority at every label and marker boundary", async () => {
  const drifts = {
    escalation: s => { s.issue.labels = ["type:implementation", "agent:needs-human"]; },
    lifecycle: s => { s.pr.labels = ["agent:running"]; },
    issueClosed: s => { s.issue.state = "closed"; },
    prClosed: s => { s.pr.state = "closed"; },
    draft: s => { s.pr.draft = true; },
    base: s => { s.pr.base.ref = "other"; },
    repository: s => { s.pr.head.repo.full_name = "other/repo"; },
    defaultBranch: s => { s.defaultBranch = "other"; },
    head: s => { s.pr.head.sha = "c".repeat(40); },
    behind: s => { s.behind = 1; },
    authorization: s => { s.issue.body += " changed"; },
    authorizedSpec: s => { s.issue.body += " reauthorized"; s.issueComments[1] = s.auth(); },
    linkage: s => { s.prComments = s.prComments.filter(c => !c.body.includes("agent-link:")); },
    rebind: s => { s.pr.body = "Agent-Issue: #144"; },
    reviewMissing: s => { s.prComments = s.prComments.filter(c => !c.body.includes("agent-codex-review:")); },
    reviewConflict: s => { s.prComments.push(s.review("BLOCK")); },
    ciRerun: s => { s.run.run_attempt = 2; },
    ciReplacement: s => { s.run.id = 11; },
    ciFailure: s => { s.run.conclusion = "failure"; },
    jobFailure: s => { s.jobs[0].conclusion = "failure"; },
  };
  for (const [name, drift] of Object.entries(drifts)) {
    // A body rebind must stop before fetching or writing the unrelated Issue.
    for (const boundary of [1, 2, 3, 4]) {
      const f = exactCiWorkflowFixture();
      f.state.onWrite = s => { if (s.writes.length === boundary) drift(s); };
      if (name === "rebind") {
        const get = f.github.rest.issues.get;
        f.github.rest.issues.get = async args => args.issue_number === 144 ? {data: structuredClone(f.state.issue)} : get(args);
      }
      await f.execute("agent-verify", "verify");
      assert.equal(f.state.writes.length, boundary, name + " after write " + boundary);
      assert.equal(f.state.outputs.verified, undefined, name);
      assert.equal(f.state.failures.length, 1, name);
      if (name === "escalation") assert.ok(f.state.issue.labels.includes("agent:needs-human"));
    }
  }
  for (const partial of [false, true]) {
    const f = exactCiWorkflowFixture();
    if (partial) f.state.pr.labels = ["agent:verified", "priority:high"];
    await f.execute("agent-verify", "verify");
    assert.deepEqual(f.state.failures, []);
    assert.equal(f.state.outputs.verified, "true");
    assert.equal(f.state.writes.length, partial ? 3 : 5);
    assert.ok(f.state.pr.labels.includes("priority:high"));
    assert.ok(f.state.issue.labels.includes("priority:high"));
    assert.match(f.state.writes.at(-1).body, /agent-verified:v2 .* ci=10 attempt=1/);
  }
});

test("verifier sees escalation injected during comment pagination before its first label write", async () => {
  const f = exactCiWorkflowFixture();
  let ciRead = false;
  f.state.onPaginate = (s, route, args) => {
    if (route === f.github.rest.actions.listWorkflowRunsForRepo) ciRead = true;
    if (ciRead && route === f.github.rest.issues.listComments && args.issue_number === 142) s.issue.labels = ["type:implementation", "agent:needs-human"];
  };
  await f.execute("agent-verify", "verify");
  assert.deepEqual(f.state.writes, []);
  assert.deepEqual(f.state.failures, ["VERIFICATION_TOCTOU_REJECTED"]);
});

test("exact-CI changes preserve default-branch failure classification", () => {
  for (const diagnostic of ["EAI_AGAIN", "ECONNRESET", "ETIMEDOUT"]) {
    assert.equal(pipeline.normalizedFailureClass({name: "security", steps: [], conclusion: "failure"}, diagnostic), "security");
    assert.equal(pipeline.normalizedFailureClass({name: "security", steps: [], conclusion: "failure"}, "\b" + diagnostic + "\b"), "infra-transient");
  }
  assert.equal(pipeline.normalizedFailureClass({name: "api", steps: [], conclusion: "failure"}), "unknown");
  assert.equal(pipeline.normalizedFailureClass({name: "\bapi\b", steps: [], conclusion: "failure"}), "api-test");
});

test("verifier rejects a rerun that starts during its final comment pagination", async () => {
  const f = exactCiWorkflowFixture();
  let ciRead = false;
  f.state.onPaginate = (s, route, args) => {
    if (route === f.github.rest.actions.listWorkflowRunsForRepo) ciRead = true;
    if (ciRead && route === f.github.rest.issues.listComments && args.issue_number === 142) s.run.run_attempt = 2;
  };
  await f.execute("agent-verify", "verify");
  assert.deepEqual(f.state.writes, []);
  assert.deepEqual(f.state.failures, ["VERIFICATION_TOCTOU_REJECTED"]);
});

test("verification label requests cannot erase escalation added after the final read", async () => {
  for (const boundary of [0, 1, 2, 3]) {
    const f = exactCiWorkflowFixture();
    f.state.beforeLabelWrite = s => {
      if (s.writes.length === boundary) {
        s.issue.labels.push("agent:needs-human", "triage:concurrent");
      }
    };
    await f.execute("agent-verify", "verify");
    assert.ok(f.state.issue.labels.includes("agent:needs-human"), "boundary " + boundary);
    assert.ok(f.state.issue.labels.includes("triage:concurrent"), "boundary " + boundary);
    assert.equal(f.state.outputs.verified, undefined);
    assert.equal(f.state.writes.length, boundary + 1);
    assert.equal(f.state.failures.length, 1);
  }
});


test("Issue #128 reviewer evidence is source-bound, bounded, sealed, and credential-isolated",()=>{
  const workflow=reviewerWorkflow(),prepare=workflow.slice(workflow.indexOf("  prepare:"),workflow.indexOf("  governance-escalation:")),collect=workflow.slice(workflow.indexOf("  collect-review-evidence:"),workflow.indexOf("  independent-review:")),model=workflow.slice(workflow.indexOf("  independent-review:"),workflow.indexOf("  trusted-record:"));
  assert.match(prepare,/main\.commit\.sha!==sourceSha[\s\S]*pr\.base\.sha!==sourceSha[\s\S]*triggering CI is no longer newest/);
  assert.match(collect,/permissions: \{actions: read, contents: read, issues: read, pull-requests: read\}[\s\S]*EVIDENCE_FAIL_CLOSED[\s\S]*65536[\s\S]*196608[\s\S]*agent-review-seal-v1/);
  assert.match(model,/Verify immutable evidence and prompt binding[\s\S]*SEALED_INPUT_BINDING_INVALID[\s\S]*Independent bounded review[\s\S]*Post-model freshness and sealed-input validation[\s\S]*POST_MODEL_SOURCE_SCOPE_AUTHORIZATION_OR_CI_DRIFT/);
  assert.doesNotMatch(collect,/OPENAI_API_KEY|AGENT_PUBLISH_TOKEN/);
  assert.doesNotMatch(model,/AGENT_PUBLISH_TOKEN|issues: write|pull-requests: write/);
  assert.match(model,/Materialize isolated trusted reviewer source[\s\S]*git worktree add --detach[\s\S]*Pre-model trusted-source and exact-CI freshness validation/);
  for(const phase of ["PRE_MODEL","POST_MODEL"]){
    assert.match(model,new RegExp(`${phase}_TRUSTED_SOURCE_INVALID[\\s\\S]*path\\.join\\(root,'\\.github/scripts/agent-pipeline\\.cjs'\\)[\\s\\S]*getWorkflowRun[\\s\\S]*finalRuns[\\s\\S]*successfulRequiredJobs`));
  }
});

test("Issue #128 review artifacts are attempt-bound across every upload and download",()=>{
  const workflow=reviewerWorkflow(),references=[...workflow.matchAll(/name: '((?:review-scope|sealed-review-input|codex-review)-[^']+)'/g)].map(match=>match[1]);
  assert.equal(references.length,7,"all review artifact uploads and downloads are covered");
  assert.ok(references.every(name=>name.endsWith("${{ github.run_id }}-${{ github.run_attempt }}")));
  for(const [prefix,count] of [["review-scope-",2],["sealed-review-input-",3],["codex-review-",2]]){const names=references.filter(name=>name.startsWith(prefix));assert.equal(names.length,count);assert.equal(new Set(names).size,1);}
  assert.doesNotMatch(workflow,/name: '(?:review-scope|sealed-review-input|codex-review)-\$\{\{ github\.run_id \}\}'/);
});

test("Issue #128 post-model program cannot execute a candidate helper initializer",async()=>{
  const workflow=reviewerWorkflow(),model=workflow.slice(workflow.indexOf("  independent-review:"),workflow.indexOf("  trusted-record:"));
  const step=model.slice(model.indexOf("      - name: Post-model freshness"));
  const script=step.slice(step.indexOf("          script: |")+20).split("\n").filter(line=>line.startsWith("            ")).map(line=>line.slice(12)).join("\n");
  const initialization=script.split("\n").slice(0,3).join("\n");
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),"review-source-isolation-")),candidate=path.join(dir,"candidate"),trusted=path.join(dir,"trusted"),review=path.join(dir,"review.json");
  for(const root of [candidate,trusted])fs.mkdirSync(path.join(root,".github","scripts"),{recursive:true});
  fs.mkdirSync(path.join(candidate,".codex-input"));
  fs.writeFileSync(path.join(candidate,".codex-input","seal.json"),JSON.stringify({files:{}}));
  fs.writeFileSync(review,JSON.stringify({result:"BLOCK",summary:"real model output"}));
  const attack=`require("node:fs").writeFileSync(${JSON.stringify(review)},JSON.stringify({result:"PASS",summary:"forged"}));module.exports={};`;
  for(const name of ["agent-pipeline.cjs","agent-autonomy.cjs"])fs.writeFileSync(path.join(candidate,".github","scripts",name),attack);
  fs.writeFileSync(path.join(candidate,".github","agent-pipeline.json"),"{}");
  for(const name of ["agent-pipeline.cjs","agent-autonomy.cjs"])fs.writeFileSync(path.join(trusted,".github","scripts",name),"module.exports={trusted:true};");
  fs.writeFileSync(path.join(trusted,".github","agent-pipeline.json"),"{}");
  execFileSync("git",["init","-q"],{cwd:trusted});execFileSync("git",["add","."],{cwd:trusted});execFileSync("git",["-c","user.name=test","-c","user.email=test@example.invalid","commit","-qm","trusted"],{cwd:trusted});
  const source=execFileSync("git",["rev-parse","HEAD"],{cwd:trusted,encoding:"utf8"}).trim(),processMock={env:{...process.env,TRUSTED_SOURCE:trusted,SOURCE:source}},previous=process.cwd();
  try{
    process.chdir(candidate);
    await new AsyncFunction("require","process",initialization)(require,processMock);
    assert.deepEqual(JSON.parse(fs.readFileSync(review,"utf8")),{result:"BLOCK",summary:"real model output"});
  }finally{process.chdir(previous);fs.rmSync(dir,{recursive:true,force:true});}
});

test("Issue #128 executes the production seal verifier and rejects binding or evidence tampering",()=>{
  const workflow=reviewerWorkflow(),model=workflow.slice(workflow.indexOf("  independent-review:"),workflow.indexOf("  trusted-record:"));
  const source=model.slice(model.indexOf("          node - <<'NODE'")+25,model.indexOf("          NODE")).split("\n").map(x=>x.startsWith("          ")?x.slice(10):x).join("\n");
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),"review-seal-")),input=path.join(dir,".codex-input");fs.mkdirSync(input);
  const files={"authorized-scope.json":"scope","ci-evidence.json":"evidence","review-prompt.md":"prompt","trusted-governance.json":"governance"},crypto=require("crypto"),hash=x=>crypto.createHash("sha256").update(x).digest("hex");for(const [name,body] of Object.entries(files))fs.writeFileSync(path.join(input,name),body);
  const env={...process.env,GITHUB_REPOSITORY:"o/r",ISSUE:"128",PR:"135",HEAD_SHA:"a".repeat(40),BASE_SHA:"b".repeat(40),SOURCE_SHA:"b".repeat(40),SPEC:"spec",REVIEW_RUN:"90",REVIEW_ATTEMPT:"4",CI_RUN:"7",CI_ATTEMPT:"2",OPENAI_API_KEY:"must-not-leak"};
  const bindings={repository:"o/r",issueNumber:128,prNumber:135,headSha:env.HEAD_SHA,baseSha:env.BASE_SHA,sourceSha:env.SOURCE_SHA,specHash:"spec",reviewerRunId:90,reviewerRunAttempt:4,ciRunId:7,ciRunAttempt:2};fs.writeFileSync(path.join(input,"seal.json"),JSON.stringify({schema:"agent-review-seal-v1",bindings,files:Object.fromEntries(Object.entries(files).map(([n,b])=>[n,hash(b)]))}));
  let run=spawnSync(process.execPath,["-e",source],{cwd:dir,env,encoding:"utf8"});assert.equal(run.status,0,run.stderr);assert.doesNotMatch(run.stdout+run.stderr,/must-not-leak/);
  fs.appendFileSync(path.join(input,"ci-evidence.json"),"tamper");run=spawnSync(process.execPath,["-e",source],{cwd:dir,env,encoding:"utf8"});assert.notEqual(run.status,0);assert.match(run.stderr,/SEALED_INPUT_BINDING_INVALID/);
  fs.writeFileSync(path.join(input,"ci-evidence.json"),files["ci-evidence.json"]);env.REVIEW_ATTEMPT="5";run=spawnSync(process.execPath,["-e",source],{cwd:dir,env,encoding:"utf8"});assert.notEqual(run.status,0);assert.match(run.stderr,/SEALED_INPUT_BINDING_INVALID/);
  env.REVIEW_ATTEMPT="4";env.CI_ATTEMPT="3";run=spawnSync(process.execPath,["-e",source],{cwd:dir,env,encoding:"utf8"});assert.notEqual(run.status,0);assert.match(run.stderr,/SEALED_INPUT_BINDING_INVALID/);fs.rmSync(dir,{recursive:true,force:true});
});

test("Issue #128 final trusted record binds source, authorization, newest CI, jobs, and sealed evidence",()=>{
  const record=reviewerWorkflow().slice(reviewerWorkflow().indexOf("  trusted-record:"),reviewerWorkflow().indexOf("  verify-after-pass:"));
  for(const proof of [/sealed evidence binding changed/,/reviewerRunAttempt:Number\(process\.env\.REVIEW_ATTEMPT\)/,/reviewRun\.run_attempt===Number\(process\.env\.REVIEW_ATTEMPT\)/,/reviewRun\.run_attempt===Number\(process\.env\.GITHUB_RUN_ATTEMPT\)/,/evidence\.reviewer\?\.runAttempt===reviewRun\.run_attempt/,/writeReviewRun\.run_attempt===Number\(process\.env\.REVIEW_ATTEMPT\)/,/writeReviewRun\.run_attempt===Number\(process\.env\.GITHUB_RUN_ATTEMPT\)/,/writeMain\.commit\.sha===process\.env\.SOURCE/,/writeAuth\.specHash===process\.env\.SPEC/,/writeCi\.id===Number\(process\.env\.CI_RUN\)/,/successfulRequiredJobs\(writeJobs/,/final authority changed at review record boundary/])assert.match(record,proof);
  assert.doesNotMatch(record,/context\.runAttempt/);
  assert.ok(record.indexOf("sealed evidence binding changed")<record.indexOf("issues.createComment"));
});

function issue128GithubScript(stepName){
  const workflow=reviewerWorkflow(),model=workflow.slice(workflow.indexOf("  independent-review:"),workflow.indexOf("  trusted-record:"));
  const tail=model.slice(model.indexOf(`      - name: ${stepName}`)),step=tail.slice(0,tail.indexOf("\n      - ",1));
  return step.slice(step.indexOf("          script: |")+20).split("\n").filter(line=>line.startsWith("            ")).map(line=>line.slice(12)).join("\n");
}

function issue128CollectorProgram(){
  const workflow=reviewerWorkflow(),section=workflow.slice(workflow.indexOf("  collect-review-evidence:"),workflow.indexOf("  independent-review:"));
  const tail=section.slice(section.indexOf("      - name: Collect and seal exact CI evidence and prompt"));
  return tail.slice(tail.indexOf("          script: |")+20).split("\n").filter(line=>line.startsWith("            ")).map(line=>line.slice(12)).join("\n");
}

async function executeIssue128Collector(change={}){
  const source=execFileSync("git",["rev-parse","HEAD"],{encoding:"utf8"}).trim(),head="a".repeat(40),base="b".repeat(40),secret="mutation-token-must-not-leak";
  const ci={id:70,...CI_META,workflow_id:CI_BINDING.workflowId,path:CI_BINDING.workflowPath,name:"CI",event:"pull_request",head_sha:head,head_repository:{full_name:"o/r"},pull_requests:[{number:135}],run_attempt:2,status:"completed",conclusion:"success"};
  const reviewer={id:90,run_attempt:4,workflow_id:900,path:".github/workflows/agent-codex-review.yml",head_sha:source};
  let runLists=0;
  const actions={listWorkflowRunsForRepo(){},listJobsForWorkflowRun(){},getWorkflowRun:async({run_id})=>{
    if(change.apiFailure)throw Object.assign(Error("HTTP 403"),{status:403});
    if(run_id===90)return {data:{...reviewer,...change.reviewerRun}};
    return {data:{...ci,...change.freshCi}};
  },getWorkflow:async({workflow_id})=>({data:workflow_id===900?{id:900,name:"Agent Codex review",path:".github/workflows/agent-codex-review.yml",...change.reviewerWorkflow}:{id:workflow_id,name:"CI",path:".github/workflows/ci.yml",...change.ciWorkflow}})};
  const github={rest:{actions,repos:{get:async()=>({data:{full_name:change.wrongRepository?"attacker/r":"o/r",default_branch:"main"}}),getBranch:async()=>({data:{commit:{sha:change.wrongMain?"c".repeat(40):source}}})},pulls:{get:async()=>({data:{state:"open",draft:false,base:{ref:"main",sha:change.wrongBase?"c".repeat(40):base},head:{sha:change.wrongHead?"c".repeat(40):head,repo:{full_name:"o/r"}},body:"Agent-Issue: #128",labels:["agent:pr"]}})},issues:{listComments(){},get:async()=>({data:{state:"open",title:"t",body:"b",labels:["type:implementation","agent:pr"]}})}},paginate:async route=>{
    if(route===actions.listWorkflowRunsForRepo){runLists++;if(runLists>1&&change.newerRun)return [{...ci,id:71,run_attempt:1},ci];if(runLists>1&&change.newerAttempt)return [{...ci,run_attempt:3},ci];return [ci];}
    if(route===actions.listJobsForWorkflowRun)return change.missingJob?[]:[{id:1,name:"required",status:"completed",conclusion:change.failingJob?"failure":"success",run_attempt:2,head_sha:head}];
    return [];
  }};
  const p={newestAuthoritativeCiRun:pipeline.newestAuthoritativeCiRun,parseAgentIssue:()=>change.wrongIssue?127:128,isImplementation:()=>true,lifecycleAtAgentPr:()=>({ok:true}),fullLinkageDecision:()=>({ok:!change.linkageDrift}),authoritativeCiIdentity:run=>run.status==="completed"&&run.conclusion==="success"&&run.head_sha===head&&run.pull_requests?.[0]?.number===135,successfulRequiredJobs:jobs=>jobs.length===1&&jobs[0].conclusion==="success"};
  const a={authorizationDecision:()=>({ok:!change.authDrift,specHash:change.wrongSpec?"other":"spec"})},c={requiredCiJobs:["required"]};
  const requireMock=id=>id.includes("agent-pipeline.cjs")?p:id.includes("agent-autonomy.cjs")?a:id.includes("agent-pipeline.json")?c:require(id);
  const context={repo:{owner:"o",repo:"r"},runId:90,workflow:"Agent Codex review",payload:{repository:{default_branch:"main"}}};
  const env={...process.env,RUNNER_TEMP:"",PR:change.wrongPr?"136":"135",ISSUE:change.wrongIssueEnv?"127":"128",HEAD:head,BASE:base,SOURCE:change.wrongSource?"d".repeat(40):source,SPEC:"spec",REVIEW_RUN:change.wrongReviewerRun?"91":"90",REVIEW_ATTEMPT:change.wrongReviewerAttempt?"3":"4",CI_RUN:change.wrongRun?"71":"70",CI_ATTEMPT:change.wrongAttempt?"1":"2",GITHUB_WORKFLOW_SHA:source,GITHUB_RUN_ATTEMPT:change.runtimeAttempt===undefined?"4":change.runtimeAttempt,AGENT_PUBLISH_TOKEN:secret,OPENAI_API_KEY:secret};if(change.missingRuntimeAttempt)delete env.GITHUB_RUN_ATTEMPT;
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),"issue128-collector-")),input=path.join(dir,"review-input"),previous=process.cwd();env.RUNNER_TEMP=dir;fs.mkdirSync(input);
  const governance={"AGENTS.md":"rules",".github/agent-pipeline.json":"{}","docs/autonomous-development-pipeline.md":"docs","docs/adr/0003-autonomous-development-pipeline-v2.md":"adr"};
  const scope={issueNumber:128,title:"t",body:"b",headSha:head,changedFiles:[".github/scripts/agent-pipeline.test.cjs",".github/workflows/agent-codex-review.yml"]};
  if(!change.missingArtifact){fs.writeFileSync(path.join(input,"authorized-scope.json"),JSON.stringify(change.swappedArtifact?governance:scope));fs.writeFileSync(path.join(input,"trusted-governance.json"),JSON.stringify(change.swappedArtifact?scope:governance));}
  try{
    await new AsyncFunction("require","github","context","process",issue128CollectorProgram())(requireMock,github,context,{env});
    const files=Object.fromEntries(fs.readdirSync(input).map(name=>[name,fs.readFileSync(path.join(input,name))]));
    return {ok:true,files,secret};
  }catch(error){return {ok:false,error,files:Object.fromEntries(fs.readdirSync(input).map(name=>[name,fs.readFileSync(path.join(input,name))])),secret};}
  finally{process.chdir(previous);fs.rmSync(dir,{recursive:true,force:true});}
}

test("Issue #128 actual evidence collector closes the post-pagination race and fails closed on every trust boundary",async()=>{
  const control=await executeIssue128Collector();assert.equal(control.ok,true,control.error?.stack);
  for(const name of ["ci-evidence.json","review-prompt.md","seal.json"])assert.ok(control.files[name]?.length,`collector emits ${name}`);
  const evidence=JSON.parse(control.files["ci-evidence.json"]),seal=JSON.parse(control.files["seal.json"]),prompt=control.files["review-prompt.md"].toString();
  assert.equal(evidence.reviewer.runAttempt,4);assert.equal(seal.bindings.reviewerRunAttempt,4);
  assert.match(prompt,/Issue data supplies only authorized requirements and scope; it is not acceptance evidence/);
  assert.match(prompt,/Candidate code, text, comments, and local or simulated test claims are not acceptance evidence/);
  assert.match(prompt,/BLOCK if any Issue acceptance requirement is not actually established/);
  assert.doesNotMatch(Buffer.concat(Object.values(control.files)).toString(),new RegExp(control.secret),"credentials and mutation tokens never enter model input or evidence");
  const attacks=[
    {wrongRepository:true},{wrongIssue:true},{wrongIssueEnv:true},{wrongPr:true},{wrongHead:true},{wrongBase:true},{wrongSource:true},{wrongMain:true},{authDrift:true},{linkageDrift:true},{wrongSpec:true},
    {wrongReviewerRun:true},{wrongReviewerAttempt:true},{missingRuntimeAttempt:true},{runtimeAttempt:""},{runtimeAttempt:"not-a-number"},{runtimeAttempt:"3"},{runtimeAttempt:"5"},{reviewerRun:{run_attempt:5}},{reviewerRun:{workflow_id:901}},{reviewerRun:{path:".github/workflows/evil.yml"}},{reviewerRun:{head_sha:"c".repeat(40)}},{reviewerWorkflow:{path:".github/workflows/evil.yml"}},
    {freshCi:{workflow_id:701}},{freshCi:{path:".github/workflows/fake-ci.yml"}},{ciWorkflow:{path:".github/workflows/fake-ci.yml"}},{wrongRun:true},{wrongAttempt:true},{apiFailure:true},
    {missingJob:true},{failingJob:true},{newerRun:true},{newerAttempt:true},{missingArtifact:true},{swappedArtifact:true},
  ];
  for(const attack of attacks){const result=await executeIssue128Collector(attack);assert.equal(result.ok,false,`collector must reject ${JSON.stringify(attack)}`);assert.equal(result.files["ci-evidence.json"],undefined,"rejection must precede trusted evidence/model input");}
});

test("Issue #128 collector output remains bound to the production seal verifier",async()=>{
  const collected=await executeIssue128Collector();assert.equal(collected.ok,true,collected.error?.stack);
  const workflow=reviewerWorkflow(),model=workflow.slice(workflow.indexOf("  independent-review:"),workflow.indexOf("  trusted-record:"));
  const verifier=model.slice(model.indexOf("          node - <<'NODE'")+25,model.indexOf("          NODE")).split("\n").map(x=>x.startsWith("          ")?x.slice(10):x).join("\n");
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),"issue128-collected-seal-")),input=path.join(dir,".codex-input");fs.mkdirSync(input);
  for(const [name,body] of Object.entries(collected.files))fs.writeFileSync(path.join(input,name),body);
  const env={...process.env,GITHUB_REPOSITORY:"o/r",ISSUE:"128",PR:"135",HEAD_SHA:"a".repeat(40),BASE_SHA:"b".repeat(40),SOURCE_SHA:execFileSync("git",["rev-parse","HEAD"],{encoding:"utf8"}).trim(),SPEC:"spec",REVIEW_RUN:"90",REVIEW_ATTEMPT:"4",CI_RUN:"70",CI_ATTEMPT:"2"};
  let run=spawnSync(process.execPath,["-e",verifier],{cwd:dir,env,encoding:"utf8"});assert.equal(run.status,0,run.stderr);
  fs.appendFileSync(path.join(input,"review-prompt.md"),"tampered prompt");run=spawnSync(process.execPath,["-e",verifier],{cwd:dir,env,encoding:"utf8"});assert.notEqual(run.status,0);assert.match(run.stderr,/SEALED_INPUT_BINDING_INVALID/);
  fs.writeFileSync(path.join(input,"review-prompt.md"),collected.files["review-prompt.md"]);fs.appendFileSync(path.join(input,"ci-evidence.json"),"tampered evidence");run=spawnSync(process.execPath,["-e",verifier],{cwd:dir,env,encoding:"utf8"});assert.notEqual(run.status,0);assert.match(run.stderr,/SEALED_INPUT_BINDING_INVALID/);fs.rmSync(dir,{recursive:true,force:true});
});

async function executeIssue128FreshnessProgram(stepName,change={}){
  const root=process.cwd(),program=issue128GithubScript(stepName),source=execFileSync("git",["rev-parse","HEAD"],{encoding:"utf8"}).trim(),head="a".repeat(40),base="b".repeat(40),writes=[];
  const ci={id:70,...CI_META,workflow_id:CI_BINDING.workflowId,path:CI_BINDING.workflowPath,name:"CI",event:"pull_request",head_sha:head,head_repository:{full_name:"o/r"},pull_requests:[{number:135}],run_attempt:2,status:"completed",conclusion:"success"};
  const reviewer={id:90,run_attempt:4,workflow_id:900,path:".github/workflows/agent-codex-review.yml",head_sha:source};
  let runLists=0;
  const actions={listWorkflowRunsForRepo(){},listJobsForWorkflowRun(){},getWorkflowRun:async({run_id})=>{if(change.apiFailure)throw Error("HTTP 503");if(run_id===90)return {data:{...reviewer,...change.reviewerRun}};return {data:{...ci,...change.freshCi}};},getWorkflow:async({workflow_id})=>({data:workflow_id===900?{id:900,name:"Agent Codex review",path:".github/workflows/agent-codex-review.yml",...change.reviewerWorkflow}:{id:CI_BINDING.workflowId,name:"CI",path:".github/workflows/ci.yml",...change.ciWorkflow}})};
  const github={rest:{actions,repos:{get:async()=>({data:{full_name:"o/r",default_branch:"main"}}),getBranch:async()=>({data:{commit:{sha:source}}})},pulls:{get:async()=>({data:{state:change.prClosed?"closed":"open",draft:false,base:{ref:"main",sha:base},head:{sha:head,repo:{full_name:"o/r"}},body:"Agent-Issue: #128",labels:["agent:pr"]}})},issues:{listComments(){},get:async()=>({data:{state:"open",title:"t",body:"b",labels:["type:implementation","agent:pr"]}}),createComment:async x=>writes.push(x)}},paginate:async(route)=>{if(route===actions.listWorkflowRunsForRepo){runLists++;if(change.newerRun&&runLists>1)return [{...ci,id:71,run_attempt:3},ci];return [ci];}if(route===actions.listJobsForWorkflowRun)return change.badJobs?[]:[{id:1,name:"required",status:"completed",conclusion:"success",run_attempt:2}];return []}};
  const p={newestAuthoritativeCiRun:pipeline.newestAuthoritativeCiRun,parseAgentIssue:()=>128,isImplementation:()=>true,lifecycleAtAgentPr:()=>({ok:true}),fullLinkageDecision:()=>({ok:!change.linkageDrift}),authoritativeCiIdentity:run=>run.status==="completed"&&run.conclusion==="success",successfulRequiredJobs:jobs=>!change.badJobs&&jobs.length===1},a={authorizationDecision:()=>({ok:!change.authDrift,specHash:"spec"})},c={requiredCiJobs:["required"]};
  const requireMock=id=>id.includes("agent-pipeline.cjs")?p:id.includes("agent-autonomy.cjs")?a:id.includes("agent-pipeline.json")?c:require(id);
  const context={repo:{owner:"o",repo:"r"},runId:90,workflow:"Agent Codex review",payload:{repository:{default_branch:"main"}}};
  const env={...process.env,TRUSTED_SOURCE:root,PR:"135",ISSUE:"128",HEAD:head,BASE:base,SOURCE:source,SPEC:"spec",REVIEW_RUN:change.wrongReviewerRun?"91":"90",REVIEW_ATTEMPT:change.wrongReviewerAttempt?"3":"4",CI_RUN:"70",CI_ATTEMPT:change.wrongAttempt?"1":"2",GITHUB_WORKFLOW_SHA:change.workflowSha||source,GITHUB_RUN_ATTEMPT:change.runtimeAttempt===undefined?"4":change.runtimeAttempt,RUNNER_TEMP:os.tmpdir()};if(change.missingRuntimeAttempt)delete env.GITHUB_RUN_ATTEMPT;
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),"issue128-freshness-")),previous=process.cwd();
  try{
    fs.mkdirSync(path.join(dir,".codex-input"));
    const body=change.tamperedEvidence?"changed":"sealed",digest=require("crypto").createHash("sha256").update("sealed").digest("hex");
    fs.writeFileSync(path.join(dir,".codex-input","evidence"),body);fs.writeFileSync(path.join(dir,".codex-input","seal.json"),JSON.stringify({files:{evidence:digest}}));
    process.chdir(dir);
    await new AsyncFunction("require","github","context","process",program)(requireMock,github,context,{env});
    return {ok:true,writes};
  }catch(error){return {ok:false,error,writes};}finally{process.chdir(previous);fs.rmSync(dir,{recursive:true,force:true});}
}

test("Issue #128 actual pre/post freshness programs fail closed on provenance, API, CI, and authority drift",async()=>{
  for(const step of ["Pre-model trusted-source and exact-CI freshness validation","Post-model freshness and sealed-input validation"]){
    {const control=await executeIssue128FreshnessProgram(step);assert.equal(control.ok,true,`${step} unchanged control: ${control.error?.stack}`);}
    const attacks=[
      {wrongReviewerRun:true},{wrongReviewerAttempt:true},{missingRuntimeAttempt:true},{runtimeAttempt:""},{runtimeAttempt:"not-a-number"},{runtimeAttempt:"3"},{runtimeAttempt:"5"},{reviewerRun:{run_attempt:5}},{reviewerWorkflow:{path:".github/workflows/evil.yml"}},{reviewerWorkflow:{name:"Candidate review"}},{reviewerRun:{head_sha:"c".repeat(40)}},{workflowSha:"d".repeat(40)},
      {ciWorkflow:{path:".github/workflows/fake-ci.yml"}},{ciWorkflow:{name:"Not CI"}},{freshCi:{path:".github/workflows/fake-ci.yml"}},{newerRun:true},{wrongAttempt:true},{freshCi:{run_attempt:3}},
      {apiFailure:true},{badJobs:true},{prClosed:true},{authDrift:true},{linkageDrift:true},...(step.startsWith("Post")?[{tamperedEvidence:true}]:[]),
    ];
    for(const attack of attacks){const result=await executeIssue128FreshnessProgram(step,attack);assert.equal(result.ok,false,`${step} must reject ${JSON.stringify(attack)}`);assert.deepEqual(result.writes,[],"rejection performs no trusted write or handoff");}
  }
});

// Conflicting current model evidence must remain visible to downstream readers.
test("review recorder records a conflicting BLOCK and stops downstream verification", async () => {
  const f=exactCiWorkflowFixture();
  f.modelResult.result="BLOCK";
  f.modelResult.findings=[{severity:"high",location:"workflow",message:"conflicting result"}];
  await f.execute("agent-codex-review","trusted-record");
  assert.equal(f.state.writes.length,1);
  assert.match(f.state.writes[0].body,/result=BLOCK/);
  assert.equal(f.state.outputs.pass,undefined);
  assert.equal(f.state.outputs.blocked,"true");
  assert.equal(pipeline.independentReviewSatisfied(f.state.prComments,{repo:"o/r",issueNumber:142,prNumber:143,headSha:f.state.headSha,specHash:f.state.specHash,ciRunId:10,ciRunAttempt:1}),false);
});

test("privileged reviewer jobs check out the GitHub-owned workflow SHA",()=>{
  const workflow=reviewerWorkflow();
  for(const name of ["collect-review-evidence","trusted-record"]){
    const section=workflow.slice(workflow.indexOf(`  ${name}:`)).split(/\n  [a-z][a-z-]+:/)[0];
    assert.match(section,/ref: '\$\{\{ github\.workflow_sha \}\}'/);
    assert.doesNotMatch(section,/ref: '\$\{\{ needs\.prepare\.outputs\.(source_sha|head_sha) \}\}'/);
  }
});


test("duplicate CI notifications cannot cancel an active reviewer or admit push CI", () => {
  const workflow = fs.readFileSync(".github/workflows/agent-codex-review.yml", "utf8");
  const concurrency = workflow.split("concurrency:\n")[1].split("permissions:")[0];
  assert.match(concurrency, /cancel-in-progress: false/);
  const condition = workflow.split("  prepare:\n    if: ")[1].split("\n")[0];
  const evaluate = new Function("github", "return " + condition.replaceAll(" == ", " === "));
  for (const event of ["push", "workflow_dispatch", "schedule"]) {
    assert.equal(Boolean(evaluate({event_name:"workflow_run",event:{workflow_run:{event,conclusion:"success",pull_requests:[{number:149}]}}})), false);
  }
  assert.equal(Boolean(evaluate({event_name:"workflow_run",event:{workflow_run:{event:"pull_request",conclusion:"success",pull_requests:[{number:149}]}}})), true);
  assert.equal(Boolean(evaluate({event_name:"workflow_dispatch",event:{}})), true);
});
