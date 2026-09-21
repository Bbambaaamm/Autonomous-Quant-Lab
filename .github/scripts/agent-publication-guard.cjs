"use strict";
const p = require("./agent-pipeline.cjs");
const a = require("./agent-autonomy.cjs");

function publicationAuthorized({env, repository, pr, issue, prComments, issueComments, main}) {
  const issueNumber = Number(env.ISSUE), prNumber = Number(env.PR);
  if (!Number.isSafeInteger(issueNumber) || issueNumber < 1 || !Number.isSafeInteger(prNumber) || prNumber < 1) return false;
  const [owner, repo] = env.GH_REPO.split("/");
  const auth = a.authorizationDecision({comments: issueComments, repo: env.GH_REPO, issueNumber,
    title: issue.title, body: issue.body || "", labels: issue.labels, state: issue.state});
  return auth.ok && auth.specHash === env.SPEC && repository.full_name === env.GH_REPO &&
    main.commit?.sha === env.BASE && pr.base?.sha === env.BASE &&
    pr.base?.ref === repository.default_branch && pr.head?.repo?.full_name === env.GH_REPO &&
    pr.head?.ref === env.HEAD_REF && [env.SHA, env.EXPECTED_RESULT].includes(pr.head?.sha) &&
    pr.number === prNumber && issue.number === issueNumber && pr.state === "open" && issue.state === "open" &&
    p.isImplementation(issue.labels) && p.lifecycleAtAgentPr(pr.labels, issue.labels).ok &&
    p.fullLinkageDecision({prBody: pr.body, prComments, issueComments, owner, repo, issueNumber, prNumber}).ok;
}

async function main(env = process.env) {
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(env.GH_REPO || "") || !env.GH_TOKEN) throw Error("PUBLICATION_AUTH_INVALID");
  const root = `https://api.github.com/repos/${env.GH_REPO}`;
  const get = async path => {
    const response = await fetch(root + path, {headers: {authorization: `Bearer ${env.GH_TOKEN}`,
      accept: "application/vnd.github+json"}, signal: AbortSignal.timeout(15000)});
    if (!response.ok) throw Error("PUBLICATION_AUTH_READ_FAILED");
    return response.json();
  };
  const comments = async number => {
    const result = [];
    for (let page = 1; page <= 100; page++) {
      const batch = await get(`/issues/${number}/comments?per_page=100&page=${page}`);
      if (!Array.isArray(batch)) throw Error("PUBLICATION_AUTH_READ_FAILED");
      result.push(...batch);
      if (batch.length < 100) return result;
    }
    throw Error("PUBLICATION_AUTH_COMMENTS_TOO_LARGE");
  };
  if (!/^[1-9][0-9]*$/.test(env.PR || "") || !/^[1-9][0-9]*$/.test(env.ISSUE || "")) throw Error("PUBLICATION_AUTH_INVALID");
  const repository = await get("");
  const [pr, issue, prComments, issueComments, branch] = await Promise.all([
    get(`/pulls/${env.PR}`), get(`/issues/${env.ISSUE}`), comments(env.PR), comments(env.ISSUE),
    get(`/branches/${encodeURIComponent(repository.default_branch)}`),
  ]);
  if (!publicationAuthorized({env, repository, pr, issue, prComments, issueComments, main: branch})) throw Error("PUBLICATION_AUTH_CHANGED");
}
module.exports = {publicationAuthorized, main};
if (require.main === module) main().catch(() => { process.stderr.write("PUBLICATION_AUTH_REJECTED\n"); process.exitCode = 1; });
