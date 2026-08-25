import { JsonTable } from "@/components/ui";
import { api } from "@/lib/api";
import { auditFilterKeys, auditPageUrl } from "@/lib/audit-pagination";

export const dynamic = "force-dynamic";

export default async function Audit({
  searchParams,
}: {
  searchParams: Promise<Record<string, string>>;
}) {
  const parameters = await searchParams;
  const query = new URLSearchParams({ limit: "50", offset: parameters.offset ?? "0" });
  for (const key of auditFilterKeys) {
    if (parameters[key]) query.set(key, parameters[key]);
  }
  const data = await api<{
    items: Record<string, unknown>[];
    total: number;
    offset: number;
    limit: number;
  }>(`/operator/audit?${query.toString()}`);

  return <><h1>Audit history</h1><form className="toolbar" method="get"><label>Event type <input name="event_type" defaultValue={parameters.event_type} /></label><label>Entity type <input name="entity_type" defaultValue={parameters.entity_type} /></label><label>Entity ID <input name="entity_id" defaultValue={parameters.entity_id} /></label><label>Correlation ID <input name="correlation_id" defaultValue={parameters.correlation_id} /></label><label>Od UTC <input type="datetime-local" name="start_utc" defaultValue={parameters.start_utc} /></label><label>Do UTC <input type="datetime-local" name="end_utc" defaultValue={parameters.end_utc} /></label><button>Filtrovat na serveru</button></form><p>{data.total} událostí · offset {data.offset}</p><JsonTable rows={data.items} /><div className="toolbar">{data.offset > 0 && <a className="badge" href={auditPageUrl(parameters, Math.max(0, data.offset - data.limit))}>Předchozí</a>}{data.offset + data.limit < data.total && <a className="badge" href={auditPageUrl(parameters, data.offset + data.limit)}>Další</a>}</div></>;
}
