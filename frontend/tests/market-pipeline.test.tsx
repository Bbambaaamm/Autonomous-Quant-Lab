import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
vi.mock("../components/mutation-form", () => ({ MutationForm: ({ title }: {title:string}) => <section>{title}</section> }));
vi.mock("../app/actions", () => ({ marketBatchAction: vi.fn(), marketIdentitiesAction: vi.fn(), marketProbeAction: vi.fn() }));
import { MarketPipelinePanel, type Pipeline } from "../components/market-pipeline-panel";
afterEach(cleanup);
const data: Pipeline = {batch:{id:"test",start:"2026-01-02",end:"2026-09-18",provider:"alpaca:iex"},counts:{DATA_BLOCKED:1},total:120,matched:120,query:"IBM &",rank:"trend",limit:50,offset:50,latest_session:"2026-09-18",identity_directory:{snapshot_id:"id",received_at:"2026-09-21T12:00:00Z"},configured_provider:"alpaca",feed:"iex",credentials_configured:true,items:[{symbol:"IBM",state:"DATA_BLOCKED",bars:0,coverage:null,momentum:null,trend:null,mean_reversion:null,detail:"Chybí historická evidence přijetí dividend nebo splitů"}]};
it("shows missing evidence, preserves filters and distinguishes unverified data", () => {
 render(<MarketPipelinePanel data={data} admin={false}/>);
 expect(screen.getByText("Chybí historická evidence přijetí dividend nebo splitů")).toBeInTheDocument();
 expect(screen.getAllByText("Neověřeno")).toHaveLength(4);
 expect(screen.getByRole("link",{name:/Další/})).toHaveAttribute("href","/market?price_q=IBM+%26&price_rank=trend&price_page=3");
 expect(screen.queryByText("1. Ověřit a načíst identity poskytovatele")).not.toBeInTheDocument();
});
it("offers admin acquisition controls without secrets", () => {
 render(<MarketPipelinePanel data={data} admin/>);
 expect(screen.getByText("1. Ověřit a načíst identity poskytovatele")).toBeInTheDocument();
 expect(screen.getByText("2. Spustit průběžný sběr cen")).toBeInTheDocument();
});
it("keeps the processing-state filter across pages", () => {
 render(<MarketPipelinePanel data={{...data,state:"DONE"}} admin={false}/>);
 expect(screen.getByRole("combobox",{name:"Stav zpracování"})).toHaveValue("DONE");
 expect(screen.getByRole("link",{name:/Další/})).toHaveAttribute("href","/market?price_q=IBM+%26&price_rank=trend&price_page=3&price_state=DONE");
});

it("distinguishes price coverage from verified corporate actions", () => {
 render(<MarketPipelinePanel data={data} admin={false}/>);
 expect(screen.getByText(/Ani 100 % nepotvrzuje úplnost událostí/)).toBeInTheDocument();
});

it("labels missing feed prices and preserves this filter across pages", () => {
 render(<MarketPipelinePanel data={{...data,state:"NO_PRICE_DATA",counts:{NO_PRICE_DATA:1},items:[{...data.items[0],state:"NO_PRICE_DATA",bars:0,coverage:"0",detail:"Zdroj nevrátil ceny pro požadované období a feed"}]}} admin={false}/>);
 expect(screen.getByRole("combobox",{name:"Stav zpracování"})).toHaveValue("NO_PRICE_DATA");
 expect(screen.getByText("Zdroj nevrátil ceny pro požadované období a feed")).toBeInTheDocument();
 expect(screen.getByRole("link",{name:/Další/})).toHaveAttribute("href","/market?price_q=IBM+%26&price_rank=trend&price_page=3&price_state=NO_PRICE_DATA");
});


it("shows observed active/inactive lifecycle without inventing delisting dates", () => {
 render(<MarketPipelinePanel data={{...data,identity_directory:{...data.identity_directory,active:14000,inactive:321,changes:{first_seen:2,no_longer_present:1,symbol_or_venue_changed:3,status_changed:4,became_inactive:3,became_active:1}}}} admin={false}/>);
 expect(screen.getByText(/Aktivní: 14.*000/)).toBeInTheDocument();
 expect(screen.getByText(/neaktivní reference: 321/)).toBeInTheDocument();
 expect(screen.getByText(/4 změn lifecycle statusu/)).toBeInTheDocument();
 expect(screen.getByText(/Datum zjištění není datum IPO ani potvrzený delisting/)).toBeInTheDocument();
});


it("shows exact completed batch telemetry", () => {
 render(<MarketPipelinePanel data={{...data,batch:{...data.batch!,completed_at:"2026-09-23T12:00:00Z",metrics:{telemetry_complete:true,duration_seconds:600,http_requests:1234,response_bytes:10485760,task_attempts:1300,peak_rss_kib:262144,database_bytes_at_start:524288000,database_bytes_at_end:536870912,database_growth_bytes:12582912,task_count:14373}}}} admin={false}/>);
 expect(screen.getByText(/Provozní měření dávky/)).toBeInTheDocument();
 expect(screen.getByText(/1.*234 HTTP pokusů/)).toBeInTheDocument();
 expect(screen.getByText(/10 MiB odpovědí/)).toBeInTheDocument();
 expect(screen.getByText(/peak RSS 256 MiB/)).toBeInTheDocument();
 expect(screen.getByText(/DB 512 MiB/)).toBeInTheDocument();
 expect(screen.getByText(/růst DB 12 MiB/)).toBeInTheDocument();
});

it("does not present incomplete transport telemetry as zero", () => {
 render(<MarketPipelinePanel data={{...data,batch:{...data.batch!,completed_at:"2026-09-23T12:00:00Z",metrics:{telemetry_complete:false,duration_seconds:600,http_requests:null,response_bytes:null,task_attempts:1300,peak_rss_kib:null,database_bytes_at_start:524288000,database_bytes_at_end:536870912,database_growth_bytes:12582912,task_count:14373}}}} admin={false}/>);
 expect(screen.getByText(/Síťové a RSS měření je neúplné/)).toBeInTheDocument();
 expect(screen.queryByText(/HTTP pokusů/)).not.toBeInTheDocument();
 expect(screen.queryByText(/peak RSS/)).not.toBeInTheDocument();
});
