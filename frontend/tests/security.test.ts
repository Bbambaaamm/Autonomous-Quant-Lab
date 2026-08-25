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
    expect(actions).toContain("if(!r.ok)");
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
