import json

from fastapi.testclient import TestClient

from quantlab import api
from quantlab.config import Settings
from quantlab.security import limiter


def client(role: str = "admin") -> TestClient:
    value = TestClient(api.app)
    value.headers["Authorization"] = f"Bearer {getattr(api.settings, f'api_{role}_token')}"
    return value


def test_health_public_but_other_routes_require_auth() -> None:
    value = TestClient(api.app)
    del value.headers["Authorization"]
    assert value.get("/healthz").json() == {"status": "ok"}
    denied = value.get("/operator/overview")
    assert denied.status_code == 401
    assert denied.headers["www-authenticate"] == "Bearer"


def test_rbac_matrix_and_actor_evidence() -> None:
    viewer = client("viewer")
    operator = client("operator")
    assert viewer.get("/operator/risk").status_code == 200
    halt = {"confirmation": "HALT", "reason": "bezpečnostní test"}
    resume = {"confirmation": "RESUME", "reason": "bezpečnostní test"}
    assert viewer.post("/operator/risk/halt", json=halt).status_code == 403
    assert operator.post("/operator/risk/halt", json=halt).status_code == 200
    assert operator.post("/operator/risk/resume", json=resume).status_code == 403
    events = client().get("/operator/audit?event_type=KILL_SWITCH_MANUAL_HALT").json()["items"]
    actor = json.loads(events[0]["payload_json"])["security_actor"]
    assert actor == {
        "actor_id": "api-operator",
        "actor_role": "OPERATOR",
        "authentication": "bearer",
    }


def test_host_cors_and_spoofed_forwarded_for() -> None:
    value = client()
    assert value.get("/operator/overview", headers={"Host": "evil.example"}).status_code == 400
    response = value.options(
        "/operator/overview",
        headers={"Origin": "https://evil.example", "X-Forwarded-For": "1.2.3.4"},
    )
    assert response.headers.get("access-control-allow-origin") is None


def test_production_configuration_fails_closed() -> None:
    base = {
        "app_env": "production",
        "database_url": "postgresql+psycopg://runtime@db/quantlab",
        "trusted_hosts": "quant.example",
    }
    overrides = (
        {},
        {
            "api_viewer_token": "short",
            "api_operator_token": "short",
            "api_admin_token": "short",
        },
        {"trusted_hosts": "*"},
        {"database_url": "sqlite:///bad.db"},
    )
    for override in overrides:
        try:
            Settings(**(base | override))
        except ValueError:
            pass
        else:
            raise AssertionError("Nebezpečná production konfigurace byla přijata")


def test_rate_limit_http_boundary_has_retry_after() -> None:
    original = api.settings.api_read_limit
    limiter.events.clear()
    api.settings.api_read_limit = 2
    try:
        admin = client()
        assert admin.get("/operator/risk").status_code == 200
        assert admin.get("/operator/risk").status_code == 200
        rejected = admin.get("/operator/risk")
        assert rejected.status_code == 429
        assert int(rejected.headers["retry-after"]) >= 1
        assert client("viewer").get("/operator/risk").status_code == 200
    finally:
        api.settings.api_read_limit = original
        limiter.events.clear()
