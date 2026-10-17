"""Run the office server and perform explicit local account/recovery operations."""
from __future__ import annotations

import argparse
import getpass
import ipaddress
from pathlib import Path
import sys


def loopback_host(value):
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return value.casefold() == "localhost"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"verify-backup", "restore-backup"}:
        restoring = argv[0] == "restore-backup"
        parser = argparse.ArgumentParser(description=(
            "Restore a verified office backup while the server is stopped." if restoring else
            "Verify an office backup and its adjacent SHA-256 manifest."))
        parser.add_argument("backup", help="Original .sqlite snapshot with its adjacent .sqlite.json manifest")
        if restoring:
            parser.add_argument("--database", required=True, help="Existing office database to replace")
            parser.add_argument("--confirm-restore", action="store_true")
        arguments = parser.parse_args(argv[1:])
        from .backups import BackupError, restore_backup, verify_backup
        try:
            if restoring:
                result = restore_backup(arguments.backup, arguments.database, confirm=arguments.confirm_restore)
                print("Office backup restored. All employee and IT sessions have been signed out.")
                print("Previous office data was saved to: " + result["safety_backup"])
            else:
                result = verify_backup(arguments.backup)
                print("Backup verified: " + result["name"])
                print("SHA-256, SQLite integrity, foreign keys, and office schema passed.")
        except BackupError as error:
            parser.error(str(error))
        except OSError:
            parser.error("The operation could not access local files. Check paths, permissions, and disk space.")
        return
    if argv and argv[0] == "reset-it-password":
        parser = argparse.ArgumentParser(description="Reset the IT administrator password on the office server.")
        parser.add_argument("--database", default="server-data/work-chat.sqlite")
        arguments = parser.parse_args(argv[1:])
        if not Path(arguments.database).is_file():
            parser.error("Database does not exist. Use the same --database path as the server.")
        from .store import ChatStore, StoreError
        from .admin_store import ITAdminStore
        password = getpass.getpass("New IT password (12–128 characters): ")
        confirmation = getpass.getpass("Confirm new IT password: ")
        if password != confirmation:
            parser.error("Passwords did not match.")
        try:
            ITAdminStore(ChatStore(arguments.database)).reset_local_password(password)
        except StoreError as error:
            parser.error(error.detail)
        print("IT password updated. All IT sessions have been signed out.")
        return
    if argv and argv[0] == "reset-password":
        parser = argparse.ArgumentParser(description="Stop the server, then reset a user's password on this machine.")
        parser.add_argument("username")
        parser.add_argument("--database", default="server-data/work-chat.sqlite")
        arguments = parser.parse_args(argv[1:])
        if not Path(arguments.database).is_file():
            parser.error("Database does not exist. Use the same --database path as the server.")
        from .store import ChatStore, StoreError
        password = getpass.getpass("New password (12–128 characters): ")
        confirmation = getpass.getpass("Confirm new password: ")
        if password != confirmation:
            parser.error("Passwords did not match.")
        try:
            ChatStore(arguments.database).reset_password(arguments.username, password)
        except StoreError as error:
            parser.error(error.detail)
        print("Password updated. All of this user's sessions have been signed out.")
        return

    parser = argparse.ArgumentParser(description="Jeffery Work Chat: a Python server for a trusted office LAN.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database", default="server-data/work-chat.sqlite")
    parser.add_argument("--certfile", help="TLS certificate PEM, trusted by the work computers")
    parser.add_argument("--keyfile", help="TLS private-key PEM")
    parser.add_argument("--allow-lan-http", action="store_true",
                        help="Allow unencrypted HTTP from local-network clients without certificates")
    parser.add_argument("--backup-directory", help="Local private snapshot directory (default: office-backups beside the database)")
    parser.add_argument("--backup-keep-days", type=int, default=14,
                        help="Keep the newest successful snapshot for each of 1–365 backup dates (default: 14)")
    parser.add_argument("--no-auto-backup", action="store_true", help="Disable daily snapshots; IT manual backups remain available")
    arguments = parser.parse_args(argv)
    if not 1 <= arguments.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    if not 1 <= arguments.backup_keep_days <= 365:
        parser.error("Backup retention must be between 1 and 365 days.")
    if bool(arguments.certfile) != bool(arguments.keyfile):
        parser.error("Provide both --certfile and --keyfile for HTTPS.")
    if not loopback_host(arguments.host) and not arguments.certfile and not arguments.allow_lan_http:
        parser.error("LAN connections require HTTPS by default. Provide --certfile and --keyfile, or explicitly use --allow-lan-http for an unencrypted office LAN.")
    for value in (arguments.certfile, arguments.keyfile):
        if value and not Path(value).is_file():
            parser.error("The certificate or private-key file does not exist.")
    try:
        import uvicorn
        from .app import create_app
        from .backups import BackupError, DatabaseLease
    except ImportError:
        parser.error("Install the optional server dependencies with: python -m pip install -r requirements-server.txt")
    try:
        # Lock before constructing the app: initialization also writes schema.
        # The lifespan shares this lease and never opens a second Windows lock.
        with DatabaseLease(arguments.database) as lease:
            app = create_app(arguments.database, allow_lan_http=arguments.allow_lan_http,
                             auto_backup=not arguments.no_auto_backup,
                             backup_directory=arguments.backup_directory,
                             backup_keep_days=arguments.backup_keep_days, database_lease=lease)
            try:
                uvicorn.run(app, host=arguments.host, port=arguments.port,
                            ssl_certfile=arguments.certfile, ssl_keyfile=arguments.keyfile,
                            proxy_headers=False, access_log=False, server_header=False,
                            limit_concurrency=100, timeout_keep_alive=5)
            finally:
                # Also cover a server-launch failure before ASGI shutdown runs.
                if not app.state.backups.stop():
                    lease.defer_release_until(app.state.backups._thread)
    except BackupError as error:
        parser.error(str(error))
    except OSError:
        parser.error("The server could not access local files. Check database and backup paths, permissions, and disk space.")


if __name__ == "__main__":
    main()
