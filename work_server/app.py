"""LAN-only HTTP API and browser interface for the optional work chat server."""
from __future__ import annotations

from collections import OrderedDict, deque
import asyncio
import ipaddress
from pathlib import Path
from threading import Lock
import time
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from .store import ChatStore, StoreError


MAX_REQUEST_BYTES = 16 * 1024
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
}
LAN_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16",
    "::1/128", "fc00::/7", "fe80::/10",
))


def is_lan_address(value):
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except (ValueError, AttributeError):
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(address.version == network.version and address in network for network in LAN_NETWORKS)


def is_loopback_address(value):
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except (ValueError, AttributeError):
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_loopback


class RequestGuard:
    """Reject off-LAN clients and oversized streamed bodies before parsing JSON."""

    def __init__(self, app, body_timeout_seconds=10, *, allow_lan_http=False):
        self.app = app
        self.body_timeout_seconds = body_timeout_seconds
        self.allow_lan_http = allow_lan_http

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def secured_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                replaced = {name.lower().encode("ascii") for name in SECURITY_HEADERS}
                headers = [(name, value) for name, value in headers if name.lower() not in replaced]
                headers.extend((name.lower().encode("ascii"), value.encode("ascii")) for name, value in SECURITY_HEADERS.items())
                if scope.get("scheme") == "https":
                    headers.append((b"strict-transport-security", b"max-age=31536000"))
                message = {**message, "headers": headers}
            await send(message)

        async def reject(status, detail):
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, secured_send)

        client = scope.get("client")
        # Inspect the socket peer only. X-Forwarded-For is deliberately ignored.
        if not client or not is_lan_address(client[0]):
            await reject(403, "Work chat is available only on the local work network.")
            return
        if not self.allow_lan_http and not is_loopback_address(client[0]) and scope.get("scheme") != "https":
            await reject(426, "Work network connections require HTTPS.")
            return
        headers = {name.lower(): value for name, value in scope.get("headers", [])}
        if scope.get("method") in ("POST", "PUT", "PATCH", "DELETE") and b"origin" in headers:
            expected = (scope.get("scheme", "http") + "://").encode("ascii") + headers.get(b"host", b"")
            if headers[b"origin"].lower() != expected.lower():
                await reject(403, "Open work chat on this server to make changes.")
                return
        try:
            content_length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            await reject(400, "Invalid request length.")
            return
        if content_length < 0:
            await reject(400, "Invalid request length.")
            return
        if content_length > MAX_REQUEST_BYTES:
            await reject(413, "Request is too large (maximum 16 KiB).")
            return
        chunks = []
        size = 0
        try:
            async with asyncio.timeout(self.body_timeout_seconds):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    if message["type"] != "http.request":
                        continue
                    chunk = message.get("body", b"")
                    size += len(chunk)
                    if size > MAX_REQUEST_BYTES:
                        await reject(413, "Request is too large (maximum 16 KiB).")
                        return
                    chunks.append(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await reject(408, "Request body did not arrive in time. Try again.")
            return
        body = b"".join(chunks)
        delivered = False

        async def buffered_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, buffered_receive, secured_send)


class RateLimiter:
    """Bounded, process-local rolling-window limits; one server worker is sufficient."""

    def __init__(self, maximum, seconds, *, clock=time.monotonic, key_limit=4096):
        self.maximum, self.seconds, self.clock, self.key_limit = maximum, seconds, clock, key_limit
        self.attempts = OrderedDict()
        self.lock = Lock()

    def check(self, key):
        now = self.clock()
        with self.lock:
            if key not in self.attempts and len(self.attempts) >= self.key_limit:
                stale = [item for item, values in self.attempts.items() if not values or values[-1] <= now - self.seconds]
                for item in stale:
                    del self.attempts[item]
                # Do not evict active counters: fail closed until old entries expire.
                if len(self.attempts) >= self.key_limit:
                    raise StoreError(429, "Too many requests. Wait a minute and try again.")
            values = self.attempts.setdefault(key, deque())
            while values and values[0] <= now - self.seconds:
                values.popleft()
            if len(values) >= self.maximum:
                raise StoreError(429, "Too many requests. Wait a minute and try again.")
            values.append(now)
            self.attempts.move_to_end(key)


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: Annotated[StrictStr, Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")]
    password: Annotated[StrictStr, Field(min_length=12, max_length=128)]


class NewMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: Annotated[StrictStr, Field(min_length=1, max_length=4000)]
    recipient_id: Annotated[StrictInt, Field(ge=1, le=2**63 - 1)] | None = None


def create_app(database_path="server-data/work-chat.sqlite", *, clock=time.time,
               session_seconds=8 * 60 * 60, auth_limit=20, send_limit=60,
               rate_window_seconds=60, static_directory=None, account_auth_limit=10,
               max_users=1000, allow_lan_http=False):
    app = FastAPI(title="Jeffery Work Chat", docs_url=None, redoc_url=None, openapi_url=None,
                  telemetry={"tracing": False, "metrics": False, "logs": False, "auto_configure": False})
    store = ChatStore(database_path, clock=clock, session_seconds=session_seconds, max_users=max_users)
    auth_limiter = RateLimiter(auth_limit, rate_window_seconds)
    account_limiter = RateLimiter(account_auth_limit, rate_window_seconds)
    send_limiter = RateLimiter(send_limit, rate_window_seconds)
    app.state.store = store
    app.state.auth_limiter = auth_limiter
    app.state.account_limiter = account_limiter
    app.state.send_limiter = send_limiter
    app.add_middleware(RequestGuard, allow_lan_http=allow_lan_http)

    @app.exception_handler(StoreError)
    async def expected_error(request, error):
        headers = {"Retry-After": str(rate_window_seconds)} if error.status == 429 else None
        return JSONResponse({"detail": error.detail}, status_code=error.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, error):
        # Pydantic's default errors include input values, which could echo passwords.
        return JSONResponse({"detail": "Invalid request. Check field names, types, and allowed lengths."}, status_code=422)

    def signed_in(request: Request):
        authorization = request.headers.get("authorization", "")
        parts = authorization.split(" ")
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise StoreError(401, "Sign in to use work chat.")
        return store.authenticate(parts[1])

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "Jeffery Work Chat", "version": 1}

    @app.post("/api/auth/register", status_code=201)
    def register(credentials: Credentials, request: Request):
        auth_limiter.check(request.client.host)
        account_limiter.check(credentials.username.casefold())
        return store.register(credentials.username, credentials.password)

    @app.post("/api/auth/login")
    def login(credentials: Credentials, request: Request):
        auth_limiter.check(request.client.host)
        account_limiter.check(credentials.username.casefold())
        return store.login(credentials.username, credentials.password)

    @app.post("/api/auth/logout", status_code=204)
    def logout(request: Request, user=Depends(signed_in)):
        store.logout(request.headers["authorization"].split(" ")[1])
        return Response(status_code=204)

    @app.get("/api/me")
    def me(user=Depends(signed_in)):
        return user

    @app.get("/api/users")
    def users(user=Depends(signed_in)):
        return {"users": store.users()}

    @app.get("/api/messages")
    def messages(peer_id: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
                 after_id: Annotated[int | None, Query(ge=0, le=2**63 - 1)] = None,
                 limit: Annotated[int, Query(ge=1, le=100)] = 100, user=Depends(signed_in)):
        return store.messages(user["id"], peer_id=peer_id, after_id=after_id, limit=limit)

    @app.post("/api/messages", status_code=201)
    def send_message(message: NewMessage, user=Depends(signed_in)):
        send_limiter.check(user["id"])
        return {"message": store.send(user["id"], message.body, message.recipient_id)}

    static_path = Path(static_directory) if static_directory else Path(__file__).parent / "static"
    if static_path.is_dir():
        app.mount("/static", StaticFiles(directory=static_path), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        if (static_path / "index.html").is_file():
            return FileResponse(static_path / "index.html", media_type="text/html")
        raise HTTPException(503, "The browser interface is missing. Restore work_server/static from the repository.")

    return app
