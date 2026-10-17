"""IT-only snapshot scheduling; recovery is deliberately a local CLI operation."""
from contextlib import closing
from typing import Annotated

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from .backups import BackupError, BackupManager
from .store import StoreError


class BackupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    admin_password: Annotated[StrictStr, Field(min_length=12, max_length=128)]


def register_backup_routes(app, store, *, directory=None, enabled=False, keep_days=14):
    admin = app.state.it_admin
    manager = BackupManager(store, admin, directory=directory, enabled=enabled, keep_days=keep_days)
    app.state.backups = manager

    def token(request):
        parts = request.headers.get("authorization", "").split(" ")
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise StoreError(401, "Sign in to the IT section.")
        return parts[1]

    @app.get("/api/it/backups")
    def status(request: Request):
        with closing(store._connect()) as connection:
            admin._require_session(connection, token(request))
        try:
            return manager.status()
        except (BackupError, OSError):
            raise StoreError(503, "Backup status is unavailable. Check the backup directory on the office server.") from None

    @app.post("/api/it/backups", status_code=202)
    def start(body: BackupRequest, request: Request):
        app.state.it_auth_limiter.check(request.client.host)
        app.state.it_account_limiter.check("admin")
        session = token(request)
        proof = admin._prove_password(session, body.admin_password)

        def authorize():
            with closing(store._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                # Session revocation or password rotation during Argon2 work
                # must prevent both scheduling and the authorization audit.
                admin._recheck_proof(connection, session, proof)
                admin._audit(connection, "backup_requested")

        try:
            accepted = manager.request(authorize)
        except (BackupError, OSError):
            raise StoreError(503, "The backup could not start. Check the backup directory on the office server.") from None
        if not accepted:
            raise StoreError(409, "A backup is already running on the server.")
        return {"status": "queued"}
