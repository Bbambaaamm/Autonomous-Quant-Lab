"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");

test("Builder branch identity is stable for the same base and changes with a new base", () => {
  const workflow = fs.readFileSync(".github/workflows/agent-builder-publish.yml", "utf8");
  assert.match(workflow, /branch="agent\/issue-\$\{ISSUE\}-\$\{SPEC:0:12\}-\$\{BASE:0:12\}"/);

  const branchFor = (issue, spec, base) => `agent/issue-${issue}-${spec.slice(0, 12)}-${base.slice(0, 12)}`;
  const spec = "c".repeat(64);
  const baseA = "a".repeat(40);
  const baseB = "b".repeat(40);

  const first = branchFor(109, spec, baseA);
  assert.equal(first, branchFor(109, spec, baseA));
  assert.notEqual(first, branchFor(109, spec, baseB));
});
