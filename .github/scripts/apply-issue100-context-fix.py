from pathlib import Path

path = Path('.github/workflows/bootstrap-issue-100-publisher.yml')
text = path.read_text(encoding='utf-8')

old_context = '''          for path in \\
            AGENTS.md \\
            docs/ROADMAP.md \\
            docs/autonomous-development-pipeline.md \\
            docs/adr/0003-autonomous-development-pipeline-v2.md \\
            .github/agent-pipeline.json \\
            .github/scripts/agent-pipeline.cjs \\
            .github/scripts/agent-pipeline.test.cjs \\
            .github/workflows/agent-state-transition.yml \\
            .github/workflows/agent-verify.yml \\
            .github/workflows/agent-invalidate-verification.yml \\
            .github/workflows/agent-codex-review.yml \\
            .github/workflows/agent-ci-fixer.yml; do
'''
new_context = '''          for path in \\
            AGENTS.md \\
            docs/ROADMAP.md \\
            docs/autonomous-development-pipeline.md \\
            docs/adr/0003-autonomous-development-pipeline-v2.md \\
            .github/agent-pipeline.json \\
            .github/scripts/agent-pipeline.cjs \\
            .github/scripts/agent-pipeline.test.cjs \\
            .github/workflows/agent-*.yml \\
            .github/workflows/bootstrap-issue-100-publisher.yml \\
            .github/workflows/ci.yml; do
'''
if text.count(old_context) != 1:
    raise SystemExit(f'context block match count={text.count(old_context)}')
text = text.replace(old_context, new_context, 1)

old_prompt = '''          Builder publication must NOT depend on Codex gh authentication, a writable remote, or make_pr. Implement generate -> credential-free validate/test/seal -> trusted publisher with a durable bounded artifact, deterministic/idempotent Draft PR publication, exact Issue-spec/base/checksum binding, and TOCTOU checks.\n\n          Return ONLY structured JSON with result=PATCH or BLOCK. PATCH must be a complete unified diff against the exact base SHA from scope.json. Do not run repository code.\n'''
new_prompt = '''          Builder publication must NOT depend on Codex gh authentication, a writable remote, or make_pr. Implement generate -> credential-free validate/test/seal -> trusted publisher with a durable bounded artifact, deterministic/idempotent Draft PR publication, exact Issue-spec/base/checksum binding, and TOCTOU checks.\n\n          The read-only generation job is NOT responsible for mutating the live GitHub ruleset or executing the live dogfood rehearsal. Lack of GitHub mutation credentials, inability to change the active ruleset, and inability to run live post-publication acceptance are expected properties of this job and MUST NOT be reasons to return BLOCK. Encode the required repository-side controller, verification, reconciliation, ruleset-management support, regression tests, and documentation in the patch; the trusted control plane performs live ruleset mutation and dogfood after publication/merge.\n\n          Return ONLY structured JSON with result=PATCH or BLOCK. PATCH must be a complete unified diff against the exact base SHA from scope.json. Do not run repository code.\n'''
if text.count(old_prompt) != 1:
    raise SystemExit(f'prompt block match count={text.count(old_prompt)}')
text = text.replace(old_prompt, new_prompt, 1)

path.write_text(text, encoding='utf-8')
print('Issue 100 bootstrap trusted context expanded')
