export const auditFilterKeys = [
  "event_type",
  "entity_type",
  "entity_id",
  "correlation_id",
  "start_utc",
  "end_utc",
] as const;

export function auditPageUrl(parameters: Record<string, string>, offset: number): string {
  const query = new URLSearchParams({ limit: "50", offset: String(offset) });
  for (const key of auditFilterKeys) {
    if (parameters[key]) query.set(key, parameters[key]);
  }
  return `?${query.toString()}`;
}
