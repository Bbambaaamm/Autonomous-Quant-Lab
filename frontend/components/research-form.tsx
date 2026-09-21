"use client";
import { useState } from "react";
import { MutationForm } from "@/components/mutation-form";
import { experimentAction } from "@/app/actions";
import { dateText, translated } from "@/lib/display";
export type ResearchOptions = {
    code_sha: string | null;
    strategies: {
        name: string;
        version: string;
        defaults: Record<string, string | number>;
    }[];
};
const names: Record<string, string> = { lookback: "Délka historie (seance)", fast: "Krátký průměr (seance)", slow: "Dlouhý průměr (seance)", threshold: "Poměr ceny k průměru", top_n: "Počet vybraných aktiv", rebalance_frequency: "Jak často přepočítat portfolio" };
export function ResearchForm({ options, snapshots }: {
    options: ResearchOptions;
    snapshots: Record<string, unknown>[];
}) {
    const [selection, setSelection] = useState(options.strategies[0]?.name ?? "");
    const [advanced, setAdvanced] = useState(false);
    const strategy = options.strategies.find(s => s.name === selection);
    const valid = snapshots.filter(s => s.status === "VALID");
    return <MutationForm action={experimentAction} title="Nový experiment" submit="Spustit historickou simulaci" disabled={!strategy || !valid.length || (!advanced && !options.code_sha)}>
 <p>Vyberte data a strategii, nastavte parametry a spusťte historický test. Experiment nezapíná obchodování. Data se chronologicky rozdělí na trénink (60 %), ověření (20 %) a závěrečný test (20 %).</p>
 <label>Uložená data<select name="snapshot_id" required defaultValue=""><option value="" disabled>Vyberte datovou sadu</option>{valid.map(s => <option key={String(s.snapshot_id)} value={String(s.snapshot_id)}>{String(s.universe_name ?? s.universe_id)} · do {dateText(String(s.end_at))} · pokrytí {Number(s.coverage) * 100} % · {String(s.snapshot_id).slice(0, 8)}</option>)}</select></label>
 {!valid.length && <p role="alert">Nejsou dostupné platné uložené verze dat. Nejprve je připravte v části Tržní data.</p>}
 <label>Strategie<select value={selection} onChange={e => setSelection(e.target.value)}>{options.strategies.map(s => <option key={s.name} value={s.name}>{translated(s.name)} · verze {s.version}</option>)}</select></label>
 <input type="hidden" name="strategy_name" value={strategy?.name ?? ""}/><input type="hidden" name="strategy_version" value={strategy?.version ?? ""}/>
 <label><input type="checkbox" checked={advanced} onChange={e => setAdvanced(e.target.checked)}/> Pokročilé nastavení: více variant parametrů a ruční verze kódu</label>
 {advanced ? <><label>Varianty parametrů (JSON)<textarea name="parameter_configs" key={selection} required defaultValue={JSON.stringify([strategy?.defaults ?? {}], null, 2)}/></label><label>Verze kódu experimentu (Git SHA)<input name="code_sha" required pattern="[0-9a-f]{40}" defaultValue={options.code_sha ?? ""}/></label></> : <><input type="hidden" name="guided" value="true"/><div className="form-fields" key={selection}>{Object.entries(strategy?.defaults ?? {}).map(([key, value]) => <label key={key}>{names[key] ?? key}{key === "rebalance_frequency" ? <select name={`param_${key}`} defaultValue={String(value)}>{["DAILY", "WEEKLY", "MONTHLY"].map(v => <option key={v} value={v}>{translated(v)}</option>)}</select> : <input name={`param_${key}`} type="number" required step={key === "threshold" ? "0.01" : "1"} min={key === "threshold" ? "0.5" : "1"} max={key === "threshold" ? "0.99" : undefined} defaultValue={String(value)}/>}</label>)}</div><p className="muted">Verzi strategie i nasazeného kódu doplní server. Každé pole představuje jednu variantu testu.</p>{!options.code_sha && <p role="alert">Backend nemá zaznamenanou verzi nasazeného kódu. Správce musí doplnit identifikaci sestavení; bez ní nelze nový experiment spolehlivě evidovat.</p>}</>}
 <details><summary>Opakovatelnost experimentu</summary><label>Číselný klíč pro opakování (seed)<input name="seed" type="number" defaultValue="42" required/></label><p>Stejná data, kód, parametry a klíč umožňují zopakovat výsledek.</p></details>
 <label>Důvod experimentu<textarea name="reason" required minLength={3} placeholder="Například: ověřit týdenní přepočet portfolia na vybrané historii"/></label>
 </MutationForm>;
}
