"""Capture dashboard screenshots for the repo.

A .pbix will not render on GitHub and a Streamlit app needs to be run, so the
screenshots are what a reviewer sees first. They are generated rather than
hand-taken so they can be regenerated when the analysis changes and never drift
out of step with the numbers.

Uses the Chrome already installed on the machine (channel="chrome"), so no
browser download is required. Playwright is a tooling dependency for producing
this artefact, not an analysis dependency -- it is deliberately kept out of
requirements.txt.

Run:  python dashboard/capture_screenshots.py
      (starts and stops its own Streamlit server)
"""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "dashboard" / "screenshots"
PORT = 8899
URL = f"http://localhost:{PORT}"

# (filename, policy, budget, population, note)
VIEWS = [
    ("01_policy_b_severity_budget5.png", "B - Severity only",
     "5 per patient-day", "All alerts",
     "The winning policy at the pre-registered budget"),
    ("02_policy_a_alert_everything.png", "A - Alert everything",
     "Unlimited", "All alerts",
     "The conventional system: the burden baseline"),
    ("03_policy_d_context_budget5.png", "D - Context-aware",
     "5 per patient-day", "All alerts",
     "The policy that lost, at the same budget"),
    ("04_policy_b_renal_subgroup.png", "B - Severity only",
     "3 per patient-day", "Renal-relevant condition",
     "Subgroup filter under a tighter budget"),
]


def wait_for_server(timeout: int = 90) -> None:
    for _ in range(timeout):
        try:
            with urllib.request.urlopen(URL, timeout=3) as r:
                if r.status == 200:
                    return
        except Exception:      # noqa: BLE001 - server not up yet
            time.sleep(1)
    raise SystemExit("Streamlit server did not start")


def main() -> int:
    from playwright.sync_api import sync_playwright

    SHOTS.mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen(
        [str(ROOT / ".venv" / "Scripts" / "streamlit.exe"), "run",
         str(ROOT / "dashboard" / "app.py"),
         "--server.headless", "true", "--server.port", str(PORT),
         "--browser.gatherUsageStats", "false"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_server()
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            # Streamlit scrolls inside its own container, so full_page does not
            # extend the document. A tall viewport is what actually captures
            # the whole page.
            page = browser.new_page(viewport={"width": 1500, "height": 2650},
                                    device_scale_factor=1.5)
            page.goto(URL, wait_until="networkidle")
            # Streamlit renders over a websocket after load; wait for real content.
            page.wait_for_selector("div[data-testid='stMetric']", timeout=60_000)
            page.wait_for_timeout(2500)

            for fname, policy, budget, population, note in VIEWS:
                boxes = page.locator("div[data-testid='stSelectbox']")
                for i, value in enumerate([policy, budget, population]):
                    boxes.nth(i).click()
                    page.get_by_role("option", name=value, exact=True).click()
                    page.wait_for_timeout(900)
                page.wait_for_selector("div[data-testid='stMetric']", timeout=30_000)
                page.wait_for_timeout(2000)
                page.screenshot(path=str(SHOTS / fname), full_page=True)
                print(f"  {fname:<42} {note}")

            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=20)

    for f in sorted(SHOTS.glob("*.png")):
        print(f"  {f.name}  {f.stat().st_size/1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
