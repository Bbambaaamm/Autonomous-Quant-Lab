import "server-only";
const base = process.env.QUANTLAB_API_URL ?? "http://127.0.0.1:8000";
export class ApiError extends Error {constructor(public status:number,message:string){super(message)}}
export async function api<T>(path:string):Promise<T>{
  if(!path.startsWith("/operator/")) throw new Error("Endpoint není v operator allowlistu");
  const response=await fetch(`${base}${path}`,{cache:"no-store",headers:{Accept:"application/json"}});
  if(!response.ok) throw new ApiError(response.status, response.status===503?"Služba není připravena":`API chyba ${response.status}`);
  return response.json() as Promise<T>;
}
export type Overview={trading_mode:string;live_trading_enabled:boolean;api_health:string;readiness:string;paper_account_id:string|null;trading_state:string|null;reconciliation_safe:boolean|null;latest_reconciliation_status:string|null;monitoring_id:string|null;monitoring_state:string|null;monitoring_verdict:string|null;paper_equity:string|null;paper_cash:string|null;cumulative_return:string|null;current_drawdown:string|null;position_count:number;open_order_count:number;last_trading_cycle:string|null;next_scheduled_paper_cycle:string|null;latest_completed_market_session:string;latest_market_data_status:string|null;latest_market_data_at:string|null;automation_enabled:boolean;enabled_job_count:number;dead_letter_count:number;healthy_worker_count:number;stale_worker_count:number;server_time_utc:string;as_of:string|null};
export type Point={session_date:string;as_of:string;marked_equity:string;cash:string;daily_return:string|null;cumulative_return:string;drawdown:string;gross_exposure:string;net_exposure:string;turnover:string;commissions:string;slippage:string;order_count:number;fill_count:number;risk_rejection_count:number};
export type Performance={period:string;monitoring_id:string|null;points:Point[]};
