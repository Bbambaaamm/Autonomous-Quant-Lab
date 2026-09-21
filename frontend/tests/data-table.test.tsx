import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { DataTable } from "../components/data-table";
import { Status } from "../components/ui";
import { dateText } from "../lib/display";
afterEach(cleanup);
it("prioritizes job outcomes while preserving fields beyond the first eight", () => {
    const row = { a: 1, b: 2, c: 3, d: 4, e: 5, f: 6, g: 7, h: 8, status: "SUCCEEDED", outcome: "NO_ACTION", no_action_reason: "NO_TRADE_DELTA", extra: "complete-evidence" };
    render(<DataTable rows={[row]}/>);
    expect(screen.getByRole("columnheader", { name: "Stav" })).toBeInTheDocument();
    expect(screen.getByText("Není potřeba změnit portfolio")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Celý záznam"));
    expect(screen.getByText("complete-evidence")).toBeVisible();
});
it("searches all loaded rows and exposes later pages without losing raw data", () => {
    render(<DataTable rows={Array.from({ length: 12 }, (_, i) => ({ id: `id-${i}`, symbol: `Symbol-${i}`, quantity: "2.50000000" }))}/>);
    expect(screen.getByText("Řádky 1–10 z 12")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Další" }));
    expect(screen.getByText("Řádky 11–12 z 12")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Symbol-11" } });
    expect(screen.getByText("Řádky 1–1 z 1")).toBeInTheDocument();
    expect(screen.getAllByText("2.50000000").length).toBeGreaterThan(0);
});
it("does not turn missing values into zero and keeps precise values in details", () => {
    render(<DataTable rows={[{ total_return: null, quantity: "0.00000000123456789" }]}/>);
    expect(screen.getAllByText("Neuvedeno").length).toBeGreaterThan(0);
    expect(screen.queryByText("0 %")).not.toBeInTheDocument();
    expect(screen.getAllByText("0.00000000123456789").length).toBeGreaterThan(0);
});
it("uses Czech risk labels but never marks an unknown state healthy", () => {
    const { container } = render(<Status value="NEW_UNKNOWN_STATE"/>);
    expect(container.querySelector('.safe')).toBeNull();
    cleanup();
    render(<Status value="HALTED"/>);
    expect(screen.getByText("Obchodování zastaveno")).toHaveClass("unsafe");
});
it("formats UTC explicitly and does not reinterpret bare identifiers", () => {
    expect(dateText("2026-09-21T17:00:00Z")).toContain("17:00");
    expect(dateText("2026-09-21T17:00:00Z")).toContain("UTC");
    expect(dateText("paper-main")).toBe("paper-main");
});
import { LineChart } from "../components/chart";
import type { Point } from "../lib/api";
it("shows a marker for one session and rejects missing values instead of plotting zero", () => {
    const p = { session_date: "2026-09-18", marked_equity: "100000" } as Point;
    const { container } = render(<LineChart points={[p]} field="marked_equity" label="Hodnota portfolia"/>);
    expect(container.querySelector('circle')).not.toBeNull();
    cleanup();
    render(<LineChart points={[{ ...p, marked_equity: "" }]} field="marked_equity" label="Hodnota portfolia"/>);
    expect(screen.getByText(/některé hodnoty chybí/)).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
});
it("labels constant chart values only once on the axis and includes currency", () => {
    const p = { session_date: "2026-09-18", marked_equity: "100000" } as Point;
    const { container } = render(<LineChart points={[p, p]} field="marked_equity" label="Portfolio" currency="USD"/>);
    expect(container.querySelectorAll('svg text').length).toBe(3);
    expect(screen.getByText(/USD/)).toBeInTheDocument();
});
