"""Isolated browser/API workflow: synthetic accounts, temporary server and database.

Install requirements-server-dev.txt and Playwright Chromium before running.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from threading import Thread
import time
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PASSWORD = "work-chat-example-password"


@contextmanager
def temporary_server():
    import uvicorn
    from work_server.app import create_app

    with tempfile.TemporaryDirectory(prefix="jeffery-web-check-") as folder:
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
            reservation.listen(128)
            app = create_app(Path(folder) / "chat.sqlite")
            server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False,
                                                  proxy_headers=False))
            thread = Thread(target=server.run, kwargs={"sockets": [reservation]}, daemon=True)
            thread.start()
            origin = f"http://127.0.0.1:{port}"
            try:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if not thread.is_alive():
                        raise RuntimeError("Temporary server exited during startup.")
                    try:
                        with urlopen(origin + "/api/health", timeout=1):
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise TimeoutError("Temporary server did not start.")
                yield origin
            finally:
                server.should_exit = True
                thread.join(timeout=10)
                if thread.is_alive():
                    raise RuntimeError("Temporary server did not shut down.")


def api(origin, path, body=None, token=""):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(origin + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def register(page, origin, username):
    page.goto(origin)
    page.get_by_role("button", name="Create account", exact=True).click()
    page.get_by_label("Username", exact=True).fill(username)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_label("Confirm password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Create account", exact=True).last.click()
    expect(page.locator("#account-name")).to_have_text(username)
    expect(page.locator("#conversation-title")).to_have_text("Team Room")


def send(page, text):
    page.locator("#message-body").fill(text)
    page.get_by_role("button", name="Send message").click()
    expect(page.locator("#message-body")).to_have_value("")
    expect(page.locator("#messages")).to_contain_text(text)


def main():
    with temporary_server() as origin, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            alice_context = browser.new_context(viewport={"width": 1280, "height": 850})
            bob_context = browser.new_context(viewport={"width": 1100, "height": 800})
            alice, bob = alice_context.new_page(), bob_context.new_page()
            errors = []
            alice.on("pageerror", lambda error: errors.append(str(error)))
            bob.on("pageerror", lambda error: errors.append(str(error)))
            register(alice, origin, "alice")
            register(bob, origin, "bob")
            third = api(origin, "/api/auth/register", {"username": "charlie", "password": PASSWORD})
            send(alice, "<b>Hello team — literal text</b>")
            bob.get_by_role("button", name="Refresh", exact=True).click()
            expect(bob.locator("#messages")).to_contain_text("<b>Hello team — literal text</b>")
            assert bob.locator("#messages b").count() == 0, "Message HTML was interpreted"
            alice.get_by_role("button", name="Refresh coworkers").click()
            alice.get_by_role("button", name="bob", exact=True).click()
            send(alice, "Private message for Bob")
            bob.get_by_role("button", name="Refresh coworkers").click()
            bob.get_by_role("button", name="alice", exact=True).click()
            expect(bob.locator("#messages")).to_contain_text("Private message for Bob")
            roster = api(origin, "/api/users", token=third["token"])["users"]
            alice_id = next(user["id"] for user in roster if user["username"] == "alice")
            charlie_messages = api(origin, f"/api/messages?peer_id={alice_id}", token=third["token"])
            assert not charlie_messages["messages"], "A third user read Alice/Bob's DM"

            # A late private-history reply must never land in the shared room.
            alice.get_by_role("button", name="Team Room", exact=False).click()
            held = {}
            def hold_private(route):
                if "peer_id=" in route.request.url and not held:
                    held["route"] = route
                    held["response"] = route.fetch()
                else:
                    route.continue_()
            alice.route("**/api/messages?*", hold_private)
            alice.get_by_role("button", name="bob", exact=True).click()
            deadline = time.monotonic() + 5
            while not held and time.monotonic() < deadline:
                alice.wait_for_timeout(50)
            assert held, "No delayed private-history request was captured"
            alice.get_by_role("button", name="Team Room", exact=False).click()
            held["route"].fulfill(response=held["response"])
            expect(alice.locator("#messages")).not_to_contain_text("Private message for Bob")
            alice.unroute("**/api/messages?*", hold_private)

            alice.locator("#message-body").fill("A shared-room draft")
            alice.get_by_role("button", name="bob", exact=True).click()
            alice.get_by_role("button", name="Team Room", exact=False).click()
            expect(alice.locator("#message-body")).to_have_value("A shared-room draft")

            def fail_send(route):
                route.fulfill(status=503, content_type="application/json", body='{"detail":"Temporary test outage"}')
            alice.route("**/api/messages", fail_send)
            alice.get_by_role("button", name="Send message").click()
            expect(alice.locator("#connection-status")).to_contain_text("Temporary test outage")
            expect(alice.locator("#message-body")).to_have_value("A shared-room draft")
            alice.unroute("**/api/messages", fail_send)

            # Review the renewed desktop and phone layout without real user data.
            screenshot_folder = os.environ.get("JEFFERY_UI_SCREENSHOT_DIR")
            if screenshot_folder:
                target = Path(screenshot_folder).resolve()
                target.mkdir(parents=True, exist_ok=True)
                alice.get_by_role("button", name="Refresh", exact=True).click()
                expect(alice.locator("#connection-status")).to_contain_text("Connected")
                alice.screenshot(path=str(target / "browser-coworkers.png"), full_page=True)
            bob.set_viewport_size({"width": 390, "height": 844})
            expect(bob.locator("#message-body")).to_be_visible()
            expect(bob.locator("#team-room")).to_be_visible()
            assert bob.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Phone chat overflows horizontally"
            if screenshot_folder:
                bob.screenshot(path=str(target / "browser-coworkers-phone.png"), full_page=True)

            alice.get_by_role("button", name="Sign out", exact=True).click()
            expect(alice.locator("#auth-view")).to_be_visible()
            expect(alice.locator("#password")).to_have_value("")
            expect(alice.locator("#message-body")).to_have_value("")
            assert alice.evaluate("localStorage.length === 0 && sessionStorage.length === 0"), "Credentials stored in browser storage"
            bob.reload()
            expect(bob.locator("#auth-view")).to_be_visible()
            if screenshot_folder:
                bob.screenshot(path=str(target / "browser-signin-phone.png"), full_page=True)
            assert not errors, errors
            print("PASS: browser sign-up, team delivery, private DMs, third-user isolation, literal HTML, stale replies, drafts, failed send, logout, and RAM-only sessions")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
