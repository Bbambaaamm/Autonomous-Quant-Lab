import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).parents[1] / "src" / "quantlab"


def test_repository_has_no_live_execution_implementation_or_credentials() -> None:
    forbidden_classes: list[str] = []
    forbidden_assignments: list[str] = []
    for path in SOURCE_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and (
                "livebroker" in node.name.lower() or "liveorder" in node.name.lower()
            ):
                forbidden_classes.append(f"{path.name}:{node.name}")
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = [target.id for target in targets if isinstance(target, ast.Name)]
                forbidden_assignments.extend(
                    f"{path.name}:{name}"
                    for name in names
                    if "live_broker_credential" in name.lower()
                    or name.lower() == "live_execution_mode"
                )
    assert forbidden_classes == []
    assert forbidden_assignments == []


def test_strategy_provider_and_deployment_modules_do_not_submit_orders() -> None:
    for module in ("strategy.py", "market_data.py", "market_data_service.py", "phase6_runtime.py"):
        tree = ast.parse((SOURCE_ROOT / module).read_text())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"submit", "fill"}
        ]
        assert calls == [], f"{module} nesmí obsahovat přímou broker/order cestu"


def test_phase6_services_preserve_the_single_phase4_economic_boundary() -> None:
    tree = ast.parse((SOURCE_ROOT / "phase6_runtime.py").read_text())
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    for name in ("Phase6EligibilityService", "DeploymentService", "Phase6ExperimentRunner"):
        calls = {
            node.func.attr
            for node in ast.walk(classes[name])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert calls.isdisjoint(
            {"submit", "fill", "process", "run"}
            if name != "Phase6ExperimentRunner"
            else {"submit", "fill", "process"}
        )
    paper = classes["Phase6PaperExecutionService"]
    referenced_names = {node.id for node in ast.walk(paper) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(paper) if isinstance(node, ast.Attribute)
    }
    assert "PersistentPaperBroker" not in referenced_names
    assert "PersistentExecutionEngine" not in referenced_names
    assert "TradingCycleService" in referenced_names
    calls = [
        node
        for node in ast.walk(paper)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert all(node.func.attr not in {"submit", "fill", "process"} for node in calls)
