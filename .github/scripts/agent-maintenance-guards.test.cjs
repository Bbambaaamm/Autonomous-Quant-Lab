"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const {rulesetDecision, reviewDecision, bindingDecision, guardedSequence} = require("./agent-maintenance-guards.cjs");
const clone = (value) => structuredClone(value);
const expected = {
  repo: "owner/repo", defaultBranch: "main", issueNumber: 123, prNumber: 125,
  headSha: "a".repeat(40), baseSha: "b".repeat(40), specHash: "c".repeat(64),
  requiredChecks: ["api", "quality", "security", "unit-research", "integration-postgres", "frontend", "container-build", "production-smoke", "agent-verified-gate"]
    .map((context) => ({context, integration_id: 15368})),
};
const ruleset = {
  name: "Protect main", target: "branch", enforcement: "active", bypass_actors: [],
  conditions: {ref_name: {include: ["refs/heads/main"], exclude: []}},
  rules: ["pull_request", "deletion", "non_fast_forward"].map((type) => ({type})).concat([
    {type: "required_status_checks", parameters: {strict_required_status_checks_policy: true, required_status_checks: clone(expected.requiredChecks)}}]),
};
const valid = () => ({
  pr: {number: 125, state: "open", draft: false, labels: ["agent:needs-human", "priority:high"],
    head: {sha: expected.headSha, repo: {full_name: expected.repo}},
    base: {ref: "main", sha: expected.baseSha, repo: {full_name: expected.repo}}},
  issue: {number: 123, state: "open", labels: ["type:implementation", "agent:needs-human"]},
  authorization: {ok: true, specHash: expected.specHash}, requesterPermission: "admin",
  currentMainSha: expected.baseSha, behindBy: 0, markerIssueNumber: 123,
  linksConflict: false, fullLinkageValid: true, fileScopeValid: true, ruleset: clone(ruleset),
  ci: {repo: expected.repo, headSha: expected.headSha, prNumber: 125, event: "pull_request", name: "CI",
    status: "completed", conclusion: "success", isNewest: true, requiredJobsSuccessful: true, runId: 9, runAttempt: 1},
});
const review = {reviewed_sha: expected.headSha, result: "PASS", findings: [], issue_scope_consistent: true,
  test_or_governance_weakened: false, paper_only_live_trading_safe: true, summary: "Independent review result."};

test("accepts complete current evidence without mutating inputs", () => {
  const snapshot = valid(), before = clone(snapshot);
  assert.deepEqual(bindingDecision(snapshot, expected), {ok: true});
  assert.deepEqual(snapshot, before);
  assert.deepEqual(reviewDecision(review, expected.headSha), {ok: true});
});
const mutations = [
  ["new head", s => {s.pr.head.sha = "d".repeat(40);}],
  ["closed PR", s => {s.pr.state = "closed";}],
  ["Draft PR", s => {s.pr.draft = true;}],
  ["fork head", s => {s.pr.head.repo.full_name = "attacker/repo";}],
  ["other base", s => {s.pr.base.ref = "dev";}],
  ["closed Issue", s => {s.issue.state = "closed";}],
  ["PR masquerading as Issue", s => {s.issue.pull_request = {};}],
  ["conflicting classification", s => {s.issue.labels.push("type:epic");}],
  ["stale authorization", s => {s.authorization.specHash = "d".repeat(64);}],
  ["missing authorization", s => {s.authorization = undefined;}],
  ["revoked maintainer", s => {s.requesterPermission = "read";}],
  ["moved main", s => {s.currentMainSha = "d".repeat(40);}],
  ["behind main", s => {s.behindBy = 1;}],
  ["wrong Issue marker", s => {s.markerIssueNumber = 124;}],
  ["conflicting durable link", s => {s.linksConflict = true;}],
  ["missing durable link", s => {s.fullLinkageValid = false;}],
  ["conflicting state labels", s => {s.pr.labels.push("agent:verified");}],
  ["missing state", s => {s.pr.labels = [];}],
  ["malformed labels", s => {s.issue.labels.push({});}],
  ["old green CI", s => {s.ci.isNewest = false;}],
  ["newer CI in progress", s => {s.ci.status = "in_progress";}],
  ["newer failed CI", s => {s.ci.conclusion = "failure";}],
  ["missing required CI job", s => {s.ci.requiredJobsSuccessful = false;}],
  ["different CI SHA", s => {s.ci.headSha = "d".repeat(40);}],
  ["different CI repository", s => {s.ci.repo = "attacker/repo";}],
  ["different CI PR", s => {s.ci.prNumber = 124;}],
  ["CI not pull_request", s => {s.ci.event = "push";}],
  ["invalid attempt", s => {s.ci.runAttempt = 0;}],
  ["scope expansion", s => {s.fileScopeValid = false;}],
  ["missing bypass_actors", s => {delete s.ruleset.bypass_actors;}],
  ["null bypass_actors", s => {s.ruleset.bypass_actors = null;}],
  ["actual bypass", s => {s.ruleset.bypass_actors = [{actor_id: 1}];}],
  ["disabled protection", s => {s.ruleset.enforcement = "disabled";}],
  ["non-strict protection", s => {s.ruleset.rules[3].parameters.strict_required_status_checks_policy = false;}],
  ["lost security check", s => {s.ruleset.rules[3].parameters.required_status_checks = s.ruleset.rules[3].parameters.required_status_checks.filter(c => c.context !== "security");}],
  ["wrong gate App", s => {s.ruleset.rules[3].parameters.required_status_checks.at(-1).integration_id = 999;}],
  ["duplicate gate", s => {s.ruleset.rules[3].parameters.required_status_checks.push(clone(expected.requiredChecks.at(-1)));}],
  ["unprotected branch", s => {s.ruleset.conditions.ref_name.include = ["refs/heads/dev"];}],
  ["excluded main", s => {s.ruleset.conditions.ref_name.exclude = ["refs/heads/main"];}],
  ["missing PR requirement", s => {s.ruleset.rules = s.ruleset.rules.filter(r => r.type !== "pull_request");}],
];
for (const [name, mutate] of mutations) {
  test(`rejects ${name} before initial write and between writes`, async () => {
    const changed = valid(); mutate(changed);
    assert.equal(bindingDecision(changed, expected).ok, false);
    for (const failureAt of [1, 2]) {
      let reads = 0; const writes = [];
      await assert.rejects(guardedSequence({snapshot: async () => ++reads === failureAt ? changed : valid(),
        validate: s => bindingDecision(s, expected), operations: [async () => writes.push("issue"), async () => writes.push("pr")]}));
      assert.deepEqual(writes, failureAt === 1 ? [] : ["issue"]);
    }
  });
}

test("both partial recovery orderings are valid; foreign labels survive validation", () => {
  for (const side of ["pr", "issue"]) {
    const s = valid(); s[side].labels = s[side].labels.map(x => x === "agent:needs-human" ? "agent:pr" : x);
    assert.equal(bindingDecision(s, expected).ok, true);
    assert.ok(s.pr.labels.includes("priority:high"));
  }
});
test("partial links are allowed only during explicitly selected linking stage", () => {
  const s = valid(); s.fullLinkageValid = false;
  assert.equal(bindingDecision(s, expected).ok, false);
  assert.equal(bindingDecision(s, expected, {allowPartialLinks: true}).ok, true);
  s.linksConflict = true;
  assert.equal(bindingDecision(s, expected, {allowPartialLinks: true}).ok, false);
});
test("missing bypass evidence is UNKNOWN, never no-bypass", () => {
  const r = clone(ruleset); delete r.bypass_actors;
  assert.deepEqual(rulesetDecision(r, expected), {ok: false, reason: "RULESET_BYPASS_UNKNOWN"});
});
test("unrelated additional required checks are preserved", () => {
  const r = clone(ruleset); r.rules[3].parameters.required_status_checks.push({context: "extra", integration_id: 99});
  const before = clone(r);
  assert.equal(rulesetDecision(r, expected).ok, true); assert.deepEqual(r, before);
});
test("invalid trusted expectations cannot silently erase the required check set", () => {
  for (const bad of [[], [{context: "api", integration_id: 15368}], [...expected.requiredChecks, expected.requiredChecks[0]],
    expected.requiredChecks.map(c => ({...c, integration_id: null}))]) {
    assert.equal(rulesetDecision(ruleset, {...expected, requiredChecks: bad}).ok, false);
  }
});
for (const [name, mutate] of [
  ["BLOCK", r => {r.result = "BLOCK";}], ["stale SHA", r => {r.reviewed_sha = "d".repeat(40);}],
  ["findings", r => {r.findings.push({severity: "high"});}], ["weakening", r => {r.test_or_governance_weakened = true;}],
  ["scope drift", r => {r.issue_scope_consistent = false;}], ["unsafe trading", r => {r.paper_only_live_trading_safe = false;}],
  ["missing field", r => {delete r.summary;}], ["unknown field", r => {r.approved = true;}],
  ["truthy string", r => {r.issue_scope_consistent = "true";}], ["oversize", r => {r.summary = "x".repeat(4097);}]
]) test(`review rejects ${name}`, () => {const r = clone(review); mutate(r); assert.equal(reviewDecision(r, expected.headSha).ok, false);});
test("API failures stop writes, including after the first successful write", async () => {
  for (const failureAt of [1, 2]) {
    let reads = 0; const writes = [];
    await assert.rejects(guardedSequence({snapshot: async () => {if (++reads === failureAt) throw new Error("HTTP_403"); return valid();},
      validate: s => bindingDecision(s, expected), operations: [async () => writes.push(1), async () => writes.push(2)]}), /HTTP_403/);
    assert.deepEqual(writes, failureAt === 1 ? [] : [1]);
  }
});
test("successful sequence fetches a new snapshot before every operation and at the end", async () => {
  let count = 0; const snapshots = [];
  await guardedSequence({snapshot: async () => ({...valid(), sequence: ++count}), validate: s => bindingDecision(s, expected),
    operations: [async s => snapshots.push(s.sequence), async s => snapshots.push(s.sequence)]});
  assert.deepEqual(snapshots, [1, 2]); assert.equal(count, 3);
});
test("postcondition failure is reported, never hidden or silently rolled back", async () => {
  let count = 0; const writes = [];
  await assert.rejects(guardedSequence({snapshot: async () => {const s = valid(); if (++count === 3) s.pr.head.sha = "d".repeat(40); return s;},
    validate: s => bindingDecision(s, expected), operations: [async () => writes.push(1), async () => writes.push(2)]}), /PR_BINDING_CHANGED/);
  assert.deepEqual(writes, [1, 2]);
});
