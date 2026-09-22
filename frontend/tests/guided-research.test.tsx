import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("../lib/auth", () => ({ assertSameOrigin: vi.fn(), requireSession: vi.fn(async () => ({ role: "ADMIN" })), backendToken: vi.fn(() => "synthetic-test-token") }));
import { ResearchForm, type ResearchOptions } from "../components/research-form";
import { TradingDiagnostics } from "../components/trading-diagnostics";
import { experimentAction, ingestionAction, snapshotAction } from "../app/actions";
import { decimalText } from "../lib/display";
const options: ResearchOptions = { code_sha: "a".repeat(40), strategies: [{ name: "multi_asset_mean_reversion", version: "1.0.0", defaults: { lookback: 20, threshold: "0.95", rebalance_frequency: "WEEKLY" } }, { name: "multi_asset_trend", version: "1.0.0", defaults: { fast: 20, slow: 100, rebalance_frequency: "MONTHLY" } }] };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it("uses named selections and correct parameters without requiring SHA or JSON", () => {
    const { container } = render(<ResearchForm options={options} snapshots={[{ snapshot_id: "data", status: "VALID", universe_id: "IBM", end_at: "2026-09-18", coverage: 1 }]}/>);
    expect(container.querySelector('[name="code_sha"]')).toBeNull();
    expect(container.querySelector('[name="parameter_configs"]')).toBeNull();
    fireEvent.change(screen.getByLabelText("Strategie"), { target: { value: "multi_asset_trend" } });
    expect(screen.getByLabelText("Dlouhý průměr (seance)")).toHaveValue(100);
    expect(screen.queryByLabelText("Poměr ceny k průměru")).not.toBeInTheDocument();
});
it("disables guided launch when actual runtime revision is unavailable", () => {
    render(<ResearchForm options={{ ...options, code_sha: null }} snapshots={[]}/>);
    expect(screen.getByRole("button", { name: "Spustit historickou simulaci" })).toBeDisabled();
});
it("sends exact decimal parameters and requests server-side revision resolution", async () => {
    const fetcher = vi.fn(async () => ({ ok: true }));
    vi.stubGlobal("fetch", fetcher);
    const form = new FormData();
    for (const [key, value] of Object.entries({ guided: "true", snapshot_id: "data", strategy_name: "multi_asset_mean_reversion", strategy_version: "1.0.0", param_lookback: "20", param_threshold: "0.95000000000000001", param_rebalance_frequency: "WEEKLY", reason: "Test varianty", code_sha: "forged" }))
        form.set(key, value);
    expect((await experimentAction({}, form)).success).toBeDefined();
    const body = JSON.parse((fetcher.mock.calls[0] as unknown as [
        string,
        {
            body: string;
        }
    ])[1].body);
    expect(body.code_sha).toBeNull();
    expect(body.parameter_configs).toEqual([{ lookback: 20, threshold: "0.95000000000000001", rebalance_frequency: "WEEKLY" }]);
});
it("does not claim a trade cause when evidence is missing or only monitoring is insufficient", () => {
    render(<TradingDiagnostics overview={{ paper_account_id: "paper-main", monitoring_verdict: "INSUFFICIENT_DATA", latest_paper_run: null }}/>);
    expect(screen.getByText(/Důvod neobchodování zatím nelze určit/)).toBeInTheDocument();
    expect(screen.getByText(/oddělený od výsledku obchodní úlohy/)).toBeInTheDocument();
});
it("normalizes zero without rounding tiny nonzero exposure", () => {
    expect(decimalText("0E-8")).toBe("0");
    expect(decimalText("1E-400")).toBe("1E-400");
    expect(decimalText(null)).toBe("Neuvedeno");
});

it("lets the backend choose its configured import and dataset provider", async () => {
    const fetcher = vi.fn(async () => ({ ok: true }));
    vi.stubGlobal("fetch", fetcher);
    const form = new FormData();
    for (const [key, val] of Object.entries({ instrument_id: "alpaca-test", universe_id: "market-test", start: "2026-01-01", end: "2026-09-21", as_of: "2026-09-22T05:00", minimum_coverage: "0.98", reason: "Test configured source" })) form.set(key, val);
    expect((await ingestionAction({}, form)).success).toBeDefined();
    expect((await snapshotAction({}, form)).success).toBeDefined();
    for (const call of fetcher.mock.calls) {
        const body = JSON.parse((call as unknown as [string, { body: string }])[1].body);
        expect(body).not.toHaveProperty("provider");
    }
});
