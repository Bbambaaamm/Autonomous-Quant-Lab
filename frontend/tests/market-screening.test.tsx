import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MarketScreeningPanel, type Screening } from "../components/market-screening-panel";
afterEach(cleanup);
const data: Screening = {run:{id:"run",batch_id:"batch",created_at:"2026-09-22T00:00:00Z",total:14346,eligible:1,content_hash:"hash"}, policy:{version:"us-current-universe-1",minimum_sessions:127,minimum_coverage:"0.98",minimum_price_usd:"5",minimum_feed_dollar_volume_20:"1000000"},matched:65,items:[{symbol:"AAA",eligible:false,momentum:null,trend:null,mean_reversion:null,reasons:["LOW_FEED_LIQUIDITY","ACTIONS_NOT_VERIFIED"]}]};
it("keeps the denominator and explicit exclusion reasons with safe pagination", () => {
 render(<MarketScreeningPanel data={data} query="A &" rank="trend" page={1}/>);
 expect(screen.getByText(/1 způsobilých z/)).toHaveTextContent(/14\s*346/);
 expect(screen.getByText(/Objem daného feedu pod limitem · Neověřené dividendy a splity/)).toBeInTheDocument();
 expect(screen.getByRole("link",{name:/Další/})).toHaveAttribute("href","/market?screen_q=A+%26&screen_rank=trend&screen_page=2");
 expect(screen.getAllByText("—")).toHaveLength(3);
});
it("distinguishes missing results from a zero-candidate completed run", () => {
 render(<MarketScreeningPanel data={{...data,run:null}} query="" rank="momentum" page={1}/>);
 expect(screen.getByText(/Dosud není k dispozici dokončený výběr/)).toBeInTheDocument();
 expect(screen.queryByRole("table")).not.toBeInTheDocument();
});
