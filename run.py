"""Launch client or server mode with optional prompts."""

import os
import subprocess
import sys

from dotenv import load_dotenv

load_dotenv()

MODE_ENV = os.getenv("APP_MODE", "").strip().lower()
STANDALONE_ENV = os.getenv("APP_STANDALONE", "").strip().lower()


def prompt_mode() -> str:
    while True:
        choice = input("Run which mode? [client/server]: ").strip().lower()
        if choice in {"client", "server", "c", "s"}:
            return "client" if choice in {"client", "c"} else "server"
        print("Please enter 'client' or 'server'.")


def prompt_standalone() -> str:
    while True:
        choice = input("Run client in standalone mode? [y/n]: ").strip().lower()
        if choice in {"y", "yes"}:
            return "true"
        if choice in {"n", "no"}:
            return "false"
        print("Please enter 'y' or 'n'.")


def main() -> int:
    if MODE_ENV in {"client", "server"}:
        mode = MODE_ENV
    else:
        mode = prompt_mode()

    if mode == "client" and STANDALONE_ENV not in {"true", "false"}:
        os.environ["APP_STANDALONE"] = prompt_standalone()

    script = "app.py" if mode == "client" else "api_server.py"
    print(f"Starting {mode} ({script})...")
    return subprocess.call([sys.executable, script])


if __name__ == "__main__":
    raise SystemExit(main())
