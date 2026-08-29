import { automationRetryAction } from "@/app/actions";
import { MutationForm } from "@/components/mutation-form";
import { JsonTable, Status } from "@/components/ui";
import { api } from "@/lib/api";
import { session } from "@/lib/auth";

export const dynamic = "force-dynamic";

type AutomationJob = Record<string, unknown> & { id?: string; job_type?: string };
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
  const managedJobIds = new Set(
    jobs
      .filter((job) => MANAGED_JOB_TYPES.has(String(job.job_type ?? "")))
      .map((job) => String(job.id ?? ""))
      .filter(Boolean),
  );
  const runs: AutomationRun[] = Array.isArray(data.runs) ? data.runs : [];
  const recoverable = runs.filter((run) =>
    ["FAILED", "DEAD_LETTER"].includes(String(run.status ?? ""))
      && managedJobIds.has(String(run.scheduled_job_id ?? "")),
  );

  return <>
    <h1>Operations</h1>
    <p>Automation global state: <Status value={data.enabled ? "ENABLED" : "DISABLED"} /></p>
    <h2>Jobs</h2>
    <JsonTable rows={data.jobs} />
    <h2>Runs / attempts / dead letters</h2>
    <JsonTable rows={data.runs} />
    {admin && recoverable.length > 0 && <section>
      <h2>Audited FAILED / DEAD_LETTER recovery</h2>
      <p className="muted">Retry je recovery stejného runu, nikoli economic run-now. Před retry nejprve odstraňte příčinu a uveďte auditní důvod.</p>
      {recoverable.map((run) => {
        const runId = String(run.id ?? "");
        return <MutationForm
          key={runId}
          action={automationRetryAction}
          title={`Retry ${String(run.status)} · ${runId}`}
          submit="Schedule audited retry"
          danger
          disabled={!runId}
        >
          <input type="hidden" name="run_id" value={runId} />
          <label>Audit reason<textarea name="reason" required minLength={3} /></label>
        </MutationForm>;
      })}
    </section>}
    <h2>Workers</h2>
    <JsonTable rows={data.workers} />
    <p className="muted">PAPER ONLY: dashboard neposkytuje run-now ekonomického cyklu.</p>
  </>;
}
