"use client";
import type { Point } from "@/lib/api";
import { dateText } from "@/lib/display";
export function LineChart({ points, field, label }: {
    points: Point[];
    field: "marked_equity" | "drawdown" | "cumulative_return";
    label: string;
}) {
    if (!points.length)
        return <div className="card muted">Pro graf {label} nejsou dostupné záznamy výkonnosti.</div>;
    const values = points.map(p => p[field] === null || String(p[field]).trim() === "" ? NaN : Number(p[field]));
    if (values.some(v => !Number.isFinite(v)))
        return <div className="card watch">Graf {label} nelze zobrazit: některé hodnoty chybí nebo nejsou platné.</div>;
    const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
    const format = (v: number) => new Intl.NumberFormat("cs-CZ", field === "marked_equity" ? { maximumFractionDigits: 2 } : { style: "percent", maximumFractionDigits: 2 }).format(v);
    const path = values.map((v, i) => `${i ? "L" : "M"} ${100 + (i / Math.max(1, values.length - 1)) * 820} ${max === min ? 115 : 190 - ((v - min) / span) * 150}`).join(" ");
    return <section className="card"><h2>{label}</h2><p><strong>{format(values.at(-1)!)}</strong> <span className="muted">· poslední hodnota · {points.length} seancí</span></p><svg className="chart" viewBox="0 0 1000 240" role="img" aria-label={`${label}, ${points.length} seancí`}><title>{`${dateText(points[0].session_date)} – ${dateText(points.at(-1)!.session_date)}`}</title>{[40, 115, 190].map(y => <line key={y} x1="100" x2="920" y1={y} y2={y} stroke="#2b3c52"/>)}<text x="0" y="45" fill="#a3b5ca" fontSize="14">{format(max)}</text><text x="0" y="195" fill="#a3b5ca" fontSize="14">{format(min)}</text><path d={path} fill="none" stroke="#61dfbc" strokeWidth="3"/>{values.length === 1 && <circle cx="100" cy="115" r="5" fill="#61dfbc"/>}<text x="100" y="230" fill="#a3b5ca" fontSize="14">{dateText(points[0].session_date)}</text><text x="920" y="230" textAnchor="end" fill="#a3b5ca" fontSize="14">{dateText(points.at(-1)!.session_date)}</text></svg><p className="paper-note">Uzavřené obchodní seance XNYS. Přesné hodnoty jsou dostupné v detailu monitoringu.</p></section>;
}
