"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const a = require("./agent-autonomy.cjs");
const c = require("../agent-pipeline.json");
const m = require("./agent-maintenance-runtime.cjs");
const clone = x => structuredClone(x);

function fixture() {
  const repo = "owner/repo", head = "a".repeat(40), base = "b".repeat(40), writes = [];
  const issue = {number: 126, title: "Repair maintenance", body: "Explicit bounded specification", state: "open", labels: ["type:implementation", "agent:needs-human", "team:quant"]};
  const specHash = a.issueSpecHash(issue);
  const request = {repo, issueNumber: 126, prNumber: 127, headSha: head, specHash, actor: "alice", requestRunId: 88,
    requestRunAttempt: 1, entryState: "agent:needs-human", reason: "Reviewed maintenance request",
    authorization: {commentId: 1, actor: "alice", runId: 77, specHash}};
  const origin = {id: 88, run_attempt: 1, actor: {login: "alice"}, triggering_actor: {login: "alice"}, event: "workflow_dispatch", status: "completed", conclusion: "success",
    path: ".github/workflows/agent-control-plane-remediation-request.yml", head_sha: base, head_branch: "main", head_repository: {full_name: repo}, repository: {full_name: repo}};
  const context = {repo: {owner: "owner", repo: "repo"}, sha: base, eventName: "workflow_run", runId: 900, runAttempt: 1,
    payload: {repository: {default_branch: "main"}, workflow_run: clone(origin)}};
  const expected = m.expectedFrom(request, context);
  const pr = {number: 127, state: "open", draft: false, auto_merge: null, body: "Agent-Issue: #126", labels: ["agent:needs-human", "priority:high"], changed_files: 1,
    head: {sha: head, repo: {full_name: repo}}, base: {sha: base, ref: "main", repo: {full_name: repo}}};
  const ci = {id: 100, run_attempt: 1, name: "CI", path: ".github/workflows/ci.yml", event: "pull_request", status: "completed", conclusion: "success",
    head_sha: head, pull_requests: [{number: 127}], head_repository: {full_name: repo}, repository: {full_name: repo}};
  const d = {pr, issue, origin, permission: "admin", main: base, runs: [ci],
    jobs: c.requiredCiJobs.map((name, n) => ({id: n+1, name, conclusion: "success", head_sha: head, run_attempt: 1})),
    issueComments: [{id: 1, user: {login: "github-actions[bot]"}, body: a.authorizationMarker({repo, issueNumber: 126, specHash, actor: "alice", runId: 77})}],
    prComments: [], files: [{filename: ".github/scripts/agent-x.cjs", status: "modified"}], statuses: [],
    ruleset: {id: 99, name: "Protect main", target: "branch", enforcement: "active", bypass_actors: [], conditions: {ref_name: {include: ["refs/heads/main"], exclude: []}},
      rules: [{type: "deletion"}, {type: "non_fast_forward"}, {type: "pull_request", parameters: {required_approving_review_count: 0}},
        {type: "required_status_checks", parameters: {strict_required_status_checks_policy: true, required_status_checks: clone(m.REQUIRED_CHECKS)}},
        {type: "code_scanning", parameters: {code_scanning_tools: [{tool: "CodeQL", security_alerts_threshold: "high_or_higher", alerts_threshold: "errors"}]}}]},
    afterWrite: null, failRead: false, rulesetReads: 0};
  const read = (value) => { if (d.failRead) throw new Error("HTTP_403"); return {data: clone(value)}; };
  const record = (name, args) => {writes.push({name, args: clone(args), auditReads: d.rulesetReads}); if (d.afterWrite) d.afterWrite(d, writes);};
  const github = {rest: {pulls: {get: async()=>read(d.pr), listFiles: "files"},
    issues: {get: async()=>read(d.issue), listComments: "comments",
      createComment: async args => { const out = {id: 200+writes.length, user: {login: "github-actions[bot]"}, body: args.body};
        (args.issue_number === 126 ? d.issueComments : d.prComments).push(out); record("comment", args); return {data: out}; },
      setLabels: async args => { (args.issue_number === 126 ? d.issue : d.pr).labels = clone(args.labels); record("labels", args); return {data: args.labels}; }},
    repos: {getCollaboratorPermissionLevel: async()=>read({permission: d.permission}), getBranch: async()=>read({commit: {sha: d.main}}),
      compareCommits: async()=>read({behind_by: d.main === base ? 0 : 1}), listCommitStatusesForRef: "statuses",
      createCommitStatus: async args => { const status={...args, id:300+writes.length, creator: {login:"github-actions[bot]"}};
        d.statuses.unshift(status); record("status",args);return {data:status}; }},
    actions: {listWorkflowRunsForRepo:"runs",listJobsForWorkflowRun:"jobs",
      getWorkflowRun:async args=>read(args.run_id===d.origin.id?d.origin:d.runs.find(x=>x.id===args.run_id))}},
    paginate:async(method,args)=>{if(d.failRead)throw new Error("HTTP_403");
      return clone(method==="files"?d.files:method==="comments"?(args.issue_number===126?d.issueComments:d.prComments):method==="runs"?d.runs:method==="jobs"?d.jobs:d.statuses);}};
  const readRuleset=async()=>{d.rulesetReads++;if(d.failRead)throw new Error("HTTP_403");return clone(d.ruleset);};
  const mergePull=async args=>{d.pr.state="closed";d.pr.merged=true;d.pr.merge_commit_sha="f".repeat(40);record("merge",args);return {merged:true,sha:d.pr.merge_commit_sha};};
  const review={reviewed_sha:head,result:"PASS",findings:[],issue_scope_consistent:true,test_or_governance_weakened:false,paper_only_live_trading_safe:true,summary:"Independently checked."};
  const deps={github,context,expected,readRuleset,review,mergePull};
  const seal=async()=>{const s=await m.runtime({...deps,review:null}).snapshot(undefined,true);
    const evidence={repository:repo,headSha:head,baseSha:base,ci:s.ci,filesHash:s.filesHash,protectionHash:m.digest(s.protection),protection:s.protection,
      authorization: clone(expected.authorization)};
    const bundle={version:2,producer:m.producer(context),request,expected,evidence};
    bundle.digest=m.digest({producer:bundle.producer,expected,evidence});deps.bundle=bundle;return m.runtime(deps);};
  const addLinks=()=>{const marker=`<!-- agent-link:v1 repo=${repo} issue=126 pr=127 -->`;
    d.issueComments.push({id:9,user:{login:"github-actions[bot]"},body:marker});d.prComments.push({id:10,user:{login:"github-actions[bot]"},body:marker});};
  return {d,deps,writes,seal,addLinks,request,expected,context,github,review};
}

test("real production entrypoints link, recover, gate and exact-head merge; every mutation has fresh audit", async()=>{
  const f=fixture(),r=await f.seal();await r.link();await r.recover();await r.gate();assert.equal(await r.merge(),"f".repeat(40));
  assert.equal(f.writes.filter(x=>x.name==="merge").length,1);
  assert.equal(f.writes.at(-1).args.sha,f.expected.headSha);
  assert.ok(f.d.pr.labels.includes("priority:high"));assert.ok(f.d.issue.labels.includes("team:quant"));
  for(let i=1;i<f.writes.length;i++)assert.ok(f.writes[i].auditReads>f.writes[i-1].auditReads,"fresh protection read for each write");
});

const drift=[
  ["GitHub auto-merge enabled",d=>{d.pr.auto_merge={merge_method:"merge"};}],
  ["GitHub auto-merge unknown",d=>{delete d.pr.auto_merge;}],
  ["different request attempt caller",d=>{d.origin.triggering_actor={login:"bob"};}],
  ["missing request attempt caller",d=>{delete d.origin.triggering_actor;}],
  ["head",d=>{d.pr.head.sha="d".repeat(40);}],
  ["authorization",d=>{d.issue.body+=" changed";}],
  ["replacement authorization with same specification",d=>{d.issueComments[0].id=2;}],
  ["closed Issue",d=>{d.issue.state="closed";}],
  ["classification",d=>{d.issue.labels.push("type:epic");}],
  ["requester authority",d=>{d.permission="read";}],
  ["main",d=>{d.main="d".repeat(40);}],
  ["body linkage",d=>{d.pr.body="Agent-Issue: #42";}],
  ["newest running CI",d=>{d.runs.push({...d.runs[0],id:101,status:"in_progress",conclusion:null});}],
  ["newest failed CI",d=>{d.runs.push({...d.runs[0],id:101,conclusion:"failure"});}],
  ["newer successful CI needs new review",d=>{d.runs.push({...d.runs[0],id:101});}],
  ["missing CI job",d=>{d.jobs.pop();}],
  ["CI attempt",d=>{d.runs[0].run_attempt++;}],
  ["wrong workflow",d=>{d.runs[0].path=".github/workflows/imposter.yml";}],
  ["request run event",d=>{d.origin.event="pull_request";}],
  ["request run failure",d=>{d.origin.conclusion="failure";}],
  ["request run attempt",d=>{d.origin.run_attempt++;}],
  ["request source",d=>{d.origin.head_sha="d".repeat(40);}],
  ["file scope",d=>{d.files[0].filename="backend/src/quantlab/trading.py";}],
  ["rename source",d=>{d.files[0].status="renamed";d.files[0].previous_filename="AGENTS.md";}],
  ["file count",d=>{d.pr.changed_files++;}],
  ["missing bypass field",d=>{delete d.ruleset.bypass_actors;}],
  ["null bypass field",d=>{d.ruleset.bypass_actors=null;}],
  ["bypass added",d=>{d.ruleset.bypass_actors.push({actor_id:1});}],
  ["required check lost",d=>{d.ruleset.rules[3].parameters.required_status_checks.shift();}],
  ["wrong App",d=>{d.ruleset.rules[3].parameters.required_status_checks[0].integration_id=99;}],
  ["CodeQL removed",d=>{d.ruleset.rules.pop();}],
  ["CodeQL weakened",d=>{d.ruleset.rules[4].parameters.code_scanning_tools[0].security_alerts_threshold="critical";}],
  ["disabled protection",d=>{d.ruleset.enforcement="disabled";}],
  ["additional restriction changed",d=>{d.ruleset.rules[2].parameters.required_approving_review_count=1;}],
  ["API 403",d=>{d.failRead=true;}],
];
for(const [name,change] of drift) for(const phase of ["link","recover","gate"])
 test(`production ${phase} stops before second write after ${name}`,async()=>{
  const f=fixture();if(phase!=="link")f.addLinks();if(phase==="gate"){f.d.pr.labels=["agent:pr"];f.d.issue.labels=["type:implementation","agent:pr"];}
  const r=await f.seal();f.d.afterWrite=(d,w)=>{if(w.length===1)change(d);};
  await assert.rejects(r[phase]());assert.equal(f.writes.length,1);
 });
for(const side of ["pr","issue"])test(`production recovers partial ${side} and is idempotent`,async()=>{
 const f=fixture();f.addLinks();f.d[side].labels=f.d[side].labels.map(x=>x==="agent:needs-human"?"agent:pr":x);
 const r=await f.seal();await r.recover();const count=f.writes.length;await r.recover();assert.equal(f.writes.length,count);
 assert.ok(f.d.pr.labels.includes("priority:high"));assert.ok(f.d.issue.labels.includes("team:quant"));
});
test("production explicit recovery supports an initially unmanaged PR only",async()=>{
 const f=fixture();f.d.pr.labels=["priority:high"];const r=await f.seal();await r.link();await r.recover();assert.ok(f.d.pr.labels.includes("agent:pr"));
 const bad=fixture();bad.request.entryState="agent:pr";bad.deps.expected=m.expectedFrom(bad.request,bad.context);bad.d.pr.labels=[];
 await assert.rejects(m.runtime(bad.deps).snapshot(undefined,true));
});
for(const phase of ["link","recover","gate","merge"])test(`production ${phase} never writes on BLOCK`,async()=>{
 const f=fixture();const r=await f.seal();f.review.result="BLOCK";await assert.rejects(r[phase]());assert.equal(f.writes.length,0);
});
for(const phase of ["link","recover","gate"])test(`production ${phase} denies forged producer artifact`,async()=>{
 const f=fixture();const r=await f.seal();f.deps.bundle.producer.sourceSha="d".repeat(40);await assert.rejects(r[phase](),/PROVENANCE/);assert.equal(f.writes.length,0);
});
test("gate refuses new needs-human after review",async()=>{
 const f=fixture();f.addLinks();const r=await f.seal();await r.recover();const count=f.writes.length;f.d.pr.labels=["agent:needs-human"];
 await assert.rejects(r.gate());assert.equal(f.writes.length,count);
});
for (const reverted of ["pr", "issue"]) test(`recovery stops the next write when recovered ${reverted} is escalated`, async()=>{
 const f=fixture();f.addLinks();
 if(reverted==="issue") f.d.issue.labels=f.d.issue.labels.map(x=>x==="agent:needs-human"?"agent:pr":x);
 const r=await f.seal();f.d.afterWrite=(d,w)=>{if(w.length===1)d[reverted].labels=d[reverted].labels.filter(x=>!x.startsWith("agent:")).concat("agent:needs-human");};
 await assert.rejects(r.recover(),/RECOVERY_PROGRESS_REVOKED/);
 assert.equal(f.writes.filter(x=>x.name==="labels").length,1,"escalation stops the following label mutation");
 assert.ok(f.d[reverted].labels.includes("agent:needs-human"));
});
test("new authorization with the same specification cannot reuse an old review bundle",async()=>{
 const f=fixture(),r=await f.seal();f.d.issueComments[0].id=2;
 await assert.rejects(r.recover(),/AUTHORIZATION_CHANGED/);assert.equal(f.writes.length,0);
});
test("production does not erase an already verified half on a fresh explicit request",async()=>{
 const f=fixture();f.addLinks();f.request.entryState="agent:pr";f.expected.entryState="agent:pr";
 f.d.pr.labels=["agent:verified","priority:high"];f.d.issue.labels=["type:implementation","agent:pr"];
 const r=await f.seal();await r.recover();assert.ok(f.d.pr.labels.includes("agent:verified"));await r.gate();assert.ok(f.d.issue.labels.includes("agent:verified"));
});
test("ruleset baseline is 9 contexts and authoritative CI independently remains 9 jobs",()=>{
 assert.equal(m.REQUIRED_CHECKS.length,9);assert.equal(c.requiredCiJobs.length,9);
 assert.ok(!m.REQUIRED_CHECKS.some(x=>x.context==="agent-pipeline"));assert.ok(c.requiredCiJobs.includes("agent-pipeline"));
});
test("audit adapter uses only fixed GET endpoints and never leaks credential on HTTP failure",async()=>{
 const requests=[],f=fixture();const fn=m.rulesetReader("owner/repo","test-secret",async(url,opts)=>{requests.push({url,opts});
 return {ok:true,text:async()=>JSON.stringify(url.includes("rulesets?")?[{id:99,name:"Protect main",target:"branch"}]:f.d.ruleset)};});
 await fn();assert.equal(requests.length,2);assert.ok(requests.every(x=>x.opts.method==="GET"&&x.opts.redirect==="error"));
 const denied=m.rulesetReader("owner/repo","test-secret",async()=>({ok:false}));await assert.rejects(denied(),e=>e.message==="RULESET_AUDIT_HTTP_FAILED"&&!e.message.includes("test-secret"));
});
test("workflow uses tested runtime phases from trusted SHA; model validation imports no candidate",()=>{
 const workflow=fs.readFileSync(".github/workflows/agent-control-plane-remediation.yml","utf8");
 for(const phase of ["prepare","recover","gate","merge"])assert.ok(workflow.includes(`phase:"${phase}"`));
 assert.ok(workflow.includes('require("./.github/scripts/agent-maintenance-runtime.cjs")'));
 assert.ok(workflow.includes("ref: '${{ github.workflow_sha }}'"));
 const model=workflow.split("  independent-review:")[1].split("  recover:")[0];
 assert.doesNotMatch(model,/AGENT_PUBLISH_TOKEN|RULESET_AUDIT_TOKEN|MERGE_TOKEN|require\(['"]\.\//);
 assert.match(model,/evidence\.json/);assert.match(model,/if: always\(\)/);
 assert.doesNotMatch(workflow,/if\(!entryOk\(s\)\)/);
 const request=fs.readFileSync(".github/workflows/agent-control-plane-remediation-request.yml","utf8");
 assert.match(request,/requestRunAttempt:Number\(process\.env\.GITHUB_RUN_ATTEMPT\)/);
});

for(const [name,change] of drift) test(`production denies ${name} before first write`,async()=>{
 const f=fixture(),r=await f.seal();change(f.d);await assert.rejects(r.link());assert.equal(f.writes.length,0);
});
for(const [name,change] of drift) test(`production merge denies ${name} after successful review and gate`,async()=>{
 const f=fixture(),r=await f.seal();await r.link();await r.recover();await r.gate();const before=f.writes.length;
 change(f.d);await assert.rejects(r.merge());assert.equal(f.writes.length,before);assert.ok(!f.writes.some(w=>w.name==='merge'));
});
test("authoritative CI source is byte-identical to the trusted baseline",()=>{
 const crypto=require('node:crypto'),data=fs.readFileSync('.github/workflows/ci.yml');
 assert.equal(crypto.createHash('sha1').update(Buffer.concat([Buffer.from(`blob ${data.length}\0`),data])).digest('hex'),'5fdf5ffc35a675a8e82b43255206a3cd8f978238');
});


// Run the real workflow_dispatch request script, not a duplicated state formula.
function requestWrapperFixture() {
  const f=fixture(), vm=require('node:vm'), pipeline=require('./agent-pipeline.cjs');
  const yaml=fs.readFileSync('.github/workflows/agent-control-plane-remediation-request.yml','utf8');
  const source=yaml.split('          script: |\n')[1].split('      - uses: actions/upload-artifact@')[0]
    .split('\n').filter(Boolean).map(line=>line.slice(12)).join('\n');
  const script=vm.runInNewContext(`(async function(require,github,context,process,core){${source}\n})`);
  const output=[], errors=[];
  const env={ISSUE:'126',PR:'127',HEAD:f.expected.headSha,REASON:'Exact authorized recovery',
    GITHUB_RUN_ATTEMPT:'1',TRIGGERING_ACTOR:'alice',RUNNER_TEMP:'/mock'};
  const context={...f.context,actor:'alice',ref:'refs/heads/main',runId:88};
  const load=name=>name==='fs'?{writeFileSync:(_path,text)=>output.push(JSON.parse(text))}:
    name==='./.github/scripts/agent-pipeline.cjs'?pipeline:
    name==='./.github/scripts/agent-autonomy.cjs'?a:
    name==='./.github/agent-pipeline.json'?c:(()=>{throw new Error('Unexpected import');})();
  return {...f,env,output,errors,requestContext:context,execute:()=>script(load,f.github,context,{env},{setFailed:msg=>errors.push(msg)})};
}
test('request wrapper admits exact needs-human Issue with unmanaged PR then actual runtime recovers it',async()=>{
 const f=requestWrapperFixture();f.d.pr.labels=['priority:high'];await f.execute();
 assert.deepEqual(f.errors,[]);assert.equal(f.output.length,1);assert.equal(f.output[0].entryState,'agent:needs-human');
 assert.deepEqual(f.output[0].authorization,{commentId:1,actor:'alice',runId:77,specHash:f.expected.specHash});
 const r=await f.seal();await r.link();await r.recover();assert.ok(f.d.pr.labels.includes('agent:pr'));
 assert.ok(f.d.pr.labels.includes('priority:high'));assert.ok(f.d.issue.labels.includes('team:quant'));
});
test('request and evidence reject missing or substituted authorization identity',async()=>{
 const f=requestWrapperFixture();await f.execute();const request=clone(f.output[0]);delete request.authorization.commentId;
 assert.throws(()=>m.expectedFrom(request,f.context),/REQUEST_BINDING_INVALID/);
 const r=await f.seal();f.deps.bundle.evidence.authorization.commentId=2;
 await assert.rejects(r.link(),/BUNDLE_EVIDENCE_INVALID/);assert.equal(f.writes.length,0);
});
for(const issueState of ['agent:running','agent:ready','agent:pr'])test(`request wrapper refuses unmanaged PR with ${issueState} Issue`,async()=>{
 const f=requestWrapperFixture();f.d.pr.labels=['priority:high'];f.d.issue.labels=['type:implementation',issueState];await f.execute();
 assert.deepEqual(f.errors,['MAINTENANCE_LIFECYCLE_REJECTED']);assert.equal(f.output.length,0);
});
for(const condition of ['enabled','missing'])test(`request wrapper rejects ${condition} auto-merge before creating artifact`,async()=>{
 const f=requestWrapperFixture();if(condition==='enabled')f.d.pr.auto_merge={merge_method:'merge'};else delete f.d.pr.auto_merge;
 await f.execute();assert.ok(f.errors.includes('MAINTENANCE_SCOPE_REJECTED'));assert.equal(f.output.length,0);
});
for(const caller of ['bob',''])test(`request wrapper rejects rerun attributed to ${caller||'missing caller'}`,async()=>{
 const f=requestWrapperFixture();f.env.GITHUB_RUN_ATTEMPT='2';f.env.TRIGGERING_ACTOR=caller;await f.execute();
 assert.deepEqual(f.errors,['REQUEST_ATTEMPT_ACTOR_MISMATCH']);assert.equal(f.output.length,0);
});
test('request wrapper accepts same-author rerun and binds its actual attempt',async()=>{
 const f=requestWrapperFixture();f.env.GITHUB_RUN_ATTEMPT='2';await f.execute();assert.deepEqual(f.errors,[]);
 assert.equal(f.output[0].actor,'alice');assert.equal(f.output[0].requestRunAttempt,2);
 const expected={...f.expected,requestRunAttempt:2};const origin={...f.d.origin,run_attempt:2};
 assert.equal(m.originValid(origin,expected),true);origin.triggering_actor.login='bob';assert.equal(m.originValid(origin,expected),false);
});

// A fresh workflow_dispatch must be able to resume a lost label-write response.
for (const interruption of ["before-write", "after-write"]) {
  test(`unmanaged recovery survives ${interruption} interruption and a fresh request`, async () => {
    const f = requestWrapperFixture();
    f.d.pr.labels = ["priority:high"];
    await f.execute();
    assert.deepEqual(f.errors, []);
    const first = await f.seal();
    await first.link();
    const originalSetLabels = f.github.rest.issues.setLabels;
    let interrupted = false;
    f.github.rest.issues.setLabels = async args => {
      if (!interrupted) {
        interrupted = true;
        if (interruption === "after-write") await originalSetLabels(args);
        throw new Error("SIMULATED_LABEL_RESPONSE_LOSS");
      }
      return originalSetLabels(args);
    };
    await assert.rejects(first.recover(), /SIMULATED_LABEL_RESPONSE_LOSS/);
    const beforeRetry = f.writes.filter(w => w.name === "labels");
    assert.equal(beforeRetry.length, interruption === "after-write" ? 1 : 0);
    f.github.rest.issues.setLabels = originalSetLabels;

    // Execute the real request wrapper again with a distinct request run ID.
    f.requestContext.runId = 89;
    await f.execute();
    assert.deepEqual(f.errors, [], "fresh request must accept the reachable partial state");
    assert.equal(f.output.length, 2);
    const freshRequest = f.output[1];
    assert.equal(freshRequest.requestRunId, 89);
    assert.equal(freshRequest.entryState, "agent:needs-human");
    assert.ok(f.d.issue.labels.includes("agent:needs-human"));
    if (interruption === "after-write") {
      assert.equal(beforeRetry[0].args.issue_number, 127, "unmanaged PR is written first");
      assert.ok(f.d.pr.labels.includes("agent:pr"));
    }

    // New producer, origin and evidence; the old bundle cannot authorize this run.
    f.d.origin.id = 89;
    f.context.payload.workflow_run = clone(f.d.origin);
    f.context.runId = 901;
    assert.throws(() => m.validateBundle(f.deps.bundle, f.context), /PROVENANCE/);
    Object.assign(f.request, freshRequest);
    Object.assign(f.expected, m.expectedFrom(freshRequest, f.context));
    const next = await f.seal();
    await next.link();
    await next.recover();
    assert.ok(f.d.pr.labels.includes("agent:pr"));
    assert.ok(f.d.issue.labels.includes("agent:pr"));
    assert.ok(f.d.pr.labels.includes("priority:high"));
    assert.ok(f.d.issue.labels.includes("team:quant"));
    assert.equal(f.writes.filter(w => w.name === "labels").length, 2);
    const completedWrites = f.writes.length;
    await next.recover();
    assert.equal(f.writes.length, completedWrites, "completed retry is idempotent");
    assert.ok(!f.writes.some(w => ["status", "merge"].includes(w.name)));
  });
}
