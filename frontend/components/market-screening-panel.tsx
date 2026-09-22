import Link from "next/link";
import { dateText } from "@/lib/display";
export type Screening = {
  run: null | { id: string; batch_id: string; created_at: string; total: number; eligible: number; content_hash: string };
  policy: { version: string; minimum_sessions: number; minimum_coverage: string; minimum_price_usd: string; minimum_feed_dollar_volume_20: string };
  matched: number;
  items: { symbol: string; eligible: boolean; momentum: string | null; trend: string | null; mean_reversion: string | null; reasons: string[] }[];
};
const reasons: Record<string, string> = {
  ACTIONS_NOT_VERIFIED: "Neověřené dividendy a splity", SHORT_HISTORY: "Krátká historie", LOW_COVERAGE: "Neúplné období", RECENT_GAPS: "Chybí některá z posledních seancí", INVALID_ADJUSTED_PRICE: "Neplatná upravená cena", LOW_PRICE: "Cena pod limitem", LOW_FEED_LIQUIDITY: "Objem daného feedu pod limitem", SCREENING_NOT_VERIFIED: "Starší import bez ověřeného screeningu", DATA_BLOCKED: "Chybí evidence corporate actions", ACCESS_BLOCKED: "Nepřístupný datový zdroj", FAILED: "Import selhal", BLOCKED: "Konflikt identity", UNSUPPORTED_VENUE: "Nepodporovaná burza",
};
const percent = (value: string | null) => value === null ? "—" : `${(Number(value) * 100).toLocaleString("cs-CZ", { maximumFractionDigits: 2 })} %`;
export function MarketScreeningPanel({ data, query, rank, page }: { data: Screening; query: string; rank: string; page: number }) {
  const href = (n: number) => `/market?${new URLSearchParams({ screen_q: query, screen_rank: rank, screen_page: String(n) })}`;
  return <section className="card" aria-labelledby="screening-title">
    <h2 id="screening-title">Výběr kandidátů podle ověřených dat</h2>
    <p>Pravidla {data.policy.version}: alespoň {data.policy.minimum_sessions} seancí, pokrytí {percent(data.policy.minimum_coverage)}, cena od {data.policy.minimum_price_usd} USD a průměrný denní objem za posledních 20 seancí od {Number(data.policy.minimum_feed_dollar_volume_20).toLocaleString("cs-CZ")} USD.</p>
    <p>Objem platí pouze pro použitý feed. Výpočty používají upravené ceny a uložený stav dividend a splitů s časem skutečného přijetí. Jde o aktuální výběr: dnes přijatá událost není důkazem, že byla známá v minulosti. Výběr není schválení strategie ani objednávka; dnešní katalog nedokládá historické složení trhu.</p>
    {!data.run ? <p>Výběr se automaticky uloží po dokončení celé cenové dávky. Dosud není k dispozici dokončený výběr.</p> : <>
      <p><strong>{data.run.eligible.toLocaleString("cs-CZ")} způsobilých z {data.run.total.toLocaleString("cs-CZ")}</strong> · uloženo {dateText(data.run.created_at)}. Vyřazené tituly zůstávají v celkovém počtu.</p>
      <details><summary>Původ výsledku</summary><p style={{overflowWrap:"anywhere"}}>Verze výběru: {data.run.id}<br />Cenová dávka: {data.run.batch_id}<br />Otisk obsahu: {data.run.content_hash}</p></details>
      <form method="get"><label>Symbol ve výběru<input name="screen_q" defaultValue={query} maxLength={100} /></label><label>Pořadí kandidátů<select name="screen_rank" defaultValue={rank}><option value="momentum">Momentum · změna za 126 seancí</option><option value="trend">Trend · průměry 20 / 100</option><option value="mean_reversion">Návrat k průměru · odchylka od 20 seancí</option></select></label><button type="submit">Zobrazit kandidáty</button></form>
      <div style={{overflowX:"auto"}}><table><thead><tr><th>Symbol</th><th>Výsledek kontroly</th><th>Momentum</th><th>Trend</th><th>Návrat k průměru</th></tr></thead><tbody>{data.items.map(row => <tr key={row.symbol}><td>{row.symbol}</td><td style={{whiteSpace:"normal",minWidth:180}}>{row.eligible ? "Splňuje pravidla výběru" : row.reasons.map(reason => reasons[reason] ?? "Neověřená evidence").join(" · ")}</td><td>{percent(row.momentum)}</td><td>{percent(row.trend)}</td><td>{percent(row.mean_reversion)}</td></tr>)}</tbody></table></div>
      <p>{data.matched.toLocaleString("cs-CZ")} výsledků · strana {page}</p><nav aria-label="Stránkování kandidátů">{page > 1 && <Link href={href(page-1)}>← Předchozí </Link>}{page*50 < data.matched && <Link href={href(page+1)}>Další →</Link>}</nav>
    </>}
  </section>;
}
