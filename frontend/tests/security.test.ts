import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { auditPageUrl } from "../lib/audit-pagination";

describe("paper-only hranice", () => {
  it("navigace neobsahuje live akci ani generic proxy", () => {
    const layout = fs.readFileSync(path.join(process.cwd(), "app/layout.tsx"), "utf8");
    expect(layout).not.toMatch(/enable.live|\/proxy\?url/i);
    expect(layout).toContain("LIVE ABSENT");
  });

  it("akce vyžadují serverovou odpověď a explicitní potvrzení", () => {
    const actions = fs.readFileSync(path.join(process.cwd(), "app/actions.ts"), "utf8");
    expect(actions).toContain("confirmation");
    expect(actions).toContain("if (!response.ok)");
  });
});

describe("Phase 9 security boundary", () => {
  it("serverový API klient používá role-specific credential", () => {
    const api = fs.readFileSync(path.join(process.cwd(), "lib/api.ts"), "utf8");
    expect(api).toContain("backendToken(current.role)");
    expect(api).not.toContain("NEXT_PUBLIC_");
  });

  it("cookie session a browser mutations jsou chráněné", () => {
    const auth = fs.readFileSync(path.join(process.cwd(), "lib/auth.ts"), "utf8");
    const actions = fs.readFileSync(path.join(process.cwd(), "app/actions.ts"), "utf8");
    expect(auth).toContain("timingSafeEqual");
    expect(auth).toContain("assertSameOrigin");
    expect(actions).toContain("await assertSameOrigin()");
  });

  it("produkční responses deklarují obranné headers", () => {
    const config = fs.readFileSync(path.join(process.cwd(), "next.config.ts"), "utf8");
    for (const header of ["Content-Security-Policy", "X-Content-Type-Options", "Referrer-Policy", "Permissions-Policy", "X-Frame-Options"]) expect(config).toContain(header);
    expect(config).not.toContain("unsafe-eval");
    expect(config).not.toMatch(/allowedOrigins[^;]*["']\*["']/s);
  });

  it("dev helper vytváří obě strany role credentials a login identitu", () => {
    const helper = fs.readFileSync(path.join(process.cwd(), "../scripts/generate-dev-secrets.sh"), "utf8");
    for (const variable of ["OPERATOR_USERNAME", "QUANTLAB_API_VIEWER_TOKEN", "QUANTLAB_API_OPERATOR_TOKEN", "QUANTLAB_API_ADMIN_TOKEN"]) expect(helper).toContain(variable);
  });
});

describe("audit pagination", () => {
  it("zachová aktivní filtry při změně offsetu", () => {
    const url = auditPageUrl({ event_type: "KILL_SWITCH_MANUAL_HALT", entity_id: "paper-main" }, 50);
    expect(url).toContain("event_type=KILL_SWITCH_MANUAL_HALT");
    expect(url).toContain("entity_id=paper-main");
    expect(url).toContain("offset=50");
  });
});
