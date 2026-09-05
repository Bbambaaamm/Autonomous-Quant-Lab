"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
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
    "backend/src/quantlab/phase4.py",
    "backend/src/quantlab/trading.py",
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

test("ordinary security failure log boilerplate is not transient",()=>{const p=require("./agent-pipeline.cjs"),sha="f".repeat(40),r=p.classifyCiFailure({jobs:[{id:8,name:"security",conclusion:"failure",steps:[{name:"npm audit",conclusion:"failure"}]}],runHeadSha:sha,expectedHeadSha:sha,sourceRunId:78,runAttempt:1,logExcerpt:"Current runner version 2.337.0\nDownloading action\nnpm audit found a critical vulnerability",config:{requiredCiJobs:["security"],failureClassPolicy:{eligible:[],denied:["infra-transient","security"]},protectedDiagnosticPatterns:[]}});assert.equal(r.failureClass,"security");});

test("Issue #110 Builder publisher uses isolated one-command Git auth and exact sealed SHA checks", () => {
  const publish = fs.readFileSync(".github/workflows/agent-builder-publish.yml", "utf8");
  const builder = fs.readFileSync(".github/workflows/agent-builder.yml", "utf8");

  assert.doesNotMatch(publish, /gh auth setup-git/);
  assert.match(publish, /AUTH_HEADER="AUTHORIZATION: basic \$\(printf x-access-token:%s "\$GH_TOKEN" \| base64 -w0\)"/);
  assert.match(publish, /remote="\$\(git -c http\.extraheader="\$AUTH_HEADER" ls-remote --refs origin "refs\/heads\/\$branch" \| awk '\{print \$1\}'\)"/);

  const absence = publish.indexOf('test -z "$remote"');
  const push = publish.indexOf('git -c http.extraheader="$AUTH_HEADER" push origin "HEAD:refs/heads/$branch"');
  const postCheck = publish.indexOf('sha="$(git -c http.extraheader="$AUTH_HEADER" ls-remote --refs origin "refs/heads/$branch"');
  const exactSha = publish.indexOf('test "$sha" = "$sealed"', postCheck);
  assert.ok(absence >= 0 && absence < push && push < postCheck && postCheck < exactSha);
  assert.doesNotMatch(publish, /git[^\n]*push[^\n]*(?:--force-with-lease|--force|\s-f(?:\s|$))/);

  const owned = publish.slice(publish.indexOf("owned(){"), publish.indexOf("prs(){"));
  assert.match(owned, /test "\$sha" = "\$sealed"/);
  assert.match(owned, /test "\$p" = "\$BASE"/);
  assert.match(owned, /test "\$t" = "\$expected_tree"/);
  assert.match(owned, /Agent-Builder-Seal: v1 issue=\$ISSUE spec=\$SPEC base=\$BASE tree=\$expected_tree/);

  const section = (name, next) => builder.slice(builder.indexOf(`  ${name}:`), next ? builder.indexOf(`  ${next}:`) : builder.length);
  for (const [name, next] of [["generate", "block"], ["validate", "seal"], ["seal", "publish"]]) {
    const job = section(name, next);
    assert.match(job, /permissions: \{contents: read\}/, name);
    assert.doesNotMatch(job, /AGENT_PUBLISH_TOKEN|GH_TOKEN|contents: write|issues: write|pull-requests: write/, name);
  }
  assert.match(section("publish", "fail-closed-finalizer"), /secrets: \{AGENT_PUBLISH_TOKEN:/);
});

test("Issue #112 verifier keeps metadata on GITHUB_TOKEN and reconciliation on publish token", () => {
  const workflow = fs.readFileSync(".github/workflows/agent-verify.yml", "utf8");
  const verifyJob = workflow.slice(workflow.indexOf("  verify:"), workflow.indexOf("\n  gate:"));
  const gateJob = workflow.slice(workflow.indexOf("\n  gate:"));
  const reconciliationStart = verifyJob.indexOf("if(state.comparison.data.behind_by>0)");
  const metadataStart = verifyJob.indexOf("const runs=", reconciliationStart);
  const reconciliation = verifyJob.slice(reconciliationStart, metadataStart);
  const metadata = verifyJob.slice(metadataStart);

  assert.match(verifyJob, /permissions:\n\s+actions: read\n\s+contents: read\n\s+issues: write\n\s+pull-requests: write/);
  assert.doesNotMatch(verifyJob, /github-token:\s*\$\{\{\s*secrets\.AGENT_PUBLISH_TOKEN/);
  assert.match(verifyJob, /AGENT_PUBLISH_TOKEN:\s*'\$\{\{ secrets\.AGENT_PUBLISH_TOKEN \}\}'/);
  assert.match(reconciliation, /RECONCILIATION_TOKEN_MISSING/);
  assert.match(reconciliation, /authorization:`Bearer \$\{process\.env\.AGENT_PUBLISH_TOKEN\}`/);
  assert.match(reconciliation, /pulls\/\$\{prNumber\}\/update-branch/);
  assert.doesNotMatch(metadata, /process\.env\.AGENT_PUBLISH_TOKEN/);
  assert.match(metadata, /github\.rest\.issues\.setLabels/);
  assert.match(metadata, /github\.rest\.issues\.createComment/);
  assert.match(gateJob, /secrets:\n\s+AGENT_PUBLISH_TOKEN:/);

  const headSha = "a".repeat(40);
  const marker = a.verificationMarker({ repo, issueNumber: 112, prNumber: 114, headSha, specHash: spec, ciRunId: 1234 });
  const args = { repo, issueNumber: 112, prNumber: 114, headSha, specHash: spec };
  assert.equal(a.exactVerificationEvidence([{ user: { login: "github-actions[bot]" }, body: marker }], args), true);
  assert.equal(a.exactVerificationEvidence([{ user: { login: "Bbambaaamm" }, body: marker }], args), false);
});

test("Issue #116 Builder keeps metadata on GITHUB_TOKEN and isolates Draft-to-Ready token", () => {
  const workflow = fs.readFileSync(".github/workflows/agent-builder-publish.yml", "utf8");
  const metadataStart = workflow.indexOf("- name: Link, transition, release Draft and recover completed CI");
  const metadata = workflow.slice(metadataStart);
  const releaseStart = metadata.indexOf("if(pr.draft){");
  const releaseEnd = metadata.indexOf("s=await snapshot(); pr=s.pr", releaseStart);
  const release = metadata.slice(releaseStart, releaseEnd);
  const beforeRelease = metadata.slice(0, releaseStart);
  const afterRelease = metadata.slice(releaseEnd);

  assert.doesNotMatch(metadata, /github-token:\s*\$\{\{\s*secrets\.AGENT_PUBLISH_TOKEN/);
  assert.match(metadata, /READY_TOKEN:\s*'\$\{\{ secrets\.AGENT_PUBLISH_TOKEN \}\}'/);
  assert.match(release, /draftReadyResponseDecision/);
  assert.match(release, /https:\/\/api\.github\.com\/graphql/);
  assert.match(release, /authorization:`Bearer \$\{readyToken\}`/);
  assert.match(release, /markPullRequestReadyForReview/);
  assert.match(afterRelease, /draftReadyPostconditionDecision/);
  assert.doesNotMatch(beforeRelease, /process\.env\.READY_TOKEN|readyToken/);
  assert.doesNotMatch(afterRelease, /process\.env\.READY_TOKEN|readyToken/);
  assert.match(beforeRelease, /github\.rest\.issues\.createComment/);
  assert.match(beforeRelease, /github\.rest\.issues\.setLabels/);
  assert.match(afterRelease, /github\.rest\.actions\.createWorkflowDispatch/);
});

test("Issue #116 Draft-to-Ready decisions fail closed for every required failure mode", () => {
  const ok = {
    tokenPresent: true,
    httpOk: true,
    graphqlErrors: [],
    releasedId: "PR_node_117",
    expectedId: "PR_node_117",
    releasedIsDraft: false,
  };
  assert.deepEqual(a.draftReadyResponseDecision(ok), { ok: true });
  assert.equal(a.draftReadyResponseDecision({ ...ok, tokenPresent: false }).reason, "READY_TOKEN_MISSING");
  assert.equal(a.draftReadyResponseDecision({ ...ok, httpOk: false }).reason, "READY_HTTP_FAILED");
  assert.equal(a.draftReadyResponseDecision({ ...ok, graphqlErrors: [{ message: "denied" }] }).reason, "READY_GRAPHQL_FAILED");
  assert.equal(a.draftReadyResponseDecision({ ...ok, graphqlErrors: { message: "bad shape" } }).reason, "READY_RESPONSE_MALFORMED");
  assert.equal(a.draftReadyResponseDecision({ ...ok, releasedId: "wrong" }).reason, "READY_RESPONSE_MALFORMED");
  assert.equal(a.draftReadyResponseDecision({ ...ok, releasedIsDraft: true }).reason, "READY_RESPONSE_MALFORMED");
  assert.deepEqual(a.draftReadyPostconditionDecision({ draft: false }), { ok: true });
  assert.equal(a.draftReadyPostconditionDecision({ draft: true }).reason, "READY_DRAFT_POSTCONDITION_FAILED");
  assert.equal(a.draftReadyPostconditionDecision({ draft: undefined }).reason, "READY_DRAFT_POSTCONDITION_FAILED");
});

test("Issue #118 Builder branch identity binds the authorized base and remains retry-idempotent", () => {
  const workflow = fs.readFileSync(".github/workflows/agent-builder-publish.yml", "utf8");
  assert.match(workflow, /branch="agent\/issue-\$\{ISSUE\}-\$\{SPEC:0:12\}-\$\{BASE:0:12\}"/);

  const branchFor = (issue, specHash, baseSha) =>
    `agent/issue-${issue}-${specHash.slice(0, 12)}-${baseSha.slice(0, 12)}`;
  const specHash = "c".repeat(64);
  const baseA = "a".repeat(40);
  const baseB = "b".repeat(40);

  const first = branchFor(109, specHash, baseA);
  assert.equal(first, branchFor(109, specHash, baseA));
  assert.notEqual(first, branchFor(109, specHash, baseB));
});

test("Issue #118 Builder revalidates authorization immediately before trigger-capable writes", () => {
  const workflow = fs.readFileSync(".github/workflows/agent-builder-publish.yml", "utf8");
  const publish = workflow.slice(workflow.indexOf("- id: p"), workflow.indexOf("- name: Link, transition"));
  const revalidation = publish.slice(publish.indexOf("revalidate_issue(){"), publish.indexOf("owned(){"));
  assert.match(revalidation, /authorizationDecision/);
  assert.match(revalidation, /p\.isImplementation\(issue\.labels\)/);
  assert.match(revalidation, /exactAgentState\(issue\.labels,\"agent:running\"\)/);
  assert.match(revalidation, /GH_TOKEN=\"\$JOB_TOKEN\" gh api/);

  const push = publish.indexOf('git -c http.extraheader="$AUTH_HEADER" push origin "HEAD:refs/heads/$branch"');
  const create = publish.indexOf('gh pr create --repo "$GH_REPO"');
  const calls = [...publish.matchAll(/^\s+revalidate_issue$/gm)].map(match => match.index);
  assert.equal(calls.length, 2);
  assert.ok(calls[0] < push);
  assert.ok(calls[1] > push && calls[1] < create);
});

test("Issue #118 Builder metadata writes revalidate complete authorization, head and linkage", () => {
  const workflow = fs.readFileSync(".github/workflows/agent-builder-publish.yml", "utf8");
  const metadata = workflow.slice(workflow.indexOf("- name: Link, transition, release Draft"));
  assert.match(metadata, /const snapshot=async\(\)=>/);
  assert.match(metadata, /const immutableBindingOk=.*s\.auth\.ok.*s\.pr\.head\.sha===process\.env\.HEAD.*parents\?\.\[0\]\?\.sha===process\.env\.BASE/);
  assert.match(metadata, /const fullLinkOk=.*fullLinkageDecision/);
  assert.match(metadata, /LINK_BINDING_CHANGED/);
  const issueLinkWrite=metadata.indexOf("issue_number:n,body:`Linked autonomous PR");
  const prLinkWrite=metadata.indexOf("issue_number:prn,body:`Linked authorized Issue");
  const issueStateWrite=metadata.indexOf("await state(n,s.i.labels)");
  const prStateWrite=metadata.indexOf("await state(prn,s.pr.labels)");
  assert.ok(metadata.lastIndexOf("immutableBindingOk(s)",issueLinkWrite)>=0);
  assert.ok(metadata.lastIndexOf("immutableBindingOk(s)",prLinkWrite)>issueLinkWrite);
  assert.ok(metadata.lastIndexOf("fullLinkOk(s)",issueStateWrite)>prLinkWrite);
  assert.ok(metadata.lastIndexOf("fullLinkOk(s)",prStateWrite)>issueStateWrite);
});

test("Issue #118 control-plane recovery revalidates mutable authority, CI and ancestry before writes", () => {
  const workflow = fs.readFileSync(".github/workflows/agent-control-plane-remediation.yml", "utf8");
  const recover = workflow.slice(workflow.indexOf("  recover:"), workflow.indexOf("\n  gate:"));
  assert.match(recover, /permissions: \{actions: read, contents: read, issues: write, pull-requests: write\}/);
  assert.match(recover, /getCollaboratorPermissionLevel/);
  assert.match(recover, /compareCommits/);
  assert.match(recover, /listWorkflowRunsForRepo/);
  assert.match(recover, /successfulRequiredJobs/);
  assert.match(recover, /const mutableGuardsOk=.*permission\.permission.*behind_by===0.*ciRun\.conclusion==='success'.*successfulRequiredJobs/);
  assert.match(recover, /const baseBindingOk=.*entryOk\(s\)&&mutableGuardsOk\(s\).*s\.auth\.ok.*s\.pr\.head\.sha===headSha/);
  assert.match(recover, /const fullBindingOk=.*baseBindingOk\(s\).*fullLinkageDecision/);
  const issueWrite = recover.indexOf("await setState(issueNumber");
  const prWrite = recover.indexOf("await setState(prNumber");
  const issueGuard = recover.lastIndexOf("if(!fullBindingOk(s))", issueWrite);
  const prGuard = recover.lastIndexOf("if(!fullBindingOk(s))", prWrite);
  assert.ok(issueGuard >= 0 && issueGuard < issueWrite);
  assert.ok(prGuard > issueWrite && prGuard < prWrite);
});

test("Issue #118 maintenance gate and merge require live strict Protect main ruleset with no bypass", () => {
  const workflow = fs.readFileSync(".github/workflows/agent-control-plane-remediation.yml", "utf8");
  const gate = workflow.slice(workflow.indexOf("  gate:"), workflow.indexOf("\n  merge:"));
  const merge = workflow.slice(workflow.indexOf("  merge:"));
  for (const section of [gate, merge]) {
    assert.match(section, /const rulesetHealthy=async\(\)=>/);
    assert.match(section, /GET \/repos\/\{owner\}\/\{repo\}\/rulesets/);
    assert.match(section, /name==='Protect main'.*target==='branch'.*enforcement==='active'/);
    assert.match(section, /r\.bypass_actors\.length===0/);
    assert.match(section, /strict_required_status_checks_policy===true/);
    assert.match(section, /required\.some\(x=>x\.context===a\.GATE_CONTEXT\)/);
    assert.match(section, /include\.includes\(`refs\/heads\/\$\{def\}`\)/);
    assert.match(section, /Array\.isArray\(exclude\)&&exclude\.length===0/);
    assert.match(section, /ruleset=await rulesetHealthy\(\)/);
    assert.match(section, /const ok=ruleset&&/);
  }
});
