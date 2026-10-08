"""Run the chat server, or reset a password locally without exposing it in arguments."""
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
    arguments = parser.parse_args(argv)
    if not 1 <= arguments.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
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
    except ImportError:
        parser.error("Install the optional server dependencies with: python -m pip install -r requirements-server.txt")
    app = create_app(arguments.database, allow_lan_http=arguments.allow_lan_http)
    uvicorn.run(app, host=arguments.host, port=arguments.port,
                ssl_certfile=arguments.certfile, ssl_keyfile=arguments.keyfile,
                proxy_headers=False, access_log=False, server_header=False,
                limit_concurrency=100, timeout_keep_alive=5)


if __name__ == "__main__":
    main()
