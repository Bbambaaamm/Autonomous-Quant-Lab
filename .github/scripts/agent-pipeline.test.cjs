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
});

test("PR linkage je právě jeden samostatný Agent-Issue marker", () => {
  assert.equal(pipeline.parseAgentIssue("x\n- Agent-Issue: #82\ny"), 82);
  assert.equal(pipeline.parseAgentIssue("Agent-Issue: #82\nAgent-Issue: #83"), null);
  assert.equal(pipeline.parseAgentIssue("Closes #82"), null);
});

test("verified vyžaduje přesný SHA, ready PR, review, stav a kompletní CI", () => {
  const valid = { workflowName: "CI", workflowConclusion: "success", headSha: "abc", prHeadSha: "abc", open: true, correctBase: true, draft: false, reviewSatisfied: true, issueIsImplementation: true, statesReconciliable: true, needsHuman: false, requiredJobsSuccessful: true };
  assert.deepEqual(pipeline.verificationDecision(valid), { ok: true });
  for (const change of [{ draft: true }, { prHeadSha: "old" }, { reviewSatisfied: false }, { needsHuman: true }, { requiredJobsSuccessful: false }]) {
    assert.equal(pipeline.verificationDecision({ ...valid, ...change }).ok, false);
  }
});

test("exact-SHA review acknowledgement je explicitní a nový commit jej invaliduje", () => {
  const comments = [{ user: { login: "github-actions[bot]" }, body: "Review acknowledged\n<!-- agent-review-ack:v1 sha=abc -->" }];
  assert.equal(pipeline.reviewSatisfied({ reviewDecision: null, acknowledgements: comments, headSha: "abc" }), true);
  assert.equal(pipeline.reviewSatisfied({ reviewDecision: null, acknowledgements: comments, headSha: "new" }), false);
  assert.equal(pipeline.reviewSatisfied({ reviewDecision: "APPROVED", acknowledgements: [], headSha: "abc" }), true);
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

test("konfigurované required joby přesně odpovídají autoritativnímu CI", () => {
  const config = require("../agent-pipeline.json");
  const ci = fs.readFileSync(`${__dirname}/../workflows/ci.yml`, "utf8");
  const jobBlock = ci.split(/^jobs:\s*$/m)[1];
  const jobNames = [...jobBlock.matchAll(/^  ([a-z][a-z0-9-]*):\s*$/gm)].map((match) => match[1]);
  assert.deepEqual(config.requiredCiJobs, jobNames);
});
