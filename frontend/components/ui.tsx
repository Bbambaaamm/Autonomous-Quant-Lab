import React from "react";

export const na = (value: unknown): string =>
  value === null || value === undefined || value === "" ? "N/A" : String(value);

export const pct = (value: unknown): string =>
  value === null || value === undefined ? "N/A" : `${(Number(value) * 100).toFixed(2)} %`;

export function Status({ value }: { value: unknown }) {
  const text = na(value);
  const normalized = text.toUpperCase();
  const unsafe =
    value === false ||
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
  return <span className={`badge ${unsafe ? "unsafe" : watch ? "watch" : "safe"}`}>{text}</span>;
}

export function Card({ title, value, asOf }: { title: string; value: React.ReactNode; asOf?: unknown }) {
  const showAsOf = asOf !== null && asOf !== undefined && asOf !== "";
  return <section className="card"><div className="muted">{title}</div><h2>{value}</h2>{showAsOf ? <small className="muted">Stav k: {String(asOf)}</small> : null}</section>;
}

export function JsonTable({ rows }: { rows: Record<string, unknown>[] }) {
  if (!rows.length) return <div className="card muted">Žádné dostupné záznamy. Hodnoty nejsou nahrazeny nulami.</div>;
  const keys = Object.keys(rows[0]).slice(0, 8);
  return <div className="card" style={{ overflowX: "auto" }}><table><thead><tr>{keys.map((key) => <th key={key}>{key}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={String(row.id ?? row.snapshot_id ?? index)}>{keys.map((key) => <td key={key}>{typeof row[key] === "object" ? <pre>{JSON.stringify(row[key], null, 2)}</pre> : na(row[key])}</td>)}</tr>)}</tbody></table></div>;
}

export function Empty({ children = "Evidence není dostupná." }: { children?: React.ReactNode }) {
  return <div className="card muted">{children}</div>;
}
