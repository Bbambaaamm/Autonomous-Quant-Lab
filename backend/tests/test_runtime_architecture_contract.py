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


def test_research_work_stays_single_slot_isolated_and_capacity_gated() -> None:
    api_source, _ = _module("api.py")
    admission_source, _ = _module("research_admission.py")

    assert "with research_admission(paper_repository.engine):" in api_source
    assert "require_capacity(" in api_source
    assert '[sys.executable, "-m", "quantlab.research_worker"]' in api_source
    assert "pg_try_advisory_lock" in admission_source
    assert "pg_advisory_unlock" in admission_source
    assert "RESEARCH_CONCURRENCY_LIMIT" in admission_source


def test_long_lived_worker_keeps_soft_rss_recycling() -> None:
    source, _ = _module("worker.py")

    assert "current_rss_mib()" in source
    assert "settings.worker_soft_rss_mb" in source
    assert "worker.request_stop()" in source


def test_current_history_limits_authoritative_rows_in_sql() -> None:
    source, tree = _module("phase6_runtime.py")
    accessor = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ValidatedCurrentDataAccessor"
    )
    history = next(
        node
        for node in accessor.body
        if isinstance(node, ast.FunctionDef) and node.name == "history"
    )
    segment = ast.get_source_segment(source, history) or ""

    assert "func.row_number()" in segment
    assert 'label("revision_rank")' in segment
    assert 'label("history_rank")' in segment
    assert ".where(bounded.c.history_rank <= lookback)" in segment
    assert "rows = tuple(" not in segment
    assert "seen:" not in segment


def test_snapshot_builder_keeps_bounded_scope_and_streaming() -> None:
    source, tree = _module("market_data_service.py")
    service = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "DatasetSnapshotService"
    )
    source_segment = ast.get_source_segment(source, service) or ""

    assert "max_snapshot_days = 5 * 366" in source_segment
    assert "stream_batch_size = 512" in source_segment
    assert "InstrumentRecord.instrument_id.in_(membership_ids)" in source_segment
    assert "MarketObservationRecord.instrument_id.in_(instrument_ids)" in source_segment
    assert "stream_results=True" in source_segment
    assert "yield_per=cls.stream_batch_size" in source_segment
    assert "select(InstrumentRecord)" in source_segment
    assert "select(InstrumentRecord).where(" in source_segment


def test_phase6_snapshot_verification_stays_batched_and_copy_bounded() -> None:
    phase6_source, phase6_tree = _module("phase6_runtime.py")
    service_source, _ = _module("market_data_service.py")
    runner = next(
        node
        for node in phase6_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Phase6ExperimentRunner"
    )
    runner_segment = ast.get_source_segment(phase6_source, runner) or ""

    assert "snapshot_load_batch_size = 1000" in runner_segment
    assert "range(0, len(entries), self.snapshot_load_batch_size)" in runner_segment
    assert "canonical_snapshot_content_hash(immutable_content)" in runner_segment
    assert "session.expunge(snapshot)" in runner_segment
    assert "entries[entry_index] = None" in runner_segment
    assert "seen_observation_ids" not in runner_segment
    assert (
        "[item for item in observations if item.timestamp <= evaluation_end]" not in runner_segment
    )
    assert "evaluation_end=evaluation_end" in runner_segment
    assert "encoder.iterencode(value)" in service_source
    assert "sys.intern(row.instrument_id)" in service_source


def test_broad_research_resource_gate_is_required_ci_work() -> None:
    ci = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text()

    unit_research = ci.split("  unit-research:", 1)[1].split("\n  api:", 1)[0]
    assert "benchmarks/broad_research.py" in unit_research
    assert "--instruments 2000" in unit_research
    assert "--sessions 250" in unit_research
    assert "--max-rss-mib 1024" in unit_research
    assert "--max-seconds 180" in unit_research


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
    assert "tests/test_market_pipeline.py" in ci


def test_rate_limiter_and_backend_enforce_single_worker_topology() -> None:
    """P2 guard from issue #217: ``RateLimiter`` in security.py is
    process-local, so its token bucket is per-process. Running uvicorn with
    multiple workers would partition that bucket and silently weaken rate
    limiting. The architecture contract locks ``--workers 1`` in the
    production Dockerfile CMD; this test fails if it is ever widened."""
    rate_source, _ = _module("security.py")
    assert "single-worker" in rate_source

    dockerfile = (REPOSITORY_ROOT / "backend" / "Dockerfile").read_text()
    assert "--workers" in dockerfile
    # Exactly one worker; never >= 2.Quoted form mirrors the existing CMD list.
    assert '"--workers","1"' in dockerfile