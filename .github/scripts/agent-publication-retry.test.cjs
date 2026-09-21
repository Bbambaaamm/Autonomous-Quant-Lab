"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), os = require("node:os"), path = require("node:path");
const {execFileSync, spawnSync} = require("node:child_process");
const a = require("./agent-autonomy.cjs");
const {publicationAuthorized} = require("./agent-publication-guard.cjs");

function fixture(t, mode = "existing") {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "publication-reuse-"));
  t.after(() => fs.rmSync(root, {recursive:true,force:true}));
  const candidate = path.join(root,"candidate"), remote = path.join(root,"remote.git");
  fs.mkdirSync(candidate);
  const git = (...args) => execFileSync("git", args, {cwd:candidate,encoding:"utf8",stdio:["ignore","pipe","pipe"]}).trim();
  git("init","-q"); git("config","user.name","test"); git("config","user.email","test@example.invalid");
  fs.writeFileSync(path.join(candidate,"data.txt"),"base\n"); git("add","."); git("commit","-qm","base");
  const base = git("rev-parse","HEAD");
  git("init","--bare","-q",remote); git("remote","add","origin",remote);
  if(mode === "existing")git("push","-q","origin","HEAD:refs/heads/candidate");
  fs.writeFileSync(path.join(candidate,"data.txt"),"sealed\n"); git("commit","-qam","sealed");
  const expected = git("rev-parse","HEAD");
  function run({push = 'git push -q origin HEAD:refs/heads/candidate', auth = ':', read = 'git ls-remote --refs origin refs/heads/candidate | awk \'{print $1}\'', source = mode === "existing" ? base : "", result = expected, before = ''}={}) {
    const script = `set -euo pipefail
source "$RETRY_SCRIPT"
sleep(){ :; }
printf 0 > "$COUNTER"
printf 0 > "$AUTH_COUNTER"
read_ref(){ ${read}; }
authorize(){ n=$(cat "$AUTH_COUNTER"); n=$((n+1)); printf '%s' "$n" > "$AUTH_COUNTER"; ${auth}; }
push_commit(){ n=$(cat "$COUNTER"); n=$((n+1)); printf '%s' "$n" > "$COUNTER"; ${push}; }
${before}
publication_retry "$SOURCE" "$EXPECTED" "$BASE" read_ref authorize push_commit`;
    const processResult = spawnSync("bash",["-c",script],{cwd:candidate,encoding:"utf8",env:{...process.env,
      RETRY_SCRIPT:path.resolve(".github/scripts/agent-publication-retry.sh"),COUNTER:path.join(root,"pushes"),AUTH_COUNTER:path.join(root,"authorizations"),
      SOURCE:source,EXPECTED:result,BASE:base}});
    return {...processResult,pushes:Number(fs.readFileSync(path.join(root,"pushes"))),auths:Number(fs.readFileSync(path.join(root,"authorizations")))};
  }
  return {git,base,expected,run};
}

test("transient push failure reuses the exact sealed commit and refreshes authorization", t=>{
  const f=fixture(t),r=f.run({push:'if [[ "$n" == 1 ]]; then return 1; fi; git push -q origin HEAD:refs/heads/candidate'});
  assert.equal(r.status,0,r.stderr); assert.equal(r.pushes,2); assert.equal(r.auths,2);
  assert.equal(f.git("ls-remote","--refs","origin","refs/heads/candidate").split(/\s/)[0],f.expected);
  assert.equal(f.git("rev-parse","HEAD"),f.expected);
});
test("lost push acknowledgement does not push or generate a second time",t=>{
  const f=fixture(t),r=f.run({push:'git push -q origin HEAD:refs/heads/candidate; return 1'});
  assert.equal(r.status,0,r.stderr);assert.equal(r.pushes,1);
});
test("already accepted sealed commit is recognized without a push",t=>{
  const f=fixture(t);f.git("push","-q","origin","HEAD:refs/heads/candidate");
  const r=f.run();assert.equal(r.status,0,r.stderr);assert.equal(r.pushes,0);assert.equal(r.auths,1);
});
test("new Builder branch retries only the same sealed commit",t=>{
  const f=fixture(t,"new"),r=f.run({push:'if [[ "$n" == 1 ]]; then return 1; fi; git push -q origin HEAD:refs/heads/candidate'});
  assert.equal(r.status,0,r.stderr);assert.equal(r.pushes,2);
});
test("exhaustion is bounded to three pushes with no regeneration",t=>{
  const f=fixture(t),r=f.run({push:'return 1'});assert.equal(r.status,1);assert.equal(r.pushes,3);assert.equal(r.auths,3);
  assert.equal(f.git("rev-parse","HEAD"),f.expected);
});
test("changed authorization stops before a second push",t=>{
  const r=fixture(t).run({push:'return 1',auth:'[[ "$n" == 1 ]]'});
  assert.equal(r.status,2);assert.equal(r.pushes,1);assert.equal(r.auths,2);
});
test("unexpected branch tip is never overwritten",t=>{
  const r=fixture(t).run({read:'printf "%040d\\n" 1'});assert.equal(r.status,2);assert.equal(r.pushes,0);
});
test("changed local commit is rejected before authorization or push",t=>{
  const r=fixture(t).run({result:"a".repeat(40)});assert.equal(r.status,2);assert.equal(r.pushes,0);assert.equal(r.auths,0);
});
test("different source parent is rejected",t=>{
  const r=fixture(t).run({source:"a".repeat(40)});assert.equal(r.status,2);assert.equal(r.pushes,0);
});
test("remote read failure does not permit a blind push",t=>{
  const r=fixture(t).run({read:'return 1'});assert.equal(r.status,1);assert.equal(r.pushes,0);assert.equal(r.auths,3);
});

function authFixture() {
  const repo="Bbambaaamm/Autonomous-Quant-Lab",labels=["type:implementation","agent:pr"];
  const issue={number:42,title:"Approved",body:"Exact scope",state:"open",labels};
  const spec=a.issueSpecHash(issue),base="b".repeat(40),source="c".repeat(40),expected="d".repeat(40);
  const link={user:{login:"github-actions[bot]"},body:`<!-- agent-link:v1 repo=${repo} issue=42 pr=77 -->`};
  const auth={id:1,user:{login:"github-actions[bot]"},body:a.authorizationMarker({repo,issueNumber:42,specHash:spec,actor:"Bbambaaamm",runId:123})};
  return {env:{GH_REPO:repo,ISSUE:"42",PR:"77",SPEC:spec,BASE:base,SHA:source,EXPECTED_RESULT:expected,HEAD_REF:"agent/fix"},
    repository:{full_name:repo,default_branch:"main"},main:{commit:{sha:base}},issue,
    pr:{number:77,state:"open",body:"Agent-Issue: #42",labels:["agent:pr"],base:{ref:"main",sha:base},head:{sha:source,ref:"agent/fix",repo:{full_name:repo}}},
    issueComments:[auth,link],prComments:[link]};
}
test("fresh publication guard accepts exact source or already published result",()=>{
  const f=authFixture();assert.equal(publicationAuthorized(f),true);
  f.pr.head.sha=f.env.EXPECTED_RESULT;assert.equal(publicationAuthorized(f),true);
});
test("fresh publication guard rejects scope, authority, base, lifecycle, linkage and fork drift",()=>{
  const changes=[f=>f.issue.body+=' changed',f=>f.issue.state='closed',f=>f.env.SPEC='stale',
    f=>f.main.commit.sha='e'.repeat(40),f=>f.pr.base.sha='e'.repeat(40),f=>f.pr.head.sha='e'.repeat(40),
    f=>f.pr.head.repo.full_name='fork/repo',f=>f.pr.head.ref='other',f=>f.pr.state='closed',
    f=>f.issue.labels=['type:implementation','agent:needs-human'],f=>f.prComments=[],f=>f.issueComments=[]];
  for(const change of changes){const f=authFixture();change(f);assert.equal(publicationAuthorized(f),false);}
});
test("both publishers retry only transport with no model credential or force push",()=>{
  for(const name of ['agent-builder-publish.yml','agent-ci-fixer.yml']){
    const w=fs.readFileSync('.github/workflows/'+name,'utf8');
    const publish=name==='agent-ci-fixer.yml'?w.split('\n  trusted-publish:')[1].split('\n  fail-closed-finalizer:')[0]:w;
    assert.match(publish,/publication_retry/);assert.doesNotMatch(publish,/OPENAI_API_KEY|codex-action|push[^\n]*--force/);
  }
});

function runPrCreation(t,{loseResponse=false,alwaysFail=false,wrongHead=false,revoke=false}={}) {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'pr-reuse-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const workflow=fs.readFileSync('.github/workflows/agent-builder-publish.yml','utf8');
  const begin=workflow.indexOf('            for publication_attempt in 1 2 3; do');
  const end=workflow.indexOf('\n          fi',begin);
  const code=workflow.slice(begin,end);
  assert.ok(begin>0&&end>begin);
  const script=`set -euo pipefail
printf 0 > "$ROOT/calls"
printf 0 > "$ROOT/auths"
printf '[]' > "$ROOT/prs"
sleep(){ :; }
prs(){ cat "$ROOT/prs"; }
owned(){ [[ "$1" == sealed ]]; }
revalidate_issue(){ n=$(cat "$ROOT/auths"); n=$((n+1)); printf '%s' "$n" > "$ROOT/auths"; [[ "$REVOKE" != true || "$n" == 1 ]]; }
gh(){
 n=$(cat "$ROOT/calls"); n=$((n+1)); printf '%s' "$n" > "$ROOT/calls"
 if [[ "$ALWAYS_FAIL" == true ]]; then return 1; fi
 if [[ "$LOSE_RESPONSE" != true && "$n" == 1 ]]; then return 1; fi
 if [[ "$WRONG_HEAD" == true ]]; then printf '[{"head":{"sha":"other"}}]' > "$ROOT/prs"; else printf '[{"head":{"sha":"sealed"}}]' > "$ROOT/prs"; fi
 if [[ "$LOSE_RESPONSE" == true ]]; then return 1; fi
}
${code}`;
  const r=spawnSync('bash',['-c',script],{encoding:'utf8',env:{...process.env,ROOT:root,
    REVOKE:String(revoke),LOSE_RESPONSE:String(loseResponse),ALWAYS_FAIL:String(alwaysFail),WRONG_HEAD:String(wrongHead),
    GH_REPO:'owner/repo',branch:'agent/issue',ISSUE:'42',RUNNER_TEMP:root}});
  return {...r,calls:Number(fs.readFileSync(path.join(root,'calls'))),auths:Number(fs.readFileSync(path.join(root,'auths')))};
}
test('actual Builder PR loop recovers lost creation response without duplicate creation',t=>{
 const r=runPrCreation(t,{loseResponse:true});assert.equal(r.status,0,r.stderr);assert.equal(r.calls,1);
});
test('actual Builder PR loop retries transient creation against the same branch',t=>{
 const r=runPrCreation(t);assert.equal(r.status,0,r.stderr);assert.equal(r.calls,2);assert.equal(r.auths,2);
});
test('actual Builder PR loop stops after three failures',t=>{
 const r=runPrCreation(t,{alwaysFail:true});assert.notEqual(r.status,0);assert.equal(r.calls,3);
});
test('actual Builder PR loop rejects changed authority or mismatched existing head',t=>{
 const revoked=runPrCreation(t,{revoke:true});assert.notEqual(revoked.status,0);assert.equal(revoked.calls,1);
 const wrong=runPrCreation(t,{wrongHead:true,loseResponse:true});assert.notEqual(wrong.status,0);assert.equal(wrong.calls,1);
});
