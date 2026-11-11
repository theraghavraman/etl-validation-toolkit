"""Launch the browser application with Python's standard library."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import webbrowser


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="Port (default: choose an available port)")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    demo = Path(__file__).resolve().parents[1] / "demo"
    handler = partial(SimpleHTTPRequestHandler, directory=str(demo))
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"ETL Guard is running at {url}\nPress Ctrl+C to stop.", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nETL Guard stopped.")


if __name__ == "__main__":
    main()
