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

    # placar dos joguinhos (SQLite): soma moedas, guarda o melhor e ignora partida repetida (mesmo pid)
    code, pl = call("GET", "/placar")
    assert code == 200 and pl["moedas_total"] == 0 and pl["jogos"]["cobrinha"] == {"melhor": 0, "partidas": 0, "moedas": 0}, pl
    code, pl = call("POST", "/placar", {"jogo": "cobrinha", "pid": "aaaaaaaa1", "pontos": 7, "moedas": 9})
    assert code == 200 and pl["jogos"]["cobrinha"] == {"melhor": 7, "partidas": 1, "moedas": 9}, pl
    call("POST", "/placar", {"jogo": "cobrinha", "pid": "aaaaaaaa2", "pontos": 4, "moedas": 4})
    code, pl = call("POST", "/placar", {"jogo": "memoria", "pid": "aaaaaaaa3", "pontos": 90, "moedas": 3})
    assert pl["jogos"]["cobrinha"] == {"melhor": 7, "partidas": 2, "moedas": 13} and pl["moedas_total"] == 16, pl
    code, pl = call("POST", "/placar", {"jogo": "cobrinha", "pid": "aaaaaaaa1", "pontos": 7, "moedas": 9})   # reenvio
    assert code == 200 and pl["moedas_total"] == 16, pl
    for ruim in [{"jogo": "xadrez", "pid": "bbbbbbbb1", "pontos": 1, "moedas": 1},
                 {"jogo": "cobrinha", "pid": "curto", "pontos": 1, "moedas": 1},
                 {"jogo": "cobrinha", "pid": "bbbbbbbb2", "pontos": -1, "moedas": 1},
                 {"jogo": "memoria", "pid": "bbbbbbbb3", "pontos": 1, "moedas": 999},
                 {"jogo": ["a"], "pid": "bbbbbbbb4", "pontos": 1, "moedas": 1},
                 {"jogo": "cobrinha", "pid": "bbbbbbbb5", "pontos": "x", "moedas": 1}]:
        assert call("POST", "/placar", ruim)[0] == 400, ruim
    assert call("GET", "/placar")[1]["moedas_total"] == 16
    assert call("GET", "/placar", headers=nohdr)[0] == 401 and call("POST", "/placar", {"jogo": "cobrinha"}, headers=nohdr)[0] == 401
    print("placar ok")
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

# ---------- crédito: API de custos falsa da Anthropic ----------
COST_CALLS = []
COST_MODE = {"mode": "ok"}


class FakeAnthropic(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        COST_CALLS.append((self.path, self.headers.get("x-api-key"), self.headers.get("anthropic-version")))
        if COST_MODE["mode"] == "401":
            self.send_response(401)
            body = b'{"error":{"message":"invalid x-api-key sk-ant-admin01-test"}}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        this_month = time.strftime("%Y-%m", time.gmtime())
        page2 = "page=P2" in self.path
        if not page2:
            data = {"data": [{"starting_at": "2020-01-01T00:00:00Z", "ending_at": "2020-01-02T00:00:00Z",
                              "results": [{"amount": "1250.5", "currency": "USD"}, {"amount": "49.5", "currency": "USD"}]}],
                    "has_more": True, "next_page": "P2"}
        else:
            data = {"data": [{"starting_at": this_month + "-01T00:00:00Z", "ending_at": this_month + "-02T00:00:00Z",
                              "results": [{"amount": "200", "currency": "USD"}, {"amount": "999", "currency": "EUR"}]}],
                    "has_more": False, "next_page": None}
        b = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


fa = HTTPServer(("127.0.0.1", 0), FakeAnthropic)
threading.Thread(target=fa.serve_forever, daemon=True).start()
env2 = dict(env, PORT="8796", ANTHROPIC_ADMIN_KEY="sk-ant-admin01-test", CREDIT_TOTAL_USD="50", CREDIT_SINCE="2020-01-01",
            ANTHROPIC_API_BASE=f"http://127.0.0.1:{fa.server_port}")
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")], env=env2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
BASE = "http://127.0.0.1:8796"
try:
    for _ in range(40):
        try:
            if call("GET", "/health")[0] == 200:
                break
        except Exception:
            time.sleep(0.25)
    code, cr = call("GET", "/credit")
    assert code == 200 and cr["enabled"] is True, cr
    # (1250.5+49.5+200) centavos = 1500 -> US$ 15,00 gastos; sobram 35
    assert cr["spent"] == 15.0 and cr["remaining"] == 35.0 and cr["total"] == 50.0 and cr["month"] == 2.0, cr
    assert len(COST_CALLS) == 2 and "page=P2" in COST_CALLS[1][0], COST_CALLS      # paginou
    assert all(c[1] == "sk-ant-admin01-test" and c[2] == "2023-06-01" for c in COST_CALLS)
    assert "starting_at=2020-01-01T00:00:00Z" in COST_CALLS[0][0] and "limit=31" in COST_CALLS[0][0]
    n = len(COST_CALLS)
    assert call("GET", "/credit")[1]["spent"] == 15.0 and len(COST_CALLS) == n         # cache: não consulta de novo
    assert call("GET", "/credit", headers={"Content-Type": "application/json"})[0] == 401   # exige token
    assert "sk-ant" not in json.dumps(cr)
    print("crédito ok")
finally:
    proc.terminate()

COST_MODE["mode"] = "401"
env2["PORT"] = "8795"
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")], env=env2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
BASE = "http://127.0.0.1:8795"
try:
    for _ in range(40):
        try:
            if call("GET", "/health")[0] == 200:
                break
        except Exception:
            time.sleep(0.25)
    code, cr = call("GET", "/credit")
    assert code == 200 and cr["enabled"] is True and "401" in cr["error"] and "sk-ant" not in json.dumps(cr), cr   # erro sem vazar a chave
    print("crédito com erro ok")
finally:
    proc.terminate()

env3 = dict(env, PORT="8794")
env3.pop("ANTHROPIC_ADMIN_KEY", None)
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")], env=env3, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
BASE = "http://127.0.0.1:8794"
try:
    for _ in range(40):
        try:
            if call("GET", "/health")[0] == 200:
                break
        except Exception:
            time.sleep(0.25)
    assert call("GET", "/credit")[1] == {"enabled": False}
    print("crédito desligado ok")
finally:
    proc.terminate()

# ---------- gasto da secretária (lê o banco SQLite dela) ----------
import sqlite3
sec_dir = tempfile.mkdtemp()
sec_db = os.path.join(sec_dir, "secretaria.db")
con = sqlite3.connect(sec_db)
con.execute("CREATE TABLE usage (month TEXT PRIMARY KEY, calls INTEGER NOT NULL DEFAULT 0, tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0)")
con.execute("INSERT INTO usage VALUES (?,?,?,?)", (time.strftime("%Y-%m"), 7, 100000, 20000))
con.execute("INSERT INTO usage VALUES ('2000-01', 1, 999999999, 999999999)")
con.commit()
con.close()
env5 = dict(env, PORT="8792", SECRETARIA_DB=sec_db, SECRETARIA_BUDGET_USD="1.5")
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")], env=env5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
BASE = "http://127.0.0.1:8792"
try:
    for _ in range(40):
        try:
            if call("GET", "/health")[0] == 200:
                break
        except Exception:
            time.sleep(0.25)
    code, g = call("GET", "/gasto")
    # 100000 tokens in x US$1/Mtok + 20000 out x US$5/Mtok = 0.1 + 0.1 = 0.2 (só o mês atual conta)
    assert code == 200 and g["enabled"] and g["calls"] == 7 and g["cost"] == 0.2 and g["budget"] == 1.5 and g["active"] is True, g
    assert call("GET", "/gasto", headers={"Content-Type": "application/json"})[0] == 401
    assert "credit_remaining" not in g   # sem SECRETARIA_CREDIT_USD não calcula saldo
    print("gasto da secretária ok")
finally:
    proc.terminate()
# saldo = crédito comprado - gasto acumulado (só do mês da compra em diante)
env7 = dict(env, PORT="8790", SECRETARIA_DB=sec_db, SECRETARIA_CREDIT_USD="5", SECRETARIA_CREDIT_FROM=time.strftime("%Y-%m"))
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")], env=env7, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
BASE = "http://127.0.0.1:8790"
try:
    for _ in range(40):
        try:
            if call("GET", "/health")[0] == 200:
                break
        except Exception:
            time.sleep(0.25)
    g = call("GET", "/gasto")[1]
    assert g["credit_total"] == 5.0 and g["credit_spent"] == 0.2 and g["credit_remaining"] == 4.8, g   # o mês 2000-01 fica de fora
    assert g["credit_months"] == [{"month": time.strftime("%Y-%m"), "cost": 0.2}], g
    print("saldo da secretária ok")
finally:
    proc.terminate()
env6 = dict(env, PORT="8791", SECRETARIA_DB=os.path.join(sec_dir, "nao-existe.db"))
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")], env=env6, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
BASE = "http://127.0.0.1:8791"
try:
    for _ in range(40):
        try:
            if call("GET", "/health")[0] == 200:
                break
        except Exception:
            time.sleep(0.25)
    assert call("GET", "/gasto")[1] == {"enabled": False}
    print("sem banco da secretária ok")
finally:
    proc.terminate()

# ---------- saldo digitado à mão (sem chave de administrador) ----------
env4 = dict(env, PORT="8793")
env4.pop("ANTHROPIC_ADMIN_KEY", None)
env4["NOTES_DIR"] = tempfile.mkdtemp()
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "server.py")], env=env4, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
BASE = "http://127.0.0.1:8793"
try:
    for _ in range(40):
        try:
            if call("GET", "/health")[0] == 200:
                break
        except Exception:
            time.sleep(0.25)
    assert call("GET", "/credit")[1] == {"enabled": False}
    code, r = call("PUT", "/credit", {"remaining": 35.2})
    assert code == 200 and r["remaining"] == 35.2 and r["manual"] is True and r["as_of"].endswith("Z"), r
    assert call("GET", "/credit")[1]["remaining"] == 35.2
    assert call("PUT", "/credit", {"remaining": -1})[0] == 400
    assert call("PUT", "/credit", {"remaining": "abc"})[0] == 400
    assert call("PUT", "/credit", {"x": 1})[0] == 400
    assert call("PUT", "/credit", {"remaining": 5}, headers={"Content-Type": "application/json"})[0] == 401
    assert call("GET", "/notes")[1] == []   # o arquivo do saldo não aparece como nota
    print("saldo manual ok")
finally:
    proc.terminate()

print("TUDO OK")
