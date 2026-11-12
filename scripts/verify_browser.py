"""Exercise the real browser demo locally or at a deployed --url (Playwright extra)."""
from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Use a deployed demo instead of starting a local server")
    parser.add_argument("--output", type=Path, default=Path("verification-evidence/browser"))
    parser.add_argument("--channel", help="Use an installed browser channel, e.g. chrome")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    server = None
    if args.url:
        url = args.url
    else:
        handler = partial(QuietHandler, directory=str(ROOT / "demo"))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_port}/"
    checks: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=args.channel)
            page = browser.new_page(viewport={"width": 1440, "height": 1050})
            errors: list[str] = []
            uploads: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: uploads.append(request.method)
                    if request.method not in {"GET", "HEAD"} else None)
            page.goto(url)
            expect(page.locator("#status")).to_have_text("Validation complete", timeout=180_000)

            def counts(read: int, valid: int, rejected: int) -> None:
                for element, value in [("rows-read", read), ("rows-valid", valid),
                                       ("rows-rejected", rejected), ("rows-loaded", valid)]:
                    expect(page.locator(f"#{element}")).to_have_text(str(value))

            counts(8, 4, 3)
            checks.append("Customer sample: 8 read, 4 valid, 3 rejected, 1 duplicate, 4 SQLite loaded")
            expect(page.locator("#deduped")).to_contain_text("1")
            page.screenshot(path=str(args.output / "desktop.png"), full_page=True)
            page.locator("#valid-tab").click()
            expect(page.locator("tbody tr")).to_have_count(4)
            page.locator("#json-tab").click()
            summary = json.loads(page.locator("pre").inner_text())
            assert summary["rows_read"] == 8
            checks.append("Accepted records and summary tabs")
            with page.expect_download() as event:
                page.locator("#download").click()
            download = event.value
            download.save_as(str(args.output / download.suggested_filename))
            exported = (args.output / download.suggested_filename).read_text(encoding="utf-8-sig")
            assert "raw_row_json" in exported and "error_message" in exported
            checks.append("Reject CSV download")
            for button, suffix in [("download-valid", ".csv"), ("download-summary", ".json"), ("template", ".csv")]:
                with page.expect_download() as event:
                    page.locator("#" + button).click()
                downloaded = event.value
                assert downloaded.suggested_filename.endswith(suffix)
                downloaded.save_as(str(args.output / downloaded.suggested_filename))
            summary_download = json.loads((args.output / "etlguard_customer_summary.json").read_text())
            assert summary_download["rows_valid"] == 4
            assert "valid_csv" not in summary_download
            accepted_csv = (args.output / "etlguard_customer_valid.csv").read_text(encoding="utf-8-sig")
            assert len(list(__import__("csv").DictReader(__import__("io").StringIO(accepted_csv)))) == 4
            checks.append("Accepted-row CSV, summary JSON and schema-template downloads")
            page.locator("#model").select_option("Transaction")
            counts(6, 3, 2)
            checks.append("Transaction sample: 6 read, 3 valid, 2 rejected, 1 duplicate")
            page.locator("#model").select_option("Customer")
            counts(8, 4, 3)
            clean = ROOT / "demo/samples/customers_clean.csv"
            if not clean.exists():
                clean = ROOT / "src/etlguard/examples/data/customers_clean.csv"
            page.locator("#file").set_input_files(clean)
            counts(3, 3, 0)
            checks.append("Clean CSV upload: all 3 records accepted")
            for name, data, message in [
                ("wrong.txt", b"data", "Select a .csv file"),
                ("empty.csv", b"", "empty"),
                ("large.csv", b"a" * 5_000_001, "up to 5 MB"),
            ]:
                page.locator("#file").set_input_files({"name": name, "mimeType": "text/csv", "buffer": data})
                expect(page.locator("#status")).to_contain_text(message)
                expect(page.locator("#download")).to_be_disabled()
                expect(page.locator("#rows-valid")).to_have_text("—")
                checks.append(f"Rejected {name}: cleared old results")
            page.locator("#file").set_input_files({
                "name": "nonutf8.csv", "mimeType": "text/csv", "buffer": b"id,name\nA,\xff\n",
            })
            expect(page.locator("#status")).to_contain_text("Could not read CSV")
            checks.append("Invalid UTF-8 rejected without replacement characters")
            page.locator("#file").set_input_files({
                "name": "broken.csv", "mimeType": "text/csv",
                "buffer": b"customer_id,full_name\nC001,Alice,extra\n",
            })
            expect(page.locator("#result-label")).to_have_text("VALIDATION ERROR")
            checks.append("Malformed CSV fails visibly")
            page.locator("#sample").click()
            counts(8, 4, 3)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(args.output / "mobile.png"), full_page=True)
            checks.append("Mobile layout without page overflow")
            assert not errors, errors
            assert not uploads, uploads
            checks.append("No uncaught JavaScript errors or CSV upload requests")
            retry = browser.new_page()
            retry.route("**/pyodide.mjs*", lambda route: route.abort())
            retry.goto(url)
            expect(retry.locator("#retry-runtime")).to_be_visible(timeout=30_000)
            expect(retry.locator("#file")).to_be_disabled()
            retry.unroute("**/pyodide.mjs*")
            retry.locator("#retry-runtime").click()
            expect(retry.locator("#status")).to_have_text("Validation complete", timeout=180_000)
            checks.append("Runtime download failure recovers using Retry without a page reload")
            retry.close()
            browser.close()
    finally:
        if server:
            server.shutdown()
            server.server_close()
    (args.output / "report.json").write_text(json.dumps({
        "status": "passed", "url": url, "checks": checks,
    }, indent=2) + "\n")
    print(f"Browser acceptance: {len(checks)} checks passed")


if __name__ == "__main__":
    main()
