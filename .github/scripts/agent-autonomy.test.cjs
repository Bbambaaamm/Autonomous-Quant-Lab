"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const a = require("./agent-autonomy.cjs");

const repo = "Bbambaaamm/Autonomous-Quant-Lab";
const labels = ["type:implementation", "agent:running"];
const title = "Implement safe feature";
const body = "Exact approved scope";
const spec = a.issueSpecHash({ title, body, labels });
const authMarker = a.authorizationMarker({ repo, issueNumber: 42, specHash: spec, actor: "Bbambaaamm", runId: 123 });
const authComments = [{ id: 9, user: { login: "github-actions[bot]" }, body: `Authorized\n${authMarker}` }];

test("authorization binds title, body and concrete implementation classification", () => {
  const ok = a.authorizationDecision({ comments: authComments, repo, issueNumber: 42, title, body, labels });
  assert.equal(ok.ok, true);
  assert.equal(ok.specHash, spec);
  assert.equal(a.authorizationDecision({ comments: authComments, repo, issueNumber: 42, title, body: `${body}!`, labels }).reason, "AUTHORIZATION_STALE");
  assert.equal(a.authorizationDecision({ comments: authComments, repo, issueNumber: 42, title, body, labels: ["type:epic", "agent:running"] }).reason, "ISSUE_NOT_IMPLEMENTATION");
});

test("missing and ambiguous authorizations fail closed", () => {
  assert.equal(a.authorizationDecision({ comments: [], repo, issueNumber: 42, title, body, labels }).reason, "AUTHORIZATION_MISSING");
  assert.equal(a.authorizationDecision({ comments: [...authComments, ...authComments.map(x => ({...x, id: 10}))], repo, issueNumber: 42, title, body, labels }).reason, "AUTHORIZATION_AMBIGUOUS");
});

test("verification requires exact current evidence and no human escalation", () => {
  const base = {
    authorizationCurrent: true,
    issueOpen: true,
    issueIsImplementation: true,
    linkageValid: true,
    open: true,
    correctBase: true,
    draft: false,
    exactHead: true,
    lifecycleValid: true,
    needsHuman: false,
    currentMain: true,
    requiredCiSuccessful: true,
    independentReviewPass: true,
  };
  assert.deepEqual(a.verificationDecision(base), { ok: true });
  assert.equal(a.verificationDecision({ ...base, requiredCiSuccessful: false }).reason, "REQUIRED_CI_MISSING");
  assert.equal(a.verificationDecision({ ...base, independentReviewPass: false }).reason, "INDEPENDENT_REVIEW_PASS_MISSING");
  assert.equal(a.verificationDecision({ ...base, currentMain: false }).reason, "PR_BEHIND_MAIN");
  assert.equal(a.verificationDecision({ ...base, needsHuman: true }).reason, "NEEDS_HUMAN_PRESENT");
  assert.equal(a.verificationDecision({ ...base, exactHead: false }).reason, "HEAD_SHA_MISMATCH");
});

test("new SHA cannot reuse old verification or gate evidence", () => {
  const marker = a.verificationMarker({ repo, issueNumber: 42, prNumber: 77, headSha: "a".repeat(40), specHash: spec, ciRunId: 999 });
  const comments = [{ user: { login: "github-actions[bot]" }, body: marker }];
  assert.equal(a.exactVerificationEvidence(comments, { repo, issueNumber: 42, prNumber: 77, headSha: "a".repeat(40), specHash: spec }), true);
  assert.equal(a.exactVerificationEvidence(comments, { repo, issueNumber: 42, prNumber: 77, headSha: "b".repeat(40), specHash: spec }), false);
});

test("gate and merge are exact-head fail-closed decisions", () => {
  const gateInput = {
    authorizationCurrent: true,
    issueOpen: true,
    issueIsImplementation: true,
    linkageValid: true,
    open: true,
    correctBase: true,
    draft: false,
    exactHead: true,
    verifiedLifecycle: true,
    needsHuman: false,
    currentMain: true,
    requiredCiSuccessful: true,
    independentReviewPass: true,
    verificationEvidence: true,
  };
  assert.equal(a.gateDecision(gateInput).ok, true);
  assert.equal(a.gateDecision({ ...gateInput, verificationEvidence: false }).reason, "VERIFICATION_EVIDENCE_MISSING");
  assert.equal(a.mergeDecision({ ...gateInput, gateSuccess: true, expectedHeadUnchanged: true }).ok, true);
  assert.equal(a.mergeDecision({ ...gateInput, gateSuccess: true, expectedHeadUnchanged: false }).reason, "MERGE_HEAD_CHANGED");
  assert.equal(a.mergeDecision({ ...gateInput, gateSuccess: false, expectedHeadUnchanged: true }).reason, "VERIFIED_GATE_NOT_SUCCESSFUL");
});

test("verified lifecycle is mutually exclusive and needs-human blocks", () => {
  assert.equal(a.lifecyclePairDecision(["agent:verified"], ["agent:verified"], "agent:verified").ok, true);
  assert.equal(a.lifecyclePairDecision(["agent:verified", "agent:needs-human"], ["agent:verified"], "agent:verified").reason, "NEEDS_HUMAN_PRESENT");
  assert.equal(a.verificationLifecyclePlan(["agent:pr"], ["agent:verified"]).ok, true);
  assert.equal(a.verificationLifecyclePlan(["agent:running"], ["agent:pr"]).reason, "INVALID_VERIFICATION_LIFECYCLE");
});

test("builder cannot mutate governance, credentials, dependencies or trading surfaces", () => {
  for (const path of [
    ".github/workflows/evil.yml",
    "AGENTS.md",
    "docs/ROADMAP.md",
    "backend/alembic/versions/x.py",
    "backend/src/quantlab/security.py",
    "backend/src/quantlab/live_trading.py",
    "frontend/package-lock.json",
    "infra/prod.tf",
  ]) assert.equal(a.builderPathAllowed(path), false, path);
  for (const path of ["backend/src/quantlab/research.py", "backend/tests/test_research.py", "frontend/src/app/page.tsx", "docs/user-guide.md"]) {
    assert.equal(a.builderPathAllowed(path), true, path);
  }
  assert.equal(a.validateBuilderPaths(["backend/src/quantlab/research.py", "backend/tests/test_research.py"]), true);
});

test("builder modes forbid executable creation/deletion and mode transitions", () => {
  assert.equal(a.validateBuilderModes(":100644 100644 aaaaaaa bbbbbbb M\tfile.py"), true);
  assert.equal(a.validateBuilderModes(":000000 100644 0000000 bbbbbbb A\tfile.py"), true);
  assert.equal(a.validateBuilderModes(":100644 000000 aaaaaaa 0000000 D\tfile.py"), true);
  assert.equal(a.validateBuilderModes(":000000 100755 0000000 bbbbbbb A\tscript.sh"), false);
  assert.equal(a.validateBuilderModes(":100755 000000 aaaaaaa 0000000 D\tscript.sh"), false);
  assert.equal(a.validateBuilderModes(":100644 100755 aaaaaaa bbbbbbb M\tfile.py"), false);
});

test("transient CI failures retry only within the bounded budget", () => {
  assert.deepEqual(a.transientCiRetryDecision({ failureClass: "infra-transient", runAttempt: 1, maxRetries: 2 }), { action: "RETRY", nextAttempt: 2 });
  assert.equal(a.transientCiRetryDecision({ failureClass: "infra-transient", runAttempt: 2, maxRetries: 2 }).reason, "TRANSIENT_RETRY_BUDGET_EXHAUSTED");
  assert.equal(a.transientCiRetryDecision({ failureClass: "security", runAttempt: 1, maxRetries: 2 }).action, "NONE");
});


test("closed Issue invalidates authorization",()=>{assert.equal(a.authorizationDecision({comments:authComments,repo,issueNumber:42,title,body,labels,state:"closed"}).reason,"ISSUE_NOT_OPEN");});
test("retired durable link is inactive",()=>{const p=require("./agent-pipeline.cjs"),c=[{user:{login:"github-actions[bot]"},body:"<!-- agent-link:v1 repo=Bbambaaamm/Autonomous-Quant-Lab issue=42 pr=70 -->\n<!-- agent-link-retired:v1 repo=Bbambaaamm/Autonomous-Quant-Lab issue=42 pr=70 -->"}];assert.deepEqual(p.durablePrLinkDecision(c,{owner:"Bbambaaamm",repo:"Autonomous-Quant-Lab",issueNumber:42}),{ok:true,prNumber:null});});
test("newest CI failure defeats older success",()=>{const p=require("./agent-pipeline.cjs"),sha="d".repeat(40),b={name:"CI",event:"pull_request",status:"completed",head_sha:sha,pull_requests:[{number:88}]};assert.equal(p.newestAuthoritativeCiRun([{...b,id:10,conclusion:"success"},{...b,id:11,conclusion:"failure"}],{workflowName:"CI",headSha:sha,prNumber:88}).conclusion,"failure");});
test("npm registry 503 is infra transient",()=>{const p=require("./agent-pipeline.cjs"),sha="e".repeat(40),r=p.classifyCiFailure({jobs:[{id:7,name:"security",conclusion:"failure",steps:[{name:"npm audit",conclusion:"failure"}]}],runHeadSha:sha,expectedHeadSha:sha,sourceRunId:77,runAttempt:1,logExcerpt:"503 Service Unavailable",config:{requiredCiJobs:["security"],failureClassPolicy:{eligible:[],denied:["infra-transient","security"]},protectedDiagnosticPatterns:[]}});assert.equal(r.failureClass,"infra-transient");});
