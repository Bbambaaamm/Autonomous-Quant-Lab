/** Presentation only: original values remain available in record details. */
export const labels: Record<string, string> = {
    minimum_sessions: "Minimum seancí", bootstrap_block_size: "Délka bloku pro přepočet", bootstrap_samples: "Počet přepočtů", hard_suspend_on_halted: "Pozastavit při zastavení účtu", hard_suspend_on_reconciliation_failure: "Pozastavit při nesouladu účtu", maximum_paper_drawdown: "Maximální pokles portfolia", review_return_percentile: "Percentil pro kontrolu", watch_return_percentile: "Percentil pro sledování", annualized_return: "Roční výnos", max_drawdown: "Maximální pokles", sharpe: "Sharpeho poměr", volatility: "Kolísání výnosů", time_weighted_exposure: "Průměrná expozice",
    id: "ID", name: "Název", symbol: "Symbol", status: "Stav", state: "Stav", verdict: "Vyhodnocení", enabled: "Zapnuto",
    created_at: "Vytvořeno", updated_at: "Aktualizováno", approved_at: "Schváleno", started_at: "Zahájeno", finished_at: "Dokončeno", stopped_at: "Ukončeno", timestamp: "Čas události", as_of: "Platnost dat",
    strategy_name: "Strategie", strategy_version: "Verze", strategy_identity: "Identita strategie", strategy_id: "ID strategie", account_id: "Účet", paper_account_id: "Simulovaný účet", base_currency: "Měna", currency: "Měna",
    equity: "Hodnota účtu", marked_equity: "Hodnota portfolia", cash: "Hotovost", starting_cash: "Počáteční kapitál", high_water_mark: "Historické maximum", trading_state: "Obchodování", session_start_equity: "Hodnota na začátku seance",
    total_return: "Celkový výnos", cumulative_return: "Kumulativní výnos", daily_return: "Denní výnos", drawdown: "Pokles od maxima", cagr: "Roční výnos", expectancy: "Očekávaný výsledek", trade_count: "Počet obchodů", turnover: "Obrat", total_costs: "Celkové náklady", quantity: "Množství", price: "Cena", side: "Směr", order_type: "Typ příkazu",
    dataset_id: "Datová sada", snapshot_id: "Verze dat", instrument_id: "Instrument", universe_id: "Investiční soubor", deployment_id: "Nasazení", monitoring_id: "Monitoring", experiment_id: "Experiment", policy_id: "Pravidla", evidence_id: "ID záznamu", content_hash: "Kontrolní otisk", runtime_manifest_hash: "Otisk konfigurace", runtime_manifest_version: "Verze konfigurace",
    parameters_json: "Parametry", parameter_space: "Prostor parametrů", selected_parameters: "Vybrané parametry", oos_metrics: "Výsledky mimo trénovací data", code_sha: "Verze kódu", seed: "Seed", strategy: "Strategie", current_decision: "Rozhodnutí",
    job_type: "Typ úlohy", schedule_type: "Plánování", interval_seconds: "Interval (s)", next_run_at: "Další spuštění", max_attempts: "Maximum pokusů", attempt_count: "Pokus", scheduled_for: "Naplánováno na", scheduled_job_id: "ID úlohy", next_attempt_at: "Další pokus", outcome: "Výsledek", no_action_reason: "Důvod bez obchodu", failure_reason: "Příčina chyby", misfire_policy: "Zmeškané spuštění", trading_cycle_id: "Obchodní cyklus", lease_owner: "Vlastník zámku",
    worker_id: "Proces", last_heartbeat_at: "Poslední odezva", scheduler_heartbeat_at: "Odezva plánovače", active_run_id: "Aktuální běh", event_type: "Typ události", entity_type: "Typ objektu", entity_id: "ID objektu", correlation_id: "Související operace", payload: "Podrobnosti", payload_json: "Podrobnosti", reason: "Důvod", actor: "Provedl",
    provider: "Zdroj dat", calendar: "Kalendář", exchange: "Burza", asset_type: "Typ aktiva", active_from: "Aktivní od", active_to: "Aktivní do", valid_from: "Platnost od", valid_to: "Platnost do", known_at: "Známo od", kind: "Typ", requested_start: "Požadováno od", requested_end: "Požadováno do", checked_at: "Ověřeno", action_count: "Počet událostí", row_count: "Počet řádků", scope_hash: "Otisk rozsahu", end_at: "Konec období", coverage: "Pokrytí", session_date: "Obchodní den", index: "Pořadí", evaluated_at: "Vyhodnoceno", paper_metrics_json: "Metriky simulace", algorithm_version: "Verze algoritmu", performance_snapshot_id: "Záznam výkonnosti", evaluation_id: "ID vyhodnocení", lookback: "Historie (seance)", rebalance_frequency: "Rebalancování", threshold: "Prahová hodnota",
};
export const states: Record<string, string> = {
    COMPLETED: "Dokončeno", STOPPED: "Ukončený proces", CLAIMED: "Převzato ke zpracování", RETRY_SCHEDULED: "Naplánováno opakování", CANCELLED: "Zrušeno", cross_sectional_momentum: "Relativní síla aktiv",
    deployment: "Nasazení", scheduled_job: "Plánovaná úloha", job_run: "Běh úlohy", reconciliation: "Kontrola shody", account: "Účet",
    PHASE6_DEPLOYMENT_APPROVED: "Schválení nasazení", PHASE6_DEPLOYMENT_CREATED: "Vytvoření nasazení",
    CONTROL_AUTONOMOUS_SCHEDULE_DISABLED: "Vypnutí automatického plánování", CONTROL_AUTONOMOUS_SCHEDULE_ENABLED: "Zapnutí automatického plánování",
    CONTROL_RECONCILIATION_RUN: "Vyžádání kontroly shody", RECONCILIATION_SUCCEEDED: "Úspěšná kontrola shody", RECONCILIATION_STARTED: "Zahájení kontroly shody", CONTROL_AUTOMATION_RUN_RETRY: "Opakování běhu",
    TRUE: "Ano", FALSE: "Ne", persistent: "Uložená data", OPERATOR_ENROLLMENT: "Zapnuto operátorem",
    NORMAL: "V provozu", SAFE: "V pořádku", UNSAFE: "Nesoulad", HALTED: "Obchodování zastaveno", SUSPENDED: "Pozastaveno ochranou", ACTIVE: "Aktivní", PAUSED: "Pozastaveno", RETIRED: "Ukončeno", HEALTHY: "V pořádku", STALE: "Neaktuální odezva", UNAVAILABLE: "Nedostupné", READY: "Připraveno", NOT_READY: "Nepřipraveno", MISSING_SESSION_DATA: "Chybí data seance", SUPPORTED: "Podporováno", UNSUPPORTED: "Nepodporováno", CAPABILITY_NOT_REPORTED: "Podpora neověřena", ENABLED: "Zapnuto", DISABLED: "Vypnuto", ON: "Zapnuto", OFF: "Vypnuto", INSUFFICIENT_DATA: "Málo dat k vyhodnocení", WATCH: "Ke sledování", REVIEW_REQUIRED: "Vyžaduje kontrolu", NOT_ENROLLED: "Monitoring nenastaven", PENDING_REVIEW: "Čeká na schválení", APPROVED: "Schváleno", SUCCEEDED: "Dokončeno", COMPLETE: "Dokončeno", FAILED: "Chyba", DEAD_LETTER: "Vyčerpány pokusy", STARTED: "Spuštěno", RUNNING: "Probíhá", PENDING: "Čeká", ELIGIBLE: "Způsobilé", INELIGIBLE: "Nezpůsobilé", PAPER_CANDIDATE: "Kandidát pro simulaci", "NOT EVALUATED": "Nevyhodnoceno", "NOT PROMOTED": "Dosud nezařazeno", NO_ACTION: "Bez změny", NO_TRADE_DELTA: "Není potřeba změnit portfolio", DAILY: "Denně", WEEKLY: "Týdně", MONTHLY: "Měsíčně", INTERVAL: "V intervalu", RUN_ONCE_IF_MISSED: "Dohnat jedním spuštěním", SKIP_IF_TOO_OLD: "Staré spuštění přeskočit", PREPARE_PAPER_SESSION: "Příprava obchodní seance", MONITOR_PAPER_DEPLOYMENT: "Kontrola výkonnosti", RUN_PAPER_DEPLOYMENT: "Simulovaný obchodní běh", BUY: "Nákup", SELL: "Prodej", EQUITY: "Akcie", POINT_IN_TIME_MEMBERSHIP: "Členství s historickou platností", multi_asset_mean_reversion: "Návrat k průměru", multi_asset_trend: "Sledování trendu",
};
export const label = (key: string) => labels[key] ?? key.replaceAll("_", " ");
export const translated = (value: unknown) => states[String(value)] ?? String(value);
export function dateText(value: string): string {
    if (/^\d{4}-\d{2}-\d{2}$/.test(value))
        return value.split("-").reverse().join(". ");
    if (!/^\d{4}-\d{2}-\d{2}T/.test(value) || !/(Z|[+-]\d{2}:\d{2})$/.test(value))
        return value;
    const date = new Date(value);
    if (!Number.isFinite(date.getTime()))
        return value;
    return new Intl.DateTimeFormat("cs-CZ", { dateStyle: "short", timeStyle: "short", timeZone: "UTC" }).format(date) + " UTC";
}
/** Keep exact nonzero decimal strings; normalize only representations of zero. */
export function decimalText(value: unknown): string {
    if (value === null || value === undefined || String(value).trim() === "")
        return "Neuvedeno";
    const raw = String(value);
    return /^[+-]?0+(?:\.0+)?(?:e[+-]?\d+)?$/i.test(raw) ? "0" : raw;
}
