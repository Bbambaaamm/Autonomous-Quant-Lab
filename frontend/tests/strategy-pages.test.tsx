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
  expect(screen.getByText(strategy.strategy_name)).toBeInTheDocument();
  expect(screen.getByText(strategy.strategy_version)).toBeInTheDocument();
  expect(screen.queryByText("undefined")).not.toBeInTheDocument();
});
it("renders the same API identity in the strategy detail heading", async () => {
  vi.mocked(api).mockResolvedValue({ ...strategy, experiments: [], deployments: [] });
  render(await Detail({ params: Promise.resolve({ identity: strategy.strategy_identity }) }));
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
    "multi_asset_mean_reversion · 1.0.0"
  );
});
