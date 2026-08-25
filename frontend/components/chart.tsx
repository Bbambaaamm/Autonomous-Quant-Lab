"use client";
import type {Point} from "@/lib/api";
export function LineChart({points,field,label}:{points:Point[];field:"marked_equity"|"drawdown"|"cumulative_return";label:string}){
 if(!points.length)return <div className="card muted">Pro graf {label} nejsou dostupné immutable snapshoty.</div>;
 const vals=points.map(p=>Number(p[field]));const min=Math.min(...vals),max=Math.max(...vals);const span=max-min||1;
 const path=vals.map((v,i)=>`${i?"L":"M"} ${(i/(Math.max(1,vals.length-1)))*1000} ${190-((v-min)/span)*170}`).join(" ");
 return <section className="card"><h2>{label}</h2><svg className="chart" viewBox="0 0 1000 210" role="img" aria-label={`${label}, ${points.length} sessions`}><path d={path} fill="none" stroke="#45d4ad" strokeWidth="4"/><title>{`${points[0].session_date} – ${points.at(-1)?.session_date}`}</title></svg><small className="muted">XNYS session řada; hodnoty pocházejí z Phase 7 snapshotů.</small></section>
}
