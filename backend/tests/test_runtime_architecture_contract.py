import ast
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "backend" / "src" / "quantlab"


def _module(name: str) -> tuple[str, ast.Module]:
    source = (SOURCE_ROOT / name).read_text()
    return source, ast.parse(source)


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _compose_service(name: str) -> str:
    lines = (REPOSITORY_ROOT / "docker-compose.production.yml").read_text().splitlines()
    start = lines.index(f"  {name}:")
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("  ") and not lines[index].startswith("    ")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_provider_factory_keeps_explicit_bounded_scope_and_scoped_ca_evidence() -> None:
    source, tree = _module("provider_factory.py")
    function = _function(tree, "build_market_data_provider")
    keyword_only = {argument.arg for argument in function.args.kwonlyargs}

    assert {"instrument", "instruments", "request_budget"} <= keyword_only
    assert "ALPACA_INSTRUMENT_SCOPE_REQUIRED" in source
    assert "corporate_action_events_for_scope" in source
    assert "service.corporate_action_events," not in source


def test_market_task_processing_stays_one_shot_and_request_bounded() -> None:
    _, tree = _module("market_task_worker.py")
    function = _function(tree, "run_once")

    assert not any(isinstance(node, ast.While) for node in ast.walk(function))

    step_calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "step"
    ]
    assert len(step_calls) == 1

    provider_calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "build_market_data_provider"
    ]
    assert len(provider_calls) == 1
    request_budget = next(
        (
            keyword.value
            for keyword in provider_calls[0].keywords
            if keyword.arg == "request_budget"
        ),
        None,
    )
    assert isinstance(request_budget, ast.Constant)
    assert isinstance(request_budget.value, int)
    assert 0 < request_budget.value <= 100


def test_automation_worker_keeps_market_work_in_a_backpressured_child_process() -> None:
    source, _ = _module("automation.py")

    assert "require_capacity(" in source
    assert '[sys.executable, "-m", "quantlab.market_task_worker"]' in source
    assert "subprocess.run(" in source
    assert "timeout=540" in source
    assert "shell=True" not in source
    assert "MarketPipeline(" not in source


def test_long_lived_worker_keeps_soft_rss_recycling() -> None:
    source, _ = _module("worker.py")

    assert "current_rss_mib()" in source
    assert "settings.worker_soft_rss_mb" in source
    assert "worker.request_stop()" in source


def test_production_compose_keeps_measured_resource_ceiling_contract() -> None:
    expected = {
        "postgres": ("2g", "2.0"),
        "backend": ("1536m", "1.5"),
        "worker": ("1024m", "1.5"),
        "alpaca-events": ("384m", "0.5"),
        "frontend": ("384m", "0.75"),
    }

    for service_name, (memory, cpus) in expected.items():
        service = _compose_service(service_name)
        assert f"mem_limit: {memory}" in service
        assert f"cpus: {cpus}" in service

    assert "ports:" not in _compose_service("worker")
    assert "ports:" not in _compose_service("alpaca-events")


def test_architecture_contract_remains_wired_into_required_ci_checks() -> None:
    ci = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    invocation = "tests/test_runtime_architecture_contract.py"

    # Both jobs are required by the main-branch ruleset. Keeping the contract
    # in two independent required contexts makes accidental removal visible.
    assert ci.count(invocation) >= 2
