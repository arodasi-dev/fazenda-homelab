#!/usr/bin/env python3
"""Testes do servidor da Fazenda (sem Docker de verdade).

Sobe um Docker falso e o servidor, e confere status, logs, reinício e notas.
Rode com:  python test_server.py
"""
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("NOTES_TOKEN", "token-de-teste-123456")
import server as S  # noqa: E402

# ---------- parsers ----------
assert S.parse_cpu("cpu  100 0 50 800 50 0 0 0 0 0") == (850, 1000)
meminfo = "MemTotal:       16000000 kB\nMemFree: 1000 kB\nMemAvailable:    4000000 kB\n"
assert round(S.parse_mem(meminfo), 1) == 75.0
assert S.parse_mem("MemTotal: 1 kB\n") is None
netdev = ("Inter-|   Receive ...\n face |bytes ...\n"
          "    lo: 999 0 0 0 0 0 0 0 999 0 0 0 0 0 0 0\n"
          "  eth0: 1000 1 0 0 0 0 0 0 2000 1 0 0 0 0 0 0\n"
          " veth1: 5000 1 0 0 0 0 0 0 5000 1 0 0 0 0 0 0\n"
          "docker0: 7000 1 0 0 0 0 0 0 7000 1 0 0 0 0 0 0\n")
assert S.parse_net(netdev) == 3000
assert S.group_of("adguardhome", {}) == "dns"
assert S.group_of("uptime-kuma", {}) == "monitor"
assert S.group_of("vaultwarden", {}) == "security"
assert S.group_of("yt-music-agent", {}) == "agent"
assert S.group_of("health-app", {}) == "app"
assert S.group_of("postgres", {}) == "db"
assert S.group_of("qualquercoisa", {"farm.group": "security"}) == "security"
assert S.group_of("adguardhome", {"farm.group": "invalido"}) == "dns"
frame = lambda s, t: struct.pack(">BxxxI", s, len(t)) + t
assert S.demux_logs(frame(1, b"ola\n") + frame(2, b"erro\n")) == "ola\nerro\n"
assert S.demux_logs(b"texto puro (tty)") == "texto puro (tty)"
print("parsers ok")

# ---------- Docker falso ----------
CONTS = [
    {"Id": "a" * 64, "Names": ["/adguardhome"], "State": "running", "Status": "Up 3 days", "Labels": {}},
    {"Id": "b" * 64, "Names": ["/yt-music-agent"], "State": "running", "Status": "Up 2 hours (healthy)", "Labels": {}},
    {"Id": "c" * 64, "Names": ["/health-agent"], "State": "exited", "Status": "Exited (1) 5 minutes ago", "Labels": {}},
    {"Id": "d" * 64, "Names": ["/uptime-kuma"], "State": "running", "Status": "Up 3 days (unhealthy)", "Labels": {}},
    {"Id": "e" * 64, "Names": ["/fazenda-notas"], "State": "running", "Status": "Up 1 hour", "Labels": {}},
]
RESTARTED = []


class FakeDocker(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/containers/json"):
            return self._json(CONTS)
        if "/stats" in self.path:
            return self._json({"cpu_stats": {"cpu_usage": {"total_usage": 300}, "system_cpu_usage": 2000, "online_cpus": 4},
                               "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000},
                               "memory_stats": {"usage": 500_000_000, "limit": 1_000_000_000, "stats": {"inactive_file": 100_000_000}}})
        if "/logs" in self.path:
            b = frame(1, b"\x1b[32mINFO\x1b[0m iniciado\n") + frame(2, b"aviso: algo\n")
            self.send_response(200)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            return self.wfile.write(b)
        self._json({}, 404)

    def do_POST(self):
        RESTARTED.append(self.path)
        self.send_response(204)
        self.end_headers()


fd = HTTPServer(("127.0.0.1", 0), FakeDocker)
threading.Thread(target=fd.serve_forever, daemon=True).start()

# ---------- servidor ----------
tmp = tempfile.mkdtemp()
env = dict(os.environ, NOTES_DIR=tmp, PORT="8799", HOST="127.0.0.1", STATS_EVERY="1",
           DOCKER_HOST=f"http://127.0.0.1:{fd.server_port}", ENABLE_RESTART="1",
           ALLOWED_ORIGIN="http://localhost:8123")
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
BASE, H = "http://127.0.0.1:8799", {"Authorization": "Bearer " + env["NOTES_TOKEN"], "Content-Type": "application/json"}


def call(method, path, body=None, headers=H):
    req = urllib.request.Request(BASE + path, method=method, headers=headers, data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw and r.headers.get("Content-Type", "").startswith("application/json") else raw.decode())
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw else None)


try:
    for _ in range(40):
        try:
            if call("GET", "/health")[0] == 200:
                break
        except Exception:
            time.sleep(0.25)
    time.sleep(2.5)  # espera a primeira leitura

    code, st = call("GET", "/status")
    assert code == 200, st
    names = [c["name"] for c in st["containers"]]
    assert names == ["adguardhome", "health-agent", "uptime-kuma", "yt-music-agent"], names  # fazenda-notas escondido
    by = {c["name"]: c for c in st["containers"]}
    assert by["adguardhome"]["group"] == "dns" and by["adguardhome"]["cpu"] == 20.0, by["adguardhome"]  # (300-100)/(2000-1000) = 20% do total
    assert by["adguardhome"]["mem_mb"] == 400.0 and by["adguardhome"]["mem_pct"] == 40.0
    assert by["yt-music-agent"]["health"] == "healthy" and by["uptime-kuma"]["health"] == "unhealthy"
    assert by["health-agent"]["state"] == "exited" and by["health-agent"]["cpu"] is None
    assert "id" not in by["adguardhome"]
    assert st["restart_enabled"] is True and st["docker_error"] is None
    print("status ok")

    code, txt = call("GET", "/containers/adguardhome/logs?tail=5")
    assert code == 200 and "INFO iniciado" in txt and "\x1b" not in txt and "aviso: algo" in txt, txt
    assert call("GET", "/containers/fazenda-notas/logs")[0] == 404  # escondido
    assert call("GET", "/containers/../etc/logs")[0] == 404
    print("logs ok")

    assert call("POST", "/containers/adguardhome/restart")[0] == 200
    assert RESTARTED and RESTARTED[-1].startswith("/containers/" + "a" * 64 + "/restart"), RESTARTED
    assert call("POST", "/containers/naoexiste/restart")[0] == 404
    assert call("POST", "/containers/fazenda-notas/restart")[0] == 404
    print("restart ok")

    nohdr = {"Content-Type": "application/json"}
    assert call("GET", "/status", headers=nohdr)[0] == 401
    assert call("POST", "/containers/adguardhome/restart", headers=nohdr)[0] == 401
    assert call("GET", "/health", headers=nohdr)[0] == 200
    print("auth ok")

    assert call("PUT", "/notes/n1", {"title": "Bot", "text": "x", "stage": "build", "updated": "2026-10-24T12:00:00.000Z"})[0] == 200
    code, notes = call("GET", "/notes")
    assert notes == [{"id": "n1", "title": "Bot", "text": "x", "stage": "build", "updated": "2026-10-24T12:00:00.000Z"}], notes
    assert call("PUT", "/notes/n2", {"title": "Sem fase", "text": "y", "stage": "lixo"})[0] == 200
    assert [n for n in call("GET", "/notes")[1] if n["id"] == "n2"][0]["stage"] == "idea"
    assert call("DELETE", "/notes/n1")[0] == 204
    print("notas ok")
finally:
    proc.terminate()

# ---------- reinício desativado ----------
env["ENABLE_RESTART"] = "0"
env["PORT"] = "8798"
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
BASE = "http://127.0.0.1:8798"
try:
    for _ in range(40):
        try:
            if call("GET", "/health")[0] == 200:
                break
        except Exception:
            time.sleep(0.25)
    time.sleep(2)
    code, body = call("POST", "/containers/adguardhome/restart")
    assert code == 403, (code, body)
    assert call("GET", "/status")[1]["restart_enabled"] is False
    print("reinício desativado ok")
finally:
    proc.terminate()

# ---------- ocioso: para de consultar o Docker e retoma quando alguém pede ----------
env.update(PORT="8797", IDLE_AFTER="2", STATS_EVERY="1", ENABLE_RESTART="0")
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
BASE = "http://127.0.0.1:8797"
try:
    for _ in range(40):
        try:
            if call("GET", "/health")[0] == 200:
                break
        except Exception:
            time.sleep(0.25)
    time.sleep(2)
    t1 = call("GET", "/status")[1]["t"]
    time.sleep(20)  # ocioso: passa de FRESH (15s); o Docker não deve ser consultado
    t0 = time.time()
    code, st2 = call("GET", "/status")
    waited = time.time() - t0
    assert code == 200 and st2["t"] > t1, (t1, st2["t"])
    assert 0.2 < waited < 9, waited  # esperou a leitura nova, sem travar
    assert st2["containers"], st2
    print("ocioso/retomada ok (esperou %.1fs)" % waited)
finally:
    proc.terminate()

print("TUDO OK")
