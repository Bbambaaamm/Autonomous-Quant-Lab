"use strict";

// Pre-merge API integration test, NOT a production request, review or gate issuer.
// The workflow provides GET-only access with a repository-scoped READ token.
const p = require('./agent-pipeline.cjs');
const a = require('./agent-autonomy.cjs');
const assert = (value, reason) => { if (!value) throw new Error(reason); };
const positive = x => Number.isSafeInteger(x) && x > 0;

async function collect({get, repository, prNumber, headSha, baseSha}) {
  assert(typeof get === 'function' && /^[\w.-]+\/[\w.-]+$/.test(repository) && positive(prNumber) &&
    /^[a-f0-9]{40}$/.test(headSha) && /^[a-f0-9]{40}$/.test(baseSha), 'PREMERGE_INPUT_INVALID');
  const prefix = `/repos/${repository}`, reads = [];
  const read = async suffix => {
    assert((suffix === '' || suffix.startsWith('/')) && !suffix.includes('://') && !suffix.includes('..'), 'PREMERGE_ENDPOINT_INVALID');
    const url = prefix + suffix; reads.push(url);
    assert(reads.length <= 200, 'PREMERGE_READ_LIMIT');
    return get(url); // No body, method argument, token, dispatch or mutation adapter.
  };
  const pages = async (suffix, key = null) => {
    const out = [];
    for (let page = 1; page <= 20; page++) {
      const response = await read(`${suffix}${suffix.includes('?') ? '&' : '?'}per_page=100&page=${page}`);
      const values = key ? response[key] : response;
      assert(Array.isArray(values), 'PREMERGE_LIST_INVALID');
      out.push(...values);
      if (values.length < 100) return out;
    }
    throw new Error('PREMERGE_LIST_INCOMPLETE');
  };
  const pr = await read(`/pulls/${prNumber}`);
  assert(pr.number === prNumber && pr.state === 'open' && pr.draft === false && pr.auto_merge === null &&
    pr.head?.repo?.full_name === repository && pr.base?.repo?.full_name === repository &&
    pr.head.sha === headSha && pr.base.sha === baseSha, 'PREMERGE_PR_BINDING');
  const issueNumber = p.parseAgentIssue(pr.body);
  assert(positive(issueNumber), 'PREMERGE_ISSUE_MARKER');
  const repo = await read('');
  assert(repo.full_name === repository && pr.base.ref === repo.default_branch, 'PREMERGE_REPOSITORY');
  const branch = await read(`/branches/${encodeURIComponent(repo.default_branch)}`);
  assert(branch.commit?.sha === baseSha, 'PREMERGE_BASE_CHANGED');
  const issue = await read(`/issues/${issueNumber}`);
  assert(issue.number === issueNumber && issue.state === 'open' && !issue.pull_request, 'PREMERGE_ISSUE_BINDING');
  const issueComments = await pages(`/issues/${issueNumber}/comments`);
  const authorization = a.authorizationDecision({comments: issueComments, repo: repository, issueNumber,
    title: issue.title, body: issue.body || '', labels: issue.labels, state: issue.state});
  assert(authorization.ok, 'PREMERGE_AUTHORIZATION');
  const definition = await read('/actions/workflows/ci.yml');
  assert(positive(definition.id) && definition.path === '.github/workflows/ci.yml', 'PREMERGE_CI_WORKFLOW');
  const runs = await pages(`/actions/workflows/ci.yml/runs?event=pull_request&head_sha=${headSha}`, 'workflow_runs');
  const ci = runs.filter(x => x.workflow_id === definition.id && x.path === definition.path &&
    x.head_sha === headSha && x.event === 'pull_request' && x.pull_requests?.length === 1 &&
    x.pull_requests[0].number === prNumber && x.head_repository?.full_name === repository)
    .sort((x,y)=>y.id-x.id)[0];
  assert(ci && positive(ci.run_attempt), 'PREMERGE_CI_MISSING');
  const jobs = await pages(`/actions/runs/${ci.id}/jobs?filter=all`, 'jobs');
  const listed = await pages('/rulesets');
  const matches = listed.filter(x => x.name === 'Protect main' && x.target === 'branch');
  assert(matches.length === 1 && positive(matches[0].id), 'PREMERGE_RULESET_AMBIGUOUS');
  const ruleset = await read(`/rulesets/${matches[0].id}`);
  assert(ruleset.id === matches[0].id, 'PREMERGE_RULESET_CHANGED');
  const after = await read(`/pulls/${prNumber}`);
  assert(after.head?.sha === headSha && after.base?.sha === baseSha && after.state === 'open', 'PREMERGE_TARGET_CHANGED');
  return {kind: 'premerge-api-observation-v1', result: 'LIVE_METADATA_OBSERVED', repository,
    prNumber, issueNumber, headSha, baseSha,
    authorization: {commentId: authorization.commentId, actor: authorization.actor, runId: authorization.runId, specHash: authorization.specHash},
    ci: {runId: ci.id, attempt: ci.run_attempt, status: ci.status, conclusion: ci.conclusion,
      jobs: jobs.map(x=>({id:x.id,name:x.name,attempt:x.run_attempt,conclusion:x.conclusion}))},
    protection: {id: ruleset.id, enforcement: ruleset.enforcement,
      bypassVisibility: Object.hasOwn(ruleset,'bypass_actors') && Array.isArray(ruleset.bypass_actors) ? 'VISIBLE' : 'UNKNOWN',
      bypassCount: Array.isArray(ruleset.bypass_actors) ? ruleset.bypass_actors.length : null},
    reads, operationalAcceptance: 'PENDING', productionEvidence: false};
}

async function canary(options) {
  const observation = await collect(options);
  const different = value => (value[0] === 'a' ? 'b' : 'a') + value.slice(1);
  const denials = [];
  // These are real re-reads of the target with deliberately invalid EXPECTATIONS,
  // not mutation of real labels/links and not fabricated API responses.
  for (const [name, overrides] of [
    ['mismatched-head', {headSha: different(options.headSha)}],
    ['mismatched-base', {baseSha: different(options.baseSha)}],
  ]) {
    let failure;
    try { await collect({...options,...overrides}); } catch (error) { failure = error; }
    assert(failure?.message === 'PREMERGE_PR_BINDING', 'PREMERGE_DENIAL_NOT_PROVEN');
    denials.push({case:name,result:'DENIED',error:failure.message,source:'live-api-read'});
  }
  return {...observation, denials, result: 'LIVE_READS_AND_IDENTITY_DENIALS_OBSERVED',
    limitations: ['Not a production request or reviewer PASS',
      'No production link/recover/gate/merge was executed',
      'Ruleset bypass authority remains unknown when GitHub omits bypass_actors',
      'Replayed label/link fault tests are reported separately from live reads']};
}
module.exports = {collect, canary};
