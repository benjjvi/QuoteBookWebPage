"""Launch client or server mode with optional prompts."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv()


def prompt_mode() -> str:
    while True:
        choice = input("Run which mode? [client/server]: ").strip().lower()
        if choice in {"client", "server", "c", "s"}:
            return "client" if choice in {"client", "c"} else "server"
        print("Please enter 'client' or 'server'.")


def prompt_standalone() -> str:
    while True:
        choice = (
            input("Run client in split standalone mode (local API + web)? [y/n]: ")
            .strip()
            .lower()
        )
        if choice in {"y", "yes"}:
            return "true"
        if choice in {"n", "no"}:
            return "false"
        print("Please enter 'y' or 'n'.")


def wait_for_healthcheck(url: str, timeout_seconds: float = 15.0) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return False

    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    conn_cls = HTTPSConnection if parsed.scheme == "https" else HTTPConnection
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        connection = None
        try:
            connection = conn_cls(host, port, timeout=1.0)
            connection.request("GET", path)
            response = connection.getresponse()
            response.read()
            if response.status == 200:
                return True
        except OSError:
            time.sleep(0.25)
        finally:
            if connection is not None:
                connection.close()
    return False


def run_client_with_local_api() -> int:
    base_env = os.environ.copy()
    try:
        api_port = int(base_env.get("API_PORT", "8050"))
    except ValueError:
        api_port = 8050
    api_health_url = f"http://127.0.0.1:{api_port}/health"
    api_url = f"http://127.0.0.1:{api_port}"

    server_env = base_env.copy()
    client_env = base_env.copy()
    client_env["APP_STANDALONE"] = "false"
    client_env["QUOTE_API_URL"] = api_url

    print(f"Starting local API server on port {api_port}...")
    server_process = subprocess.Popen([sys.executable, "api_server.py"], env=server_env)

    try:
        if not wait_for_healthcheck(api_health_url):
            if server_process.poll() is None:
                server_process.terminate()
                server_process.wait(timeout=5)
            print("Failed to start API server in time.")
            return 1

        print("API server is healthy. Starting web client...")
        return subprocess.call([sys.executable, "app.py"], env=client_env)
    finally:
        if server_process.poll() is None:
            print("Stopping local API server...")
            server_process.terminate()
            try:
                server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_process.kill()


def main() -> int:
    mode_env = os.getenv("APP_MODE", "").strip().lower()
    standalone_env = os.getenv("APP_STANDALONE", "").strip().lower()

    if mode_env in {"client", "server"}:
        mode = mode_env
    else:
        mode = prompt_mode()

    if mode == "server":
        print("Starting server (api_server.py)...")
        return subprocess.call([sys.executable, "api_server.py"])

    if standalone_env not in {"true", "false"}:
        standalone_env = prompt_standalone()
        os.environ["APP_STANDALONE"] = standalone_env

    if standalone_env == "true":
        print("Starting client in split standalone mode (server + client)...")
        return run_client_with_local_api()

    print("Starting client (app.py)...")
    return subprocess.call([sys.executable, "app.py"])


if __name__ == "__main__":
    raise SystemExit(main())
