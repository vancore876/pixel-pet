"""Create a least-privilege SQL login for the staging-only bridge agent."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import pyodbc


ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
LOGIN = "jeffery_bridge_staging"
DATABASE = "FT_JefferyStaging_20261112"
SERVER = r"SERVER\SQLEXPRESS"
READ_PROCEDURES = (
    "spGetItemInfo",
    "spGetOrder",
    "spOrderSearch",
    "spGetInvoice",
    "spGetReceivals",
)


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env(values: dict[str, str]) -> None:
    ENV_FILE.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")


def main() -> None:
    values = read_env()
    password = values.get("JEFFERY_AUTOMATE_SQL_PASSWORD") or secrets.token_urlsafe(48)
    values["JEFFERY_AUTOMATE_SQL_PASSWORD"] = password
    write_env(values)
    admin_connection = pyodbc.connect(
        f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={SERVER};DATABASE=master;"
        "Trusted_Connection=yes;Encrypt=no;Connection Timeout=5;",
        timeout=5,
        autocommit=True,
    )
    try:
        cursor = admin_connection.cursor()
        cursor.execute("SELECT DB_ID(?)", DATABASE)
        if cursor.fetchone()[0] is None:
            raise RuntimeError("The approved staging database was not found.")
        cursor.execute("SELECT SUSER_ID(?)", LOGIN)
        if cursor.fetchone()[0] is None:
            cursor.execute(
                "EXEC master.dbo.sp_addlogin @loginame=?, @passwd=?, @defdb=?",
                LOGIN,
                password,
                DATABASE,
            )
        else:
            cursor.execute(
                "EXEC master.dbo.sp_password @old=NULL, @new=?, @loginame=?",
                password,
                LOGIN,
            )
            cursor.execute("EXEC master.dbo.sp_defaultdb @loginame=?, @defdb=?", LOGIN, DATABASE)
        cursor.execute(f"ALTER DATABASE [{DATABASE}] SET MULTI_USER")
        cursor.execute(f"USE [{DATABASE}]")
        cursor.execute(
            "IF DATABASE_PRINCIPAL_ID(?) IS NULL CREATE USER [jeffery_bridge_staging] "
            "FOR LOGIN [jeffery_bridge_staging]",
            LOGIN,
        )
        for proc in READ_PROCEDURES:
            cursor.execute(f"GRANT EXECUTE ON OBJECT::dbo.[{proc}] TO [jeffery_bridge_staging]")
    finally:
        admin_connection.close()

    reader_connection = pyodbc.connect(
        f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={SERVER};DATABASE={DATABASE};"
        f"UID={LOGIN};PWD={password};Encrypt=no;Connection Timeout=5;",
        timeout=5,
        autocommit=True,
    )
    try:
        cursor = reader_connection.cursor()
        cursor.execute(
            "IF DATABASE_PRINCIPAL_ID(?) IS NULL CREATE USER [jeffery_bridge_staging] "
            "FOR LOGIN [jeffery_bridge_staging]",
            LOGIN,
        )
        for proc in READ_PROCEDURES:
            cursor.execute(f"GRANT EXECUTE ON OBJECT::dbo.[{proc}] TO [jeffery_bridge_staging]")
        cursor.execute("SELECT DB_NAME()")
        if cursor.fetchone()[0] != DATABASE:
            raise RuntimeError("The restricted AutoMate connection is not on staging.")
    finally:
        reader_connection.close()

    values["JEFFERY_AUTOMATE_SQL_CONNECTION_STRING"] = (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={SERVER};DATABASE={DATABASE};"
        f"UID={LOGIN};PWD={password};Encrypt=no;"
    )
    write_env(values)
    print("Staging-only SQL reader is configured; it has EXECUTE on the five approved read procedures.")


if __name__ == "__main__":
    main()
