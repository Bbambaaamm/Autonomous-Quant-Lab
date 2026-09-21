import { automationRetryAction } from "@/app/actions";
import { MutationForm } from "@/components/mutation-form";
import { JsonTable, Status } from "@/components/ui";
import { api } from "@/lib/api";
import { session } from "@/lib/auth";
export const dynamic = "force-dynamic";
type AutomationJob = Record<string, unknown> & {
    id?: string;
    job_type?: string;
};
type AutomationRun = Record<string, unknown> & {
    id?: string;
    status?: string;
    scheduled_job_id?: string;
};
const MANAGED_JOB_TYPES = new Set([
    "RUN_PAPER_DEPLOYMENT",
    "PREPARE_PAPER_SESSION",
    "MONITOR_PAPER_DEPLOYMENT",
]);
export default async function Operations() {
    const [data, user] = await Promise.all([
        api<Record<string, any>>("/operator/automation"),
        session(),
    ]);
    const admin = user?.role === "ADMIN";
    const jobs: AutomationJob[] = Array.isArray(data.jobs) ? data.jobs : [];
    const managedJobIds = new Set(jobs
        .filter((job) => MANAGED_JOB_TYPES.has(String(job.job_type ?? "")))
        .map((job) => String(job.id ?? ""))
        .filter(Boolean));
    const runs: AutomationRun[] = Array.isArray(data.runs) ? data.runs : [];
    const recoverable = runs.filter((run) => ["FAILED", "DEAD_LETTER"].includes(String(run.status ?? ""))
        && managedJobIds.has(String(run.scheduled_job_id ?? "")));
    return <>
    <h1>Provoz</h1>
    <p>Automatický provoz: <Status value={data.enabled ? "ENABLED" : "DISABLED"}/></p>
    <h2>Plánované úlohy</h2>
    <JsonTable rows={data.jobs} columns={["job_type", "enabled", "schedule_type", "next_run_at"]}/>
    <h2>Historie běhů</h2>
    <JsonTable rows={data.runs} columns={["scheduled_for", "status", "outcome", "no_action_reason", "attempt_count"]}/>
    {admin && recoverable.length > 0 && <section>
      <h2>Obnovení neúspěšných běhů</h2>
      <p className="muted">Opakování obnoví stejný neúspěšný běh. Nejprve odstraňte příčinu chyby a uveďte důvod opakování.</p>
      {recoverable.map((run) => {
                const runId = String(run.id ?? "");
                return <MutationForm key={runId} action={automationRetryAction} title={`Opakování běhu · ${runId}`} submit="Naplánovat opakování" danger disabled={!runId}>
          <input type="hidden" name="run_id" value={runId}/>
          <label>Důvod změny<textarea name="reason" required minLength={3}/></label>
        </MutationForm>;
            })}
    </section>}
    <h2>Pracovní procesy</h2>
    <JsonTable rows={data.workers} columns={["state", "last_heartbeat_at", "started_at", "stopped_at"]}/>
    <p className="muted">POUZE SIMULACE: obchodní cykly spouští plánovač podle pravidel nasazení.</p>
  </>;
}
