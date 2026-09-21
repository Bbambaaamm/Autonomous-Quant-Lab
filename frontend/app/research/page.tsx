import Link from "next/link";
import { api } from "@/lib/api";
import { session } from "@/lib/auth";
import { JsonTable } from "@/components/ui";
import { ResearchForm, type ResearchOptions } from "@/components/research-form";
import { dateText, translated } from "@/lib/display";
export const dynamic = "force-dynamic";
export default async function Research() {
    const [d, user, data, options] = await Promise.all([
        api<{
            items: Record<string, unknown>[];
            total: number;
        }>("/operator/research/experiments?limit=50&offset=0"), session(),
        api<{
            snapshots: Record<string, unknown>[];
            universes: {
                universe_id: string;
                name: string;
            }[];
        }>("/operator/data-health?membership_limit=1&membership_offset=0"),
        api<ResearchOptions>("/operator/research/options")
    ]);
    return <><h1>Výzkumné experimenty</h1><p>{d.total} uložených experimentů. Výsledky mimo trénovací období a rozhodnutí pocházejí ze serveru.</p>
 <div className="experiment-links">{d.items.map(x => <Link key={String(x.id)} href={`/research/${String(x.id)}`}>{translated(x.strategy_name)} · {x.created_at ? dateText(String(x.created_at)) : "Datum neuvedeno"}<code>{String(x.id).slice(0, 12)}…</code></Link>)}</div>
 <JsonTable rows={d.items} columns={["strategy_name", "status", "total_return", "trade_count", "created_at"]}/>
 {user?.role === "ADMIN" ? <ResearchForm options={options} snapshots={data.snapshots.map(s => ({ ...s, universe_name: data.universes.find(u => u.universe_id === s.universe_id)?.name ?? s.universe_id }))}/> : <p className="card muted">Máte přístup pouze ke čtení. Experiment může spustit správce.</p>}</>;
}
