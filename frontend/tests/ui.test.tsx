import {render,screen} from "@testing-library/react";import {describe,it,expect} from "vitest";import {Status,pct,JsonTable} from "../components/ui";import {LineChart} from "../components/chart";
describe("pravdivá operator prezentace",()=>{it("zobrazuje HALTED jako unsafe",()=>{render(<Status value="HALTED"/>);expect(screen.getByText("HALTED")).toHaveClass("unsafe")});it("SUSPENDED je viditelně unsafe",()=>{render(<Status value="SUSPENDED"/>);expect(screen.getByText("SUSPENDED")).toHaveClass("unsafe")});it("INSUFFICIENT_DATA není healthy",()=>{render(<Status value="INSUFFICIENT_DATA"/>);expect(screen.getByText("INSUFFICIENT_DATA")).toHaveClass("watch")});it("null return není 0 %",()=>expect(pct(null)).toBe("N/A"));it("prázdná performance řada je pravdivá",()=>{render(<LineChart points={[]} field="marked_equity" label="Equity"/>);expect(screen.getByText(/nejsou dostupné/)).toBeInTheDocument()});it("tabulka vykreslí přesná API data",()=>{render(<JsonTable rows={[{instrument_id:"SPY",quantity:"2.50000000"}]}/>);expect(screen.getByText("2.50000000")).toBeInTheDocument()})});

describe("kritické data statusy", () => {
  it("missing session data je unsafe", () => {
    render(<Status value="MISSING_SESSION_DATA" />);
    expect(screen.getByText("MISSING_SESSION_DATA")).toHaveClass("unsafe");
  });
});

describe("neznámé statusy", () => {
  it("N/A ani nový status nejsou zobrazené jako safe", () => {
    const { rerender } = render(<Status value={null} />);
    expect(screen.getByText("N/A")).toHaveClass("watch");
    rerender(<Status value="NEW_UNKNOWN_STATE" />);
    expect(screen.getByText("NEW_UNKNOWN_STATE")).toHaveClass("watch");
  });
});
