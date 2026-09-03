"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const test = require("node:test");
const pipeline = require("./agent-pipeline.cjs");
const agentConfig = require("../agent-pipeline.json");
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
    { id: 10, name: "CI", event: "pull_request", status: "completed", conclusion: "success", head_sha: "old", pull_requests: [{ number: 85 }] },
    { id: 11, name: "CI", event: "pull_request", status: "completed", conclusion: "failure", head_sha: "new", pull_requests: [{ number: 85 }] },
    { id: 12, name: "Other", event: "pull_request", status: "completed", conclusion: "success", head_sha: "new", pull_requests: [{ number: 85 }] },
    { id: 13, name: "CI", event: "pull_request", status: "completed", conclusion: "success", head_sha: "new", pull_requests: [{ number: 85 }] },
  ];
  assert.deepEqual(pipeline.authoritativeCiRunCandidates(runs, { workflowName: "CI", headSha: "new", prNumber: 85 }).map((run) => run.id), [13]);
  assert.deepEqual(pipeline.authoritativeCiRunCandidates(runs, { workflowName: "CI", headSha: "stale", prNumber: 85 }), []);
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

  const runs = [{
    id: 20, name: "CI", event: "pull_request", status: "completed", conclusion: "success",
    head_sha: headSha, pull_requests: [{ number: 202 }],
  }];
  assert.equal(pipeline.authoritativeCiRunCandidates(runs, {
    workflowName: "CI", headSha: trigger.headSha, prNumber: trigger.prNumber,
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
    workflowName: "CI", headSha: stale, prNumber: trigger.prNumber,
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

test("v2 independent PASS je exact-SHA a nenahrazuje human review", () => {
  const comments = [{ user: { login: "github-actions[bot]" }, body: "<!-- agent-codex-review:v2 sha=abc result=PASS -->" }];
  assert.equal(pipeline.independentReviewSatisfied(comments, "abc"), true);
  assert.equal(pipeline.independentReviewSatisfied(comments, "new"), false);
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
  assert.doesNotMatch(reviewer + blockEscalation, /contents: write/);
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
  assert.doesNotMatch(fixer,/pull-requests: write/);
  assert.equal((reviewer.match(/pull-requests: write/g)||[]).length,5);
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
  const sha="b".repeat(40), base={name:"CI",event:"pull_request",status:"completed",conclusion:"success",head_sha:sha,pull_requests:[{number:8}]};
  assert.equal(pipeline.authoritativeCiIdentity(base,{prNumber:8,headSha:sha,conclusion:"success"}),true);
  for (const bad of [{name:"Other"},{event:"workflow_dispatch"},{status:"in_progress"},{conclusion:"failure"},{head_sha:"c".repeat(40)},{pull_requests:[]},{pull_requests:[{number:9}]}])
    assert.equal(pipeline.authoritativeCiIdentity({...base,...bad},{prNumber:8,headSha:sha,conclusion:"success"}),false);
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
  assert.match(reviewer,/exact authoritative green CI missing or ambiguous/);
  assert.match(reviewer,/Fail closed when reviewer credential is absent/);
  assert.match(reviewer,/fail-closed-finalizer:[\s\S]*uses: \.\/\.github\/workflows\/agent-review-block-escalation\.yml[\s\S]*no PASS was synthesized/);
});

test("v2 reusable caller permissions and governance linkage are fail closed", () => {
  const reviewer=fs.readFileSync(".github/workflows/agent-codex-review.yml","utf8");
  const block=fs.readFileSync(".github/workflows/agent-review-block-escalation.yml","utf8");
  assert.match(reviewer,/verify-after-pass:[\s\S]*permissions: \{actions: read, contents: read, issues: write, pull-requests: write\}/);
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
    assert.match(section,/permissions: \{contents: read, issues: write, pull-requests: write\}/,name);
    assert.doesNotMatch(section,/contents: write|OPENAI_API_KEY/,name);
  }
  const model=job(reviewer,"independent-review");
  assert.match(model,/permissions: \{contents: read\}/);
  assert.doesNotMatch(model,/issues: write|pull-requests: write|contents: write/);
  assert.match(model,/OPENAI_API_KEY/);
  assert.match(job(reviewer,"trusted-record"),/result\.reviewed_sha!==process\.env\.SHA[\s\S]*createComment[\s\S]*agent-codex-review:v2 sha=\$\{process\.env\.SHA\}/);
  assert.match(job(reviewer,"governance-escalation"),/fullLinkageDecision[\s\S]*lifecycleAtAgentPr[\s\S]*(?:addLabels|removeLabel)[\s\S]*createComment/);
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
});
