import {afterEach, describe, expect, it, vi} from "vitest";

import {validateFrontendSecurityConfig} from "../lib/security-config";

function validProductionEnvironment() {
  vi.stubEnv("NODE_ENV", "production");
  vi.stubEnv("SESSION_SECRET", "s".repeat(43));
  vi.stubEnv("QUANTLAB_API_VIEWER_TOKEN", "v".repeat(43));
  vi.stubEnv("QUANTLAB_API_OPERATOR_TOKEN", "o".repeat(43));
  vi.stubEnv("QUANTLAB_API_ADMIN_TOKEN", "a".repeat(43));
  vi.stubEnv("OPERATOR_USERNAME", "operator");
  vi.stubEnv("OPERATOR_PASSWORD_SCRYPT", `${"c".repeat(22)}:${"h".repeat(43)}`);
  vi.stubEnv("OPERATOR_ROLE", "ADMIN");
  vi.stubEnv("SESSION_MAX_AGE_SECONDS", "3600");
  vi.stubEnv("PUBLIC_BASE_URL", "https://quant.example");
  vi.stubEnv("FRONTEND_ALLOWED_HOSTS", "quant.example");
}

afterEach(() => vi.unstubAllEnvs());

describe("production frontend security konfigurace", () => {
  it("přijme úplnou fail-closed konfiguraci", () => {
    validProductionEnvironment();
    expect(validateFrontendSecurityConfig()).toEqual({role: "ADMIN", sessionMaxAgeSeconds: 3600});
  });

  it.each([
    ["SESSION_SECRET", "short"],
    ["OPERATOR_PASSWORD_SCRYPT", "plaintext"],
    ["OPERATOR_ROLE", "UNKNOWN"],
    ["SESSION_MAX_AGE_SECONDS", "0"],
    ["PUBLIC_BASE_URL", "http://quant.example"],
    ["FRONTEND_ALLOWED_HOSTS", "evil.example"],
  ])("odmítne nebezpečnou hodnotu %s", (name, value) => {
    validProductionEnvironment();
    vi.stubEnv(name, value);
    expect(() => validateFrontendSecurityConfig()).toThrow();
  });
});
