"""Live demo UI: python app.py [--bills data/train/bills]  ->  http://localhost:8000"""
import argparse, json, uuid, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from extract import extract_bill, problems
from llm import MODEL, image_data_url
from qa import answer_questions

EXTS = (".png", ".jpg", ".jpeg")
UPLOADS = Path(".cache/uploads")
SESSIONS: dict[str, dict] = {}  # sid -> {path, l1, hist}
BILLS = Path("data/train/bills")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body: bytes, ctype="application/json", code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def js(self, obj, code=200):
        self.send(json.dumps(obj).encode(), code=code)

    def body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length", 0)))

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            self.send(Path("ui.html").read_bytes(), "text/html; charset=utf-8")
        elif u.path == "/bills":
            self.js({"model": MODEL, "bills": sorted(p.name for p in BILLS.iterdir() if p.suffix.lower() in EXTS)})
        elif u.path == "/img":
            name = parse_qs(u.query).get("bill", [""])[0]
            sid = parse_qs(u.query).get("sid", [""])[0]
            path = SESSIONS[sid]["path"] if sid in SESSIONS else BILLS / Path(name).name
            if not path.is_file():
                return self.send(b"not found", "text/plain", 404)
            mime = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
            self.send(path.read_bytes(), mime)
        else:
            self.send(b"not found", "text/plain", 404)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            if u.path == "/extract":  # raw upload body, or ?bill=<name> for a bundled bill
                q = parse_qs(u.query)
                if "bill" in q:
                    path = BILLS / Path(q["bill"][0]).name
                else:
                    UPLOADS.mkdir(parents=True, exist_ok=True)
                    ext = Path(q.get("name", ["x.png"])[0]).suffix.lower()
                    ext = ext if ext in EXTS else ".png"
                    path = UPLOADS / f"{uuid.uuid4().hex}{ext}"
                    path.write_bytes(self.body())
                l1, hist = extract_bill(path)
                sid = uuid.uuid4().hex
                SESSIONS[sid] = {"path": path, "l1": l1, "hist": hist}
                self.js({"sid": sid, "l1": l1, "hist": hist, "issues": problems(l1)})
            elif u.path == "/ask":
                req = json.loads(self.body())
                s = SESSIONS[req["sid"]]
                ans = answer_questions(s["l1"], s["hist"], [("Q1", req["question"])], image_data_url(s["path"]))
                self.js({"answer": ans["Q1"]})
            else:
                self.send(b"not found", "text/plain", 404)
        except Exception as e:
            self.js({"error": f"{type(e).__name__}: {e}"}, 500)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bills", type=Path, default=BILLS)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-open", action="store_true", help="do not open the browser")
    a = ap.parse_args()
    BILLS = a.bills
    print(f"Open http://localhost:{a.port}  (model: {MODEL})")
    if not a.no_open:
        webbrowser.open(f"http://localhost:{a.port}")
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()
