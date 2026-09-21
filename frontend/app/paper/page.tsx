import Link from "next/link";
import { api, type Performance } from "@/lib/api";
import { Empty, JsonTable, Status } from "@/components/ui";
import { LineChart } from "@/components/chart";
export const dynamic = "force-dynamic";
type Paper = {
    account: Record<string, unknown> | null;
    marked_equity: string | null;
    positions: Record<string, unknown>[];
    orders: Record<string, unknown>[];
    fills: Record<string, unknown>[];
    monitoring: Record<string, unknown> | null;
    latest_evaluation: Record<string, unknown> | null;
    as_of: string | null;
};
export default async function Paper({ searchParams }: {
    searchParams: Promise<{
        period?: string;
    }>;
}) { const q = await searchParams; const period = ["1M", "3M", "6M", "YTD", "1Y", "ALL"].includes(q.period ?? "") ? q.period! : "ALL"; const [d, p] = await Promise.all([api<Paper>("/operator/paper"), api<Performance>(`/operator/paper/performance?period=${period}`)]); return <><h1>Simulované portfolio</h1><p className="muted">Simulovaný účet · poslední aktualizace záznamu {d.as_of ?? "N/A"}</p><div className="toolbar">{["1M", "3M", "6M", "YTD", "1Y", "ALL"].map(x => <Link className="badge" key={x} href={`/paper?period=${x}`} aria-current={x === period ? "page" : undefined}>{({ "1M": "1 měsíc", "3M": "3 měsíce", "6M": "6 měsíců", "YTD": "Od začátku roku", "1Y": "1 rok", "ALL": "Celá historie" } as Record<string, string>)[x]}</Link>)}</div><LineChart points={p.points} field="marked_equity" label="Hodnota portfolia"/><LineChart points={p.points} field="drawdown" label="Pokles od maxima"/><LineChart points={p.points} field="cumulative_return" label="Kumulativní výnos"/><section><h2>Účet</h2>{d.account ? <JsonTable rows={[d.account]}/> : <Empty />}</section><section><h2>Monitoring</h2>{d.monitoring ? <div className="card"><Status value={d.monitoring.state}/> {d.latest_evaluation && <Status value={d.latest_evaluation.verdict}/>} <Link href={`/paper/monitoring/${d.monitoring.monitoring_id}`}>Detail →</Link></div> : <Empty />}</section><h2>Pozice</h2><JsonTable rows={d.positions}/><h2>Příkazy</h2><JsonTable rows={d.orders}/><h2>Realizované obchody</h2><JsonTable rows={d.fills}/></>; }
