"""Launcher for the KDS: starts the web server (no console window when packaged) and opens the board full screen.

Run from source:  python run_kds.py          Packaged:  KDS.exe
Options:          --port 8000   --host 0.0.0.0 (all screens on the network) or 127.0.0.1 (this PC only)
                  --no-browser  (start the service only)
Log file (packaged): C:\\ProgramData\\NeptonKDS\\kds.log
"""
import argparse
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser


def port_in_use(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def setup_logging(data_dir):
    """Windowed exe has no console (sys.stdout is None): send logs to a rotating file instead."""
    fmt = "%(asctime)s %(levelname)s %(message)s"
    if sys.stdout is None or sys.stderr is None:
        sys.stdout = sys.stderr = open(os.devnull, "w")
        handler = logging.handlers.RotatingFileHandler(os.path.join(str(data_dir), "kds.log"),
                                                       maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        logging.basicConfig(level=logging.INFO, format=fmt, handlers=[handler])
    else:
        logging.basicConfig(level=logging.INFO, format=fmt)


def browser_exe():
    """Chrome or Edge (Edge ships with Windows 10/11)."""
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        base = os.environ.get(env)
        if not base:
            continue
        for rel in (r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe"):
            path = os.path.join(base, rel)
            if os.path.isfile(path):
                return path
    return None


def open_board(url, data_dir):
    """Open the board in an app-style window that starts in full screen (like pressing F11)."""
    exe = browser_exe()
    if exe:
        try:
            subprocess.Popen([exe, f"--app={url}", "--start-fullscreen",
                              f"--user-data-dir={os.path.join(str(data_dir), 'browser')}",
                              "--no-first-run", "--no-default-browser-check", "--overscroll-history-navigation=0",
                              "--autoplay-policy=no-user-gesture-required"])
            return
        except OSError:
            pass
    webbrowser.open(url)


def open_when_ready(port, url, data_dir, wait=90):
    """At Windows start-up the service may take a few seconds: wait until it answers, then open the board."""
    for _ in range(wait * 4):
        if port_in_use(port):
            break
        time.sleep(0.25)
    open_board(url, data_dir)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("KDS_PORT", 8000)))
    ap.add_argument("--host", default=os.environ.get("KDS_HOST", "0.0.0.0"))
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    url = f"http://localhost:{args.port}/"

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "kds_project.settings")
    from kds_project import settings
    setup_logging(settings.DATA_DIR)

    if port_in_use(args.port):  # already running: just show the board
        logging.info("KDS already running on port %s", args.port)
        if not args.no_browser:
            open_board(url, settings.DATA_DIR)
        return

    from django.core.wsgi import get_wsgi_application
    from waitress import serve

    app = get_wsgi_application()
    print(f"Kitchen Display running at {url}  (other screens: http://<this-pc-ip>:{args.port}/)")
    logging.info("KDS serving on %s:%s", args.host, args.port)
    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(args.port, url, settings.DATA_DIR), daemon=True).start()
    serve(app, host=args.host, port=args.port, threads=8, ident="KDS")


if __name__ == "__main__":
    sys.exit(main())