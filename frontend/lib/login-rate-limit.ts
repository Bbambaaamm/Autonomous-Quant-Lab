export class LoginFailureLimiter {
  private readonly attempts = new Map<string, number[]>();

  constructor(
    private readonly limit = 5,
    private readonly windowMs = 300_000,
    private readonly clock: () => number = Date.now,
  ) {}

  private state(identity: string) {
    const now = this.clock();
    const key = identity.slice(0, 128);
    const recent = (this.attempts.get(key) ?? []).filter(
      (value) => value > now - this.windowMs,
    );
    this.attempts.set(key, recent);
    return {key, recent, now};
  }

  blocked(identity: string) {
    return this.state(identity).recent.length >= this.limit;
  }

  recordFailure(identity: string) {
    const {key, recent, now} = this.state(identity);
    recent.push(now);
    this.attempts.set(key, recent);
  }

  clear(identity: string) {
    this.attempts.delete(identity.slice(0, 128));
  }
}
