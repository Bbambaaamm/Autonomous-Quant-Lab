"use client";
import { useState } from "react";
import { dateText, label, translated } from "@/lib/display";
const priority = ["timestamp", "session_date", "symbol", "strategy_name", "job_type", "status", "state", "verdict", "enabled", "outcome", "no_action_reason", "failure_reason", "event_type", "entity_type", "paper_account_id", "account_id", "instrument_id", "equity", "marked_equity", "quantity", "total_return", "cumulative_return", "trade_count", "coverage", "next_run_at", "finished_at", "started_at", "last_heartbeat_at", "created_at"];
const percentages = new Set(["total_return", "cumulative_return", "daily_return", "drawdown", "cagr", "coverage"]);
function valueText(key: string, value: unknown): string {
    if (value === null || value === undefined || value === "")
        return "Neuvedeno";
    if (typeof value === "boolean")
        return value ? "Ano" : "Ne";
    if (typeof value === "object")
        return JSON.stringify(value);
    const raw = String(value);
    if (percentages.has(key) && raw.trim() !== "" && Number.isFinite(Number(raw)))
        return new Intl.NumberFormat("cs-CZ", { style: "percent", maximumFractionDigits: 2 }).format(Number(raw));
    return translated(dateText(raw));
}
export function DataTable({ rows, columns }: {
    rows: Record<string, unknown>[];
    columns?: string[];
}) {
    const [query, setQuery] = useState("");
    const [page, setPage] = useState(0);
    const keys = Array.from(new Set(rows.flatMap(row => Object.keys(row))));
    const shown = columns ?? [...priority.filter(k => keys.includes(k)), ...keys.filter(k => !priority.includes(k))].slice(0, 5);
    const needle = query.toLocaleLowerCase("cs");
    const filtered = rows.filter(row => !needle || Object.entries(row).some(([key, v]) => `${label(key)} ${valueText(key, v)} ${JSON.stringify(v)}`.toLocaleLowerCase("cs").includes(needle)));
    const current = Math.min(page, Math.max(0, Math.ceil(filtered.length / 10) - 1));
    if (!rows.length)
        return <div className="card muted">Žádné dostupné záznamy. Chybějící hodnoty nejsou nahrazeny nulami.</div>;
    return <section className="data-table card"><div className="table-tools"><label>Hledat v načtených záznamech<input type="search" value={query} onChange={e => { setQuery(e.target.value); setPage(0); }} placeholder="Název, stav nebo ID…"/></label><span className="muted" aria-live="polite">{filtered.length} z {rows.length} záznamů</span></div><div className="table-scroll" tabIndex={0} role="region" aria-label="Tabulka záznamů, lze posouvat vodorovně"><table><thead><tr>{shown.map(k => <th key={k} scope="col">{label(k)}</th>)}<th scope="col">Podrobnosti</th></tr></thead><tbody>{filtered.slice(current * 10, current * 10 + 10).map((row, i) => <tr key={`${String(row.id ?? row.snapshot_id ?? "row")}-${current * 10 + i}`}>{shown.map(k => <td key={k}><span className="cell-value" title={valueText(k, row[k])}>{valueText(k, row[k])}</span></td>)}<td><details className="record-details"><summary>Celý záznam</summary><dl>{Object.entries(row).map(([k, v]) => <div key={k}><dt>{label(k)} <small className="muted">({k})</small></dt><dd>{v === null || v === undefined ? <span className="muted">Neuvedeno</span> : <pre>{typeof v === "object" ? JSON.stringify(v, null, 2) : String(v)}</pre>}</dd></div>)}</dl></details></td></tr>)}</tbody></table></div>{!filtered.length && <p className="empty-state">Žádné záznamy neodpovídají hledání.</p>}<div className="table-footer"><span>Řádky {filtered.length ? current * 10 + 1 : 0}–{Math.min((current + 1) * 10, filtered.length)} z {filtered.length}</span><div className="pagination"><button type="button" disabled={current === 0} onClick={() => setPage(current - 1)}>Předchozí</button><button type="button" disabled={(current + 1) * 10 >= filtered.length} onClick={() => setPage(current + 1)}>Další</button></div></div></section>;
}
