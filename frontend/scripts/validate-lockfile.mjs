import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const DEPENDENCY_FIELDS = [
  "dependencies",
  "devDependencies",
  "optionalDependencies",
  "peerDependencies",
];

function fail(message) {
  throw new Error(`Neplatný package-lock.json: ${message}`);
}

function normalizedDependencies(packageDocument, field) {
  const value = packageDocument[field] ?? {};
  if (typeof value !== "object" || Array.isArray(value)) {
    fail(`pole ${field} musí být objekt`);
  }
  return value;
}

function dependencyMapsMatch(actual, expected) {
  const actualEntries = Object.entries(actual).sort(([left], [right]) => left.localeCompare(right));
  const expectedEntries = Object.entries(expected).sort(([left], [right]) => left.localeCompare(right));
  return JSON.stringify(actualEntries) === JSON.stringify(expectedEntries);
}

export function validateLockfile(packageDocument, lockDocument) {
  if (![2, 3].includes(lockDocument.lockfileVersion)) {
    fail(`nepodporovaná lockfileVersion ${String(lockDocument.lockfileVersion)}`);
  }
  if (!lockDocument.packages || typeof lockDocument.packages !== "object") {
    fail("chybí mapa packages");
  }

  const root = lockDocument.packages[""];
  if (!root || typeof root !== "object") {
    fail('chybí root package packages[""]');
  }

  const directNames = new Set();
  for (const field of DEPENDENCY_FIELDS) {
    const expected = normalizedDependencies(packageDocument, field);
    const actual = normalizedDependencies(root, field);
    if (!dependencyMapsMatch(actual, expected)) {
      fail(`root ${field} neodpovídá package.json`);
    }
    for (const name of Object.keys(expected)) directNames.add(name);
  }

  if (directNames.size > 0 && Object.keys(lockDocument.packages).length <= 1) {
    fail("dependency tree je prázdný root-only placeholder");
  }

  for (const name of directNames) {
    const entry = lockDocument.packages[`node_modules/${name}`];
    if (!entry || typeof entry !== "object" || typeof entry.version !== "string") {
      fail(`chybí skutečný lockfile entry pro přímou závislost ${name}`);
    }
  }
}

export async function validateFiles(packagePath, lockPath) {
  const [packageText, lockText] = await Promise.all([
    readFile(packagePath, "utf8"),
    readFile(lockPath, "utf8"),
  ]);
  validateLockfile(JSON.parse(packageText), JSON.parse(lockText));
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const packagePath = resolve(process.argv[2] ?? "package.json");
  const lockPath = resolve(process.argv[3] ?? "package-lock.json");
  await validateFiles(packagePath, lockPath);
  console.log("package-lock.json je strukturálně konzistentní s package.json");
}
