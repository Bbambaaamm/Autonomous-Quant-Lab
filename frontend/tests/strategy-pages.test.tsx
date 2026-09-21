import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import Strategies from "../app/strategies/page";
import Detail from "../app/strategies/[identity]/page";
import { api } from "../lib/api";
vi.mock("../lib/api", () => ({ api: vi.fn() }));
vi.mock("../lib/auth", () => ({ session: async () => ({ role: "VIEWER" }) }));
vi.mock("../app/actions", () => ({
    approvalAction: vi.fn(), autonomousScheduleAction: vi.fn(),
    monitoringEnrollmentAction: vi.fn(), monitoringPolicyAction: vi.fn(),
}));
afterEach(cleanup);
const strategy = {
    strategy_identity: "mean-reversion", strategy_name: "multi_asset_mean_reversion",
    strategy_version: "1.0.0", metadata_json: "{}",
};
it("renders the persisted API strategy name and version in the list", async () => {
    vi.mocked(api).mockResolvedValue([strategy]);
    render(await Strategies());
    expect(screen.getByText("Návrat k průměru")).toBeInTheDocument();
    expect(screen.getByText(strategy.strategy_version)).toBeInTheDocument();
    expect(screen.queryByText("undefined")).not.toBeInTheDocument();
});
it("renders the same API identity in the strategy detail heading", async () => {
    vi.mocked(api).mockResolvedValue({ ...strategy, experiments: [], deployments: [] });
    render(await Detail({ params: Promise.resolve({ identity: strategy.strategy_identity }) }));
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("multi_asset_mean_reversion · 1.0.0");
});
it("links to deployment settings instead of implying missing implementation parameters", async () => {
    vi.mocked(api).mockResolvedValue([strategy]);
    render(await Strategies());
    expect(screen.getByRole("link", { name: "Zobrazit nastavení nasazení →" }))
        .toHaveAttribute("href", `/strategies/${strategy.strategy_identity}`);
    expect(screen.queryByText("N/A")).not.toBeInTheDocument();
});
it("shows each deployment's persisted parameters without substituting strategy defaults", async () => {
    const parameters = [
        '{"lookback":20,"rebalance_frequency":"WEEKLY","threshold":"0.95"}',
        '{"lookback":40,"rebalance_frequency":"MONTHLY","threshold":"0.90"}',
    ];
    vi.mocked(api).mockResolvedValue({ ...strategy, experiments: [], deployments: parameters.map((parameters_json, index) => ({ deployment_id: `deployment-${index}`,
            status: "APPROVED", paper_account_id: `paper-${index}`, parameters_json })) });
    render(await Detail({ params: Promise.resolve({ identity: strategy.strategy_identity }) }));
    for (const value of parameters)
        expect(screen.getByText((_, element) => element?.tagName === "PRE" && element.textContent === JSON.stringify(JSON.parse(value), null, 2))).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { name: "Parametry nasazení" })).toHaveLength(2);
});
