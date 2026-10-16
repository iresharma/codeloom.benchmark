"""Open a local page without interactions; save browser evidence for shell agents."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--width", type=int, default=1280)
    args = parser.parse_args()
    if urlparse(args.url).hostname not in ("localhost", "127.0.0.1"):
        parser.error("A task-local URL is required")
    folder = Path("/task/.engine/debug/baseline")
    folder.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        page = browser.new_page(viewport={"width": args.width, "height": 800})
        console, network = [], []
        page.on(
            "console",
            lambda message: console.append(
                {"type": message.type, "text": message.text}
            ),
        )
        page.on(
            "requestfailed",
            lambda request: network.append(
                {"url": request.url, "error": request.failure}
            ),
        )
        page.goto(args.url, wait_until="networkidle")
        shot = folder / f"page-{args.width}.png"
        page.screenshot(path=str(shot), full_page=True)
        result = {
            "url": page.url,
            "title": page.title(),
            "console": console,
            "network": network,
            "screenshot": str(shot),
            "body": page.locator("body").inner_text()[:16000],
        }
        (folder / f"page-{args.width}.json").write_text(json.dumps(result))
        print(json.dumps(result))
        browser.close()


if __name__ == "__main__":
    main()
