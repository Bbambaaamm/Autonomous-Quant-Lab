# ADR 0005 — One-time Issue #100 bootstrap publisher

## Context

Issue #100 must replace the current manual autonomous-development workflow with a one-approval operating model. Two Codex issue tasks completed candidate commits only inside isolated workspaces and could not push or open a GitHub pull request because those sandboxes intentionally lacked authenticated writable Git remotes.

That limitation is desirable for model isolation, but it means the migration itself needs a one-time trusted bootstrap path before the permanent Builder publisher exists on `main`.

## Decision

Add `bootstrap-issue-100-publisher.yml` as a narrowly scoped temporary migration workflow.

It is triggered only by the exact maintainer Issue #100 command and only while Issue #100 is an open `type:implementation` Issue in `agent:running` without `agent:needs-human`.

The workflow separates trust domains:

1. preparation reads the exact Issue specification and current default-branch SHA;
2. Codex receives `OPENAI_API_KEY` but only read permissions and returns a bounded structured patch;
3. validation receives neither model secret nor repository write credential and verifies exact base/spec/checksum binding, allowed governance-only paths, modes, JSON/YAML syntax, pipeline tests and `git diff --check`;
4. the final publisher reconstructs only the sealed patch, repeats mutable Issue/base checks, and receives `AGENT_PUBLISH_TOKEN` only for deterministic branch and Draft PR publication.

The bootstrap workflow cannot modify application/runtime, dependencies, migrations, deployment, broker/execution/risk/trading, `AGENTS.md`, or `docs/ROADMAP.md`.

## Consequences

The Issue #100 implementation can now reach a real GitHub Draft PR without giving Codex GitHub write credentials or depending on `gh auth`, writable remotes, or `make_pr` inside the model sandbox.

After Issue #100 is merged and its permanent Builder publisher is proven by dogfood, this bootstrap workflow is obsolete and should be removed by the permanent implementation or a follow-up cleanup.
