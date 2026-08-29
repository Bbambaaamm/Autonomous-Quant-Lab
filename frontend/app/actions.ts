"use server";

import { revalidatePath } from "next/cache";
import { assertSameOrigin, backendToken, requireSession } from "../lib/auth";

const base = process.env.QUANTLAB_API_URL ?? "http://127.0.0.1:8000";
export type ActionState = { error?: string; success?: string };

function value(form: FormData, key: string): string {
  return String(form.get(key) ?? "").trim();
}

function utcValue(form: FormData, key: string): string {
  const raw = value(form, key);
  return raw && !/[zZ]|[+-]\d\d:\d\d$/.test(raw) ? `${raw}:00Z` : raw;
}

function segment(value: string): string {
  return encodeURIComponent(value);
}

async function mutate(path: string, body: Record<string, unknown>): Promise<void> {
  if (!path.startsWith("/operator/")) throw new Error("Mutation není v operator allowlistu");
  await assertSameOrigin();
  const current = await requireSession();
  const response = await fetch(`${base}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json", Authorization: `Bearer ${backendToken(current.role)}` },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: unknown } | null;
    const detail = typeof payload?.detail === "string" ? payload.detail : payload?.detail ? JSON.stringify(payload.detail) : `HTTP ${response.status}`;
    throw new Error(detail);
  }
  revalidatePath("/");
  revalidatePath("/data");
  revalidatePath("/research");
  revalidatePath("/strategies");
  revalidatePath("/operations");
  revalidatePath("/paper");
  revalidatePath("/risk");
}

async function result(work: () => Promise<void>, success: string): Promise<ActionState> {
  try { await work(); return { success }; }
  catch (error) { return { error: error instanceof Error ? error.message : "Akce byla serverem odmítnuta" }; }
}

export async function eligibilityAction(_: ActionState, form: FormData) { const id=value(form,"id"); return result(()=>mutate(`/operator/research/experiments/${segment(id)}/eligibility`,{reason:value(form,"reason")}),"Eligibility byla autoritativně vyhodnocena."); }
export async function promotionAction(_: ActionState, form: FormData) { const id=value(form,"id"); return result(()=>mutate(`/operator/research/experiments/${segment(id)}/promote`,{reason:value(form,"reason")}),"Experiment byl promován na PAPER_CANDIDATE."); }
export async function deploymentAction(_: ActionState, form: FormData) { return result(()=>mutate("/operator/deployments",{experiment_id:value(form,"experiment_id"),paper_account_id:"paper-main",reason:value(form,"reason")}),"Paper deployment byl vytvořen; načtený stav je autoritativní."); }
export async function approvalAction(_: ActionState, form: FormData) { const id=value(form,"deployment_id"); return result(()=>mutate(`/operator/deployments/${segment(id)}/approve`,{reason:value(form,"reason")}),"Paper deployment byl schválen."); }
export async function autonomousScheduleAction(_: ActionState, form: FormData) { const id=value(form,"deployment_id"), action=value(form,"action"); return result(()=>mutate(`/operator/deployments/${segment(id)}/autonomous/${action}`,{reason:value(form,"reason")}),`Autonomous scheduling byl ${action==="enable"?"zapnut":"vypnut"}.`); }
export async function monitoringEnrollmentAction(_: ActionState, form: FormData) { return result(()=>mutate("/operator/monitoring/enrollments",{deployment_id:value(form,"deployment_id"),policy_id:value(form,"policy_id"),reason:value(form,"reason")}),"Deployment byl zařazen do monitoringu a monitoring schedule byl ověřen."); }
export async function monitoringPolicyAction(_: ActionState, form: FormData) { const raw=value(form,"config"); let config:unknown; try{config=raw?JSON.parse(raw):undefined;}catch{return {error:"Monitoring policy config musí být platný JSON."};} const body:Record<string,unknown>={name:value(form,"name"),reason:value(form,"reason")}; if(config!==undefined)body.config=config; return result(()=>mutate("/operator/monitoring/policies",body),"Monitoring policy byla vytvořena."); }
export async function monitoringAction(_: ActionState, form: FormData) { const id=value(form,"id"), action=value(form,"action"); return result(()=>mutate(`/operator/monitoring/${segment(id)}/${action}`,{reason:value(form,"reason")}),`Monitoring transition ${action.toUpperCase()} byla potvrzena serverem.`); }
export async function riskAction(_: ActionState, form: FormData) { const action=value(form,"action"); return result(()=>mutate(`/operator/risk/${action}`,{confirmation:value(form,"confirmation"),reason:value(form,"reason")}),"Risk stav byl načten ze serveru."); }
export async function reconciliationAction(_: ActionState, form: FormData) { return result(()=>mutate("/operator/reconciliation/run",{reason:value(form,"reason")}),"Reconciliation byla znovu provedena autoritativním backendem."); }
export async function automationRetryAction(_: ActionState, form: FormData) { const runId=value(form,"run_id"); return result(()=>mutate(`/operator/automation/runs/${segment(runId)}/retry`,{reason:value(form,"reason")}),"Automation run byl auditovaně naplánován k retry."); }

export async function instrumentAction(_: ActionState, form: FormData) { return result(()=>mutate("/operator/instruments",{instrument_id:value(form,"instrument_id"),symbol:value(form,"symbol"),active_from:value(form,"active_from"),reason:value(form,"reason")}),"Instrument byl registrován."); }
export async function universeAction(_: ActionState, form: FormData) { return result(()=>mutate("/operator/universes",{universe_id:value(form,"universe_id"),name:value(form,"name"),reason:value(form,"reason")}),"Universe byl vytvořen."); }
export async function membershipAction(_: ActionState, form: FormData) { const id=value(form,"universe_id"); return result(()=>mutate(`/operator/universes/${segment(id)}/memberships`,{instrument_id:value(form,"instrument_id"),valid_from:utcValue(form,"valid_from"),known_at:utcValue(form,"known_at"),reason:value(form,"reason")}),"Point-in-time membership byla přidána."); }
export async function ingestionAction(_: ActionState, form: FormData) { return result(()=>mutate("/operator/market-data/ingestions",{provider:"stooq",instrument_id:value(form,"instrument_id"),start:value(form,"start"),end:value(form,"end"),reason:value(form,"reason")}),"Ingest dokončen; tabulka byla načtena z backendu."); }
export async function snapshotAction(_: ActionState, form: FormData) { return result(()=>mutate("/operator/datasets",{provider:"stooq",universe_id:value(form,"universe_id"),start:value(form,"start"),end:value(form,"end"),as_of:utcValue(form,"as_of"),minimum_coverage:value(form,"minimum_coverage"),reason:value(form,"reason")}),"Immutable dataset snapshot byl vytvořen."); }
export async function experimentAction(_: ActionState, form: FormData) { let parameters:unknown; try{parameters=JSON.parse(value(form,"parameter_configs")); if(!Array.isArray(parameters))throw new Error();}catch{return {error:"Parameter configs musí být JSON pole."};} return result(()=>mutate("/operator/research/experiments",{snapshot_id:value(form,"snapshot_id"),strategy_name:value(form,"strategy_name"),strategy_version:value(form,"strategy_version"),parameter_configs:parameters,code_sha:value(form,"code_sha"),seed:Number(value(form,"seed")||42),reason:value(form,"reason")}),"Research experiment byl dokončen."); }
