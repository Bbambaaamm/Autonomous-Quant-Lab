import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { validateLockfile } from "./validate-lockfile.mjs";

const packageDocument = JSON.parse(await readFile("package.json", "utf8"));
const authoritativeLock = JSON.parse(await readFile("package-lock.json", "utf8"));

test("autoritativní lockfile projde kontrolou", () => {
  assert.doesNotThrow(() => validateLockfile(packageDocument, authoritativeLock));
});

test("root-only placeholder lockfile je odmítnut", () => {
  const placeholder = {
    name: packageDocument.name,
    version: packageDocument.version,
    lockfileVersion: 3,
    requires: true,
    packages: { "": {} },
  };
  assert.throws(
    () => validateLockfile(packageDocument, placeholder),
    /root dependencies neodpovídá package.json|root-only placeholder/,
  );
});
