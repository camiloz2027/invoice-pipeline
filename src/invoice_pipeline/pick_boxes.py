"""Choose a vendor's crop boxes by dragging on its pages, in the browser.

    python src/invoice_pipeline/pick_boxes.py <vendor>

Starts a small server that only your own computer can reach, opens a page that shows
the vendor's page images (data/interim/images/<vendor>/), and saves the box you draw
into data/vendors/<vendor>.toml when you click a button. Nothing leaves your computer.
Stop it with Ctrl+C.
"""

from __future__ import annotations

import argparse
import json
import logging
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from invoice_pipeline.vendors import VendorError, load_boxes, save_box, settings_path

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
MAX_BODY_BYTES = 1024

PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pick crop boxes</title>
<link rel="stylesheet" href="/app.css?t=__TOKEN__">
</head>
<body>
<header>
  <strong id="vendor"></strong>
  <button id="prev" type="button">&larr; Prev</button>
  <span id="position"></span>
  <button id="next" type="button">Next &rarr;</button>
  <span class="spacer"></span>
  <span id="selection">Drag on the page to select a zone</span>
  <button id="save-body" type="button" class="body" disabled>Save as body zone</button>
  <button id="save-id" type="button" class="id" disabled>Save as invoice-number zone</button>
  <button id="finish" type="button">Finish</button>
</header>
<p id="status" role="status"></p>
<p id="saved"></p>
<div class="row">
  <button id="whole" type="button">Select whole page</button>
  <button id="load-body" type="button" class="body" disabled>Edit saved body zone</button>
  <button id="load-id" type="button" class="id" disabled>Edit saved invoice-number zone</button>
  <span class="hint">Drag on the page to draw a zone. Drag its handles to resize it, or drag
  inside it to move it. You can start outside the page to reach its edges.</span>
</div>
<div id="stage"><div id="canvas"><img id="page" alt="Invoice page" draggable="false">
  <div id="box-body" class="box body"></div>
  <div id="box-id" class="box id"></div>
  <div id="box-sel" class="box sel"></div>
</div></div>
<script src="/app.js?t=__TOKEN__"></script>
</body>
</html>
"""

APP_CSS = """
:root { --bg: #fafafa; --fg: #1b1b1b; --line: #d0d0d0; --body: #1a6fd4; --id: #168a45; --sel: #d4531a; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #17181a; --fg: #e8e8e8; --line: #3a3c40; --body: #5aa2ff; --id: #4cc482; --sel: #ff8a55; }
}
body { margin: 0; padding: 0 16px 24px; background: var(--bg); color: var(--fg);
  font: 15px/1.4 system-ui, sans-serif; }
header { position: sticky; top: 0; z-index: 2; display: flex; flex-wrap: wrap; gap: 8px;
  align-items: center; padding: 10px 0; background: var(--bg); border-bottom: 1px solid var(--line); }
.spacer { flex: 1; }
button { padding: 6px 12px; border: 1px solid var(--line); border-radius: 6px;
  background: transparent; color: var(--fg); font: inherit; cursor: pointer; }
button:disabled { opacity: .4; cursor: default; }
button.body { border-color: var(--body); color: var(--body); }
button.id { border-color: var(--id); color: var(--id); }
#status { min-height: 1.4em; margin: 8px 0 0; }
#saved { margin: 0 0 8px; font-size: 13px; opacity: .8; }
.row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 4px; }
.hint { font-size: 13px; opacity: .75; }
#stage { display: inline-block; max-width: 100%; box-sizing: border-box; padding: 28px;
  cursor: crosshair; user-select: none; }
#canvas { position: relative; }
#page { display: block; max-width: 100%; height: auto; border: 1px solid var(--line); }
.box { position: absolute; display: none; box-sizing: border-box; pointer-events: none; }
.box.body { border: 2px solid var(--body); background: color-mix(in srgb, var(--body) 12%, transparent); }
.box.id { border: 2px solid var(--id); background: color-mix(in srgb, var(--id) 14%, transparent); }
.box.sel { border: 2px dashed var(--sel); pointer-events: auto; cursor: move; }
.handle { position: absolute; width: 14px; height: 14px; box-sizing: border-box;
  transform: translate(-50%, -50%); background: var(--sel); border: 2px solid var(--bg);
  border-radius: 3px; }
.handle[data-h="nw"] { left: 0; top: 0; cursor: nwse-resize; }
.handle[data-h="n"] { left: 50%; top: 0; cursor: ns-resize; }
.handle[data-h="ne"] { left: 100%; top: 0; cursor: nesw-resize; }
.handle[data-h="e"] { left: 100%; top: 50%; cursor: ew-resize; }
.handle[data-h="se"] { left: 100%; top: 100%; cursor: nwse-resize; }
.handle[data-h="s"] { left: 50%; top: 100%; cursor: ns-resize; }
.handle[data-h="sw"] { left: 0; top: 100%; cursor: nesw-resize; }
.handle[data-h="w"] { left: 0; top: 50%; cursor: ew-resize; }
"""

APP_JS = """
const token = new URLSearchParams(location.search).get("t");
const url = (path) => path + "?t=" + encodeURIComponent(token);
const $ = (id) => document.getElementById(id);
const BOXES = { body_box: $("box-body"), id_box: $("box-id") };

const MIN_SIZE = 0.005;
const clamp = (v, low = 0, high = 1) => Math.min(high, Math.max(low, v));

let pages = [], index = 0, saved = {}, selection = null;
// What the mouse is doing right now: drawing a new zone, moving it, or resizing it.
let drag = null;

for (const h of ["nw", "n", "ne", "e", "se", "s", "sw", "w"]) {
  const handle = document.createElement("div");
  handle.className = "handle";
  handle.dataset.h = h;
  $("box-sel").appendChild(handle);
}

function show(el, box) {
  if (!box) { el.style.display = "none"; return; }
  el.style.display = "block";
  el.style.left = box[0] * 100 + "%";
  el.style.top = box[1] * 100 + "%";
  el.style.width = (box[2] - box[0]) * 100 + "%";
  el.style.height = (box[3] - box[1]) * 100 + "%";
}

const fmt = (box) => "[" + box.map((v) => v.toFixed(3)).join(", ") + "]";

function refresh() {
  for (const key in BOXES) show(BOXES[key], saved[key]);
  show($("box-sel"), selection);
  $("selection").textContent = selection
    ? "Selected " + fmt(selection) : "Drag on the page to select a zone";
  $("save-body").disabled = $("save-id").disabled = !selection;
  $("load-body").disabled = !saved.body_box;
  $("load-id").disabled = !saved.id_box;
  const parts = [];
  if (saved.body_box) parts.push("body zone " + fmt(saved.body_box));
  if (saved.id_box) parts.push("invoice-number zone " + fmt(saved.id_box));
  $("saved").textContent = parts.length ? "Saved: " + parts.join("  |  ") : "Nothing saved yet.";
}

function goTo(i) {
  if (!pages.length) return;
  index = (i + pages.length) % pages.length;
  $("page").src = url("/img/" + index);
  $("position").textContent = (index + 1) + " / " + pages.length + "  " + pages[index];
}

// Mouse position as fractions of the page; outside the page it sticks to the nearest edge.
function point(event) {
  const r = $("page").getBoundingClientRect();
  return [clamp((event.clientX - r.left) / r.width), clamp((event.clientY - r.top) / r.height)];
}

function say(text) { $("status").textContent = text; }

$("stage").addEventListener("mousedown", (event) => {
  event.preventDefault();
  const p = point(event);
  if (event.target.classList.contains("handle")) {
    drag = { type: "resize", handle: event.target.dataset.h };
  } else if (event.target.id === "box-sel") {
    drag = { type: "move", from: p, original: selection.slice() };
  } else {
    drag = { type: "new", from: p };
    selection = null;
  }
});

window.addEventListener("mousemove", (event) => {
  if (!drag) return;
  const p = point(event);
  if (drag.type === "new") {
    selection = [Math.min(drag.from[0], p[0]), Math.min(drag.from[1], p[1]),
                 Math.max(drag.from[0], p[0]), Math.max(drag.from[1], p[1])];
  } else if (drag.type === "move") {
    const o = drag.original;
    const dx = clamp(p[0] - drag.from[0], -o[0], 1 - o[2]);
    const dy = clamp(p[1] - drag.from[1], -o[1], 1 - o[3]);
    selection = [o[0] + dx, o[1] + dy, o[2] + dx, o[3] + dy];
  } else {
    // Each handle moves the edges named in its code: n, s, w and/or e.
    const h = drag.handle;
    if (h.includes("w")) selection[0] = Math.min(p[0], selection[2] - MIN_SIZE);
    if (h.includes("e")) selection[2] = Math.max(p[0], selection[0] + MIN_SIZE);
    if (h.includes("n")) selection[1] = Math.min(p[1], selection[3] - MIN_SIZE);
    if (h.includes("s")) selection[3] = Math.max(p[1], selection[1] + MIN_SIZE);
  }
  refresh();
});

window.addEventListener("mouseup", () => {
  if (!drag) return;
  const tooSmall = selection
    && (selection[2] - selection[0] < MIN_SIZE || selection[3] - selection[1] < MIN_SIZE);
  if (drag.type === "new" && tooSmall) selection = null;
  drag = null;
  refresh();
});

$("whole").addEventListener("click", () => { selection = [0, 0, 1, 1]; refresh(); });
$("load-body").addEventListener("click", () => { selection = saved.body_box.slice(); refresh(); });
$("load-id").addEventListener("click", () => { selection = saved.id_box.slice(); refresh(); });

async function save(key) {
  if (!selection) return;
  const response = await fetch(url("/api/box"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key: key, box: selection }),
  });
  const data = await response.json();
  if (!response.ok) { say("Not saved: " + data.error); return; }
  saved = data.boxes;
  selection = null;
  say("Saved " + (key === "body_box" ? "body zone" : "invoice-number zone") + ".");
  refresh();
}

$("save-body").addEventListener("click", () => save("body_box"));
$("save-id").addEventListener("click", () => save("id_box"));
$("finish").addEventListener("click", async () => {
  await fetch(url("/api/stop"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  document.body.innerHTML = "<p style='padding:24px'>Finished. The server is stopped; "
    + "your saved zones are in data/vendors/. You can close this tab.</p>";
});
$("prev").addEventListener("click", () => goTo(index - 1));
$("next").addEventListener("click", () => goTo(index + 1));
window.addEventListener("keydown", (event) => {
  if (event.key === "ArrowLeft") goTo(index - 1);
  if (event.key === "ArrowRight") goTo(index + 1);
});

(async () => {
  const response = await fetch(url("/api/state"));
  const state = await response.json();
  pages = state.pages;
  saved = state.boxes;
  $("vendor").textContent = state.vendor;
  goTo(0);
  refresh();
})();
"""


def find_pages(images_dir: Path) -> list[Path]:
    """Every page image of a vendor, in a stable order."""
    return sorted(Path(images_dir).rglob("page_*.png"))


def _make_handler(vendor: str, vendors_dir: Path, pages: list[Path], token: str, port: int):
    """Build the request handler bound to one vendor and one random access token."""
    allowed_hosts = {f"{HOST}:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://{host}" for host in allowed_hosts}
    labels = [f"{page.parent.name} / {page.name}" for page in pages]

    class Handler(BaseHTTPRequestHandler):
        server_version = "pick_boxes"

        def log_message(self, format: str, *args: object) -> None:
            logger.debug("%s - %s", self.address_string(), format % args)

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # Nothing is cached and nothing outside this server can be loaded.
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'self'; style-src 'self'; "
                "img-src 'self'; connect-src 'self'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data: object) -> None:
            self._send(status, json.dumps(data).encode(), "application/json")

        def _allowed(self, query: dict[str, list[str]]) -> bool:
            """Only this page may use the server: right host, right origin, right token."""
            if self.headers.get("Host", "").lower() not in allowed_hosts:
                return False
            origin = self.headers.get("Origin")
            if origin is not None and origin not in allowed_origins:
                return False
            given = (query.get("t") or [""])[0]
            return secrets.compare_digest(given.encode(), token.encode())

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if not self._allowed(parse_qs(parsed.query)):
                self._send(403, b"Forbidden", "text/plain")
                return

            path = parsed.path
            if path == "/":
                page = PAGE_HTML.replace("__TOKEN__", token)
                self._send(200, page.encode(), "text/html; charset=utf-8")
            elif path == "/app.css":
                self._send(200, APP_CSS.encode(), "text/css; charset=utf-8")
            elif path == "/app.js":
                self._send(200, APP_JS.encode(), "text/javascript; charset=utf-8")
            elif path == "/api/state":
                self._json(
                    200,
                    {"vendor": vendor, "pages": labels, "boxes": load_boxes(vendors_dir, vendor)},
                )
            elif path.startswith("/img/") and path[5:].isdigit() and int(path[5:]) < len(pages):
                # Images are picked by position in our own list, never by a path.
                self._send(200, pages[int(path[5:])].read_bytes(), "image/png")
            else:
                self._send(404, b"Not found", "text/plain")

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if not self._allowed(parse_qs(parsed.query)):
                self._send(403, b"Forbidden", "text/plain")
                return
            if parsed.path not in ("/api/box", "/api/stop"):
                self._send(404, b"Not found", "text/plain")
                return
            # JSON only: a plain form from another site could not send this content type.
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self._json(415, {"error": "expected JSON"})
                return
            if parsed.path == "/api/stop":
                self._json(200, {"stopped": True})
                # shutdown() waits for the serving loop, so it cannot run in this request's thread.
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                length = -1
            if not 0 < length <= MAX_BODY_BYTES:
                self._json(400, {"error": "bad request size"})
                return

            try:
                data = json.loads(self.rfile.read(length))
                key, box = data["key"], data["box"]
                if not (isinstance(box, list) and len(box) == 4):
                    raise ValueError("box must be 4 numbers")
                boxes = save_box(vendors_dir, vendor, key, tuple(float(v) for v in box))
            except (ValueError, KeyError, TypeError, VendorError) as error:
                self._json(400, {"error": str(error)})
                return
            logger.info("Saved %s of %s: %s", key, vendor, list(boxes[key]))
            self._json(200, {"boxes": boxes})

    return Handler


def main() -> None:
    """Open the box picker for one vendor (manual run)."""
    from invoice_pipeline.config import load_config
    from invoice_pipeline.logging_setup import setup_logging

    parser = argparse.ArgumentParser(description="Choose a vendor's crop boxes in the browser.")
    parser.add_argument("vendor", help="vendor folder name, e.g. the one in data/raw/")
    parser.add_argument("--no-browser", action="store_true", help="only print the address")
    args = parser.parse_args()

    config = load_config()
    setup_logging(config)
    try:
        settings_path(config.vendors_dir, args.vendor)
    except VendorError as error:
        parser.error(str(error))

    images_dir = config.images_dir / args.vendor
    pages = find_pages(images_dir) if images_dir.is_dir() else []
    if not pages:
        parser.error(
            f"No page images in {images_dir}. Put the PDFs in data/raw/{args.vendor}/ "
            "and run pdf_to_images.py first."
        )

    token = secrets.token_urlsafe(24)
    # Port 0 lets the system pick a free port.
    server = ThreadingHTTPServer((HOST, 0), BaseHTTPRequestHandler)
    port = server.server_address[1]
    server.RequestHandlerClass = _make_handler(args.vendor, config.vendors_dir, pages, token, port)

    address = f"http://{HOST}:{port}/?t={token}"
    logger.info("Open %s (only this computer can reach it). Stop with Ctrl+C.", address)
    if not args.no_browser:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    # Re-import under the real module name so this module's logger falls under
    # the configured "invoice_pipeline" logger (when run directly, __name__ is
    # "__main__" and its messages would be lost).
    from invoice_pipeline.pick_boxes import main as _main

    _main()
