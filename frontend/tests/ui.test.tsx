import {render,screen} from "@testing-library/react";import {describe,it,expect} from "vitest";import {Status,pct,JsonTable} from "../components/ui";import {LineChart} from "../components/chart";
describe("pravdivá operator prezentace",()=>{it("zobrazuje HALTED jako unsafe",()=>{render(<Status value="HALTED"/>);expect(screen.getByText("HALTED")).toHaveClass("unsafe")});it("SUSPENDED je viditelně unsafe",()=>{render(<Status value="SUSPENDED"/>);expect(screen.getByText("SUSPENDED")).toHaveClass("unsafe")});it("INSUFFICIENT_DATA není healthy",()=>{render(<Status value="INSUFFICIENT_DATA"/>);expect(screen.getByText("INSUFFICIENT_DATA")).toHaveClass("watch")});it("null return není 0 %",()=>expect(pct(null)).toBe("N/A"));it("prázdná performance řada je pravdivá",()=>{render(<LineChart points={[]} field="marked_equity" label="Equity"/>);expect(screen.getByText(/nejsou dostupné/)).toBeInTheDocument()});it("tabulka vykreslí přesná API data",()=>{render(<JsonTable rows={[{instrument_id:"SPY",quantity:"2.50000000"}]}/>);expect(screen.getByText("2.50000000")).toBeInTheDocument()})});

describe("kritické data statusy", () => {
  it("missing session data je unsafe", () => {
    render(<Status value="MISSING_SESSION_DATA" />);
    expect(screen.getByText("MISSING_SESSION_DATA")).toHaveClass("unsafe");
  });
});

describe("autonomous worker readiness", () => {
  it("nedostupný worker není zobrazen jako bezpečný", () => {
    render(<Status value="UNAVAILABLE" />);
    expect(screen.getByText("UNAVAILABLE")).toHaveClass("unsafe");
  });

  it("globálně vypnutý runtime není zobrazen jako ready", () => {
    render(<Status value="DISABLED" />);
    expect(screen.getByText("DISABLED")).toHaveClass("unsafe");
  });
});

import { MutationForm } from "../components/mutation-form";
import { fireEvent } from "@testing-library/react";

describe("kritické operator mutations", () => {
  it("viewer-compatible disabled action nelze odeslat", () => {
    render(<MutationForm action={async () => ({success:"neočekáváno"})} title="Promotion" submit="Promote" disabled><input name="reason" /></MutationForm>);
    expect(screen.getByRole("button", {name:"Promote"})).toBeDisabled();
  });

  it("během čekání na server zakáže double submit", async () => {
    let finish: ((state:{success:string})=>void) | undefined;
    const action = () => new Promise<{success:string}>(resolve => { finish=resolve; });
    render(<MutationForm action={action} title="Approval" submit="Approve"><input name="reason" /></MutationForm>);
    fireEvent.click(screen.getByRole("button", {name:"Approve"}));
    expect(await screen.findByRole("button", {name:"Ověřuji na serveru…"})).toBeDisabled();
    finish?.({success:"APPROVED"});
    expect(await screen.findByRole("status")).toHaveTextContent("APPROVED");
  });

  it("zobrazí bezpečnou doménovou chybu bez optimistic success", async () => {
    render(<MutationForm action={async()=>({error:"Deployment vyžaduje PAPER_CANDIDATE"})} title="Deployment" submit="Create"><input /></MutationForm>);
    fireEvent.click(screen.getByRole("button", {name:"Create"}));
    expect(await screen.findByRole("alert")).toHaveTextContent("PAPER_CANDIDATE");
    expect(screen.queryByText("Deployment vyžaduje PAPER_CANDIDATE", {selector:".success"})).not.toBeInTheDocument();
  });
});
