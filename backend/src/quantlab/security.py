import hashlib
import hmac
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import IntEnum
from threading import Lock
from typing import Awaitable, Callable

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse, Response

from quantlab.config import Settings


class Role(IntEnum):
    VIEWER = 1
    OPERATOR = 2
    ADMIN = 3


@dataclass(frozen=True)
class Principal:
    actor_id: str
    role: Role


class RateLimiter:
    """Process-local limiter pro vynucenou single-worker production topologii."""

    def __init__(self) -> None:
        self.events: dict[str, deque[float]] = defaultdict(deque)
        self.lock = Lock()

    def allow(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        now = time.monotonic()
        with self.lock:
            events = self.events[key]
            while events and events[0] <= now - window:
                events.popleft()
            if len(events) >= limit:
                return False, max(1, int(window - (now - events[0])))
            events.append(now)
            return True, 0


limiter = RateLimiter()
logger = logging.getLogger("quantlab.security")
PUBLIC_PATHS = frozenset({"/healthz", "/readyz"})


def authenticate(request: Request, settings: Settings) -> Principal | None:
    authorization = request.headers.get("authorization", "")
    if len(authorization) > 512 or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    candidates = (
        (settings.api_viewer_token, Principal("api-viewer", Role.VIEWER)),
        (settings.api_operator_token, Principal("api-operator", Role.OPERATOR)),
        (settings.api_admin_token, Principal("api-admin", Role.ADMIN)),
    )
    return next(
        (principal for expected, principal in candidates if hmac.compare_digest(token, expected)),
        None,
    )


async def security_boundary(
    request: Request, call_next: Callable[[Request], Awaitable[Response]], settings: Settings
) -> Response:
    correlation_id = request.headers.get("x-correlation-id")
    if not correlation_id or len(correlation_id) > 128:
        correlation_id = hashlib.sha256(f"{time.time_ns()}".encode()).hexdigest()[:24]
    if request.url.path in PUBLIC_PATHS:
        response = await call_next(request)
    else:
        principal = authenticate(request, settings)
        if principal is None:
            logger.warning("Odmítnutý autentizační pokus correlation_id=%s", correlation_id)
            source = request.client.host if request.client else "unknown"
            allowed, retry = limiter.allow(
                f"authentication-failure:{source}", settings.api_auth_failure_limit, 60
            )
            if not allowed:
                return JSONResponse(
                    {"detail": "Příliš mnoho požadavků"},
                    429,
                    headers={"Retry-After": str(retry), "Cache-Control": "no-store"},
                )
            return JSONResponse(
                {"detail": "Neplatné přihlašovací údaje"},
                401,
                headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
            )
        request.state.principal = principal
        required = Role.VIEWER if request.method in {"GET", "HEAD", "OPTIONS"} else Role.ADMIN
        if request.url.path in {"/operator/risk/halt", "/risk/halt"}:
            required = Role.OPERATOR
        if principal.role < required:
            return JSONResponse({"detail": "Nedostatečné oprávnění"}, 403)
        if request.url.path in {"/operator/risk/halt", "/risk/halt"}:
            bucket, limit, window = "halt", settings.api_halt_limit, 60
        elif request.url.path in {"/operator/risk/resume", "/risk/resume"}:
            bucket, limit, window = "resume", settings.api_resume_limit, 600
        elif request.method in {"GET", "HEAD"}:
            bucket, limit, window = "read", settings.api_read_limit, 60
        else:
            bucket, limit, window = "mutation", settings.api_mutation_limit, 60
        allowed, retry = limiter.allow(f"{principal.actor_id}:{bucket}", limit, window)
        if not allowed:
            return JSONResponse(
                {"detail": "Příliš mnoho požadavků"},
                429,
                headers={"Retry-After": str(retry)},
            )
        response = await call_next(request)
    response.headers["cache-control"] = "no-store"
    response.headers["x-correlation-id"] = correlation_id
    return response


def current_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal):
        raise HTTPException(401, "Neplatné přihlašovací údaje")
    return principal
