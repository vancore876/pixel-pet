"""Authenticated conversation read receipts, separate from alert delivery."""
from typing import Annotated

from fastapi import Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt


class ReadReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    peer_id: Annotated[StrictInt, Field(ge=1, le=2**63 - 1)] | None = None
    through_id: Annotated[StrictInt, Field(ge=1, le=2**63 - 1)]


def register_receipt_routes(app, store, signed_in, *, rate_limiter, rate_window_seconds=60):
    limiter = rate_limiter(120, rate_window_seconds)
    app.state.receipt_limiter = limiter

    @app.post("/api/read-receipts")
    def mark_read(payload: ReadReceipt, request: Request, user=Depends(signed_in)):
        limiter.check(user["id"])
        return store.mark_read(user["id"], peer_id=payload.peer_id, through_id=payload.through_id,
                               session_token=request.headers["authorization"].split(" ")[1])

    @app.get("/api/read-receipts")
    def receipts(request: Request, peer_id: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
                 after_id: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
                 limit: Annotated[int, Query(ge=1, le=50)] = 50, user=Depends(signed_in)):
        limiter.check(user["id"])
        return store.message_receipts(user["id"], peer_id=peer_id, after_id=after_id, limit=limit,
                                      session_token=request.headers["authorization"].split(" ")[1])
