import Link from "next/link";
import { api } from "@/lib/api";
import { session } from "@/lib/auth";
import { JsonTable } from "@/components/ui";
import { MutationForm } from "@/components/mutation-form";
import { experimentAction } from "@/app/actions";
export const dynamic = "force-dynamic";
export default async function Research() { const [d, user] = await Promise.all([api<{
        items: Record<string, unknown>[];
        total: number;
    }>("/operator/research/experiments?limit=50&offset=0"), session()]); return <><h1>Výzkumné experimenty</h1><p>{d.total} uložených experimentů. Výsledky mimo trénovací období a rozhodnutí pocházejí ze serveru.</p><div className="experiment-links">{d.items.map((x, i) => <Link key={String(x.id)} href={`/research/${String(x.id)}`}>Experiment {i + 1}<code>{String(x.id).slice(0, 12)}…</code></Link>)}</div><JsonTable rows={d.items}/>{user?.role === "ADMIN" ? <MutationForm action={experimentAction} title="Nový chronologický experiment" submit="Spustit experiment"><label>ID verze dat<input name="snapshot_id" required/></label><label>Název strategie<input name="strategy_name" required/></label><label>Verze strategie<input name="strategy_version" required/></label><label>Varianty parametrů (pole JSON)<textarea name="parameter_configs" required defaultValue={'[{"lookback":20}]'}/></label><label>Verze kódu (SHA, 40 znaků)<input name="code_sha" required minLength={40} maxLength={40}/></label><label>Seed pro opakovatelné výsledky<input name="seed" type="number" defaultValue="42"/></label><label>Důvod změny<textarea name="reason" required minLength={3}/></label></MutationForm> : <p className="card muted">Máte přístup pouze ke čtení. Experiment může spustit správce.</p>}</>; }
