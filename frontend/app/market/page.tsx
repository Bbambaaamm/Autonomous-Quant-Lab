import { MarketScreeningPanel, type Screening } from "@/components/market-screening-panel";
import { MarketPipelinePanel, type Pipeline } from "@/components/market-pipeline-panel";
import Link from "next/link";
import { api } from "@/lib/api";
import { session } from "@/lib/auth";
import { dateText } from "@/lib/display";
import { MutationForm } from "@/components/mutation-form";
import { marketCatalogAction, marketCatalogScheduleAction } from "@/app/actions";
export const dynamic = "force-dynamic";
type Coverage = {
  status: string; total: number; offset: number; limit: number;
  snapshot: null | { received_at: string; listing_count: number; test_count: number };
  items: { symbol: string; name: string; exchange: string; security_type: string }[];
  exchanges: { exchange: string; count: number }[]; limitations: string[];
};
export default async function Market({ searchParams }: { searchParams: Promise<{ q?: string; page?: string; price_q?: string; price_rank?: string; price_page?: string; price_state?: string; screen_q?: string; screen_rank?: string; screen_page?: string }> }) {
  const params = await searchParams;
  const q = (typeof params.q === "string" ? params.q : "").slice(0, 100);
  const requested = Number(params.page ?? 1);
  const page = Number.isSafeInteger(requested) && requested > 0 && requested < 1000000 ? requested : 1;
  const priceQuery = (typeof params.price_q === "string" ? params.price_q : "").slice(0,100);
  const priceRank = ["symbol", "momentum", "trend", "mean_reversion"].includes(params.price_rank ?? "") ? params.price_rank! : "symbol";
  const priceState = ["PENDING", "RUNNING", "RETRY", "DONE", "FAILED", "BLOCKED", "DATA_BLOCKED", "NO_PRICE_DATA", "ACCESS_BLOCKED", "UNSUPPORTED_VENUE"].includes(params.price_state ?? "") ? params.price_state! : "";
  const priceRequested = Number(params.price_page ?? 1);
  const pricePage = Number.isSafeInteger(priceRequested) && priceRequested > 0 && priceRequested < 1000000 ? priceRequested : 1;
  const screenQuery = (typeof params.screen_q === "string" ? params.screen_q : "").slice(0,100);
  const screenRank = ["momentum", "trend", "mean_reversion"].includes(params.screen_rank ?? "") ? params.screen_rank! : "momentum";
  const screenRequested = Number(params.screen_page ?? 1);
  const screenPage = Number.isSafeInteger(screenRequested) && screenRequested > 0 && screenRequested < 1000000 ? screenRequested : 1;
  const [data, user, pipeline, screening] = await Promise.all([
    api<Coverage>(`/operator/market-coverage?q=${encodeURIComponent(q)}&offset=${(page - 1) * 50}&limit=50`), session(), api<Pipeline>(`/operator/market-pipeline?${new URLSearchParams({q:priceQuery,rank:priceRank,state:priceState,offset:String((pricePage - 1)*50),limit:"50"})}`),
    api<Screening>(`/operator/market-screening?${new URLSearchParams({q:screenQuery,rank:screenRank,offset:String((screenPage-1)*50),limit:"50"})}`),
  ]);
  const pageLink = (n: number) => `/market?${new URLSearchParams({ q, page: String(n) })}`;
  return <>
    <h1>Pokrytí trhu</h1>
    <MarketScreeningPanel data={screening} query={screenQuery} rank={screenRank} page={screenPage} />
    <MarketPipelinePanel data={pipeline} admin={user?.role === "ADMIN"} />
    <p className="muted">Cílem je globální sledování. První dostupnou vrstvou je referenční katalog amerických burzovních cenných papírů.</p>
    <div className="grid">
      <section className="card"><h2>Instrumenty v katalogu</h2><strong>{data.snapshot?.listing_count.toLocaleString("cs-CZ") ?? "Dosud nenačteno"}</strong><p>Počet záznamů ve zdroji, nikoli počet instrumentů s cenovými daty.</p></section>
      <section className="card"><h2>Poslední přijetí katalogu</h2><strong>{data.snapshot ? dateText(data.snapshot.received_at) : "Dosud neproběhlo"}</strong><p>{data.status === "STALE" ? "Přijetí katalogu nebo datum zdrojového souboru je starší než tři dny." : data.status === "RECEIVED" ? "Oba zdrojové soubory byly přijaty a zkontrolovány." : "Správce může načíst katalog níže."}</p></section>
      <section className="card"><h2>Pokrytí cen a historie</h2><strong>{pipeline.coverage_summary ? `${pipeline.coverage_summary.downloaded.toLocaleString("cs-CZ")} titulů s cenami` : "Dosud neověřeno"}</strong><p>Počet se vztahuje k poslední cenové dávce. Neprokazuje úplnost referenčního katalogu ani pokrytí globálních trhů.</p></section>
    </div>
    <section className="card"><h2>Rozsah a zbývající kroky</h2><ul>{data.limitations.map(text => <li key={text}>{text}</li>)}</ul><Link href="/data">Otevřít aktuálně registrované instrumenty a tržní data →</Link></section>
    {data.exchanges.length > 0 && <section className="card"><h2>Burzy v přijatém katalogu</h2><div className="grid">{data.exchanges.map(row => <p key={row.exchange}><strong>{row.exchange}</strong><br />{row.count.toLocaleString("cs-CZ")} záznamů</p>)}</div></section>}
    <section className="card"><h2>Vyhledávání v katalogu</h2>
      <form method="get"><label>Symbol nebo název<input name="q" defaultValue={q} maxLength={100} placeholder="Například IBM" /></label><button type="submit">Vyhledat</button></form>
      <p>{data.total.toLocaleString("cs-CZ")} výsledků · strana {page}</p>
      <div style={{ overflowX: "auto" }}><table><thead><tr><th>Symbol</th><th>Název</th><th>Burza</th><th>Typ podle zdroje</th></tr></thead><tbody>{data.items.map(row => <tr key={`${row.exchange}:${row.symbol}`}><td>{row.symbol}</td><td style={{ whiteSpace: "normal", minWidth: 200 }}>{row.name}</td><td>{row.exchange}</td><td>{row.security_type === "ETF" ? "ETF" : "Ostatní cenný papír"}</td></tr>)}</tbody></table></div>
      {!data.items.length && <p>Pro tento výběr nejsou záznamy.</p>}
      <nav aria-label="Stránkování katalogu">{page > 1 && <Link href={pageLink(page - 1)}>← Předchozí strana </Link>}{data.offset + data.limit < data.total && <Link href={pageLink(page + 1)}> Další strana →</Link>}</nav>
    </section>
    {user?.role === "ADMIN" && <MutationForm action={marketCatalogScheduleAction} title="Pravidelná aktualizace" submit="Založit denní aktualizaci"><p>První běh při nejbližším zpracování workerem, další denně v 01:00 UTC. Stav a vypnutí jsou na stránce Provoz.</p><label>Důvod<textarea name="reason" required minLength={3} maxLength={1000} defaultValue="Denní sledování referenčního katalogu" /></label></MutationForm>}
    {user?.role === "ADMIN" && <MutationForm action={marketCatalogAction} title="Aktualizace referenčního katalogu" submit="Načíst aktuální katalog"><p>Načte dva veřejné adresáře Nasdaq Trader. Při chybě zůstane poslední úspěšná verze dostupná. Bez volání AI.</p><label>Důvod aktualizace<textarea name="reason" required minLength={3} maxLength={1000} defaultValue="Aktualizace přehledu pokrytí trhu" /></label></MutationForm>}
  </>;
}
