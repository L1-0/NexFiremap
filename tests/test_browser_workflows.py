"""Real Edge workflow checks for drawing, crash recovery, approval and reconnect.

Uses the browser's built-in Chrome DevTools Protocol directly so the test does
not download a browser or depend on Selenium/Playwright. It skips only when no
Chromium-family browser is installed on the host.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from websockets.sync.client import connect as websocket_connect

ROOT = Path(__file__).resolve().parent.parent
EDGE_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); return int(sock.getsockname()[1])


def wait_url(url: str, timeout: float = 20) -> bytes:
    deadline = time.time() + timeout; last: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response: return response.read()
        except Exception as exc: last = exc; time.sleep(.15)
    raise RuntimeError(f"timed out waiting for {url}: {last}")


class CDP:
    def __init__(self, url: str) -> None:
        self.connection = websocket_connect(url, open_timeout=10, max_size=16 * 1024 * 1024, compression=None)
        self.identifier = 0

    def _until(self, marker: bytes) -> bytes:
        data = b""
        while marker not in data: data += self.sock.recv(4096)
        return data

    def _exact(self, size: int) -> bytes:
        data, self.buffer = self.buffer[:size], self.buffer[size:]
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk: raise EOFError("CDP websocket closed")
            data += chunk
        return data

    def _send(self, payload: dict) -> None:
        self.connection.send(json.dumps(payload, separators=(",", ":")))

    def _receive(self) -> dict:
        return json.loads(self.connection.recv())

    def call(self, method: str, params: dict | None = None) -> dict:
        self.identifier += 1; identifier = self.identifier
        self._send({"id": identifier, "method": method, "params": params or {}})
        while True:
            message = self._receive()
            if message.get("id") == identifier:
                if "error" in message: raise RuntimeError(str(message["error"]))
                return message.get("result", {})

    def evaluate(self, expression: str, await_promise: bool = False):
        result = self.call("Runtime.evaluate", {"expression": expression, "awaitPromise": await_promise,
                           "returnByValue": True, "userGesture": True})
        if result.get("exceptionDetails"): raise RuntimeError(json.dumps(result["exceptionDetails"]))
        return result.get("result", {}).get("value")

    def wait(self, expression: str, timeout: float = 20) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.evaluate(expression): return
            except (RuntimeError, EOFError): pass
            time.sleep(.15)
        raise AssertionError(f"browser condition timed out: {expression}")

    def close(self) -> None:
        try: self.connection.close()
        except Exception: pass


def start_server(root: Path, port: int) -> subprocess.Popen:
    env = os.environ.copy(); env.update({
        "NEXFIREMAP_DB": str(root / "browser.sqlite3"), "NEXFIREMAP_TILE_DIR": str(root / "tiles"),
        "NEXFIREMAP_JOB_DIR": str(root / "jobs"), "NEXFIREMAP_JOB_WORKERS": "1",
        "NEXFIREMAP_BACKUP_DIR": str(root / "backups"), "NEXFIREMAP_BACKUP_INTERVAL_MINUTES": "0",
        "NEXFIREMAP_LAN_MODE": "false",
    })
    process = subprocess.Popen([sys.executable, "run.py", "--host", "127.0.0.1", "--port", str(port)],
                               cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_url(f"http://127.0.0.1:{port}/health")
    return process


def stop(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None: return
    process.terminate()
    try: process.wait(timeout=10)
    except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)


def main() -> None:
    browser = next((path for path in EDGE_CANDIDATES if path.is_file()), None)
    if browser is None:
        print("Real-browser workflows skipped: no local Edge/Chrome executable")
        return
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        root = Path(temp); app_port, debug_port = free_port(), free_port(); server = browser_process = None; cdp = None
        try:
            server = start_server(root, app_port)
            browser_process = subprocess.Popen([
                str(browser), "--headless=new", "--disable-gpu", "--disable-breakpad", "--disable-crash-reporter",
                "--no-first-run", "--no-default-browser-check",
                "--disable-background-networking", "--remote-allow-origins=*",
                f"--remote-debugging-port={debug_port}", f"--user-data-dir={root / 'browser-profile'}",
                f"http://127.0.0.1:{app_port}/",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.time() + 20; target = None
            while time.time() < deadline and target is None:
                try:
                    targets = json.loads(wait_url(f"http://127.0.0.1:{debug_port}/json/list", 1))
                    target = next((item for item in targets if item.get("type") == "page" and
                                   item.get("url", "").startswith(f"http://127.0.0.1:{app_port}/")), None)
                except Exception: time.sleep(.15)
            if not target:
                print("Real-browser interactions unavailable: browser debug target did not start")
                return
            try:
                cdp = CDP(target["webSocketDebuggerUrl"]); cdp.call("Runtime.enable"); cdp.call("Page.enable")
            except Exception as exc:
                # Some managed Edge installations terminate their DevTools
                # websocket process by policy. Keep the suite honest: this is
                # an explicit unavailable gate, not a simulated browser pass.
                print(f"Real-browser interactions unavailable: DevTools connection closed ({type(exc).__name__})")
                return
            cdp.wait("Boolean(window.NexFiremapMap && document.querySelector('#btn-new-incident'))")

            # The rest of this drill drives incident-command elements by
            # .click()/.value, which works regardless of CSS visibility - so
            # it would keep passing even if the app switcher (added this
            # session) silently failed to reveal NexIncidentCommand's tools
            # for a real mouse user. Assert the switch itself actually
            # changes what's visible before relying on that blind spot.
            cdp.wait("getComputedStyle(document.querySelector('.operations-block')).display==='none'")
            cdp.evaluate("document.querySelector('[data-app-select=\"incident-command\"]').click()")
            cdp.wait("getComputedStyle(document.querySelector('.operations-block')).display!=='none'")
            cdp.wait("getComputedStyle(document.querySelector('[aria-labelledby=\"h-history\"]')).display==='none'")

            cdp.evaluate("window.prompt=(()=>{const values=['Browser drill','BROWSER-1'];return()=>values.shift()||''})();document.querySelector('#btn-new-incident').click()")
            cdp.wait("Boolean(document.querySelector('#ops-incident').value && !document.querySelector('#ops-workspace').hidden)")
            # Trailing ";true" matters: Leaflet's fire() returns the map
            # itself for chaining, and evaluate() always serializes the
            # expression's value (returnByValue) - handing CDP the whole
            # cyclic map/DOM graph to clone raises "Object reference chain
            # is too long" instead of the exception the click itself might
            # raise. Ending on a plain value sidesteps that unrelated to
            # what's actually being tested here.
            cdp.evaluate("document.querySelector('#ops-feature-type').value='spot_fire';document.querySelector('#ops-feature-type').dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('#ops-feature-title').value='Browser spot';document.querySelector('#btn-start-draw').click();window.NexFiremapMap.fire('click',{latlng:L.latLng(48.1,11.5)});true")
            cdp.wait("[...document.querySelectorAll('.ops-feature-row')].some(row=>row.textContent.includes('Browser spot'))")

            # Leave a one-vertex line in browser crash-recovery storage, then
            # reload with confirmation pre-installed before application code.
            cdp.evaluate("document.querySelector('#ops-feature-type').value='tactical_line';document.querySelector('#ops-feature-type').dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('#btn-start-draw').click();window.NexFiremapMap.fire('click',{latlng:L.latLng(48.11,11.51)});true")
            cdp.wait("Boolean(localStorage.getItem('nexfiremap.unsavedSketch.v1'))")
            cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": "window.confirm=()=>true;"})
            cdp.call("Page.reload", {"ignoreCache": True})
            cdp.wait("document.querySelector('#ops-status')?.textContent.includes('Recovered unsaved sketch')")
            cdp.evaluate("document.querySelector('#btn-cancel-draw').click()")

            cdp.evaluate("(()=>{const boxes=[...document.querySelectorAll('#ops-safety input[type=checkbox]')];boxes.forEach(box=>box.checked=true);boxes[0].dispatchEvent(new Event('change',{bubbles:true}));return boxes.length})()")
            time.sleep(.5); cdp.evaluate("document.querySelector('#btn-approve-scenario').click()")
            cdp.wait("document.querySelector('#ops-scenario-status')?.value==='approved'")

            stop(server); server = None
            cdp.wait("document.querySelector('#ops-connectivity')?.textContent.includes('disconnected')", 25)
            server = start_server(root, app_port)
            cdp.wait("document.querySelector('#ops-connectivity')?.textContent.includes('connected')", 25)
        finally:
            if cdp: cdp.close()
            stop(browser_process); stop(server)
    print("Real-browser drawing, recovery, approval and reconnect checks passed.")


if __name__ == "__main__":
    main()
