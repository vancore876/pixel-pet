"""LAN-side, read-only AutoMate worker for the Jeffery Supabase job queue.

The initial connector deliberately supports read operations only. Writes stay
disabled until their AutoMate procedure parameters and effects are verified in
staging.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent


def load_env(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


load_env()

BRIDGE_URL = os.environ.get("JEFFERY_AUTOMATE_SUPABASE_URL", "").rstrip("/")
BRIDGE_PUBLIC_KEY = os.environ.get("JEFFERY_AUTOMATE_SUPABASE_ANON_KEY", "")
BRIDGE_SECRET = os.environ.get("JEFFERY_AUTOMATE_BRIDGE_SECRET", "")
BRIDGE_ID = os.environ.get("JEFFERY_AUTOMATE_BRIDGE_ID", "automate-lan-1")
SQL_CONNECTION_STRING = os.environ.get("JEFFERY_AUTOMATE_SQL_CONNECTION_STRING", "")
FUNCTION_PATH = "/functions/v1/automate-bridge"
POLL_SECONDS = max(1, min(int(os.environ.get("JEFFERY_AUTOMATE_POLL_SECONDS", "3")), 30))
SENSITIVE_FIELD = re.compile(r"password|passwd|pin|hash|secret|token|credential|fingerprint|smtp", re.I)


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    raise TypeError(f"Cannot encode {type(value).__name__}")


def clean_result(value: Any) -> Any:
    if isinstance(value, list):
        return [clean_result(row) for row in value]
    if isinstance(value, dict):
        return {key: clean_result(item) for key, item in value.items() if not SENSITIVE_FIELD.search(key)}
    return value


def encode_body(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=json_default).encode("utf-8")


def call_bridge(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    raw = encode_body(payload)
    timestamp = str(int(time.time()))
    message = f"{timestamp}\nPOST\n{FUNCTION_PATH}{path}\n{raw.decode('utf-8')}".encode("utf-8")
    signature = hmac.new(BRIDGE_SECRET.encode("utf-8"), message, hashlib.sha256).digest()
    import base64

    headers = {
        "Content-Type": "application/json",
        "apikey": BRIDGE_PUBLIC_KEY,
        "Authorization": f"Bearer {BRIDGE_PUBLIC_KEY}",
        "X-Bridge-Id": BRIDGE_ID,
        "X-Bridge-Timestamp": timestamp,
        "X-Bridge-Signature": base64.b64encode(signature).decode("ascii"),
    }
    request = urllib.request.Request(BRIDGE_URL + FUNCTION_PATH + path, data=raw, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Bridge request failed ({exc.code})") from None


def run_read(connection: Any, operation: str, payload: dict[str, Any]) -> dict[str, Any] | list[dict[str, Any]]:
    cursor = connection.cursor()
    if operation == "get_inventory_item":
        part = str(payload.get("part_number", "")).strip()
        if not part or len(part) > 80:
            raise ValueError("invalid_part_number")
        cursor.execute("EXEC dbo.spGetItemInfo ?", part)
    elif operation == "get_order":
        transaction = int(payload.get("transaction_number", 0))
        if transaction <= 0:
            raise ValueError("invalid_transaction_number")
        cursor.execute("EXEC dbo.spGetOrder ?", transaction)
    elif operation == "search_orders":
        vendor = int(payload.get("vendor_id", 0))
        location = int(payload.get("location_id", 0))
        start = payload.get("start_date")
        end = payload.get("end_date")
        if not start or not end:
            raise ValueError("date_range_required")
        cursor.execute("EXEC dbo.spOrderSearch ?, ?, ?, ?", vendor, location, start, end)
    elif operation == "get_invoice":
        transaction = int(payload.get("transaction_number", 0))
        transaction_type = int(payload.get("transaction_type", 0))
        if transaction <= 0 or transaction_type < 0:
            raise ValueError("invalid_invoice_reference")
        cursor.execute("EXEC dbo.spGetInvoice ?, ?", transaction, transaction_type)
    elif operation == "search_receivals":
        start = payload.get("start_date")
        end = payload.get("end_date")
        if not start or not end:
            raise ValueError("date_range_required")
        values = (
            start,
            end,
            payload.get("receival_number"),
            payload.get("invoice_number"),
            payload.get("location_id"),
            payload.get("received_by"),
            payload.get("vendor_id"),
            payload.get("transaction_type"),
        )
        cursor.execute("EXEC dbo.spGetReceivals ?, ?, ?, ?, ?, ?, ?, ?", *values)
    else:
        raise ValueError("operation_not_enabled")

    columns = [item[0] for item in cursor.description or []]
    rows = [clean_result(dict(zip(columns, row))) for row in cursor.fetchmany(501)]
    if len(rows) > 500:
        raise ValueError("result_limit_exceeded")
    return rows


def complete(job: dict[str, Any], success: bool, result: Any = None, error_code: str | None = None) -> None:
    call_bridge(f"/bridge/jobs/{job['id']}/complete", {
        "success": success,
        "result": result if success else None,
        "error_code": error_code,
    })


def main() -> None:
    if not all((BRIDGE_URL, BRIDGE_PUBLIC_KEY, BRIDGE_SECRET, SQL_CONNECTION_STRING)):
        raise SystemExit("Set the bridge URL, publishable key, bridge secret, and staging SQL connection string in automate_backend/.env.")
    if not BRIDGE_URL.startswith("https://"):
        raise SystemExit("The bridge URL must use HTTPS.")
    try:
        import pyodbc
    except ImportError as exc:
        raise SystemExit("Install the connector dependencies with: python -m pip install -r automate_backend/requirements.txt") from exc

    connection = None
    while True:
        try:
            if connection is None:
                connection = pyodbc.connect(SQL_CONNECTION_STRING, timeout=5, autocommit=True)
            claim = call_bridge("/bridge/claim", {"limit": 5})
            for job in claim.get("jobs", []):
                try:
                    result = run_read(connection, str(job.get("operation", "")), job.get("payload") or {})
                    complete(job, True, result=result)
                except ValueError as exc:
                    complete(job, False, error_code=str(exc)[:64])
                except Exception:
                    complete(job, False, error_code="automate_read_failed")
        except Exception as exc:
            print(f"AutoMate bridge is waiting for a connection ({type(exc).__name__}).")
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
                connection = None
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
