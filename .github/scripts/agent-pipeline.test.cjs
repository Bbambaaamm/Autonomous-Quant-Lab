"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const pipeline = require("./agent-pipeline.cjs");

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
  const valid = { workflowName: "CI", workflowConclusion: "success", headSha: "abc", prHeadSha: "abc", open: true, correctBase: true, draft: false, reviewSatisfied: true, issueIsImplementation: true, statesReconciliable: true, needsHuman: false, requiredJobsSuccessful: true };
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
    open: true, correctBase: true, draft: false, reviewSatisfied: true,
    issueIsImplementation: true, statesReconciliable: true, needsHuman: false,
    requiredJobsSuccessful: true,
  }), { ok: true });

  const stale = "b".repeat(40);
  assert.equal(pipeline.authoritativeCiRunCandidates(runs, {
    workflowName: "CI", headSha: stale, prNumber: trigger.prNumber,
  }).length, 0);
  assert.equal(pipeline.verificationDecision({
    workflowName: "CI", workflowConclusion: "success", headSha, prHeadSha: stale,
    open: true, correctBase: true, draft: false, reviewSatisfied: true,
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
  assert.equal(pipeline.verificationDecision({ workflowName: "CI", workflowConclusion: "success", headSha: "abc", prHeadSha: "abc", open: true, correctBase: true, draft: false, reviewSatisfied: true, issueIsImplementation: true, statesReconciliable: true, needsHuman: false, requiredJobsSuccessful: true }).ok, true);
  assert.equal(pipeline.stateMutationPlan(["agent:needs-human"], "agent:pr", "agent:verified").ok, false);
});

test("CI joby musí všechny uspět na ověřovaném SHA", () => {
  const jobs = [{ name: "quality", conclusion: "success", head_sha: "abc", run_attempt: 1 }];
  assert.equal(pipeline.successfulRequiredJobs(jobs, ["quality"], "abc"), true);
  assert.equal(pipeline.successfulRequiredJobs(jobs, ["quality", "api"], "abc"), false);
  assert.equal(pipeline.successfulRequiredJobs(jobs, ["quality"], "other"), false);
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
