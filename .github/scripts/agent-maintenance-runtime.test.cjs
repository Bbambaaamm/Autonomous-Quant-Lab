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
const executeRecover = r => m.executePhase ? m.executePhase(r,"recover") : (async()=>{await r.link();await r.recover();})();

function fixture() {
  const repo = "owner/repo", head = "a".repeat(40), base = "b".repeat(40), writes = [];
  const issue = {number: 126, title: "Repair maintenance", body: "Explicit bounded specification", state: "open", labels: ["type:implementation", "agent:needs-human", "team:quant"]};
  const specHash = a.issueSpecHash(issue);
  const request = {repo, issueNumber: 126, prNumber: 127, headSha: head, specHash, actor: "alice", requestRunId: 88,
    requestRunAttempt: 1, entryState: "agent:needs-human", reason: "Reviewed maintenance request",
    mode: "production", canary_only: false,
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
        (args.issue_number === 126 ? d.issueComments : d.prComments).push(out); const response=clone(out); record("comment", args); return {data: response}; },
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
      authorization: clone(expected.authorization),linkage: m.linkEvidence(s,expected),
      initialStates: {pr: m.currentState(s.pr),issue: m.currentState(s.issue)},recoveryProgress:Object.fromEntries(["pr","issue"].map(side=>[side,
        a.exactAgentState(s[side].labels,"agent:pr")||a.exactAgentState(s[side].labels,"agent:verified")]))};
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
  const r=await f.seal();if(phase==='gate'){await r.recover();f.writes.length=0;}
  f.d.afterWrite=(d,w)=>{if(w.length===1)change(d);};
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
 const f=fixture();const r=await f.seal();f.deps.bundle.producer.sourceSha="d".repeat(40);await assert.rejects(r[phase](),/PROVENANCE|RECOVERY_RECEIPT/);assert.equal(f.writes.length,0);
});
test("gate refuses new needs-human after review",async()=>{
 const f=fixture();f.addLinks();const r=await f.seal();await r.recover();const count=f.writes.length;f.d.pr.labels=["agent:needs-human"];
 await assert.rejects(r.gate());assert.equal(f.writes.length,count);
});
for (const reverted of ["pr", "issue"]) test(`recovery stops the next write when recovered ${reverted} is escalated`, async()=>{
 const f=fixture();f.addLinks();
 if(reverted==="issue") f.d.issue.labels=f.d.issue.labels.map(x=>x==="agent:needs-human"?"agent:pr":x);
 const r=await f.seal();f.d.afterWrite=(d,w)=>{if(w.at(-1).name==='labels'&&w.filter(x=>x.name==='labels').length===1)d[reverted].labels=d[reverted].labels.filter(x=>!x.startsWith("agent:")).concat("agent:needs-human");};
 await assert.rejects(r.recover(),/RECOVERY_PROGRESS_REVOKED/);
 assert.equal(f.writes.filter(x=>x.name==="labels").length,1,"escalation stops the following label mutation");
 assert.ok(f.d[reverted].labels.includes("agent:needs-human"));
});
test("new authorization with the same specification cannot reuse an old review bundle",async()=>{
 const f=fixture(),r=await f.seal();f.d.issueComments[0].id=2;
 await assert.rejects(r.recover(),/AUTHORIZATION_CHANGED/);assert.equal(f.writes.length,0);
});
test("real recover wrapper carries initial PR progress through linkage writes",async()=>{
 const f=fixture();f.d.pr.labels=["agent:pr","priority:high"];
 const r=await f.seal();f.d.afterWrite=(d,w)=>{if(w.length===1)d.pr.labels=["agent:needs-human","priority:high"];};
 await assert.rejects(executeRecover(r),/RECOVERY_PROGRESS_REVOKED/);
 assert.equal(f.writes.filter(x=>x.name==="comment").length,1);
 assert.equal(f.writes.filter(x=>x.name==="labels").length,0);
 assert.ok(f.d.pr.labels.includes("agent:needs-human"));
});
test("fresh runtime honors progress sealed before an intervening escalation",async()=>{
 const f=fixture();f.d.pr.labels=["agent:pr","priority:high"];const first=await f.seal();
 f.d.pr.labels=["agent:needs-human","priority:high"];
 const fresh=m.runtime(f.deps);await assert.rejects(executeRecover(fresh),/RECOVERY_PROGRESS_REVOKED/);
 assert.equal(f.writes.length,0);void first;
});
test("symmetric initial Issue progress is not erased after linkage",async()=>{
 const f=fixture();f.d.issue.labels=["type:implementation","agent:pr","team:quant"];
 const r=await f.seal();f.d.afterWrite=(d,w)=>{if(w.length===1)d.issue.labels=["type:implementation","agent:needs-human","team:quant"];};
 await assert.rejects(executeRecover(r),/RECOVERY_PROGRESS_REVOKED/);
 assert.equal(f.writes.filter(x=>x.name==="comment").length,1);assert.equal(f.writes.filter(x=>x.name==="labels").length,0);
});
test("read-only API boundary denies every maintenance mutation adapter",async()=>{
 const f=fixture(),ro=m.readOnlyGithub(f.github);
 for(const call of [()=>ro.rest.issues.createComment({}),()=>ro.rest.issues.setLabels({}),()=>ro.rest.repos.createCommitStatus({})])
   await assert.rejects(call,/READ_ONLY_API_WRITE_DENIED/);
 assert.equal(f.writes.length,0);
});
test("executable canary run performs live-shaped reads and emits the bounded schema without writes",async()=>{
 const f=fixture(),directory=fs.mkdtempSync(path.join(os.tmpdir(),"maintenance-canary-"));
 f.request.mode="canary_only";f.request.canary_only=true;
 fs.writeFileSync(path.join(directory,"request.json"),JSON.stringify(f.request));
 const outputs=[];const fetcher=async url=>({ok:true,text:async()=>JSON.stringify(url.includes("rulesets?")?
   [{id:99,name:"Protect main",target:"branch"}]:f.d.ruleset)});
 const report=await m.run({phase:"canary",github:f.github,context:f.context,core:{setOutput:(k,v)=>outputs.push([k,v])},
   auditToken:"collector-only-token",directory,fetcher});
 assert.deepEqual(outputs,[["result","SNAPSHOT_COLLECTED"]]);assert.equal(report.mode,"read-only-snapshot");
 assert.equal(report.result,"SNAPSHOT_COLLECTED");assert.equal(report.operationalAcceptance,"PENDING");
 assert.deepEqual(Object.keys(report).sort(),["authorization","baseSha","ci","filesHash","headSha","issueNumber","mode","operationalAcceptance","prNumber","protectionHash","repository","requestRunAttempt","requestRunId","result","version"].sort());
 assert.equal(f.writes.length,0);assert.deepEqual(JSON.parse(fs.readFileSync(path.join(directory,"canary-report.json"))),report);
});
test("canary job uses the authorized default-branch follower and exact request schema",()=>{
 const workflow=followerJob("canary");
 assert.match(workflow,/github\.event\.workflow_run\.id/);assert.match(workflow,/github\.event\.workflow_run\.run_attempt/);assert.match(workflow,/phase:'canary'/);
 assert.match(workflow,/issues: read, pull-requests: read/);
 assert.doesNotMatch(workflow,/issues: write|pull-requests: write|statuses: write|MERGE_TOKEN|codex-action|createComment|setLabels|createCommitStatus/);
});
test("production does not erase an already verified half on a fresh explicit request",async()=>{
 const f=fixture();f.addLinks();f.request.entryState="agent:pr";f.expected.entryState="agent:pr";
 f.d.pr.labels=["agent:verified","priority:high"];f.d.issue.labels=["type:implementation","agent:pr"];
 const r=await f.seal();await r.recover();assert.ok(f.d.pr.labels.includes("agent:verified"));await r.gate();assert.ok(f.d.issue.labels.includes("agent:verified"));
});
test("ruleset baseline is 8 contexts and authoritative CI independently remains 9 jobs",()=>{
 assert.equal(m.REQUIRED_CHECKS.length,8);assert.equal(c.requiredCiJobs.length,9);
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
 assert.equal(crypto.createHash('sha1').update(Buffer.concat([Buffer.from(`blob ${data.length}\0`),data])).digest('hex'),'3669d02bd139654532ced0864aa30d8ebbc53e0d');
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
  // Actions exposes typed inputs separately from the string-valued event payload.
  const inputs={canary_only:false};
  const context={...f.context,actor:'alice',ref:'refs/heads/main',runId:88,
    payload:{...f.context.payload,inputs:{canary_only:'false'}}};
  const load=name=>name==='fs'?{writeFileSync:(_path,text)=>output.push(JSON.parse(text))}:
    name==='./.github/scripts/agent-pipeline.cjs'?pipeline:
    name==='./.github/scripts/agent-autonomy.cjs'?a:
    name==='./.github/agent-pipeline.json'?c:(()=>{throw new Error('Unexpected import');})();
  const execute=(transportOverrides={})=>{
    context.payload.inputs=Object.fromEntries(Object.entries(inputs).map(([key,value])=>[key,String(value)]));
    const transported={...env,CANARY_ONLY_JSON:JSON.stringify(inputs.canary_only),...transportOverrides};
    return script(load,f.github,context,{env:transported},{setFailed:msg=>errors.push(msg)});
  };
  return {...f,env,inputs,output,errors,requestContext:context,execute};
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
    assert.equal(f.writes.length, completedWrites, "completed retry is idempotentence");
    assert.ok(!f.writes.some(w => ["status", "merge"].includes(w.name)));
  });
}

test("real request wrapper safely defaults deliberate requests to explicit production mode",async()=>{
 const f=requestWrapperFixture();await f.execute();assert.deepEqual(f.errors,[]);
 assert.equal(f.output[0].mode,"production");assert.equal(f.output[0].canary_only,false);
});
test("real request wrapper creates only an explicitly boolean canary request",async()=>{
 const f=requestWrapperFixture();f.inputs.canary_only=true;await f.execute();
 assert.deepEqual(f.errors,[]);assert.equal(f.output[0].mode,"canary_only");assert.equal(f.output[0].canary_only,true);
});
for(const value of [undefined,null,"true","false",1]) test(`real request wrapper rejects ambiguous canary mode ${String(value)}`,async()=>{
 const f=requestWrapperFixture();if(value===undefined)delete f.inputs.canary_only;else f.inputs.canary_only=value;
 await f.execute();assert.deepEqual(f.errors,["REQUEST_MODE_INVALID"]);assert.equal(f.output.length,0);assert.equal(f.writes.length,0);
});
for(const pair of [[undefined,undefined],["production",null],["canary_only","true"],["other",false],["production",true],["canary_only",false]])
 test(`artifact mode ${String(pair[0])}/${String(pair[1])} fails closed`,()=>{
  const f=fixture();f.request.mode=pair[0];f.request.canary_only=pair[1];assert.throws(()=>m.expectedFrom(f.request,f.context),/REQUEST_BINDING_INVALID/);
 });
test("trusted route classifies exact artifact and a canary route cannot release production jobs",async()=>{
 const f=fixture(),directory=fs.mkdtempSync(path.join(os.tmpdir(),"maintenance-route-")),outputs=[];
 f.request.mode="canary_only";f.request.canary_only=true;fs.writeFileSync(path.join(directory,"request.json"),JSON.stringify(f.request));
 const report=await m.run({phase:"route",github:f.github,context:f.context,core:{setOutput:(k,v)=>outputs.push([k,v])},directory});
 assert.equal(report.mode,"canary_only");assert.deepEqual(outputs,[["production_route","false"],["canary_route","true"]]);assert.equal(f.writes.length,0);
 const workflow=fs.readFileSync(".github/workflows/agent-control-plane-remediation.yml","utf8");
 for(const job of ["prepare","independent-review","recover","gate","merge"]) assert.match(workflow,new RegExp(`${job}:[\\s\\S]*?if: [^\\n]*production_route == 'true'`));
 const route=followerJob("route");assert.doesNotMatch(route,/AGENT_PUBLISH_TOKEN|OPENAI_API_KEY|MERGE_TOKEN|issues: write|pull-requests: write|statuses: write/);
});
test("successful route job without an explicit production output cannot release writers",()=>{
 const workflow=fs.readFileSync(".github/workflows/agent-control-plane-remediation.yml","utf8");
 for(const job of ["recover","gate","merge"]) assert.match(workflow,new RegExp(`${job}:[\\s\\S]*?needs\\.route\\.outputs\\.production_route == 'true'`));
});
test("selected canary run id cannot be swapped with a run sharing its attempt",()=>{
 const f=fixture(),swapped={...f.d.origin,id:89};assert.equal(m.originValid(swapped,f.expected),false);
});
test("sealed evidence rejects a tampered mode",async()=>{
 const f=fixture();await f.seal();f.deps.bundle.request.mode="canary_only";f.deps.bundle.request.canary_only=true;
 assert.throws(()=>m.validateBundle(f.deps.bundle,f.context),/BUNDLE_EXPECTATION_INVALID/);
});
for(const phase of ["prepare","recover","gate","merge"]) test(`direct ${phase} entrypoint rejects canary-only artifacts before credentials or writes`,async()=>{
 const f=fixture(),directory=fs.mkdtempSync(path.join(os.tmpdir(),"maintenance-mode-"));f.request.mode="canary_only";f.request.canary_only=true;
 fs.writeFileSync(path.join(directory,"request.json"),JSON.stringify(f.request));
 await assert.rejects(m.run({phase,github:f.github,context:f.context,core:{setOutput(){}},directory}),/PRODUCTION_MODE_REQUIRED/);assert.equal(f.writes.length,0);
});
test("direct runtime mutation methods reject canary evidence",()=>{
 const f=fixture();f.expected.mode="canary_only";f.expected.canaryOnly=true;
 assert.throws(()=>m.runtime(f.deps),/PRODUCTION_MODE_REQUIRED/);assert.equal(f.writes.length,0);
});
test("collector schema never reports review or gate PASS",async()=>{
 const f=fixture(),directory=fs.mkdtempSync(path.join(os.tmpdir(),"maintenance-result-"));
 f.request.mode="canary_only";f.request.canary_only=true;fs.writeFileSync(path.join(directory,"request.json"),JSON.stringify(f.request));
 const fetcher=async url=>({ok:true,text:async()=>JSON.stringify(url.includes("rulesets?")?[{id:99,name:"Protect main",target:"branch"}]:f.d.ruleset)});
 const report=await m.run({phase:"canary",github:f.github,context:f.context,core:{setOutput(){}},auditToken:"read-token",directory,fetcher});
 assert.equal(report.result,"SNAPSHOT_COLLECTED");assert.ok(!JSON.stringify(report).includes('"PASS"'));assert.equal(f.writes.length,0);
});


// GitHub workflow_dispatch serializes github.event.inputs booleans as strings.
// Only toJSON(inputs.canary_only) carries the typed value into github-script.
for(const value of [false,true]) test(`dispatch input ${value} survives the real string-valued event payload`,async()=>{
 const f=requestWrapperFixture();f.inputs.canary_only=value;await f.execute();
 assert.equal(f.requestContext.payload.inputs.canary_only,String(value));
 assert.deepEqual(f.errors,[]);assert.equal(f.output.length,1);
 assert.equal(f.output[0].canary_only,value);
 assert.equal(f.output[0].mode,value?"canary_only":"production");
 assert.equal(f.writes.length,0);
});
for(const transport of [undefined,"","null",'"true"','"false"',"0","{}","[]","TRUE"])
 test(`typed canary input rejects invalid transport ${String(transport)} without event fallback`,async()=>{
  const f=requestWrapperFixture();await f.execute({CANARY_ONLY_JSON:transport});
  assert.equal(f.requestContext.payload.inputs.canary_only,"false");
  assert.deepEqual(f.errors,["REQUEST_MODE_INVALID"]);
  assert.equal(f.output.length,0);assert.equal(f.writes.length,0);
 });
test("dispatch input wiring serializes typed inputs and never reads the untyped payload",()=>{
 const workflow=fs.readFileSync(".github/workflows/agent-control-plane-remediation-request.yml","utf8");
 assert.match(workflow,/CANARY_ONLY_JSON: '\$\{\{ toJSON\(inputs\.canary_only\) \}\}'/);
 const source=workflow.split("          script: |\n")[1].split("      - uses: actions/upload-artifact@")[0];
 assert.doesNotMatch(source,/context\.payload\.inputs/);
 assert.match(source,/JSON\.parse\(process\.env\.CANARY_ONLY_JSON\)/);
});


// Scope regression 5199699417: execute the real authorized follower scripts.
function followerJob(name) {
  const workflow=fs.readFileSync('.github/workflows/agent-control-plane-remediation.yml','utf8');
  const match=workflow.match(new RegExp(`^  ${name}:\\n[\\s\\S]*?(?=^  [a-z][a-z0-9-]*:\\n|(?![\\s\\S]))`,'m'));
  assert.ok(match,`missing authorized follower job ${name}`);return match[0];
}
function followerScript(name) {
  const job=followerJob(name), block=job.split('          script: |\n')[1];
  assert.ok(block,`missing script in ${name}`);
  const source=block.split('\n').slice(0,block.split('\n').findIndex(line=>line.trim() && !line.startsWith('            ')))
    .map(line=>line.slice(12)).join('\n');
  // The source is the workflow script itself; only external APIs are replaced.
  return require('node:vm').runInNewContext(`(async function(require,github,context,process,core){${source}\n})`);
}
async function followerCanaryFixture(run) {
  const f=requestWrapperFixture();f.inputs.canary_only=true;await f.execute();
  assert.deepEqual(f.errors,[]);
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'scoped-canary-'));
  const request=clone(f.output[0]),outputs=[];
  fs.writeFileSync(path.join(directory,'request.json'),JSON.stringify(request));
  const fetcher=async(url,options)=>{
    assert.equal(options.method,'GET');assert.equal(options.redirect,'error');
    assert.ok(url.startsWith('https://api.github.com/repos/owner/repo/rulesets'));
    return {ok:true,text:async()=>JSON.stringify(url.includes('rulesets?')?
      [{id:99,name:'Protect main',target:'branch'}]:f.d.ruleset)};
  };
  const load=name=>{
    assert.equal(name,'./.github/scripts/agent-maintenance-runtime.cjs');
    return {...m,run:args=>m.run({...args,fetcher})};
  };
  const env={DIRECTORY:directory,TRUSTED_SHA:f.context.sha,GITHUB_RUN_ATTEMPT:'1',RULESET_AUDIT_TOKEN:'synthetic-audit-only'};
  const execute=job=>followerScript(job)(load,f.github,f.context,{env},{setOutput:(key,value)=>outputs.push([key,value])});
  try { await run({...f,directory,request,outputs,env,execute}); }
  finally { fs.rmSync(directory,{recursive:true,force:true}); }
}
test('scoped canary has no separately authorized workflow file',()=>{
  assert.equal(fs.existsSync('.github/workflows/agent-control-plane-remediation-canary.yml'),false);
  assert.match(followerJob('canary'),/needs: route/);
});
test('scoped canary executes actual request, route and collector scripts without mutation',async()=>{
  await followerCanaryFixture(async f=>{
    await f.execute('route');assert.deepEqual(f.outputs,[['production_route','false'],['canary_route','true']]);
    f.outputs.length=0;await f.execute('canary');
    assert.deepEqual(f.outputs,[['result','SNAPSHOT_COLLECTED']]);
    const report=JSON.parse(fs.readFileSync(path.join(f.directory,'canary-report.json'),'utf8'));
    assert.equal(report.result,'SNAPSHOT_COLLECTED');assert.equal(report.operationalAcceptance,'PENDING');
    assert.equal(report.requestRunId,f.request.requestRunId);assert.equal(report.requestRunAttempt,f.request.requestRunAttempt);
    assert.equal(report.headSha,f.request.headSha);assert.equal(report.baseSha,f.env.TRUSTED_SHA);
    assert.equal(f.writes.length,0);
  });
});
for(const [name,change] of [
  ['production mode',q=>{q.mode='production';q.canary_only=false;}],
  ['missing mode',q=>{delete q.mode;delete q.canary_only;}],
  ['swapped request run',q=>{q.requestRunId++;}],
  ['swapped request attempt',q=>{q.requestRunAttempt++;}],
  ['replacement authorization',q=>{q.authorization.commentId++;}],
]) test(`scoped canary real job denies ${name} without writes or report`,async()=>{
  await followerCanaryFixture(async f=>{
    change(f.request);fs.writeFileSync(path.join(f.directory,'request.json'),JSON.stringify(f.request));
    await assert.rejects(f.execute('canary'));assert.equal(f.writes.length,0);
    assert.deepEqual(f.outputs,[]);assert.equal(fs.existsSync(path.join(f.directory,'canary-report.json')),false);
  });
});
test('scoped follower routes canary and production jobs exclusively from explicit outputs',()=>{
  const eligible=(name,route)=>{
    const condition=followerJob(name).match(/^    if: (.+)$/m)?.[1];assert.ok(condition);
    const needs={route,...Object.fromEntries(['prepare','independent-review','recover','gate'].map(x=>[x,{result:'success'}]))};
    // Evaluate only the simple actual equality/conjunction job conditions used here.
    assert.match(condition,/^[a-zA-Z0-9_.\s'=&!-]+$/);
    return require('node:vm').runInNewContext(`Boolean(${condition.replace(/needs\.([a-z][a-z0-9-]*)/g,'needs["$1"]')})`,{needs});
  };
  const canary={result:'success',outputs:{canary_route:'true',production_route:'false'}};
  const ordinary={result:'success',outputs:{canary_route:'false',production_route:'true'}};
  assert.equal(eligible('canary',canary),true);assert.equal(eligible('canary',ordinary),false);
  for(const route of [{result:'success',outputs:{}},{result:'failure',outputs:canary.outputs},
    {result:'success',outputs:{canary_route:'true',production_route:'true'}}]) assert.equal(eligible('canary',route),false);
  for(const job of ['prepare','independent-review','recover','gate','merge']) {
    assert.equal(eligible(job,canary),false,`${job} cannot run for canary`);
    assert.equal(eligible(job,ordinary),true,`${job} retains its ordinary route`);
    assert.equal(eligible(job,{result:'success',outputs:{}}),false);
  }
});
test('scoped collector retains source, request-artifact and credential boundaries',()=>{
  const job=followerJob('canary');
  assert.match(job,/ref: '\$\{\{ github\.workflow_sha \}\}'/);assert.match(job,/persist-credentials: false/);
  assert.match(job,/run-id: \$\{\{ github\.event\.workflow_run\.id \}\}/);
  assert.match(job,/control-plane-request-\$\{\{ github\.event\.workflow_run\.id \}\}-\$\{\{ github\.event\.workflow_run\.run_attempt \}\}/);
  assert.match(job,/permissions: \{actions: read, contents: read, issues: read, pull-requests: read\}/);
  assert.match(job,/phase:'canary'/);assert.match(job,/payload:context\.payload/);
  assert.doesNotMatch(job,/contents: write|issues: write|pull-requests: write|statuses: write|MERGE_TOKEN|OPENAI_API_KEY|codex-action|phase:'(?:recover|gate|merge)'/);
  assert.match(job,/maintenance-read-only-canary-\$\{\{ github\.run_id \}\}-\$\{\{ github\.run_attempt \}\}/);
});

// Diagnostic 34869646359: complete-operation linkage identity and audit.
const linkageComments=(f,side)=>f.d[`${side}Comments`].filter(x=>String(x.body).includes('<!-- agent-link:v1'));
for(const side of ['issue','pr']) for(const change of ['remove','replace','edit','retire']) {
 test(`diagnostic linkage ${side} ${change} after prepare blocks the first write`,async()=>{
  const f=fixture();f.addLinks();const r=await f.seal();
  const arr=f.d[`${side}Comments`],item=linkageComments(f,side)[0];
  if(change==='remove')arr.splice(arr.indexOf(item),1);
  if(change==='replace')item.id+=1000;
  if(change==='edit')item.body+='\nchanged audit';
  if(change==='retire')arr.push({id:900,user:{login:'github-actions[bot]'},body:'<!-- agent-link-retired:v1 repo=owner/repo issue=126 pr=127 -->'});
  await assert.rejects(executeRecover(r),/LINKAGE/);assert.equal(f.writes.length,0);
 });
}
for(const change of ['remove','replace','edit'])test(`diagnostic first created link ${change} blocks the next link write`,async()=>{
 const f=fixture();const r=await f.seal();f.d.afterWrite=(d,w)=>{
  if(w.length===1){const x=linkageComments(f,'issue')[0];if(change==='remove')d.issueComments.splice(d.issueComments.indexOf(x),1);
   if(change==='replace')x.id+=1000;if(change==='edit')x.body+='edited';}
 };
 await assert.rejects(executeRecover(r),/LINKAGE/);assert.equal(f.writes.length,1);
 assert.equal(f.writes[0].name,'comment');assert.equal(f.writes.filter(x=>x.name==='labels').length,0);
});
test('diagnostic recovery writes bound intention and completion audits once on both objects',async()=>{
 const f=fixture();f.addLinks();const r=await f.seal();await r.recover();
 const audit=f.writes.filter(x=>x.name==='comment'&&x.args.body.includes('agent-maintenance-recovery:v1'));
 assert.equal(audit.length,4);
 for(const side of [126,127])for(const phase of ['started','completed']) {
  const item=audit.find(x=>x.args.issue_number===side&&x.args.body.includes(`phase=${phase}`));assert.ok(item);
  for(const value of ['alice','88','900',f.expected.headSha,f.expected.specHash,f.request.reason])assert.ok(item.args.body.includes(value));
 }
 const before=f.writes.length;await r.recover();assert.equal(f.writes.length,before);
});
test('diagnostic partial failure never records recovery completed',async()=>{
 const f=fixture();f.addLinks();const r=await f.seal();const orig=f.github.rest.issues.setLabels;
 f.github.rest.issues.setLabels=async args=>{if(args.issue_number===126)throw Error('interrupted');return orig(args);};
 await assert.rejects(r.recover(),/interrupted/);
 assert.ok(!f.writes.some(x=>x.name==='comment'&&x.args.body.includes('phase=completed')));
 assert.ok(f.writes.some(x=>x.name==='comment'&&x.args.body.includes('phase=started')));
});
test('diagnostic gate refuses missing recovery completion audit',async()=>{
 const f=fixture();f.addLinks();f.d.pr.labels=['agent:pr'];f.d.issue.labels=['type:implementation','agent:pr'];
 const r=await f.seal();await assert.rejects(r.gate(),/RECOVERY_AUDIT|RECOVERY_RECEIPT/);assert.equal(f.writes.length,0);
});
for(const change of ['delete','replace'])test(`diagnostic completed recovery never re-creates ${change}d intention evidence`,async()=>{
 const f=fixture();f.addLinks();const r=await f.seal();await r.recover();const count=f.writes.length;
 const start=f.d.issueComments.find(x=>x.body.includes('agent-maintenance-recovery:v1')&&x.body.includes('phase=started'));
 if(change==='delete')f.d.issueComments.splice(f.d.issueComments.indexOf(start),1);else start.id+=1000;
 await assert.rejects(m.runtime(f.deps).recover(),/RECOVERY_AUDIT/);assert.equal(f.writes.length,count);
});
for(const side of ['issue','pr'])test(`diagnostic new gate runtime refuses substituted ${side} link after recovery`,async()=>{
 const f=fixture();const r=await f.seal();f.deps.recoveryReceipt=await executeRecover(r);const count=f.writes.length;
 linkageComments(f,side)[0].id+=1000;
 await assert.rejects(async()=>m.runtime(f.deps).gate(),/RECOVERY_AUDIT|LINKAGE/);assert.equal(f.writes.length,count);
});
for(const timing of ['after-first-intent','between-labels']) test(`diagnostic deleted recovery intent ${timing} stops next write`,async()=>{
 const f=fixture();f.addLinks();const r=await f.seal();let deleted=false,atCount=0;
 f.d.afterWrite=(d,w)=>{
  if(!deleted && (timing==='after-first-intent' ? w.length===1 : w.at(-1).name==='labels')) {
   const index=d.issueComments.findIndex(x=>x.body.includes('agent-maintenance-recovery:v1')&&x.body.includes('phase=started'));
   if(index>=0){d.issueComments.splice(index,1);deleted=true;atCount=w.length;}
  }
 };
 await assert.rejects(r.recover(),/RECOVERY_AUDIT/);assert.equal(deleted,true);
 assert.equal(f.writes.length,atCount);assert.ok(!f.writes.some(x=>x.name==='status'||x.name==='merge'));
});

// Review 5206960781: immutable cross-job recovery provenance.
const auditComment = (f, side, phase) => f.d[`${side}Comments`].find(x =>
  String(x.body).includes('agent-maintenance-recovery:v1') && String(x.body).includes(`phase=${phase}`));
const mutateAudit = (f, side, phase, change) => {
  const rows = f.d[`${side}Comments`], item = auditComment(f, side, phase);
  assert.ok(item);
  if (change === 'delete') rows.splice(rows.indexOf(item), 1);
  if (change === 'recreate') { rows.splice(rows.indexOf(item), 1); rows.push({...clone(item), id: item.id + 5000}); }
  if (change === 'edit') item.body += '\nsubstituted';
  if (change === 'duplicate') rows.push({...clone(item), id: item.id + 5000});
};
for (const boundary of ['recover-gate', 'gate-merge']) for (const side of ['issue', 'pr'])
 for (const phase of ['started', 'completed']) for (const change of ['delete', 'recreate', 'edit', 'duplicate'])
test(`receipt rejects ${change}d ${side} ${phase} audit at ${boundary}`, async () => {
  const f = fixture(); f.addLinks(); const recovering = await f.seal();
  const receipt = await recovering.recover(); f.deps.recoveryReceipt = receipt;
  if (boundary === 'gate-merge') await m.runtime(f.deps).gate();
  const before = f.writes.length; mutateAudit(f, side, phase, change);
  const consumer = m.runtime(f.deps);
  await assert.rejects(boundary === 'recover-gate' ? consumer.gate() : consumer.merge(), /RECOVERY_AUDIT/);
  assert.equal(f.writes.length, before);
});

test('receipt permits legitimate separate recover, gate and merge runtimes', async () => {
  const f = fixture(); f.addLinks(); const recovering = await f.seal();
  const receipt = await recovering.recover(); f.deps.recoveryReceipt = clone(receipt);
  await m.runtime(f.deps).gate();
  assert.equal(await m.runtime(f.deps).merge(), 'f'.repeat(40));
});

for (const change of ['missing', 'hash', 'run', 'attempt', 'source', 'authorization', 'bundle'])
test(`receipt rejects ${change} provenance before a gate write`, async () => {
  const f = fixture(); f.addLinks(); const recovering = await f.seal();
  const receipt = await recovering.recover();
  if (change === 'missing') f.deps.recoveryReceipt = null;
  else {
    f.deps.recoveryReceipt = clone(receipt);
    if (change === 'hash') f.deps.recoveryReceipt.digest = '0'.repeat(64);
    if (change === 'run') f.deps.recoveryReceipt.producer.runId++;
    if (change === 'attempt') f.deps.recoveryReceipt.producer.runAttempt++;
    if (change === 'source') f.deps.recoveryReceipt.producer.sourceSha = 'c'.repeat(40);
    if (change === 'authorization') f.deps.recoveryReceipt.authorization.commentId++;
    if (change === 'bundle') f.deps.recoveryReceipt.evidenceBundleDigest = '0'.repeat(64);
  }
  const before = f.writes.length;
  await assert.rejects(async () => m.runtime(f.deps).gate(), /RECOVERY_RECEIPT/);
  assert.equal(f.writes.length, before);
});

test('follower transports one immutable recover receipt to both consumers', () => {
  const workflow = fs.readFileSync(path.join(__dirname, '../workflows/agent-control-plane-remediation.yml'), 'utf8');
  assert.equal((workflow.match(/name: maintenance-recovery-receipt-\$\{\{ github\.run_id \}\}-\$\{\{ github\.run_attempt \}\}/g) || []).length, 3);
  assert.equal((workflow.match(/uses: actions\/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02\n\s+with:\n\s+name: maintenance-recovery-receipt/g) || []).length, 1);
  for (const job of ['gate', 'merge']) assert.match(followerJob(job), /Consume original recovery receipt[\s\S]*maintenance-recovery-receipt/);
});

test('historical ruleset sync is now a GET-only audit and preserves stronger review settings', async () => {
  const workflow = fs.readFileSync('.github/workflows/agent-ruleset-sync.yml', 'utf8');
  assert.match(workflow, /ref: \$\{\{ github\.workflow_sha \}\}/);
  assert.doesNotMatch(workflow, /--method PUT|contents: write|pull-requests: write/);
  const script = workflow.split('          script: |\n')[1].split('\n').map(line => line.replace(/^            /, '')).join('\n');
  const f = fixture(), calls = [], notices = [];
  f.d.ruleset.rules.find(r => r.type === 'pull_request').parameters.required_approving_review_count = 2;
  const original = clone(f.d.ruleset);
  const fetcher = async (url, options) => {
    assert.equal(options.method, 'GET'); calls.push(url);
    return {ok: true, text: async () => JSON.stringify(url.includes('/rulesets?') ? [{id: 99, name: 'Protect main', target: 'branch'}] : f.d.ruleset)};
  };
  const runtime = {...m, rulesetReader: repo => m.rulesetReader(repo, 'synthetic-audit-token', fetcher)};
  const summary = {addHeading() {return this;}, addRaw() {return this;}, async write() {}};
  const execute = new (Object.getPrototypeOf(async function(){}).constructor)('require', 'context', 'github', 'core', 'process', script);
  const context = {repo: {owner:'owner',repo:'repo'}, payload:{repository:{default_branch:'main'}}, ref:'refs/heads/main', sha:f.d.main};
  await execute(() => runtime, context, f.github, {notice: value => notices.push(value), summary}, {env:{}});
  assert.equal(calls.length, 2); assert.equal(notices.length, 1);
  assert.deepEqual(f.d.ruleset, original); assert.equal(f.writes.length, 0);
  delete f.d.ruleset.bypass_actors;
  await assert.rejects(execute(() => runtime, context, f.github, {notice: value => notices.push(value), summary}, {env:{}}));
  assert.equal(notices.length, 1);
  const count = calls.length;
  await assert.rejects(execute(() => runtime, {...context, sha:'f'.repeat(40)}, f.github, {notice() {}, summary}, {env:{}}), /STALE_OR_NON_DEFAULT/);
  assert.equal(calls.length, count);
});
