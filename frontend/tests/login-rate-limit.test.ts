import {describe, expect, it} from "vitest";

import {LoginFailureLimiter} from "../lib/login-rate-limit";

describe("login failure limiter", () => {
  it("počítá pouze zaznamenaná selhání a úspěch bucket vyčistí", () => {
    let now = 1_000_000;
    const limiter = new LoginFailureLimiter(2, 300_000, () => now);

    expect(limiter.blocked("operator")).toBe(false);
    expect(limiter.blocked("operator")).toBe(false);
    limiter.recordFailure("operator");
    expect(limiter.blocked("operator")).toBe(false);
    limiter.recordFailure("operator");
    expect(limiter.blocked("operator")).toBe(true);

    limiter.clear("operator");
    expect(limiter.blocked("operator")).toBe(false);
    now += 300_001;
    expect(limiter.blocked("operator")).toBe(false);
  });
});
