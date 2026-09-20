"""Launch Streamlit from source or a PyInstaller bundle."""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path


def project_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def unused_port(preferred: int = 8501) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])


def main() -> None:
    root = project_root()
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from gmail_bot.config import load_settings
    from gmail_bot.local_api import start_local_api

    settings = load_settings(root)
    api_port = unused_port(settings.local_api_port)
    start_local_api(port=api_port)
    os.environ["GMAIL_BOT_API_PORT"] = str(api_port)
    print(f"Gmail click-to-draft API: http://127.0.0.1:{api_port}")
    port = unused_port(int(os.environ.get("STREAMLIT_SERVER_PORT", "8501")))
    app = root / "app.py"
    from streamlit.web import cli as stcli

    sys.argv = [
        "streamlit",
        "run",
        str(app),
        "--server.port",
        str(port),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]
    raise SystemExit(stcli.main())


if __name__ == "__main__":
    main()
