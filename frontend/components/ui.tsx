import React from "react";
import { DataTable } from "./data-table";
import { dateText, translated, label } from "@/lib/display";
export const na = (value: unknown): string => value === null || value === undefined || value === "" ? "Neuvedeno" : dateText(String(value));
export const pct = (value: unknown): string => value === null || value === undefined || value === "" || !Number.isFinite(Number(value)) ? "Neuvedeno" : new Intl.NumberFormat("cs-CZ", {style:"percent",minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(value));
export function Status({ value }: {
    value: unknown;
}) {
    const text = na(value);
    const normalized = text.toUpperCase();
    const unsafe = value === false ||
        [
            "FALSE",
            "HALTED",
            "SUSPENDED",
            "FAILED",
            "DEAD_LETTER",
            "STALE",
            "UNAVAILABLE",
            "UNSUPPORTED",
            "NOT_READY",
            "CAPABILITY_NOT_REPORTED",
            "DISABLED",
            "MISSING_SESSION_DATA",
            "UNSAFE",
        ].includes(normalized);
    const watch = ["WATCH", "REVIEW_REQUIRED", "INSUFFICIENT_DATA", "STARTED"].includes(normalized);
    return <span className={`badge ${unsafe ? "unsafe" : watch ? "watch" : ["NORMAL", "SAFE", "ACTIVE", "HEALTHY", "READY", "SUPPORTED", "ENABLED", "ON", "APPROVED", "SUCCEEDED", "COMPLETE", "ELIGIBLE", "0"].includes(normalized) ? "safe" : "neutral"}`} title={text}>{translated(text)}</span>;
}
export function Card({ title, value, asOf, asOfLabel = "Stav k" }: {
    title: string;
    value: React.ReactNode;
    asOf?: unknown;
    asOfLabel?: string;
}) {
    const showAsOf = asOf !== null && asOf !== undefined && asOf !== "";
    return <section className="card"><div className="muted">{title}</div><h2>{value}</h2>{showAsOf ? <small className="muted">{asOfLabel}: {dateText(String(asOf))}</small> : null}</section>;
}
export function JsonTable({ rows, columns }: {
    rows: Record<string, unknown>[];
    columns?: string[];
}) {
    return <DataTable rows={rows} columns={columns}/>;
}
export function Empty({ children = "Evidence není dostupná." }: {
    children?: React.ReactNode;
}) {
    return <div className="card muted">{children}</div>;
}
export function RecordSummary({ value }: {
    value: unknown;
}) {
    let parsed: unknown = value;
    if (typeof value === "string") {
        try {
            parsed = JSON.parse(value);
        }
        catch { /* Keep unstructured source text. */ }
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
        return <pre>{na(value)}</pre>;
    return <><dl className="summary-grid">{Object.entries(parsed).map(([key, item]) => <div key={key}><dt>{label(key)}</dt><dd>{item === null || item === undefined ? "Neuvedeno" : typeof item === "object" ? <details><summary>Zobrazit hodnoty</summary><pre>{JSON.stringify(item, null, 2)}</pre></details> : typeof item === "boolean" ? (item ? "Ano" : "Ne") : translated(dateText(String(item)))}</dd></div>)}</dl><details><summary>Technický záznam</summary><pre>{JSON.stringify(parsed, null, 2)}</pre></details></>;
}
