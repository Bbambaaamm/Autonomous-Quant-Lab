from pathlib import Path
import re

ROOT = Path(".")

def read(path):
    return (ROOT / path).read_text()

def write(path, text):
    (ROOT / path).write_text(text)

def replace_once(path, old, new):
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one exact match, found {count}")
    write(path, text.replace(old, new, 1))

def regex_once(path, pattern, replacement, flags=0):
    text = read(path)
    new, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise SystemExit(f"{path}: expected one regex match, found {count}")
    write(path, new)

replace_once(
    ".github/workflows/ci.yml",
    '''      - run: npm audit --omit=dev --audit-level=high
        working-directory: frontend
      - run: npm audit --audit-level=critical
        working-directory: frontend
''',
    '''      - name: npm audit with bounded network retry
        working-directory: frontend
        env:
          npm_config_fetch_timeout: "60000"
          npm_config_fetch_retries: "1"
        run: |
          set -euo pipefail
          audit_with_retry() {
            local attempt output status
            for attempt in 1 2 3; do
              set +e
              output="$("$@" 2>&1)"
              status=$?
              set -e
              printf '%s\\n' "$output"
              if test "$status" -eq 0; then
                return 0
              fi
              if ! grep -Eiq 'network timeout|audit endpoint returned an error|EAI_AGAIN|ECONNRESET|ETIMEDOUT|502 Bad Gateway|503 Service Unavailable|504 Gateway Timeout' <<<"$output"; then
                return "$status"
              fi
              if test "$attempt" -eq 3; then
                return "$status"
              fi
              sleep $((attempt * 10))
            done
          }
          audit_with_retry npm audit --omit=dev --audit-level=high
          audit_with_retry npm audit --audit-level=critical
''',
)

replace_once(
    ".github/workflows/agent-authorize.yml",
    '''      actions: write
      contents: read
      issues: write
''',
    '''      actions: write
      contents: read
      issues: write
      pull-requests: write
''',
)

replace_once(
    ".github/workflows/agent-review-acknowledgement.yml",
    '''    permissions:
      actions: read
      contents: read
      issues: write
      pull-requests: write
''',
    '''    permissions:
      actions: read
      contents: write
      issues: write
      pull-requests: write
      statuses: write
    secrets:
      AGENT_PUBLISH_TOKEN: '${{ secrets.AGENT_PUBLISH_TOKEN }}'
''',
)

replace_once(
    ".github/workflows/agent-builder.yml",
    "    permissions: {actions: read, contents: read, issues: write, pull-requests: write}\n",
    "    permissions: {actions: write, contents: read, issues: write, pull-requests: write}\n",
)

replace_once(
    ".github/scripts/agent-autonomy.cjs",
    '''const BUILDER_DENIED_SEGMENTS = new Set([
  "auth",
''',
    '''const BUILDER_DENIED_EXACT_PATHS = new Set([
  "backend/src/quantlab/trading.py",
  "backend/src/quantlab/phase4.py",
  "backend/src/quantlab/security.py",
]);
const BUILDER_DENIED_SEGMENTS = new Set([
  "auth",
''',
)
replace_once(
    ".github/scripts/agent-autonomy.cjs",
    '''  if (BUILDER_DENIED_PREFIXES.some((prefix) => path.startsWith(prefix))) return false;
  const basename = path.split("/").at(-1);
''',
    '''  if (BUILDER_DENIED_PREFIXES.some((prefix) => path.startsWith(prefix))) return false;
  if (BUILDER_DENIED_EXACT_PATHS.has(path)) return false;
  const basename = path.split("/").at(-1);
''',
)

regex_once(
    ".github/scripts/agent-pipeline.cjs",
    r'''function normalizedFailureClass\(job, logExcerpt = ""\) \{
.*?
\}
''',
    '''function normalizedFailureClass(job, logExcerpt = "") {
  const name = job.name.toLowerCase();
  const steps = failedStepNames(job).join(" ");
  const diagnostic = String(logExcerpt || "").toLowerCase();
  const metadata = `${name} ${steps} ${diagnostic}`;
  const transientEvidence = /network timeout|audit endpoint returned an error|\\beai_again\\b|\\beconnreset\\b|\\betimedout\\b|socket hang up|temporary failure|connection reset|502 bad gateway|503 service unavailable|504 gateway timeout/;
  if (job.conclusion === "timed_out" || transientEvidence.test(diagnostic)) return "infra-transient";
  if (/dependenc|lock|npm ci|uv lock|uv sync/.test(metadata)) return "dependency-lock";
  if (/security|audit|bandit|pip-audit/.test(metadata)) return "security";
  if (/integration-postgres|postgres/.test(metadata)) return "integration-postgres";
  if (/container-build|docker/.test(metadata)) return "container-build";
  if (/production-smoke|smoke/.test(metadata)) return "production-smoke";
  if (/frontend|npm test|next build/.test(metadata)) return "frontend-test-build";
  if (/mypy|typecheck|type check/.test(metadata)) return "typecheck";
  if (/ruff|lint|format/.test(metadata)) return "lint-format";
  if (/\\bapi\\b/.test(metadata)) return "api-test";
  if (/unit|pytest/.test(metadata)) return "unit-test";
  return "unknown";
}
''',
    flags=re.S,
)

replace_once(
    ".github/workflows/agent-verify.yml",
    '''  workflow_call:
    inputs:
      pr_number: {required: true, type: number}
      head_sha: {required: true, type: string}
''',
    '''  workflow_call:
    inputs:
      pr_number: {required: true, type: number}
      head_sha: {required: true, type: string}
    secrets:
      AGENT_PUBLISH_TOKEN: {required: false}
''',
)
replace_once(
    ".github/workflows/agent-verify.yml",
    '''        with:
          script: |
            const p=require('./.github/scripts/agent-pipeline.cjs')''',
    '''        with:
          github-token: ${{ secrets.AGENT_PUBLISH_TOKEN }}
          script: |
            const p=require('./.github/scripts/agent-pipeline.cjs')''',
)

replace_once(
    ".github/workflows/agent-auto-merge.yml",
    '''  workflow_call:
    inputs:
      pr_number: {required: true, type: number}
      head_sha: {required: true, type: string}
      spec_hash: {required: true, type: string}
''',
    '''  workflow_call:
    inputs:
      pr_number: {required: true, type: number}
      head_sha: {required: true, type: string}
      spec_hash: {required: true, type: string}
    secrets:
      AGENT_PUBLISH_TOKEN: {required: true}
''',
)
replace_once(
    ".github/workflows/agent-auto-merge.yml",
    '''        with:
          script: |
            const p=require('./.github/scripts/agent-pipeline.cjs')''',
    '''        with:
          github-token: ${{ secrets.AGENT_PUBLISH_TOKEN }}
          script: |
            const p=require('./.github/scripts/agent-pipeline.cjs')''',
)

replace_once(
    ".github/workflows/agent-verified-gate.yml",
    '''  workflow_call:
    inputs:
      pr_number: {required: true, type: number}
      head_sha: {required: true, type: string}
      spec_hash: {required: true, type: string}
''',
    '''  workflow_call:
    inputs:
      pr_number: {required: true, type: number}
      head_sha: {required: true, type: string}
      spec_hash: {required: true, type: string}
    secrets:
      AGENT_PUBLISH_TOKEN: {required: true}
''',
)
replace_once(
    ".github/workflows/agent-verified-gate.yml",
    '''    with:
      pr_number: ${{ inputs.pr_number }}
      head_sha: ${{ inputs.head_sha }}
      spec_hash: ${{ inputs.spec_hash }}
''',
    '''    with:
      pr_number: ${{ inputs.pr_number }}
      head_sha: ${{ inputs.head_sha }}
      spec_hash: ${{ inputs.spec_hash }}
    secrets:
      AGENT_PUBLISH_TOKEN: '${{ secrets.AGENT_PUBLISH_TOKEN }}'
''',
)
replace_once(
    ".github/workflows/agent-verify.yml",
    '''    with:
      pr_number: ${{ fromJSON(needs.verify.outputs.pr_number) }}
      head_sha: ${{ needs.verify.outputs.head_sha }}
      spec_hash: ${{ needs.verify.outputs.spec_hash }}
''',
    '''    with:
      pr_number: ${{ fromJSON(needs.verify.outputs.pr_number) }}
      head_sha: ${{ needs.verify.outputs.head_sha }}
      spec_hash: ${{ needs.verify.outputs.spec_hash }}
    secrets:
      AGENT_PUBLISH_TOKEN: '${{ secrets.AGENT_PUBLISH_TOKEN }}'
''',
)

replace_once(
    ".github/workflows/agent-codex-review.yml",
    '''    with:
      pr_number: ${{ fromJSON(needs.prepare.outputs.pr_number) }}
      head_sha: ${{ needs.prepare.outputs.head_sha }}


  route-block:''',
    '''    with:
      pr_number: ${{ fromJSON(needs.prepare.outputs.pr_number) }}
      head_sha: ${{ needs.prepare.outputs.head_sha }}
    secrets:
      AGENT_PUBLISH_TOKEN: '${{ secrets.AGENT_PUBLISH_TOKEN }}'


  route-block:''',
)

replace_once(
    ".github/workflows/agent-ci-fixer.yml",
    '''            const lifecycle=p.lifecycleAtAgentPr(pr.labels,issue.labels);
            const linkage=p.fullLinkageDecision({prBody:pr.body,prComments:comments,issueComments,...context.repo,issueNumber,prNumber});
            if (!p.isImplementation(issue.labels) || !lifecycle.ok || !linkage.ok) return core.notice(`NO_WRITE: ${lifecycle.reason||linkage.reason||"INELIGIBLE"}`);
''',
    '''            const lifecycle=p.lifecycleAtAgentPr(pr.labels,issue.labels);
            const linkage=p.fullLinkageDecision({prBody:pr.body,prComments:comments,issueComments,...context.repo,issueNumber,prNumber});
            const auth=a.authorizationDecision({comments:issueComments,repo:`${context.repo.owner}/${context.repo.repo}`,issueNumber,title:issue.title,body:issue.body||'',labels:issue.labels,state:issue.state});
            if (!auth.ok || !p.isImplementation(issue.labels) || !lifecycle.ok || !linkage.ok) return core.notice(`NO_WRITE: ${auth.reason||lifecycle.reason||linkage.reason||"INELIGIBLE"}`);
''',
)
replace_once(
    ".github/workflows/agent-ci-fixer.yml",
    '''const p=require(`${process.env.GITHUB_WORKSPACE}/.trusted-policy/.github/scripts/agent-pipeline.cjs`),a=require(`${process.env.GITHUB_WORKSPACE}/.trusted-policy/.github/scripts/agent-autonomy.cjs`),c=require(`${process.env.GITHUB_WORKSPACE}/.trusted-policy/.github/agent-pipeline.json`); const prNumber=Number(process.env.PR),issueNumber=Number(process.env.ISSUE),runId=Number(process.env.CI_RUN),attempt=Number(process.env.CI_ATTEMPT),sha=process.env.SHA; const {data:run}=await github.rest.actions.getWorkflowRun({...context.repo,run_id:runId}); const {data:pr}=await github.rest.pulls.get({...context.repo,pull_number:prNumber}); const {data:issue}=await github.rest.issues.get({...context.repo,issue_number:issueNumber}); const pc=await github.paginate(github.rest.issues.listComments,{...context.repo,issue_number:prNumber,per_page:100}),ic=await github.paginate(github.rest.issues.listComments,{...context.repo,issue_number:issueNumber,per_page:100}); if(process.env.CLASS!=="infra-transient"||run.run_attempt!==attempt||!p.authoritativeCiIdentity(run,{prNumber,headSha:sha,conclusion:"failure"})||pr.head.sha!==sha||pr.state!=="open"||issue.state!=="open"||!p.lifecycleAtAgentPr(pr.labels,issue.labels).ok||!p.fullLinkageDecision({prBody:pr.body,prComments:pc,issueComments:ic,...context.repo,issueNumber,prNumber}).ok)return core.setFailed("TRANSIENT_RETRY_TOCTOU_REJECTED"); const retry=a.transientCiRetryDecision({failureClass:"infra-transient",runAttempt:attempt,maxRetries:c.v2.maxTransientCiRetries});''',
    '''const p=require(`${process.env.GITHUB_WORKSPACE}/.trusted-policy/.github/scripts/agent-pipeline.cjs`),a=require(`${process.env.GITHUB_WORKSPACE}/.trusted-policy/.github/scripts/agent-autonomy.cjs`),c=require(`${process.env.GITHUB_WORKSPACE}/.trusted-policy/.github/agent-pipeline.json`); const prNumber=Number(process.env.PR),issueNumber=Number(process.env.ISSUE),runId=Number(process.env.CI_RUN),attempt=Number(process.env.CI_ATTEMPT),sha=process.env.SHA,repo=`${context.repo.owner}/${context.repo.repo}`; const {data:run}=await github.rest.actions.getWorkflowRun({...context.repo,run_id:runId}); const {data:pr}=await github.rest.pulls.get({...context.repo,pull_number:prNumber}); const {data:issue}=await github.rest.issues.get({...context.repo,issue_number:issueNumber}); const pc=await github.paginate(github.rest.issues.listComments,{...context.repo,issue_number:prNumber,per_page:100}),ic=await github.paginate(github.rest.issues.listComments,{...context.repo,issue_number:issueNumber,per_page:100}); const auth=a.authorizationDecision({comments:ic,repo,issueNumber,title:issue.title,body:issue.body||'',labels:issue.labels,state:issue.state}); if(!auth.ok||process.env.CLASS!=="infra-transient"||run.run_attempt!==attempt||!p.authoritativeCiIdentity(run,{prNumber,headSha:sha,conclusion:"failure"})||pr.head.sha!==sha||pr.state!=="open"||issue.state!=="open"||!p.lifecycleAtAgentPr(pr.labels,issue.labels).ok||!p.fullLinkageDecision({prBody:pr.body,prComments:pc,issueComments:ic,...context.repo,issueNumber,prNumber}).ok)return core.setFailed("TRANSIENT_RETRY_TOCTOU_REJECTED"); const retry=a.transientCiRetryDecision({failureClass:"infra-transient",runAttempt:attempt,maxRetries:c.v2.maxTransientCiRetries});''',
)
replace_once(
    ".github/workflows/agent-ci-fixer.yml",
    '''            const p=require(`${process.env.GITHUB_WORKSPACE}/.trusted-policy/.github/scripts/agent-pipeline.cjs`);
            const {data:pr}=await github.rest.pulls.get({...context.repo,pull_number:Number(process.env.PR)});
            const {data:issue}=await github.rest.issues.get({...context.repo,issue_number:Number(process.env.ISSUE)});
''',
    '''            const p=require(`${process.env.GITHUB_WORKSPACE}/.trusted-policy/.github/scripts/agent-pipeline.cjs`),a=require(`${process.env.GITHUB_WORKSPACE}/.trusted-policy/.github/scripts/agent-autonomy.cjs`);
            const {data:pr}=await github.rest.pulls.get({...context.repo,pull_number:Number(process.env.PR)});
            const {data:issue}=await github.rest.issues.get({...context.repo,issue_number:Number(process.env.ISSUE)});
''',
)
replace_once(
    ".github/workflows/agent-ci-fixer.yml",
    '''            const linkage=p.fullLinkageDecision({prBody:pr.body,prComments:comments,issueComments,...context.repo,issueNumber:Number(process.env.ISSUE),prNumber:Number(process.env.PR)});
            if(pr.head.repo?.full_name!==process.env.EXPECTED_REPO||pr.head.sha!==process.env.SHA||pr.state!=="open"||pr.base.ref!==context.payload.repository.default_branch||!p.isImplementation(issue.labels)||!p.lifecycleAtAgentPr(pr.labels,issue.labels).ok||!linkage.ok) core.setFailed("TOCTOU_REJECTED");
''',
    '''            const linkage=p.fullLinkageDecision({prBody:pr.body,prComments:comments,issueComments,...context.repo,issueNumber:Number(process.env.ISSUE),prNumber:Number(process.env.PR)});
            const auth=a.authorizationDecision({comments:issueComments,repo:`${context.repo.owner}/${context.repo.repo}`,issueNumber:Number(process.env.ISSUE),title:issue.title,body:issue.body||'',labels:issue.labels,state:issue.state});
            if(!auth.ok||pr.head.repo?.full_name!==process.env.EXPECTED_REPO||pr.head.sha!==process.env.SHA||pr.state!=="open"||pr.base.ref!==context.payload.repository.default_branch||!p.isImplementation(issue.labels)||!p.lifecycleAtAgentPr(pr.labels,issue.labels).ok||!linkage.ok) core.setFailed("TOCTOU_REJECTED");
''',
)

write(".github/workflows/agent-ruleset-sync.yml", r'''name: Agent Protect main ruleset sync

on:
  workflow_dispatch:

permissions: {contents: read}

jobs:
  sync:
    runs-on: ubuntu-latest
    permissions: {contents: read}
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
        with:
          ref: ${{ github.event.repository.default_branch }}
          persist-credentials: false
          fetch-depth: 1
      - name: Require exact gate binding and one-Issue approval model
        env: {GH_TOKEN: '${{ secrets.AGENT_PUBLISH_TOKEN }}', GH_REPO: '${{ github.repository }}'}
        run: |
          set -euo pipefail
          test -n "$GH_TOKEN"
          test "${GITHUB_REF}" = "refs/heads/${{ github.event.repository.default_branch }}"
          test "$(git rev-parse HEAD)" = "${GITHUB_SHA}"
          rulesets="$(gh api "repos/$GH_REPO/rulesets")"
          id="$(jq -r '[.[]|select(.name=="Protect main" and .target=="branch" and .enforcement=="active")]|if length==1 then .[0].id else empty end'<<<"$rulesets")"
          test -n "$id"
          current="$(gh api "repos/$GH_REPO/rulesets/$id")"
          jq -e '
            (.bypass_actors|length==0) and
            (.conditions.ref_name.include==["refs/heads/main"]) and
            ((.conditions.ref_name.exclude//[])==[]) and
            ([.rules[]|select(.type=="deletion")]|length==1) and
            ([.rules[]|select(.type=="non_fast_forward")]|length==1) and
            ([.rules[]|select(.type=="pull_request")]|length==1) and
            ([.rules[]|select(.type=="required_status_checks")]|length==1)
          ' <<<"$current" >/dev/null
          jq '
            .rules|=map(
              if .type=="required_status_checks" then
                .parameters.strict_required_status_checks_policy=true |
                .parameters.required_status_checks=([.parameters.required_status_checks[]|select(.context!="agent-verified-gate")]+[{"context":"agent-verified-gate","integration_id":15368}]|sort_by(.context))
              elif .type=="pull_request" then
                .parameters.required_approving_review_count=0 |
                .parameters.required_reviewers=[] |
                .parameters.require_code_owner_review=false |
                .parameters.require_last_push_approval=false |
                .parameters.require_extra_approval_for_unattributed_changes=false
              else . end
            ) |
            {name,target,enforcement,conditions,rules,bypass_actors}
          ' <<<"$current" >"$RUNNER_TEMP/ruleset.json"
          gh api --method PUT "repos/$GH_REPO/rulesets/$id" --input "$RUNNER_TEMP/ruleset.json" >/dev/null
          fresh="$(gh api "repos/$GH_REPO/rulesets/$id")"
          jq -e '
            (.bypass_actors|length==0) and
            (.conditions.ref_name.include==["refs/heads/main"]) and
            ((.conditions.ref_name.exclude//[])==[]) and
            ([.rules[]|select(.type=="deletion")]|length==1) and
            ([.rules[]|select(.type=="non_fast_forward")]|length==1) and
            ([.rules[]|select(.type=="pull_request")]|length==1) and
            ([.rules[]|select(.type=="required_status_checks")]|length==1) and
            ([.rules[]|select(.type=="required_status_checks")][0].parameters.strict_required_status_checks_policy==true) and
            ([.rules[]|select(.type=="required_status_checks")][0].parameters.required_status_checks|[.[]|select(.context=="agent-verified-gate" and .integration_id==15368)]|length==1) and
            ([.rules[]|select(.type=="pull_request")][0].parameters.required_approving_review_count==0)
          ' <<<"$fresh" >/dev/null
''')

doc = read("docs/autonomous-development-pipeline.md")
marker = doc.index("## Classification and human opt-in")
replacement = r'''# Autonomous Development Pipeline v2

## Purpose and boundary

The normal operating model is **one explicit human authorization of one concrete
`type:implementation` Issue**, followed by a fail-closed autonomous Builder,
authoritative CI, independent Codex review, exact-SHA verification,
`agent-verified-gate`, and expected-head automatic merge. Codex/model jobs never
receive GitHub write credentials. The system remains paper-only and never
authorizes live trading or an economic/runtime deployment.

```text
human authorizes exact Issue specification once
  → agent:running → autonomous Builder → Draft PR → agent:pr
  → newest exact-SHA CI + independent Codex PASS
  → agent:verified → agent-verified-gate → expected-head AUTO MERGE

any stale/ambiguous/conflicting condition → agent:needs-human
```

## Maintainer quick-start: normal happy path

- [ ] Create or select one concrete `type:implementation` Issue with an exact,
      reviewable Definition of Done.
- [ ] Run **Agent authorize implementation** once with that Issue number. This
      writes the durable authorization marker bound to the canonical Issue
      title/body/classification and starts the Builder.
- [ ] Do not manually create/update the autonomous PR, acknowledge a SHA, approve
      the PR, rerun CI, reconcile `main`, or merge during the normal path.
- [ ] The trusted controller publishes the sealed Builder artifact, creates the
      deterministic linked PR, reconciles current `main` without force-push,
      handles bounded eligible fixes/transient infrastructure retries, and
      regenerates all SHA-bound evidence after every head change.
- [ ] The newest authoritative exact-SHA CI run must have all nine required jobs
      green and the independent Codex Reviewer must PASS the same SHA.
- [ ] `agent-verified-gate` then revalidates current authorization, linkage,
      current-main ancestry, CI, review and verification evidence. The trusted
      merge controller performs a final fresh TOCTOU evaluation and merges only
      the expected unchanged head SHA.
- [ ] If the Issue specification/classification changes, the Issue closes, a
      conflict occurs, a retry/fix budget is exhausted, or evidence becomes
      ambiguous, the pair fails closed to `agent:needs-human`. A new explicit
      authorization is required before autonomous work can resume.

**Agent exact-SHA review acknowledgement** and the legacy manual state-transition
workflow are compatibility/recovery tools only. They are not part of the normal
Issue #100 happy path and never substitute for current Issue authorization,
newest exact-SHA CI, independent Codex PASS, or `agent-verified-gate`.

'''
write("docs/autonomous-development-pipeline.md", replacement + doc[marker:])

replace_once(
    ".github/scripts/agent-autonomy.test.cjs",
    '''    "backend/src/quantlab/security.py",
    "backend/src/quantlab/live_trading.py",
''',
    '''    "backend/src/quantlab/security.py",
    "backend/src/quantlab/phase4.py",
    "backend/src/quantlab/trading.py",
    "backend/src/quantlab/live_trading.py",
''',
)
with (ROOT / ".github/scripts/agent-autonomy.test.cjs").open("a") as f:
    f.write(r'''
test("ordinary security failure log boilerplate is not transient",()=>{const p=require("./agent-pipeline.cjs"),sha="f".repeat(40),r=p.classifyCiFailure({jobs:[{id:8,name:"security",conclusion:"failure",steps:[{name:"npm audit",conclusion:"failure"}]}],runHeadSha:sha,expectedHeadSha:sha,sourceRunId:78,runAttempt:1,logExcerpt:"Current runner version 2.337.0\nDownloading action\nnpm audit found a critical vulnerability",config:{requiredCiJobs:["security"],failureClassPolicy:{eligible:[],denied:["infra-transient","security"]},protectedDiagnosticPatterns:[]}});assert.equal(r.failureClass,"security");});
''')

for path in [".github/scripts/pr108-finalize.py", ".github/workflows/pr108-finalize.yml"]:
    p = ROOT / path
    if p.exists():
        p.unlink()
