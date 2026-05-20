import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


APP_FILENAME = "streamlit_app.py"


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def resolve_app_path() -> Path:
    if getattr(sys, "frozen", False):
        bundle_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        bundled_app = bundle_dir / APP_FILENAME
        if bundled_app.exists():
            return bundled_app
        return Path(sys.executable).resolve().parent / APP_FILENAME
    return Path(__file__).resolve().parent / APP_FILENAME


def wait_for_server(url: str, timeout_seconds: int = 20) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            import urllib.request

            with urllib.request.urlopen(url, timeout=1):
                return True
        except Exception:
            time.sleep(0.25)
    return False


def main() -> int:
    app_path = resolve_app_path()
    if not app_path.exists():
        print(f"Could not locate {APP_FILENAME} at {app_path}")
        return 1

    port = find_free_port()
    url = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env["STREAMLIT_SERVER_HEADLESS"] = "true"
    env["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"

    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.port",
        str(port),
        "--server.address",
        "127.0.0.1",
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--server.fileWatcherType",
        "none",
    ]

    proc = subprocess.Popen(command, env=env)

    if wait_for_server(url):
        webbrowser.open(url)

    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
