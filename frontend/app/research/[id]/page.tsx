import { api } from "@/lib/api";
import { session } from "@/lib/auth";
import { deploymentAction, eligibilityAction, promotionAction } from "@/app/actions";
import { MutationForm } from "@/components/mutation-form";
import { JsonTable, Status, na } from "@/components/ui";
export const dynamic = "force-dynamic";
type Decision = {
    id?: string;
    decision_id?: string;
    status: string;
    policy_id: string;
    policy_version: number;
    evaluated_at: string;
    reason: string;
    actor: Record<string, unknown>;
    rules: Record<string, unknown>[];
    integrity_hash?: string;
};
export default async function ExperimentDetail({ params }: {
    params: Promise<{
        id: string;
    }>;
}) { const { id } = await params; const [e, user] = await Promise.all([api<Record<string, any> & {
        eligibility: Decision | null;
    }>(`/operator/research/experiments/${encodeURIComponent(id)}`), session()]); const d = e.eligibility, admin = user?.role === "ADMIN", promoted = e.decision === "PAPER_CANDIDATE" || e.status === "PAPER_CANDIDATE"; return <><h1>Detail experimentu</h1><p className="muted">ID experimentu: <code>{id}</code></p><div className="workflow" aria-label="Postup vyhodnocení">Data → verze dat <strong>{na(e.snapshot_id)}</strong> → experiment <Status value={e.status}/> → způsobilost <Status value={d?.status ?? "NOT EVALUATED"}/> → zařazení <Status value={promoted ? "PAPER_CANDIDATE" : "NOT PROMOTED"}/> → nasazení → schválení → monitoring → automatická simulace</div><h2>Původ výzkumu</h2><JsonTable rows={[{ strategy: e.strategy_identity ?? `${e.strategy_name}:${e.strategy_version}`, snapshot_id: e.snapshot_id, code_sha: e.code_sha, seed: e.seed, parameter_space: e.parameter_configs ?? e.parameter_space, selected_parameters: e.selected_parameters, oos_metrics: e.oos_metrics, status: e.status, current_decision: e.decision }]}/><h2>Posouzení způsobilosti</h2>{d ? <div className="card"><Status value={d.status}/><p>ID rozhodnutí: <code>{na(d.decision_id ?? d.id)}</code> · kontrolní otisk: <code>{na(d.integrity_hash)}</code></p><p>Použitá pravidla: <strong>{d.policy_id} / v{d.policy_version}</strong> · vyhodnoceno {d.evaluated_at}</p><p>Provedl: {JSON.stringify(d.actor)} · důvod: {d.reason}</p><JsonTable rows={d.rules}/></div> : <p className="card">Způsobilost zatím nebyla vyhodnocena.</p>}{admin && <div className="grid"><MutationForm action={eligibilityAction} title="Vyhodnotit způsobilost" submit="Vyhodnotit způsobilost"><input type="hidden" name="id" value={id}/><label>Důvod změny<textarea name="reason" required minLength={3}/></label></MutationForm><MutationForm action={promotionAction} title="Zařazení kandidáta" submit="Zařadit pro simulaci" disabled={d?.status !== "ELIGIBLE" || promoted}><input type="hidden" name="id" value={id}/><p>Experiment {id} · pravidla v{d?.policy_version ?? "N/A"} · stav <Status value={e.status}/> → kandidát pro simulaci.</p><label>Důvod změny<textarea name="reason" required minLength={3}/></label></MutationForm><MutationForm action={deploymentAction} title="Vytvořit nasazení do simulace" submit="Vytvořit nasazení do simulace" disabled={!promoted}><input type="hidden" name="experiment_id" value={id}/><p>Experiment {id} · verze dat {na(e.snapshot_id)} · účet paper-main. Konfiguraci nasazení sestaví server.</p><label>Důvod změny<textarea name="reason" required minLength={3}/></label></MutationForm></div>}{!admin && <p className="card muted">Máte přístup pouze ke čtení. Způsobilost a nasazení spravuje administrátor.</p>}</>; }
