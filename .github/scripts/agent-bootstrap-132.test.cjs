"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const b = require("./agent-bootstrap-132.cjs");
const required = ["agent-pipeline", "quality", "unit-research", "api", "integration-postgres", "frontend", "security", "container-build", "production-smoke"];

const base = () => ({ repository:b.BINDING.repository, defaultBranch:"main", trustedBaseSha:b.BINDING.trustedBaseSha, mainContainsTrustedBase:true, mainBootstrapPathsOnly:true,
  issue:{number:130,state:"open"}, issueIsImplementation:true, authorizationOk:true, specHash:b.BINDING.specHash,
  pr:{number:131,state:"open",draft:false,base:{ref:"main"},head:{sha:b.BINDING.headSha}}, prIssueNumber:130,
  linkageRecoverable:true,lifecycle:"needs-human",changedFilesComplete:true,scopeAllowed:true,candidateDescendsTrustedBase:true });

test("hard binding accepts only the authorized tuple",()=>assert.deepEqual(b.bindingDecision(base()),{ok:true}));
for (const [name, mutate, reason] of [
  ["head drift",x=>x.pr.head.sha="a".repeat(40),"PR_BINDING_CHANGED"], ["auth drift",x=>x.specHash="b".repeat(64),"ISSUE_BINDING_CHANGED"],
  ["link drift",x=>x.linkageRecoverable=false,"LINKAGE_CHANGED"], ["lifecycle drift",x=>x.lifecycle="verified","LIFECYCLE_CHANGED"],
  ["main drift",x=>x.mainBootstrapPathsOnly=false,"MAIN_BINDING_CHANGED"],
]) test(name,()=>{const x=base();mutate(x);assert.equal(b.bindingDecision(x).reason,reason);});

test("newest exact-attempt CI requires all nine green jobs",()=>{
  const run={name:"CI",event:"pull_request",status:"completed",conclusion:"success",head_sha:b.BINDING.headSha,pull_requests:[{number:131}],run_attempt:2};
  const jobs=required.map(name=>({name,conclusion:"success",run_attempt:2}));
  assert.deepEqual(b.ciDecision(run,jobs,required),{ok:true});
  assert.equal(b.ciDecision(run,jobs.map((j,i)=>i?j:{...j,run_attempt:1}),required).reason,"CI_JOBS_CHANGED");
  assert.equal(b.ciDecision({...run,run_attempt:3},jobs,required).reason,"CI_JOBS_CHANGED");
});

test("ruleset drift fails closed",()=>{
  const ruleset={name:"Protect main",target:"branch",enforcement:"active",bypass_actors:[],conditions:{ref_name:{include:["refs/heads/main"],exclude:[]}},rules:[{type:"deletion"},{type:"non_fast_forward"},{type:"pull_request"},{type:"required_status_checks",parameters:{strict_required_status_checks_policy:true,required_status_checks:[{context:"agent-verified-gate",integration_id:15368}]}}]};
  assert.deepEqual(b.rulesetDecision(ruleset),{ok:true});
  assert.equal(b.rulesetDecision({...ruleset,bypass_actors:[{}]}).reason,"RULESET_BYPASS_PRESENT");
  const changed=structuredClone(ruleset); changed.rules[3].parameters.required_status_checks[0].integration_id=1;
  assert.equal(b.rulesetDecision(changed).reason,"RULESET_GATE_CHANGED");
});

test("paired writes require a fresh guard read between writes",()=>{
  assert.deepEqual(b.pairedWriteDecision({guardsCurrent:true,stage:0},0),{ok:true,nextStage:1});
  assert.equal(b.pairedWriteDecision({guardsCurrent:false,stage:1},1).reason,"MUTABLE_GUARD_DRIFT");
  assert.equal(b.pairedWriteDecision({guardsCurrent:true,stage:0},1).reason,"PAIRED_WRITE_STAGE_CHANGED");
});


test("post-bootstrap main accepts only commit-by-commit bootstrap paths while candidate stays on trusted baseline",()=>{
  const commits=[{sha:"1"},{sha:"2"}], comparison={status:"ahead",total_commits:2,commits,files:[{filename:".github/workflows/agent-bootstrap-132.yml"},{filename:"docs/adr/0008-one-time-control-plane-bootstrap.md"}]};
  const details=[[{filename:".github/workflows/agent-bootstrap-132.yml"}],[{filename:"docs/adr/0008-one-time-control-plane-bootstrap.md"}]];
  assert.deepEqual(b.mainLineageDecision(comparison,details),{ok:true});
  assert.deepEqual(b.bindingDecision(base()),{ok:true});
  assert.equal(b.mainLineageDecision(comparison,[details[0],[{filename:"README.md"}]]).reason,"UNRELATED_MAIN_DRIFT");
  assert.equal(b.mainLineageDecision({...comparison,status:"behind"},details).reason,"TRUSTED_BASE_NOT_ON_MAIN");
});

const ruleset=()=>({name:"Protect main",target:"branch",enforcement:"active",bypass_actors:[],conditions:{ref_name:{include:["refs/heads/main"],exclude:[]}},rules:[{type:"deletion"},{type:"non_fast_forward"},{type:"pull_request"},{type:"required_status_checks",parameters:{strict_required_status_checks_policy:true,required_status_checks:[{context:"agent-verified-gate",integration_id:15368}]}}]});
const reviewContext=()=>({version:1,binding:b.BINDING,issue:{number:130,title:"authorized",body:"exact body",classification:["type:implementation"]},authorization:{ok:true,specHash:b.BINDING.specHash,marker:`agent-merge-authorization:v2 spec=${b.BINDING.specHash}`},governanceFiles:b.GOVERNANCE_PATHS.map(path=>({path,content:"trusted"})),diff:{base:b.BINDING.trustedBaseSha,head:b.BINDING.headSha,content:"diff --git"},ci:{runId:1,attempt:1,headSha:b.BINDING.headSha,jobs:required.map(name=>({name,conclusion:"success",run_attempt:1}))},ruleset:ruleset()});
test("independent review context blocks when any trusted input is missing or stale",()=>{
  assert.deepEqual(b.reviewContextDecision(reviewContext()),{ok:true});
  for(const mutate of [x=>x.issue.body=undefined,x=>x.authorization.marker="",x=>x.governanceFiles.pop(),x=>x.diff.head="f".repeat(40),x=>x.ci.jobs.pop(),x=>x.ruleset.bypass_actors.push({})]){const x=reviewContext();mutate(x);assert.equal(b.reviewContextDecision(x).ok,false);}
});

test("linkage controller permits zero, one, and two-sided recovery and catches between-write drift",()=>{
  const state={bodyIssueNumber:130,issueExact:false,prExact:false,issueConflicting:false,prConflicting:false};
  assert.deepEqual(b.linkageRecoveryDecision(state,0),{ok:true,next:0});
  state.issueExact=true;assert.deepEqual(b.linkageRecoveryDecision(state,1),{ok:true,next:1});
  state.prExact=true;assert.deepEqual(b.linkageRecoveryDecision(state,2),{ok:true,next:2});
  assert.equal(b.linkageRecoveryDecision({...state,prExact:false},2).reason,"LINKAGE_STAGE_INCOMPLETE");
  assert.equal(b.linkageRecoveryDecision({...state,prConflicting:true},2).reason,"LINKAGE_CONFLICT");
  assert.equal(b.linkageRecoveryDecision({...state,bodyIssueNumber:129},0).reason,"LINKAGE_CONFLICT");
});
