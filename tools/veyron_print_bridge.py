"""
Minimal local print bridge for Veyron POS (development / reference).

Listens on http://127.0.0.1:19191/print
Accepts POST with JSON body: {"schema":"veyron.print/1","receipt":{...}}

Responds with Access-Control-Allow-Origin: * so the browser may POST from the app origin.

A production bridge should translate `receipt` to ESC/POS bytes and send to a USB/thermal printer.
Run: python tools/veyron_print_bridge.py
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HOST = "127.0.0.1"
PORT = 19191
LAST_RECEIPT_FILE = Path(__file__).resolve().parents[1] / "instance" / "last_bridge_receipt.json"


class PrintBridgeHandler(BaseHTTPRequestHandler):
    server_version = "VeyronPrintBridge/1.0"

    def log_message(self, format: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self) -> None:
        if self.path not in ("/print", "/print/"):
            self.send_response(404)
            self._cors()
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_response(400)
            self._cors()
            self.end_headers()
            return

        if data.get("schema") != "veyron.print/1":
            self.send_response(422)
            self._cors()
            self.end_headers()
            return

        receipt = data.get("receipt") or {}
        LAST_RECEIPT_FILE.parent.mkdir(parents=True, exist_ok=True)
        LAST_RECEIPT_FILE.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

        lines = receipt.get("lines") or []
        sale = receipt.get("sale") or {}
        print("--- receipt", sale.get("id"), receipt.get("business_name"), "---")
        for line in lines:
            sku = line.get("sku") or ""
            suf = line.get("variant_sku_suffix") or ""
            name = line.get("name") or ""
            qty = line.get("quantity")
            print(f"  {qty}x {name} ({sku}{suf})")
        print("  total:", sale.get("total"))
        print("(also wrote", LAST_RECEIPT_FILE, ")")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(b'{"ok":true}')


def main() -> None:
    httpd = HTTPServer((HOST, PORT), PrintBridgeHandler)
    print(f"Veyron print bridge on http://{HOST}:{PORT}/print (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
