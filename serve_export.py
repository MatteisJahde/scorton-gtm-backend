#!/usr/bin/env python3
"""One-shot server: serves the latest export CSV at /export-target-dataset."""

from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent / "data" / "export-target-dataset.csv"
HOST = "127.0.0.1"
PORT = 8012


class ExportHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.rstrip("/") != "/export-target-dataset":
            self.send_error(404, "Not Found")
            return

        if not CSV_PATH.exists():
            self.send_error(404, "CSV not generated yet")
            return

        data = CSV_PATH.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/csv")
        self.send_header("Content-Disposition", 'attachment; filename="target_dataset.csv"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    if not CSV_PATH.exists():
        raise SystemExit(f"Missing CSV: {CSV_PATH}")
    print(f"Serving {CSV_PATH} at http://{HOST}:{PORT}/export-target-dataset")
    HTTPServer((HOST, PORT), ExportHandler).serve_forever()
