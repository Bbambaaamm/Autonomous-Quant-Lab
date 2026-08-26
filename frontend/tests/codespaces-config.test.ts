import { afterEach, describe, expect, it, vi } from "vitest";

import { codespacesAllowedOrigins } from "../next.config";

afterEach(() => vi.unstubAllEnvs());

describe("GitHub Codespaces Server Actions origins", () => {
  it("povolí pouze konkrétní proxy host a lokální přístup v Codespaces", () => {
    const origins = codespacesAllowedOrigins({
      CODESPACES: "true",
      CODESPACE_NAME: "probable-space",
      GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN: "app.github.dev",
    });
    expect(origins).toEqual([
      "probable-space-3000.app.github.dev",
      "localhost:3000",
      "127.0.0.1:3000",
    ]);
    expect(origins).not.toContain("*");
  });

  it("mimo Codespaces neaktivuje localhost výjimku", () => {
    expect(codespacesAllowedOrigins({ CODESPACES: "false" })).toBeUndefined();
  });

  it("odmítne neplatný proxy host namísto wildcard fallbacku", () => {
    expect(() => codespacesAllowedOrigins({
      CODESPACES: "true",
      CODESPACE_NAME: "space/attacker",
      GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN: "app.github.dev",
    })).toThrow("Neplatný GitHub Codespaces host");
  });
});
