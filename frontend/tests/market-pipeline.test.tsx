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
