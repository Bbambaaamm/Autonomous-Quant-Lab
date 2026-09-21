import Link from "next/link";
import { na, Status } from "@/components/ui";
import { translated } from "@/lib/display";
export function TradingDiagnostics({ overview }: {
    overview: Record<string, any>;
}) {
    const run = overview.latest_paper_run;
    return <section className="card diagnostic-note"><h2>Co se děje s obchodováním</h2><p>Účet: <strong>{overview.paper_account_id ?? "Neuvedeno"}</strong> · stav ověřen {na(overview.server_time_utc)}</p>
 {overview.trading_state === "HALTED" && <p className="unsafe">Obchodování je zastavené řízením rizik.</p>}
 {overview.reconciliation_safe === false && <p className="unsafe">Kontrola shody účtu hlásí nesoulad.</p>}
 {overview.automation_enabled === false && <p>Automatický provoz je vypnutý.</p>}
 {run ? <><p>Poslední obchodní úloha: {na(run.scheduled_for)} · <Status value={run.status}/></p><p>Výsledek: <strong>{run.outcome ? translated(run.outcome) : "Zatím nezaznamenán"}</strong>.</p>{run.no_action_reason && <p>{translated(run.no_action_reason)}.</p>}{run.outcome === "NO_ACTION" && run.no_action_reason === "NO_TRADE_DELTA" && <p>Tento běh nevytvořil změnu portfolia. Samotný výsledek neurčuje, zda důvodem byl signál strategie, načasování nebo jiná podmínka.</p>}{run.failure_reason && <p className="unsafe">Chyba: {String(run.failure_reason)}</p>}<details><summary>Identifikace běhu</summary><p><code>{run.id}</code></p><p>Nasazení: <code>{run.deployment_id}</code></p></details></> : <p>Pro tento účet není doložen obchodní běh. Důvod neobchodování zatím nelze určit.</p>}
 <p>Další naplánovaný běh: {na(overview.next_scheduled_paper_cycle)}.</p>
 <p>Vyhodnocení výkonnosti: <Status value={overview.monitoring_verdict ?? "NOT EVALUATED"}/>. Výsledek monitoringu je oddělený od výsledku obchodní úlohy.</p>
 <Link href="/operations">Podrobnosti běhů →</Link></section>;
}
