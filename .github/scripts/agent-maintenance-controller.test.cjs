"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const {collectSnapshot, controller, requiredCiEvidence} = require("./agent-maintenance-controller.cjs");

const expected = {repo: "owner/repo", defaultBranch: "main", issueNumber: 126, prNumber: 127,
  headSha: "a".repeat(40), baseSha: "b".repeat(40), specHash: "c".repeat(64), requester: "alice",
  authorization: {commentId: 1, actor: "alice", runId: 77, specHash: "c".repeat(64)},
  requestRunId: 88, requestRunAttempt: 3, requiredJobNames: ["agent-pipeline"],
  requiredChecks: [{context: "agent-verified-gate", integration_id: 15368}]};
const ruleset = {name: "Protect main", target: "branch", enforcement: "active", bypass_actors: [],
  conditions: {ref_name: {include: ["refs/heads/main"], exclude: []}}, rules: ["pull_request", "deletion", "non_fast_forward"]
    .map((type) => ({type})).concat([{type: "required_status_checks", parameters: {strict_required_status_checks_policy: true,
      required_status_checks: expected.requiredChecks}}])};

test("newest CI and required jobs are derived from API objects, never caller booleans", () => {
  const runs = [{id: 8, name: "CI", event: "pull_request", head_sha: expected.headSha,
    pull_requests: [{number: 127}], status: "completed", conclusion: "success", run_attempt: 1},
  {id: 9, name: "CI", event: "pull_request", head_sha: expected.headSha,
    pull_requests: [{number: 127}], status: "in_progress", conclusion: null, run_attempt: 2}];
  const evidence = requiredCiEvidence(runs, new Map([[9, []]]), expected, {successfulRequiredJobs: () => false});
  assert.equal(evidence.runId, 9); assert.equal(evidence.status, "in_progress");
  assert.equal(evidence.requiredJobsSuccessful, false);
});

function apiFixture() {
  const calls = [], labels = ["type:implementation", "agent:pr"];
  const data = {
    pr: {number: 127, state: "open", draft: false, auto_merge: null, changed_files: 1, body: "Agent-Issue: #126", labels: ["agent:pr"],
      head: {sha: expected.headSha, repo: {full_name: expected.repo}}, base: {ref: "main", sha: expected.baseSha, repo: {full_name: expected.repo}}},
    issue: {number: 126, state: "open", title: "repair", body: "spec", labels},
  };
  const endpoint = (name, value) => async (args) => {calls.push([name, args]); return {data: structuredClone(value)};};
  const github = {rest: {pulls: {get: endpoint("pulls.get", data.pr), listFiles: "listFiles"},
    issues: {get: endpoint("issues.get", data.issue), listComments: "listComments"},
    repos: {getCollaboratorPermissionLevel: endpoint("permission", {permission: "write"}),
      getBranch: endpoint("branch", {commit: {sha: expected.baseSha}}), compareCommits: endpoint("compare", {behind_by: 0})},
    actions: {listWorkflowRunsForRepo: "runs", listJobsForWorkflowRun: "jobs",
      getWorkflowRun: endpoint("origin", {id: 88, run_attempt: 3, actor: {login: "alice"},
        path: ".github/workflows/agent-control-plane-remediation-request.yml", head_repository: {full_name: expected.repo}, head_branch: "main"})}},
    paginate: async (method) => {calls.push([method]); if(method === "listFiles") return [{filename: ".github/scripts/agent-x.cjs", status: "modified"}];
      if(method === "runs") return [{id: 9, run_attempt: 1, name: "CI", event: "pull_request", head_sha: expected.headSha,
        pull_requests: [{number: 127}], status: "completed", conclusion: "success"}];
      if(method === "jobs") return [{name: "agent-pipeline", conclusion: "success", head_sha: expected.headSha}]; return [];}};
  const pipeline = {parseAgentIssue: () => 126, durablePrLinkDecision: () => ({ok: true, prNumber: 127}),
    durableIssueLinkDecision: () => ({ok: true, issueNumber: 126}), fullLinkageDecision: () => ({ok: true}),
    successfulRequiredJobs: () => true};
  const autonomy = {authorizationDecision: () => ({ok: true, ...expected.authorization})};
  return {github, context: {repo: {owner: "owner", repo: "repo"}}, expected, pipeline, autonomy,
    rulesetAudit: {repo: expected.repo, requestRunId: 88, requestRunAttempt: 3, defaultBranch: "main", ruleset}, calls, data};
}

test("collector obtains identity, authorization, links, files, ancestry and CI from GitHub", async () => {
  const fixture = apiFixture(); const snapshot = await collectSnapshot(fixture);
  assert.equal(snapshot.requestOrigin.actor, "alice"); assert.equal(snapshot.requesterPermission, "write");
  assert.equal(snapshot.ci.runId, 9); assert.equal(snapshot.fileScopeValid, true);
  assert.ok(fixture.calls.some(([name]) => name === "origin"));
});

test("controller re-fetches the whole API snapshot and prevents a second mutation after drift", async () => {
  const fixture = apiFixture(); let reads = 0; const writes = [];
  const c = controller(fixture); c.snapshot = undefined;
  await assert.rejects(controller({...fixture, github: {...fixture.github, rest: {...fixture.github.rest,
    pulls: {...fixture.github.rest.pulls, get: async (args) => {reads++; const value = structuredClone(fixture.data.pr);
      if(reads > 1) value.head.sha = "d".repeat(40); return {data: value};}}}}}).guarded([
        async () => writes.push("first"), async () => writes.push("second")]), /PR_BINDING_CHANGED|RULESET/);
  assert.deepEqual(writes, ["first"]);
});
