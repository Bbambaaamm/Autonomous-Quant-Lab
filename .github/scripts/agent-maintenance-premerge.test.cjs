'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const live=require('./agent-maintenance-premerge.cjs'),a=require('./agent-autonomy.cjs');
function fixture() {
 const repository='owner/repo',headSha='a'.repeat(40),baseSha='b'.repeat(40),calls=[];
 const issue={number:126,title:'Repair',body:'Exact task',labels:['type:implementation','agent:needs-human'],state:'open'};
 const pr={number:127,state:'open',draft:false,auto_merge:null,body:'Agent-Issue: #126',head:{sha:headSha,repo:{full_name:repository}},base:{sha:baseSha,ref:'main',repo:{full_name:repository}}};
 const auth={id:1,user:{login:'github-actions[bot]'},body:a.authorizationMarker({repo:repository,issueNumber:126,specHash:a.issueSpecHash(issue),actor:'alice',runId:8})};
 const d={pr,issue,auth,ruleset:{id:99,name:'Protect main',enforcement:'active',bypass_actors:[]},fail:false};
 const get=async path=>{
  calls.push(path);if(d.fail)throw new Error('HTTP_403');
  const u=new URL('https://api.github.com'+path),p=u.pathname.replace('/repos/owner/repo','');
  if(p==='/pulls/127')return structuredClone(d.pr);
  if(p==='')return {full_name:repository,default_branch:'main'};
  if(p==='/branches/main')return {commit:{sha:baseSha}};
  if(p==='/issues/126')return structuredClone(d.issue);
  if(p==='/issues/126/comments')return [structuredClone(d.auth)];
  if(p==='/actions/workflows/ci.yml')return {id:2,path:'.github/workflows/ci.yml'};
  if(p==='/actions/workflows/ci.yml/runs')return {workflow_runs:[{id:3,workflow_id:2,path:'.github/workflows/ci.yml',head_sha:headSha,event:'pull_request',pull_requests:[{number:127}],head_repository:{full_name:repository},run_attempt:1,status:'in_progress',conclusion:null}]};
  if(p==='/actions/runs/3/jobs')return {jobs:[{id:1,name:'quality',run_attempt:1,conclusion:'success'}]};
  if(p==='/rulesets')return [{id:99,name:'Protect main',target:'branch'}];
  if(p==='/rulesets/99')return structuredClone(d.ruleset);
  throw Error('unexpected read '+p);
 };
 return {get,repository,prNumber:127,headSha,baseSha,d,calls};
}
test('premerge candidate can collect and deny identities without production-origin fabrication',async()=>{
 const f=fixture(),r=await live.canary(f);assert.equal(r.productionEvidence,false);assert.equal(r.operationalAcceptance,'PENDING');assert.equal(r.denials.length,2);
 assert.equal(r.ci.status,'in_progress');assert.ok(!JSON.stringify(r).includes('"PASS"'));assert.ok(f.calls.every(x=>x.startsWith('/repos/owner/repo')));
});
test('premerge omitted bypass actors remain UNKNOWN, never empty or approved',async()=>{
 const f=fixture();delete f.d.ruleset.bypass_actors;const r=await live.canary(f);
 assert.equal(r.protection.bypassVisibility,'UNKNOWN');assert.equal(r.protection.bypassCount,null);assert.equal(r.operationalAcceptance,'PENDING');
});
for(const kind of ['head','base','draft','closed','auth','permission'])test(`premerge rejects ${kind}`,async()=>{
 const f=fixture();if(kind==='head')f.d.pr.head.sha='c'.repeat(40);if(kind==='base')f.d.pr.base.sha='c'.repeat(40);
 if(kind==='draft')f.d.pr.draft=true;if(kind==='closed')f.d.pr.state='closed';if(kind==='auth')f.d.issue.body+=' changed';if(kind==='permission')f.d.fail=true;
 await assert.rejects(live.canary(f));
});
test('premerge workflow uses READ-only token and never supplies repository secrets',()=>{
 const fs=require('node:fs'),workflow=fs.readFileSync('.github/workflows/agent-maintenance-guard-tests.yml','utf8');
 const job=workflow.split('  premerge-api-canary:')[1];assert.ok(job);
 assert.match(job,/permissions: \{actions: read, contents: read, issues: read, pull-requests: read\}/);
 assert.doesNotMatch(job,/\$\{\{\s*secrets(?:\.|\[)|contents: write|issues: write|pull-requests: write|statuses: write|actions: write|pull_request_target|workflow_dispatch/);
 assert.match(job,/github\.request\(`GET \$\{path\}`\)/);assert.match(job,/persist-credentials: false/);
});
