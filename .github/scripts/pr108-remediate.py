from pathlib import Path
import re

def read(path):
    return Path(path).read_text()

def write(path, text):
    Path(path).write_text(text)

def replace_once(path, old, new):
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, got {count}: {old[:120]!r}")
    write(path, text.replace(old, new, 1))

pipeline = ".github/scripts/agent-pipeline.cjs"
text = read(pipeline)
start = text.index("function hasDurableLink(")
end = text.index("function hasExactShaReviewAcknowledgement(", start)
replacement = r'''function trustedCommentLines(comments) {
  return comments
    .filter((comment) => comment.user?.login === "github-actions[bot]")
    .flatMap((comment) => (comment.body || "").split("\n"));
}

function retiredLinkSet(comments, { owner, repo }) {
  const prefix = `<!-- agent-link-retired:v1 repo=${owner}/${repo} issue=`;
  const matches = trustedCommentLines(comments)
    .filter((line) => line.startsWith(prefix) && line.endsWith(" -->"))
    .map((line) => /^<!-- agent-link-retired:v1 repo=[^ ]+ issue=([1-9][0-9]*) pr=([1-9][0-9]*) -->$/.exec(line))
    .filter(Boolean)
    .map((match) => `${Number(match[1])}:${Number(match[2])}`);
  return new Set(matches);
}

function hasDurableLink(comments, { owner, repo, issueNumber, prNumber }) {
  return parseDurableLink(comments, { owner, repo, prNumber }) === issueNumber;
}

function parseDurableLink(comments, { owner, repo, prNumber }) {
  const prefix = `<!-- agent-link:v1 repo=${owner}/${repo} issue=`;
  const suffix = ` pr=${prNumber} -->`;
  const retired = retiredLinkSet(comments, { owner, repo });
  const issueNumbers = trustedCommentLines(comments)
    .filter((line) => line.startsWith(prefix) && line.endsWith(suffix))
    .map((line) => Number(line.slice(prefix.length, -suffix.length)))
    .filter((number) => Number.isSafeInteger(number) && number > 0)
    .filter((number) => !retired.has(`${number}:${prNumber}`));
  const unique = [...new Set(issueNumbers)];
  return unique.length === 1 ? unique[0] : null;
}

function durablePrLinkDecision(comments, { owner, repo, issueNumber }) {
  const prefix = `<!-- agent-link:v1 repo=${owner}/${repo} issue=${issueNumber} pr=`;
  const suffix = " -->";
  const retired = retiredLinkSet(comments, { owner, repo });
  const prNumbers = trustedCommentLines(comments)
    .filter((line) => line.startsWith(prefix) && line.endsWith(suffix))
    .map((line) => Number(line.slice(prefix.length, -suffix.length)))
    .filter((number) => Number.isSafeInteger(number) && number > 0)
    .filter((number) => !retired.has(`${issueNumber}:${number}`));
  const unique = [...new Set(prNumbers)];
  if (unique.length > 1) return { ok: false, reason: "AMBIGUOUS_DURABLE_LINK" };
  return { ok: true, prNumber: unique[0] ?? null };
}

function durableIssueLinkDecision(comments, { owner, repo, prNumber }) {
  const prefix = `<!-- agent-link:v1 repo=${owner}/${repo} issue=`;
  const suffix = ` pr=${prNumber} -->`;
  const retired = retiredLinkSet(comments, { owner, repo });
  const issueNumbers = trustedCommentLines(comments)
    .filter((line) => line.startsWith(prefix) && line.endsWith(suffix))
    .map((line) => Number(line.slice(prefix.length, -suffix.length)))
    .filter((number) => Number.isSafeInteger(number) && number > 0)
    .filter((number) => !retired.has(`${number}:${prNumber}`));
  const unique = [...new Set(issueNumbers)];
  if (unique.length > 1) return { ok: false, reason: "AMBIGUOUS_DURABLE_LINK" };
  return { ok: true, issueNumber: unique[0] ?? null };
}

'''
write(pipeline, text[:start] + replacement + text[end:])
replace_once(pipeline, '''function normalizedFailureClass(job) {
  const name = job.name.toLowerCase();
  const steps = failedStepNames(job).join(" ");
  const metadata = `${name} ${steps}`;''', '''function normalizedFailureClass(job, logExcerpt = "") {
  const name = job.name.toLowerCase();
  const steps = failedStepNames(job).join(" ");
  const metadata = `${name} ${steps} ${String(logExcerpt || "").toLowerCase()}`;''')
replace_once(pipeline, 'const job = failed[0], failureClass = normalizedFailureClass(job);', 'const job = failed[0], failureClass = normalizedFailureClass(job, logExcerpt);')
old_candidates = '''function authoritativeCiRunCandidates(runs, { workflowName, headSha, prNumber }) {
  return runs
    .filter((run) => run.name === workflowName && run.event === "pull_request" &&
      run.status === "completed" && run.conclusion === "success" && run.head_sha === headSha &&
      run.pull_requests?.length === 1 && run.pull_requests[0].number === prNumber)
    .sort((left, right) => right.id - left.id);
}'''
new_candidates = '''function newestAuthoritativeCiRun(runs, { workflowName, headSha, prNumber }) {
  return runs
    .filter((run) => run.name === workflowName && run.event === "pull_request" &&
      run.status === "completed" && run.head_sha === headSha &&
      run.pull_requests?.length === 1 && run.pull_requests[0].number === prNumber)
    .sort((left, right) => right.id - left.id)[0] ?? null;
}

function authoritativeCiRunCandidates(runs, binding) {
  const newest = newestAuthoritativeCiRun(runs, binding);
  return newest?.conclusion === "success" ? [newest] : [];
}'''
replace_once(pipeline, old_candidates, new_candidates)
replace_once(pipeline, "  authoritativeCiRunCandidates,\n", "  authoritativeCiRunCandidates,\n  newestAuthoritativeCiRun,\n")

autonomy = ".github/scripts/agent-autonomy.cjs"
replace_once(autonomy, 'function authorizationDecision({ comments, repo, issueNumber, title, body, labels }) {\n  const specHash = issueSpecHash({ title, body, labels });', 'function authorizationDecision({ comments, repo, issueNumber, title, body, labels, state = "open" }) {\n  if (state !== "open") return { ok: false, reason: "ISSUE_NOT_OPEN" };\n  const specHash = issueSpecHash({ title, body, labels });')
replace_once(autonomy, '    [input.authorizationCurrent, "AUTHORIZATION_NOT_CURRENT"],\n    [input.issueIsImplementation, "ISSUE_NOT_IMPLEMENTATION"],', '    [input.authorizationCurrent, "AUTHORIZATION_NOT_CURRENT"],\n    [input.issueOpen, "ISSUE_NOT_OPEN"],\n    [input.issueIsImplementation, "ISSUE_NOT_IMPLEMENTATION"],')

write(".github/workflows/agent-authorization-invalidation.yml", r'''name: Agent authorization invalidation

on:
  issues:
    types: [edited, labeled, unlabeled, closed, reopened]

permissions:
  contents: read

jobs:
  invalidate:
    if: '!github.event.issue.pull_request'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      issues: write
      pull-requests: write
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
        with: {ref: '${{ github.event.repository.default_branch }}', persist-credentials: false, fetch-depth: 1}
      - name: Fail closed when authorization becomes stale
        uses: actions/github-script@f28e40c7f34bde8b3046d885e986cb6290c5673b # v7
        with:
          script: |
            const p=require('./.github/scripts/agent-pipeline.cjs'),a=require('./.github/scripts/agent-autonomy.cjs');
            const issueNumber=context.payload.issue.number,repo=`${context.repo.owner}/${context.repo.repo}`;
            let {data:issue}=await github.rest.issues.get({...context.repo,issue_number:issueNumber});
            const comments=await github.paginate(github.rest.issues.listComments,{...context.repo,issue_number:issueNumber,per_page:100});
            const parsed=a.parseAuthorization(comments,{repo,issueNumber});
            if(!parsed.ok){ if(parsed.reason==='AUTHORIZATION_MISSING') return core.notice('NO_WRITE: Issue has never been authorized'); return core.setFailed(parsed.reason); }
            let reason;
            if(context.payload.action==='closed') reason='ISSUE_CLOSED';
            else if(context.payload.action==='reopened') reason='ISSUE_REOPEN_REQUIRES_REAUTH';
            else { const decision=a.authorizationDecision({comments,repo,issueNumber,title:issue.title,body:issue.body||'',labels:issue.labels,state:issue.state}); if(decision.ok) return core.notice('Authorization remains current'); reason=decision.reason; }
            const setNeedsHuman=async(number,item)=>{ const names=a.labelNames(item.labels),preserved=names.filter(label=>!label.startsWith('agent:')); if(!a.exactAgentState(item.labels,'agent:needs-human')) await github.rest.issues.setLabels({...context.repo,issue_number:number,labels:[...preserved,'agent:needs-human']}); };
            await setNeedsHuman(issueNumber,issue);
            const link=p.durablePrLinkDecision(comments,{...context.repo,issueNumber}); if(!link.ok) return core.setFailed(link.reason);
            if(link.prNumber){ try{ const {data:pr}=await github.rest.pulls.get({...context.repo,pull_number:link.prNumber}); if(pr.state==='open') await setNeedsHuman(link.prNumber,pr); }catch(error){if(error.status!==404)throw error;} }
            await github.rest.issues.createComment({...context.repo,issue_number:issueNumber,body:`Autonomous merge authorization invalidated fail-closed: ${reason}. A new explicit human authorization is required before further autonomous writes or merge.`});
''')

authorize = ".github/workflows/agent-authorize.yml"
text = read(authorize)
needle = '''            if (!existing.ok && existing.reason !== 'AUTHORIZATION_MISSING') {
              return core.setFailed(existing.reason);
            }

            issue = await loadIssue();'''
insert = '''            if (!existing.ok && existing.reason !== 'AUTHORIZATION_MISSING') {
              return core.setFailed(existing.reason);
            }

            if (state === 'agent:needs-human') {
              const link = p.durablePrLinkDecision(comments, { ...context.repo, issueNumber });
              if (!link.ok) return core.setFailed(link.reason);
              if (link.prNumber !== null) {
                const { data: oldPr } = await github.rest.pulls.get({ ...context.repo, pull_number: link.prNumber });
                const oldPrComments = await github.paginate(github.rest.issues.listComments, { ...context.repo, issue_number: link.prNumber, per_page: 100 });
                const linkage = p.fullLinkageDecision({ prBody: oldPr.body, prComments: oldPrComments, issueComments: comments, ...context.repo, issueNumber, prNumber: link.prNumber });
                if (!linkage.ok) return core.setFailed(`OLD_PR_${linkage.reason}`);
                if (oldPr.state === 'open') await github.rest.pulls.update({ ...context.repo, pull_number: link.prNumber, state: 'closed' });
                const retired = `<!-- agent-link-retired:v1 repo=${context.repo.owner}/${context.repo.repo} issue=${issueNumber} pr=${link.prNumber} -->`;
                if (!comments.some(comment => comment.user?.login === 'github-actions[bot]' && (comment.body || '').split('\\n').includes(retired))) await github.rest.issues.createComment({...context.repo,issue_number:issueNumber,body:`Retired prior autonomous PR #${link.prNumber} before reauthorization.\\n\\n${retired}`});
                if (!oldPrComments.some(comment => comment.user?.login === 'github-actions[bot]' && (comment.body || '').split('\\n').includes(retired))) await github.rest.issues.createComment({...context.repo,issue_number:link.prNumber,body:`Retired by a fresh explicit authorization cycle for Issue #${issueNumber}.\\n\\n${retired}`});
                const oldNames=a.labelNames(oldPr.labels),oldPreserved=oldNames.filter(label=>!label.startsWith('agent:')); await github.rest.issues.setLabels({...context.repo,issue_number:link.prNumber,labels:[...oldPreserved,'agent:needs-human']});
                const refreshedComments=await github.paginate(github.rest.issues.listComments,{...context.repo,issue_number:issueNumber,per_page:100}); const retiredDecision=p.durablePrLinkDecision(refreshedComments,{...context.repo,issueNumber}); if(!retiredDecision.ok||retiredDecision.prNumber!==null) return core.setFailed('OLD_PR_RETIREMENT_POSTCONDITION_FAILED');
              }
            }

            issue = await loadIssue();'''
if needle not in text: raise SystemExit('authorize insertion point missing')
text=text.replace(needle,insert,1)
text=text.replace("              labels: post.labels,\n            });", "              labels: post.labels,\n              state: post.state,\n            });")
write(authorize,text)

publisher = ".github/workflows/agent-builder-publish.yml"
text=read(publisher)
text=text.replace("const auth=a.authorizationDecision({comments:ic,repo,issueNumber:n,title:i.title,body:i.body||'',labels:i.labels});", "const auth=a.authorizationDecision({comments:ic,repo,issueNumber:n,title:i.title,body:i.body||'',labels:i.labels,state:i.state});")
text=text.replace('sealed="$(git rev-parse HEAD)"', 'sealed="$(git rev-parse HEAD)"; expected_tree="$(jq -r .tree_sha "$RUNNER_TEMP/s/validated.json")"; test "$(jq -r .base_sha "$RUNNER_TEMP/s/validated.json")" = "$BASE"; test "$(jq -r .spec_hash "$RUNNER_TEMP/s/validated.json")" = "$SPEC"')
pattern=r'owned\(\)\{ local sha="\$1" m p t;.*?; \}'
replacement_owned='owned(){ local sha="$1" m p t; test "$sha" = "$sealed" || return 1; m="$(gh api "repos/$GH_REPO/git/commits/$sha" --jq .message)"||return 1; p="$(gh api "repos/$GH_REPO/commits/$sha" --jq ".parents[0].sha")"||return 1; t="$(gh api "repos/$GH_REPO/git/commits/$sha" --jq ".tree.sha")"||return 1; test "$p" = "$BASE" || return 1; test "$t" = "$expected_tree" || return 1; grep -Fxq "Agent-Builder-Seal: v1 issue=$ISSUE spec=$SPEC base=$BASE tree=$expected_tree" <<<"$m" || return 1; return 0; }'
text,n=re.subn(pattern,replacement_owned,text,count=1)
if n!=1: raise SystemExit(f'publisher owned replacement failed {n}')
write(publisher,text)

builder=".github/workflows/agent-builder.yml"
text=read(builder).replace("const auth=a.authorizationDecision({comments:cs,repo,issueNumber:n,title:i.title,body:i.body||'',labels:i.labels});", "const auth=a.authorizationDecision({comments:cs,repo,issueNumber:n,title:i.title,body:i.body||'',labels:i.labels,state:i.state});")
if '  fail-closed-finalizer:' not in text:
    text += r'''

  fail-closed-finalizer:
    needs: [prepare, generate, validate, seal, publish]
    if: >-
      always() && needs.prepare.result == 'success' && needs.generate.outputs.blocked != 'true' &&
      (needs.generate.result != 'success' || needs.validate.result != 'success' || needs.seal.result != 'success' || needs.publish.result != 'success')
    runs-on: ubuntu-latest
    permissions: {contents: read, issues: write, pull-requests: write}
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
        with: {ref: '${{ github.event.repository.default_branch }}', persist-credentials: false}
      - uses: actions/github-script@f28e40c7f34bde8b3046d885e986cb6290c5673b # v7
        env: {ISSUE: '${{ needs.prepare.outputs.issue }}', SPEC: '${{ needs.prepare.outputs.spec }}'}
        with:
          script: |
            const p=require('./.github/scripts/agent-pipeline.cjs'),a=require('./.github/scripts/agent-autonomy.cjs'); const n=Number(process.env.ISSUE),repo=`${context.repo.owner}/${context.repo.repo}`; const {data:i}=await github.rest.issues.get({...context.repo,issue_number:n}); const ic=await github.paginate(github.rest.issues.listComments,{...context.repo,issue_number:n,per_page:100}); const auth=a.authorizationDecision({comments:ic,repo,issueNumber:n,title:i.title,body:i.body||'',labels:i.labels,state:i.state}); if(!auth.ok||auth.specHash!==process.env.SPEC)return core.notice(`NO_WRITE:${auth.reason||'STALE'}`); const setNeedsHuman=async(number,item)=>{const names=a.labelNames(item.labels),preserved=names.filter(x=>!x.startsWith('agent:'));await github.rest.issues.setLabels({...context.repo,issue_number:number,labels:[...preserved,'agent:needs-human']});}; await setNeedsHuman(n,i); const link=p.durablePrLinkDecision(ic,{...context.repo,issueNumber:n}); if(link.ok&&link.prNumber){const {data:pr}=await github.rest.pulls.get({...context.repo,pull_number:link.prNumber});if(pr.state==='open')await setNeedsHuman(link.prNumber,pr);} await github.rest.issues.createComment({...context.repo,issue_number:n,body:'Autonomous Builder stopped fail-closed after an execution, validation, sealing, or publication failure.'});
'''
write(builder,text)

replace_once('.github/workflows/ci.yml','run: node --test .github/scripts/agent-pipeline.test.cjs','run: node --test .github/scripts/agent-pipeline.test.cjs .github/scripts/agent-autonomy.test.cjs')

fixer='.github/workflows/agent-ci-fixer.yml'
replace_once(fixer,'      fix_scope: ${{ steps.classify.outputs.fix_scope }}\n','      fix_scope: ${{ steps.classify.outputs.fix_scope }}\n      transient_retry: ${{ steps.classify.outputs.transient_retry }}\n')
replace_once(fixer,'const p=require(`${process.env.GITHUB_WORKSPACE}/.trusted-policy/.github/scripts/agent-pipeline.cjs`), c=require(`${process.env.GITHUB_WORKSPACE}/.trusted-policy/.github/agent-pipeline.json`);','const p=require(`${process.env.GITHUB_WORKSPACE}/.trusted-policy/.github/scripts/agent-pipeline.cjs`), a=require(`${process.env.GITHUB_WORKSPACE}/.trusted-policy/.github/scripts/agent-autonomy.cjs`), c=require(`${process.env.GITHUB_WORKSPACE}/.trusted-policy/.github/agent-pipeline.json`);')
replace_once(fixer,'            core.setOutput("failure_class",classification.failureClass);\n            if (classification.disposition!=="FIX") { core.setOutput("escalation","true"); core.setOutput("reason",classification.reason); return; }','            core.setOutput("failure_class",classification.failureClass);\n            if (classification.failureClass==="infra-transient") { const retry=a.transientCiRetryDecision({failureClass:classification.failureClass,runAttempt:run.run_attempt,maxRetries:c.v2.maxTransientCiRetries}); core.setOutput("budget_state",JSON.stringify(retry)); if(retry.action==="RETRY"){core.setOutput("transient_retry","true");return;} if(retry.action==="NEEDS_HUMAN"){core.setOutput("escalation","true");core.setOutput("reason",retry.reason);return;} }\n            if (classification.disposition!=="FIX") { core.setOutput("escalation","true"); core.setOutput("reason",classification.reason); return; }')
retry_job=r'''
  retry-transient:
    needs: classify
    if: needs.classify.outputs.transient_retry == 'true'
    runs-on: ubuntu-latest
    permissions: {actions: write, contents: read, issues: read, pull-requests: read}
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
        with: {ref: '${{ github.event.repository.default_branch }}', path: .trusted-policy, persist-credentials: false}
      - uses: actions/github-script@f28e40c7f34bde8b3046d885e986cb6290c5673b # v7
        env: {PR: '${{ needs.classify.outputs.pr_number }}', ISSUE: '${{ needs.classify.outputs.issue_number }}', SHA: '${{ needs.classify.outputs.source_sha }}', CI_RUN: '${{ needs.classify.outputs.ci_run_id }}', CI_ATTEMPT: '${{ needs.classify.outputs.ci_run_attempt }}', CLASS: '${{ needs.classify.outputs.failure_class }}'}
        with:
          script: |
            const p=require(`${process.env.GITHUB_WORKSPACE}/.trusted-policy/.github/scripts/agent-pipeline.cjs`),a=require(`${process.env.GITHUB_WORKSPACE}/.trusted-policy/.github/scripts/agent-autonomy.cjs`),c=require(`${process.env.GITHUB_WORKSPACE}/.trusted-policy/.github/agent-pipeline.json`); const prNumber=Number(process.env.PR),issueNumber=Number(process.env.ISSUE),runId=Number(process.env.CI_RUN),attempt=Number(process.env.CI_ATTEMPT),sha=process.env.SHA; const {data:run}=await github.rest.actions.getWorkflowRun({...context.repo,run_id:runId}); const {data:pr}=await github.rest.pulls.get({...context.repo,pull_number:prNumber}); const {data:issue}=await github.rest.issues.get({...context.repo,issue_number:issueNumber}); const pc=await github.paginate(github.rest.issues.listComments,{...context.repo,issue_number:prNumber,per_page:100}),ic=await github.paginate(github.rest.issues.listComments,{...context.repo,issue_number:issueNumber,per_page:100}); if(process.env.CLASS!=="infra-transient"||run.run_attempt!==attempt||!p.authoritativeCiIdentity(run,{prNumber,headSha:sha,conclusion:"failure"})||pr.head.sha!==sha||pr.state!=="open"||issue.state!=="open"||!p.lifecycleAtAgentPr(pr.labels,issue.labels).ok||!p.fullLinkageDecision({prBody:pr.body,prComments:pc,issueComments:ic,...context.repo,issueNumber,prNumber}).ok)return core.setFailed("TRANSIENT_RETRY_TOCTOU_REJECTED"); const retry=a.transientCiRetryDecision({failureClass:"infra-transient",runAttempt:attempt,maxRetries:c.v2.maxTransientCiRetries}); if(retry.action!=="RETRY")return core.setFailed(retry.reason||"TRANSIENT_RETRY_NOT_ALLOWED"); await github.rest.actions.reRunWorkflowFailedJobs({...context.repo,run_id:runId});

'''
replace_once(fixer,'\n  record-classification:\n','\n'+retry_job+'  record-classification:\n')

write('.github/workflows/agent-ruleset-sync.yml',r'''name: Agent Protect main ruleset sync

on:
  workflow_dispatch:

permissions: {contents: read}

jobs:
  sync:
    runs-on: ubuntu-latest
    permissions: {contents: read}
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
        with: {ref: ${{ github.event.repository.default_branch }}, persist-credentials: false, fetch-depth: 1}
      - name: Require exact gate binding and one-Issue approval model
        env: {GH_TOKEN: '${{ secrets.AGENT_PUBLISH_TOKEN }}', GH_REPO: '${{ github.repository }}'}
        run: |
          set -euo pipefail; test -n "$GH_TOKEN"; test "${GITHUB_REF}" = "refs/heads/${{ github.event.repository.default_branch }}"; test "$(git rev-parse HEAD)" = "${GITHUB_SHA}"
          rulesets="$(gh api "repos/$GH_REPO/rulesets")"; id="$(jq -r '[.[]|select(.name=="Protect main" and .target=="branch" and .enforcement=="active")]|if length==1 then .[0].id else empty end'<<<"$rulesets")"; test -n "$id"; current="$(gh api "repos/$GH_REPO/rulesets/$id")"; test "$(jq -r '.bypass_actors|length'<<<"$current")" = 0
          jq '.rules|=map(if .type=="required_status_checks" then .parameters.strict_required_status_checks_policy=true|.parameters.required_status_checks=([.parameters.required_status_checks[]|select(.context!="agent-verified-gate")]+[{"context":"agent-verified-gate","integration_id":15368}]|sort_by(.context)) elif .type=="pull_request" then .parameters.required_approving_review_count=0|.parameters.required_reviewers=[]|.parameters.require_code_owner_review=false|.parameters.require_last_push_approval=false|.parameters.require_extra_approval_for_unattributed_changes=false else . end)|{name,target,enforcement,conditions,rules,bypass_actors}'<<<"$current">"$RUNNER_TEMP/ruleset.json"
          gh api --method PUT "repos/$GH_REPO/rulesets/$id" --input "$RUNNER_TEMP/ruleset.json" >/dev/null; fresh="$(gh api "repos/$GH_REPO/rulesets/$id")"
          jq -e '(.bypass_actors|length==0) and ([.rules[]|select(.type=="required_status_checks")][0].parameters.strict_required_status_checks_policy==true) and ([.rules[]|select(.type=="required_status_checks")][0].parameters.required_status_checks|[.[]|select(.context=="agent-verified-gate" and .integration_id==15368)]|length==1) and ([.rules[]|select(.type=="pull_request")][0].parameters.required_approving_review_count==0)'<<<"$fresh">/dev/null
''')

for path in ['.github/workflows/agent-verified-gate.yml','.github/workflows/agent-verify.yml']:
    t=read(path)
    t=t.replace("const auth=a.authorizationDecision({comments:issueComments,repo,issueNumber,title:issue.title,body:issue.body||'',labels:issue.labels});","const auth=a.authorizationDecision({comments:issueComments,repo,issueNumber,title:issue.title,body:issue.body||'',labels:issue.labels,state:issue.state});")
    t=t.replace("authorizationCurrent:auth.ok&&auth.specHash===spec,\n                issueIsImplementation:","authorizationCurrent:auth.ok&&auth.specHash===spec,\n                issueOpen:issue.state==='open',\n                issueIsImplementation:")
    t=t.replace("authorizationCurrent:state.auth.ok,\n              issueIsImplementation:","authorizationCurrent:state.auth.ok,\n              issueOpen:state.issue.state==='open',\n              issueIsImplementation:")
    write(path,t)

gate='.github/workflows/agent-verified-gate.yml';t=read(gate)
old='''              const runs=await github.paginate(github.rest.actions.listWorkflowRunsForRepo,{...context.repo,event:'pull_request',head_sha:headSha,status:'completed',per_page:100});
              const candidates=p.authoritativeCiRunCandidates(runs,{workflowName:'CI',headSha,prNumber});
              let ciRun=null,jobs=null;
              for(const candidate of candidates){
                const candidateJobs=await github.paginate(github.rest.actions.listJobsForWorkflowRun,{...context.repo,run_id:candidate.id,filter:'all',per_page:100});
                if(p.successfulRequiredJobs(candidateJobs,c.requiredCiJobs,headSha)){ciRun=candidate;jobs=candidateJobs;break;}
              }'''
new='''              const runs=await github.paginate(github.rest.actions.listWorkflowRunsForRepo,{...context.repo,event:'pull_request',head_sha:headSha,status:'completed',per_page:100});
              const ciRun=p.newestAuthoritativeCiRun(runs,{workflowName:'CI',headSha,prNumber});
              const jobs=ciRun?await github.paginate(github.rest.actions.listJobsForWorkflowRun,{...context.repo,run_id:ciRun.id,filter:'all',per_page:100}):null;'''
if old not in t: raise SystemExit('gate CI block missing')
t=t.replace(old,new,1).replace('requiredCiSuccessful:!!ciRun&&p.successfulRequiredJobs(jobs,c.requiredCiJobs,headSha),','requiredCiSuccessful:!!ciRun&&ciRun.conclusion===\'success\'&&p.successfulRequiredJobs(jobs,c.requiredCiJobs,headSha),')
t=t.replace('      contents: read\n      issues: write\n      pull-requests: write\n    with:\n      pr_number:','      contents: write\n      issues: write\n      pull-requests: write\n    with:\n      pr_number:',1);write(gate,t)

verify='.github/workflows/agent-verify.yml';t=read(verify);s=t.index("            let run=trigger.kind==='ci'");e=t.index("            if(!run||",s);t=t[:s]+'''            const runs=await github.paginate(github.rest.actions.listWorkflowRunsForRepo,{...context.repo,event:'pull_request',head_sha:requestedSha,status:'completed',per_page:100});
            const run=p.newestAuthoritativeCiRun(runs,{workflowName:'CI',headSha:requestedSha,prNumber});
            const jobs=run?await github.paginate(github.rest.actions.listJobsForWorkflowRun,{...context.repo,run_id:run.id,filter:'all',per_page:100}):null;
'''+t[e:];t=t.replace("if(!run||!p.authoritativeCiIdentity(run,{prNumber,headSha:requestedSha,conclusion:'success'})", "if(!run||run.conclusion!=='success'||!p.authoritativeCiIdentity(run,{prNumber,headSha:requestedSha,conclusion:'success'})");t=t.replace('      actions: read\n      contents: read\n      issues: read\n      pull-requests: read\n      statuses: write','      actions: read\n      contents: write\n      issues: write\n      pull-requests: write\n      statuses: write');write(verify,t)

write('.github/workflows/agent-auto-merge.yml',r'''name: Agent exact-SHA auto merge

on:
  workflow_call:
    inputs:
      pr_number: {required: true, type: number}
      head_sha: {required: true, type: string}
      spec_hash: {required: true, type: string}

permissions: {contents: read}

jobs:
  merge:
    runs-on: ubuntu-latest
    permissions: {actions: read, contents: write, issues: write, pull-requests: write}
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
        with: {ref: '${{ github.event.repository.default_branch }}', persist-credentials: false, fetch-depth: 1}
      - uses: actions/github-script@f28e40c7f34bde8b3046d885e986cb6290c5673b # v7
        env: {PR: '${{ inputs.pr_number }}', HEAD: '${{ inputs.head_sha }}', SPEC: '${{ inputs.spec_hash }}'}
        with:
          script: |
            const p=require('./.github/scripts/agent-pipeline.cjs'),a=require('./.github/scripts/agent-autonomy.cjs'),c=require('./.github/agent-pipeline.json'); const prNumber=Number(process.env.PR),headSha=process.env.HEAD,spec=process.env.SPEC,repo=`${context.repo.owner}/${context.repo.repo}`;
            const evaluate=async()=>{const {data:pr}=await github.rest.pulls.get({...context.repo,pull_number:prNumber});const issueNumber=p.parseAgentIssue(pr.body);if(!issueNumber)return {ok:false,reason:'LINKAGE_INVALID',pr};const {data:issue}=await github.rest.issues.get({...context.repo,issue_number:issueNumber});const prComments=await github.paginate(github.rest.issues.listComments,{...context.repo,issue_number:prNumber,per_page:100}),issueComments=await github.paginate(github.rest.issues.listComments,{...context.repo,issue_number:issueNumber,per_page:100});const auth=a.authorizationDecision({comments:issueComments,repo,issueNumber,title:issue.title,body:issue.body||'',labels:issue.labels,state:issue.state}),linkage=p.fullLinkageDecision({prBody:pr.body,prComments,issueComments,...context.repo,issueNumber,prNumber});const {data:main}=await github.rest.repos.getBranch({...context.repo,branch:context.payload.repository.default_branch});const comparison=pr.head.sha===headSha?await github.rest.repos.compareCommits({...context.repo,base:main.commit.sha,head:headSha}):null;const runs=await github.paginate(github.rest.actions.listWorkflowRunsForRepo,{...context.repo,event:'pull_request',head_sha:headSha,status:'completed',per_page:100}),ciRun=p.newestAuthoritativeCiRun(runs,{workflowName:'CI',headSha,prNumber}),jobs=ciRun?await github.paginate(github.rest.actions.listJobsForWorkflowRun,{...context.repo,run_id:ciRun.id,filter:'all',per_page:100}):null,statuses=(await github.rest.repos.listCommitStatusesForRef({...context.repo,ref:headSha,per_page:100})).data,gate=statuses.find(s=>s.context===a.GATE_CONTEXT);const decision=a.mergeDecision({authorizationCurrent:auth.ok&&auth.specHash===spec,issueOpen:issue.state==='open',issueIsImplementation:p.isImplementation(issue.labels),linkageValid:linkage.ok,open:pr.state==='open',correctBase:pr.base.ref===context.payload.repository.default_branch,draft:pr.draft,exactHead:pr.head.sha===headSha,verifiedLifecycle:a.lifecyclePairDecision(pr.labels,issue.labels,'agent:verified').ok,needsHuman:[...a.labelNames(pr.labels),...a.labelNames(issue.labels)].includes('agent:needs-human'),currentMain:comparison?.data.behind_by===0,requiredCiSuccessful:!!ciRun&&ciRun.conclusion==='success'&&p.successfulRequiredJobs(jobs,c.requiredCiJobs,headSha),independentReviewPass:p.independentReviewSatisfied(prComments,headSha),verificationEvidence:a.exactVerificationEvidence(prComments,{repo,issueNumber,prNumber,headSha,specHash:spec}),gateSuccess:gate?.state==='success'&&gate?.creator?.login==='github-actions[bot]',expectedHeadUnchanged:pr.head.sha===headSha});return {...decision,pr,issue,issueNumber};};
            let state=await evaluate(); if(state.pr?.head.sha!==headSha)return core.notice('NO_WRITE: head changed'); if(!state.ok&&state.reason==='PR_BEHIND_MAIN'){try{await github.rest.pulls.updateBranch({...context.repo,pull_number:prNumber,expected_head_sha:headSha});return core.notice('AUTO_RECONCILED_NEW_SHA_REQUIRED');}catch(error){if(error.status===409||error.status===422)return core.setFailed('RECONCILIATION_CONFLICT');throw error;}} if(!state.ok)return core.setFailed(`MERGE_REJECTED:${state.reason}`); state=await evaluate(); if(!state.ok)return core.setFailed(`FINAL_TOCTOU_REJECTED:${state.reason}`); const {data:merged}=await github.rest.pulls.merge({...context.repo,pull_number:prNumber,sha:headSha,merge_method:'merge'}); if(!merged.merged)return core.setFailed(`MERGE_API_REJECTED:${merged.message||'unknown'}`); const marker=a.mergeMarker({repo,issueNumber:state.issueNumber,prNumber,headSha,specHash:spec,mergeSha:merged.sha}); await github.rest.issues.createComment({...context.repo,issue_number:state.issueNumber,body:`PR #${prNumber} automatically merged.\\n\\n${marker}`});
''')

codex='.github/workflows/agent-codex-review.yml';replace_once(codex,'permissions: {actions: read, contents: read, issues: write, pull-requests: write}\n    with:\n      pr_number: ${{ fromJSON(needs.prepare.outputs.pr_number) }}','permissions: {actions: read, contents: write, issues: write, pull-requests: write, statuses: write}\n    with:\n      pr_number: ${{ fromJSON(needs.prepare.outputs.pr_number) }}')
ack='.github/workflows/agent-review-acknowledgement.yml'
if Path(ack).exists(): write(ack,read(ack).replace('permissions: {actions: read, contents: read, issues: write, pull-requests: write}','permissions: {actions: read, contents: write, issues: write, pull-requests: write, statuses: write}'))

doc='docs/autonomous-development-pipeline.md';t=read(doc);s=t.index('## Automated verification gate');e=t.index('## Verification invalidation after a push',s);write(doc,t[:s]+'''## Automated verification gate\n\nIssue #100 changes the normal operating contract to **one explicit human authorization at the concrete Issue level**. The trusted authorization marker is bound to the canonical Issue title, body and single `type:implementation` classification. Closing, reopening, or changing approval-relevant Issue data invalidates autonomous progress and requires fresh human authorization.\n\nVerification is automatic for the exact current PR head. It requires the Issue to be open with current authorization, one active durable Issue ↔ PR link, an open non-draft default-base PR that is not behind `main`, a coherent non-escalated lifecycle, the **newest** authoritative completed CI run for that PR/SHA with all nine required jobs green, and an independent Codex `PASS` for the same SHA. A successful verifier records exact `agent-verified:v2` evidence and invokes `agent-verified-gate`, which revalidates these facts before publishing the trusted required status. The merge controller then performs another full TOCTOU re-evaluation and merges with the expected exact head SHA. If `main` moves, the branch is reconciled without force-push and all SHA-bound evidence is regenerated.\n\n### Human acknowledgement and native PR approval\n\nThe old exact-SHA human acknowledgement/native-approval handoff is not part of the normal successful path. The sole normal human decision is Issue authorization. Historical acknowledgement tooling is compatibility/recovery only and cannot replace current Issue authorization, newest exact-SHA CI, independent Codex PASS, current-main reconciliation, or `agent-verified-gate`.\n\n`Protect main` remains the independent backstop. Under the Issue #100 model it requires no separate native PR approval, retains strict existing CI checks, adds `agent-verified-gate` bound to GitHub Actions, and has no bypass actors.\n\n'''+t[e:])
adr='docs/adr/0003-autonomous-development-pipeline-v2.md';t=read(adr);s=t.index('## Issue #100 one-time bootstrap generation boundary');write(adr,t[:s]+'''## Issue #100 bootstrap boundary — retired\n\nThe one-time Issue #100 bootstrap publisher/generator was migration infrastructure only and is now retired. It is not an active authorization, generation, publication, verification, or merge boundary. The authoritative post-#100 model is ADR 0006 and the current default-branch agent authorization, Builder, fixer, independent Reviewer, verifier, gate, merge, and ruleset-sync workflows. Model execution remains read-only; validation/sealing remain credential-free; trusted mutation jobs receive only narrowly required credentials.\n''')

testfile='.github/scripts/agent-autonomy.test.cjs';t=read(testfile).replace('authorizationCurrent: true,\n    issueIsImplementation:','authorizationCurrent: true,\n    issueOpen: true,\n    issueIsImplementation:');extra='''\n\ntest("closed Issue invalidates authorization",()=>{assert.equal(a.authorizationDecision({comments:authComments,repo,issueNumber:42,title,body,labels,state:"closed"}).reason,"ISSUE_NOT_OPEN");});\ntest("retired durable link is inactive",()=>{const p=require("./agent-pipeline.cjs"),c=[{user:{login:"github-actions[bot]"},body:"<!-- agent-link:v1 repo=Bbambaaamm/Autonomous-Quant-Lab issue=42 pr=70 -->\\n<!-- agent-link-retired:v1 repo=Bbambaaamm/Autonomous-Quant-Lab issue=42 pr=70 -->"}];assert.deepEqual(p.durablePrLinkDecision(c,{owner:"Bbambaaamm",repo:"Autonomous-Quant-Lab",issueNumber:42}),{ok:true,prNumber:null});});\ntest("newest CI failure defeats older success",()=>{const p=require("./agent-pipeline.cjs"),sha="d".repeat(40),b={name:"CI",event:"pull_request",status:"completed",head_sha:sha,pull_requests:[{number:88}]};assert.equal(p.newestAuthoritativeCiRun([{...b,id:10,conclusion:"success"},{...b,id:11,conclusion:"failure"}],{workflowName:"CI",headSha:sha,prNumber:88}).conclusion,"failure");});\ntest("npm registry 503 is infra transient",()=>{const p=require("./agent-pipeline.cjs"),sha="e".repeat(40),r=p.classifyCiFailure({jobs:[{id:7,name:"security",conclusion:"failure",steps:[{name:"npm audit",conclusion:"failure"}]}],runHeadSha:sha,expectedHeadSha:sha,sourceRunId:77,runAttempt:1,logExcerpt:"503 Service Unavailable",config:{requiredCiJobs:["security"],failureClassPolicy:{eligible:[],denied:["infra-transient","security"]},protectedDiagnosticPatterns:[]}});assert.equal(r.failureClass,"infra-transient");});\n''';
if 'closed Issue invalidates authorization' not in t:t+=extra
write(testfile,t)

Path('.github/scripts/pr108-remediate.py').unlink()
Path('.github/workflows/pr108-remediate.yml').unlink()
