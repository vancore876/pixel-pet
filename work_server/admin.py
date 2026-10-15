"""IT administration API, isolated from ordinary employee authentication."""
from __future__ import annotations

from typing import Annotated

from fastapi import Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from .admin_store import ITAdminStore
from .store import StoreError


class ITLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: Annotated[StrictStr, Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")]
    password: Annotated[StrictStr, Field(min_length=1, max_length=128)]


class ITPasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    new_password: Annotated[StrictStr, Field(min_length=12, max_length=128)]


class ITEmployeeAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    admin_password: Annotated[StrictStr, Field(min_length=12, max_length=128)]
    confirm_username: Annotated[StrictStr, Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")]


class ITEmployeePassword(ITEmployeeAction):
    new_password: Annotated[StrictStr, Field(min_length=12, max_length=128)]


def register_admin_routes(app, store, *, rate_limiter, is_loopback, rate_window_seconds=60):
    admin = ITAdminStore(store)
    auth_limiter = rate_limiter(10, rate_window_seconds)
    account_limiter = rate_limiter(20, rate_window_seconds)
    action_limiter = rate_limiter(20, rate_window_seconds)
    app.state.it_admin = admin
    app.state.it_auth_limiter = auth_limiter
    app.state.it_account_limiter = account_limiter
    app.state.it_action_limiter = action_limiter

    def token(request):
        parts = request.headers.get("authorization", "").split(" ")
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise StoreError(401, "Sign in to the IT section.")
        return parts[1]

    def local(request):
        # Socket peer only: a Forwarded/X-Forwarded-For header is never authority.
        return bool(request.client and is_loopback(request.client.host))

    def limit_password_work(request):
        auth_limiter.check(request.client.host)
        account_limiter.check("admin")

    @app.post("/api/it/login")
    def login(body: ITLogin, request: Request):
        limit_password_work(request)
        return admin.login(body.username, body.password, local=local(request))

    @app.post("/api/it/password")
    def change_password(body: ITPasswordChange, request: Request):
        limit_password_work(request)
        return admin.change_password(token(request), body.current_password, body.new_password, local=local(request))

    @app.post("/api/it/logout", status_code=204)
    def logout(request: Request):
        action_limiter.check(request.client.host)
        admin.logout(token(request), local=local(request))
        return Response(status_code=204)

    @app.get("/api/it/overview")
    def overview(request: Request):
        return admin.overview(token(request))

    @app.get("/api/it/users")
    def users(request: Request):
        return admin.users(token(request))

    @app.post("/api/it/users/{identifier}/reset-password", status_code=204)
    def reset_employee_password(identifier: Annotated[int, Path(ge=1, le=2**63 - 1)], body: ITEmployeePassword, request: Request):
        limit_password_work(request)
        admin.employee_action(token(request), identifier, body.admin_password, body.confirm_username, new_password=body.new_password)
        return Response(status_code=204)

    @app.post("/api/it/users/{identifier}/sign-out", status_code=204)
    def sign_out_employee(identifier: Annotated[int, Path(ge=1, le=2**63 - 1)], body: ITEmployeeAction, request: Request):
        limit_password_work(request)
        admin.employee_action(token(request), identifier, body.admin_password, body.confirm_username)
        return Response(status_code=204)
