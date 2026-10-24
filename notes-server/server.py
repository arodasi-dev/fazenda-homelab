#!/usr/bin/env python3
"""Servidor de notas da Fazenda Homelab.

Guarda cada ideia como um arquivo Markdown na pasta NOTES_DIR, para você poder
abrir os arquivos direto no servidor (ou por Samba/Syncthing) além de ver no app.
Só usa a biblioteca padrão do Python.

Variáveis de ambiente:
  NOTES_TOKEN      (obrigatória) senha que o app envia em "Authorization: Bearer ..."
  NOTES_DIR        pasta das notas (padrão: ./notas)
  ALLOWED_ORIGIN   origens permitidas no CORS, separadas por vírgula
                   (padrão: https://arodasi-dev.github.io)
  PORT             porta (padrão: 8787)
  HOST             endereço de escuta (padrão: 0.0.0.0)
"""
import hmac
import json
import os
import re
import sys
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote

NOTES_DIR = Path(os.environ.get("NOTES_DIR", "./notas"))
TOKEN = os.environ.get("NOTES_TOKEN", "")
ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGIN", "https://arodasi-dev.github.io").split(",") if o.strip()]
PORT = int(os.environ.get("PORT", "8787"))
HOST = os.environ.get("HOST", "0.0.0.0")

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")
STAGES = {"idea", "build", "live"}
MAX_BODY = 200_000
LOCK = threading.Lock()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def read_note(path: Path):
    raw = path.read_text(encoding="utf-8")
    title, updated, stage, body = path.stem, None, "idea", raw
    if raw.startswith("---\n"):
        head, sep, rest = raw[4:].partition("\n---\n")
        if sep:
            body = rest
            for line in head.splitlines():
                key, _, val = line.partition(":")
                if key.strip() == "title":
                    title = val.strip()
                elif key.strip() == "updated":
                    updated = val.strip()
                elif key.strip() == "stage" and val.strip() in STAGES:
                    stage = val.strip()
    if not updated:
        updated = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return {"id": path.stem, "title": title, "text": body.strip("\n"), "stage": stage, "updated": updated}


def write_note(note_id: str, title: str, text: str, updated: str, stage: str = "idea"):
    title = " ".join(title.split())[:120] or note_id
    content = f"---\ntitle: {title}\nstage: {stage}\nupdated: {updated}\n---\n{text}\n"
    fd, tmp = tempfile.mkstemp(dir=NOTES_DIR, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, NOTES_DIR / f"{note_id}.md")


class Handler(BaseHTTPRequestHandler):
    server_version = "FazendaNotas/1.0"

    # ----- utilidades -----
    def _cors(self):
        origin = self.headers.get("Origin", "")
        if origin in ORIGINS or "*" in ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin or "*")
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, PUT, DELETE, OPTIONS")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Access-Control-Allow-Private-Network", "true")

    def _send(self, code, payload=None):
        body = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        if body:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _authed(self):
        got = self.headers.get("Authorization", "")
        return hmac.compare_digest(got.encode(), f"Bearer {TOKEN}".encode())

    def _route(self):
        parts = [unquote(p) for p in urlparse(self.path).path.strip("/").split("/") if p]
        return parts

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ----- métodos -----
    def do_OPTIONS(self):
        self._send(204)

    def do_GET(self):
        parts = self._route()
        if parts == ["health"]:
            return self._send(200, {"ok": True})
        if not self._authed():
            return self._send(401, {"error": "token inválido"})
        if parts == ["notes"]:
            with LOCK:
                notes = [read_note(p) for p in NOTES_DIR.glob("*.md") if ID_RE.match(p.stem)]
            return self._send(200, notes)
        if len(parts) == 2 and parts[0] == "notes" and ID_RE.match(parts[1]):
            path = NOTES_DIR / f"{parts[1]}.md"
            if not path.exists():
                return self._send(404, {"error": "não encontrada"})
            with LOCK:
                return self._send(200, read_note(path))
        self._send(404, {"error": "rota desconhecida"})

    def do_PUT(self):
        parts = self._route()
        if not self._authed():
            return self._send(401, {"error": "token inválido"})
        if not (len(parts) == 2 and parts[0] == "notes" and ID_RE.match(parts[1])):
            return self._send(404, {"error": "rota desconhecida"})
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self._send(413, {"error": "corpo vazio ou grande demais"})
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            title, text = str(data.get("title", "")), str(data.get("text", ""))
            updated = str(data.get("updated", ""))
            stage = str(data.get("stage", "idea"))
        except Exception:
            return self._send(400, {"error": "JSON inválido"})
        if not ISO_RE.match(updated):
            updated = now_iso()
        if stage not in STAGES:
            stage = "idea"
        with LOCK:
            write_note(parts[1], title, text, updated, stage)
        self._send(200, {"ok": True, "id": parts[1], "updated": updated})

    def do_DELETE(self):
        parts = self._route()
        if not self._authed():
            return self._send(401, {"error": "token inválido"})
        if not (len(parts) == 2 and parts[0] == "notes" and ID_RE.match(parts[1])):
            return self._send(404, {"error": "rota desconhecida"})
        with LOCK:
            try:
                (NOTES_DIR / f"{parts[1]}.md").unlink()
            except FileNotFoundError:
                pass
        self._send(204)


def main():
    if not TOKEN or len(TOKEN) < 12:
        sys.exit("Defina NOTES_TOKEN com pelo menos 12 caracteres (ex.: openssl rand -hex 24).")
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Notas em {NOTES_DIR.resolve()} · http://{HOST}:{PORT} · origens: {', '.join(ORIGINS)}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
