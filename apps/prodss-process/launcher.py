import os
import socket
import sys
import threading
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


def open_browser_when_ready(url: str, timeout_seconds: int = 30) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            import urllib.request

            with urllib.request.urlopen(url, timeout=1):
                webbrowser.open(url)
                return
        except Exception:
            time.sleep(0.25)


def main() -> int:
    app_path = resolve_app_path()
    if not app_path.exists():
        print(f"Could not locate {APP_FILENAME} at {app_path}")
        return 1

    port = find_free_port()
    url = f"http://127.0.0.1:{port}"

    os.environ["STREAMLIT_SERVER_HEADLESS"] = "true"
    os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"

    # Open the browser once the server is reachable. Streamlit runs in the main
    # thread (below) and blocks, so the wait/open loop runs on a background thread.
    threading.Thread(
        target=open_browser_when_ready, args=(url,), daemon=True
    ).start()

    # In a PyInstaller-frozen app, sys.executable is this .exe -- not a Python
    # interpreter -- so spawning "python -m streamlit" does not work. Instead we
    # invoke Streamlit's CLI in-process by setting sys.argv and calling its entry
    # point directly.
    sys.argv = [
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

    from streamlit.web import cli as stcli

    return stcli.main()


if __name__ == "__main__":
    raise SystemExit(main())
