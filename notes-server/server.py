#!/usr/bin/env python3
"""Servidor da Fazenda Homelab: notas das ideias + status do servidor.

- Notas: cada ideia vira um arquivo Markdown em NOTES_DIR.
- Status: CPU, RAM, disco e rede do servidor (lidos de /proc) e a lista de
  containers Docker (lida por um docker-socket-proxy somente leitura).

Só usa a biblioteca padrão do Python.

Variáveis de ambiente:
  NOTES_TOKEN       (obrigatória) senha enviada pelo app em "Authorization: Bearer ..."
  NOTES_DIR         pasta das notas (padrão: ./notas)
  ALLOWED_ORIGIN    origens permitidas no CORS, separadas por vírgula
                    (padrão: https://arodasi-dev.github.io)
  PORT / HOST       porta e endereço de escuta (padrão: 8787 / 0.0.0.0)
  DOCKER_HOST       endereço do docker-socket-proxy (padrão: http://127.0.0.1:2375)
  ENABLE_RESTART    "1" deixa o app reiniciar containers (o proxy também precisa permitir)
  HIDE_CONTAINERS   nomes de containers que não aparecem na fazenda
                    (padrão: fazenda-notas,fazenda-docker-proxy)
  DISK_PATH         caminho usado para medir o disco (padrão: /)
  STATS_EVERY       segundos entre leituras (padrão: 5)

Crédito da API da Anthropic (opcional; só funciona em contas de organização):
  ANTHROPIC_ADMIN_KEY  chave de administrador (sk-ant-admin...). Fica só aqui, nunca vai para o app.
  CREDIT_TOTAL_USD     quanto de crédito você comprou (ex.: 50)
  CREDIT_SINCE         data da compra, AAAA-MM-DD: o gasto é somado desde esse dia

Gasto da secretária (o mesmo número do /gasto do Telegram), lido do banco dela:
  SECRETARIA_DB                 caminho do banco dentro do container (padrão: /secretaria/secretaria.db)
  SECRETARIA_BUDGET_USD         limite mensal (o mesmo LLM_MONTHLY_BUDGET_USD dela; padrão 1.5)
  SECRETARIA_PRICE_IN_PER_MTOK  preço por milhão de tokens de entrada (padrão 1.0)
  SECRETARIA_PRICE_OUT_PER_MTOK preço por milhão de tokens de saída (padrão 5.0)
  SECRETARIA_CREDIT_USD         (opcional) crédito que você comprou; o saldo = crédito - gasto acumulado dela
  SECRETARIA_CREDIT_FROM        (opcional) mês da compra, AAAA-MM: só conta o gasto desse mês em diante

Fliperama (joguinhos da fazenda): o placar e as moedas ficam em NOTES_DIR/placar.db (SQLite),
sem configuração nenhuma.
"""
import hmac
import http.client
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote, quote, parse_qs

VERSION = "1.12.0"
NOTES_DIR = Path(os.environ.get("NOTES_DIR", "./notas"))
TOKEN = os.environ.get("NOTES_TOKEN", "")
ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGIN", "https://arodasi-dev.github.io").split(",") if o.strip()]
PORT = int(os.environ.get("PORT", "8787"))
HOST = os.environ.get("HOST", "0.0.0.0")
DOCKER_HOST = os.environ.get("DOCKER_HOST", "http://127.0.0.1:2375")
ENABLE_RESTART = os.environ.get("ENABLE_RESTART", "0") == "1"
HIDE = {x.strip() for x in os.environ.get("HIDE_CONTAINERS", "fazenda-notas,fazenda-docker-proxy").split(",") if x.strip()}
DISK_PATH = os.environ.get("DISK_PATH", "/")
STATS_EVERY = max(1.0, float(os.environ.get("STATS_EVERY", "5")))
ADMIN_KEY = os.environ.get("ANTHROPIC_ADMIN_KEY", "").strip()
CREDIT_TOTAL = float(os.environ.get("CREDIT_TOTAL_USD", "0") or 0)
CREDIT_SINCE = os.environ.get("CREDIT_SINCE", "").strip()
API_BASE = os.environ.get("ANTHROPIC_API_BASE", "https://api.anthropic.com").rstrip("/")
CREDIT_TTL = 300  # a API de custos aceita ~1 consulta por minuto; 5 min sobra
SEC_DB = os.environ.get("SECRETARIA_DB", "/secretaria/secretaria.db")
SEC_BUDGET = float(os.environ.get("SECRETARIA_BUDGET_USD", "1.5") or 1.5)
SEC_PRICE_IN = float(os.environ.get("SECRETARIA_PRICE_IN_PER_MTOK", "1.0") or 1.0)
SEC_PRICE_OUT = float(os.environ.get("SECRETARIA_PRICE_OUT_PER_MTOK", "5.0") or 5.0)
SEC_CREDIT = float(os.environ.get("SECRETARIA_CREDIT_USD", "0") or 0)
SEC_CREDIT_FROM = os.environ.get("SECRETARIA_CREDIT_FROM", "").strip()

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
STAGES = {"idea", "build", "live"}
MAX_BODY = 200_000
LOCK = threading.Lock()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------------------------------------------------------------- notas
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


# ---------------------------------------------------------------- host (/proc)
def parse_cpu(line: str):
    """'cpu  user nice system idle iowait irq softirq steal ...' -> (ocioso, total)"""
    p = [int(x) for x in line.split()[1:9]]
    return p[3] + p[4], sum(p)


def parse_mem(text: str):
    """/proc/meminfo -> % de RAM em uso"""
    m = {}
    for line in text.splitlines():
        k, _, v = line.partition(":")
        if v.strip():
            m[k] = int(v.split()[0])
    total, avail = m.get("MemTotal"), m.get("MemAvailable")
    if not total or avail is None:
        return None
    return max(0.0, min(100.0, (total - avail) / total * 100))


SKIP_IF = ("lo", "veth", "docker", "br-", "virbr", "tailscale")


def parse_net(text: str):
    """/proc/net/dev -> bytes (rx+tx) das interfaces físicas"""
    total = 0
    for line in text.splitlines()[2:]:
        name, _, rest = line.partition(":")
        name = name.strip()
        if not rest or name == "lo" or name.startswith(SKIP_IF[1:]):
            continue
        f = rest.split()
        total += int(f[0]) + int(f[8])
    return total


_prev = {"cpu": None, "net": None}


def read_file(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def host_metrics():
    out = {"cpu": None, "ram": None, "disk": None, "net_mbps": None, "uptime": None}
    now = time.time()
    try:
        idle, total = parse_cpu(read_file("/proc/stat").splitlines()[0])
        if _prev["cpu"]:
            di, dt = idle - _prev["cpu"][0], total - _prev["cpu"][1]
            if dt > 0:
                out["cpu"] = round(max(0.0, min(100.0, (1 - di / dt) * 100)), 1)
        _prev["cpu"] = (idle, total)
    except Exception:
        pass
    try:
        v = parse_mem(read_file("/proc/meminfo"))
        out["ram"] = None if v is None else round(v, 1)
    except Exception:
        pass
    try:
        du = shutil.disk_usage(DISK_PATH)
        out["disk"] = round(du.used / du.total * 100, 1)
    except Exception:
        pass
    try:
        b = parse_net(read_file("/proc/net/dev"))
        if _prev["net"]:
            pb, pt = _prev["net"]
            if now > pt and b >= pb:
                out["net_mbps"] = round((b - pb) * 8 / (now - pt) / 1e6, 2)
        _prev["net"] = (b, now)
    except Exception:
        pass
    try:
        out["uptime"] = int(float(read_file("/proc/uptime").split()[0]))
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- docker
def docker(method, path, timeout=10):
    u = urlparse(DOCKER_HOST)
    conn = http.client.HTTPConnection(u.hostname or "127.0.0.1", u.port or 2375, timeout=timeout)
    try:
        conn.request(method, path)
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


RULES = [
    ("dns", ("adguard", "pihole", "pi-hole", "unbound", "dnsmasq", "coredns")),
    ("monitor", ("kuma", "uptime", "grafana", "prometheus", "netdata", "glances", "dozzle", "portainer")),
    ("security", ("vaultwarden", "bitwarden", "authelia", "authentik", "wireguard", "crowdsec", "fail2ban")),
    ("db", ("postgres", "mysql", "mariadb", "mongo", "redis", "influx", "-db", "_db")),
    ("agent", ("agent", "bot", "worker", "secretaria", "alexa", "gemini")),
]
GROUPS = {"dns", "monitor", "security", "db", "agent", "app"}


def group_of(name, labels):
    g = (labels or {}).get("farm.group", "")
    if g in GROUPS:
        return g
    n = name.lower()
    for group, words in RULES:
        if any(w in n for w in words):
            return group
    return "app"


def container_stats(cid):
    try:
        st, data = docker("GET", f"/containers/{cid}/stats?stream=false", timeout=15)
        if st != 200:
            return None
        d = json.loads(data)
        cpu, pre = d.get("cpu_stats", {}), d.get("precpu_stats", {})
        cd = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        sd = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
        pct = (cd / sd * 100) if sd > 0 and cd >= 0 else 0.0
        m = d.get("memory_stats", {})
        st_ = m.get("stats", {})
        used = max(0, m.get("usage", 0) - st_.get("inactive_file", st_.get("cache", 0)))
        limit = m.get("limit") or 0
        return {"cpu": round(min(100.0, pct), 1), "mem_mb": round(used / 1e6, 1),
                "mem_pct": round(used / limit * 100, 1) if limit else None}
    except Exception:
        return None


def collect():
    host = host_metrics()
    containers, err = None, None
    try:
        st, data = docker("GET", "/containers/json?all=1")
        if st != 200:
            raise RuntimeError(f"docker respondeu HTTP {st}")
        items = []
        for c in json.loads(data):
            name = (c.get("Names") or ["/" + c["Id"][:12]])[0].lstrip("/")
            if name in HIDE or not NAME_RE.match(name):
                continue
            status = c.get("Status", "")
            health = ("unhealthy" if "(unhealthy)" in status else "healthy" if "(healthy)" in status
                      else "starting" if "health: starting" in status else None)
            items.append({"id": c["Id"], "name": name, "state": c.get("State", "unknown"), "health": health,
                          "status": status, "group": group_of(name, c.get("Labels")),
                          "cpu": None, "mem_mb": None, "mem_pct": None})
        running = [i for i in items if i["state"] == "running"]
        if running:
            with ThreadPoolExecutor(max_workers=6) as ex:
                for i, res in zip(running, ex.map(lambda i: container_stats(i["id"]), running)):
                    if res:
                        i.update(res)
        items.sort(key=lambda i: i["name"])
        containers = items
    except Exception as e:
        err = str(e)[:140]
    return host, containers, err


SNAP = {"t": None, "host": {}, "containers": None, "docker_error": "ainda lendo…"}


LAST_REQ = time.time()
SNAP_TS = 0.0
IDLE_AFTER = float(os.environ.get("IDLE_AFTER", "60"))  # sem ninguém pedindo /status: para de consultar o Docker
FRESH = max(15.0, STATS_EVERY * 3)  # idade máxima para considerar o snapshot atual


def stats_loop():
    global SNAP, SNAP_TS
    while True:
        if time.time() - LAST_REQ > IDLE_AFTER:
            time.sleep(0.5)
            continue
        t0 = time.time()
        try:
            host, containers, err = collect()
            with LOCK:
                SNAP = {"t": now_iso(), "host": host, "containers": containers, "docker_error": err}
                SNAP_TS = time.time()
        except Exception as e:  # nunca deixa a thread morrer
            print("erro ao coletar:", e, file=sys.stderr, flush=True)
        time.sleep(max(0.5, STATS_EVERY - (time.time() - t0)))


# ---------------------------------------------------------------- crédito (API de custos da Anthropic)
CREDIT = {"t": 0.0, "data": None}
CREDIT_LOCK = threading.Lock()
SINCE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def fetch_spend(since: str):
    """Soma o custo (em dólares) desde `since`. Devolve (total, mês atual)."""
    start = f"{since}T00:00:00Z"
    end = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat() + "T00:00:00Z"
    this_month = datetime.now(timezone.utc).strftime("%Y-%m")
    total = month = 0.0
    page = None
    for _ in range(60):
        q = f"starting_at={start}&ending_at={end}&bucket_width=1d&limit=31" + (f"&page={page}" if page else "")
        req = urllib.request.Request(f"{API_BASE}/v1/organizations/cost_report?{q}", headers={
            "x-api-key": ADMIN_KEY, "anthropic-version": "2023-06-01",
            "User-Agent": "FazendaHomelab/" + VERSION})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
        for bucket in data.get("data", []):
            for res in bucket.get("results", []):
                if res.get("currency", "USD") != "USD":
                    continue
                cents = float(res.get("amount", "0"))
                total += cents
                if str(bucket.get("starting_at", ""))[:7] == this_month:
                    month += cents
        if not data.get("has_more"):
            break
        page = data.get("next_page")
        if not page:
            break
    return round(total / 100, 2), round(month / 100, 2)


def secretaria_gasto():
    """Mesma conta do /gasto da secretária: tokens do mês × preço, contra o limite mensal."""
    if not os.path.exists(SEC_DB):
        return {"enabled": False}
    month = datetime.now().strftime("%Y-%m")
    try:
        con = sqlite3.connect(f"file:{SEC_DB}?mode=ro", uri=True, timeout=3)
        try:
            row = con.execute("SELECT calls, tokens_in, tokens_out FROM usage WHERE month=?", (month,)).fetchone()
            rows = con.execute("SELECT month, tokens_in, tokens_out FROM usage").fetchall() if SEC_CREDIT > 0 else []
        finally:
            con.close()
    except Exception as e:
        return {"enabled": True, "error": "não consegui ler o banco da secretária: " + type(e).__name__}
    calls, tin, tout = row if row else (0, 0, 0)
    cost = tin * SEC_PRICE_IN / 1e6 + tout * SEC_PRICE_OUT / 1e6
    out = {"enabled": True, "month": month, "calls": calls, "tokens_in": tin, "tokens_out": tout,
           "cost": round(cost, 4), "budget": SEC_BUDGET, "active": cost < SEC_BUDGET}
    if SEC_CREDIT > 0:  # saldo estimado: crédito comprado - gasto acumulado desde o mês da compra
        months = sorted((m, i * SEC_PRICE_IN / 1e6 + o * SEC_PRICE_OUT / 1e6) for m, i, o in rows if m >= SEC_CREDIT_FROM)
        spent = sum(c for _, c in months)
        out.update(credit_total=SEC_CREDIT, credit_from=SEC_CREDIT_FROM or None,
                   credit_spent=round(spent, 4), credit_remaining=round(SEC_CREDIT - spent, 2),
                   credit_months=[{"month": m, "cost": round(c, 4)} for m, c in months])
    return out


def manual_path():
    return NOTES_DIR / ".credito.json"


def read_manual():
    try:
        m = json.loads(manual_path().read_text(encoding="utf-8"))
        return {"enabled": True, "manual": True, "remaining": float(m["remaining"]), "as_of": str(m["as_of"])}
    except Exception:
        return None


def credit_status():
    if not ADMIN_KEY:  # sem chave de administrador: usa o saldo que você digitou no app
        return read_manual() or {"enabled": False}
    with CREDIT_LOCK:
        if CREDIT["data"] is not None and time.time() - CREDIT["t"] < CREDIT_TTL:
            return CREDIT["data"]
        prev = CREDIT["data"] or {"enabled": True}
        if not SINCE_RE.match(CREDIT_SINCE):
            data = {"enabled": True, "error": "defina CREDIT_SINCE (AAAA-MM-DD) no .env"}
        else:
            try:
                spent, month = fetch_spend(CREDIT_SINCE)
                data = {"enabled": True, "since": CREDIT_SINCE, "spent": spent, "month": month, "t": now_iso(),
                        "total": CREDIT_TOTAL or None,
                        "remaining": round(CREDIT_TOTAL - spent, 2) if CREDIT_TOTAL else None}
            except urllib.error.HTTPError as e:   # nunca repete o corpo da resposta (poderia citar a chave)
                data = dict(prev, error=f"a Anthropic respondeu HTTP {e.code}"
                            + (" (a chave precisa ser de administrador de uma organização)" if e.code in (401, 403) else ""))
            except Exception as e:
                data = dict(prev, error="não consegui falar com a Anthropic: " + type(e).__name__)
        CREDIT["data"] = data
        CREDIT["t"] = time.time() if "error" not in data else time.time() - CREDIT_TTL + 60  # erro: tenta de novo em 1 min
        return data


def demux_logs(b: bytes) -> str:
    if len(b) < 8 or b[0] not in (0, 1, 2) or b[1:4] != b"\0\0\0":
        return b.decode("utf-8", "replace")
    out, i = [], 0
    while i + 8 <= len(b):
        size = int.from_bytes(b[i + 4:i + 8], "big")
        out.append(b[i + 8:i + 8 + size])
        i += 8 + size
    return b"".join(out).decode("utf-8", "replace")


def known_container(name):
    with LOCK:
        for c in SNAP["containers"] or []:
            if c["name"] == name:
                return c
    return None


# ---------------------------------------------------------------- HTTP
# ---------------------------------------------------------------- placar dos joguinhos
JOGOS = {"cobrinha": 800, "toupeira": 100, "memoria": 10, "galinha": 100}   # jogo -> máximo de moedas por partida
PID_RE = re.compile(r"^[A-Za-z0-9_-]{8,40}$")
PLACAR_LOCK = threading.Lock()


def placar_db():
    con = sqlite3.connect(str(NOTES_DIR / "placar.db"), timeout=5)
    con.execute("CREATE TABLE IF NOT EXISTS partidas (id INTEGER PRIMARY KEY AUTOINCREMENT, pid TEXT UNIQUE, "
                "jogo TEXT NOT NULL, pontos INTEGER NOT NULL, moedas INTEGER NOT NULL, quando TEXT NOT NULL)")
    return con


def placar_resumo(con):
    jogos = {j: {"melhor": 0, "partidas": 0, "moedas": 0} for j in JOGOS}
    for jogo, melhor, n, moedas in con.execute("SELECT jogo, MAX(pontos), COUNT(*), SUM(moedas) FROM partidas GROUP BY jogo"):
        if jogo in jogos:
            jogos[jogo] = {"melhor": melhor, "partidas": n, "moedas": moedas}
    return {"jogos": jogos, "moedas_total": sum(j["moedas"] for j in jogos.values())}


def placar_ler():
    with PLACAR_LOCK:
        con = placar_db()
        try:
            return placar_resumo(con)
        finally:
            con.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "FazendaServidor/" + VERSION

    def _cors(self):
        origin = self.headers.get("Origin", "")
        if origin in ORIGINS or "*" in ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin or "*")
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, PUT, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Access-Control-Allow-Private-Network", "true")

    def _send(self, code, payload=None, text=None):
        if text is not None:
            body, ctype = text.encode("utf-8"), "text/plain; charset=utf-8"
        elif payload is not None:
            body, ctype = json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8"
        else:
            body, ctype = b"", None
        self.send_response(code)
        self._cors()
        if body:
            self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _authed(self):
        got = self.headers.get("Authorization", "")
        return hmac.compare_digest(got.encode(), f"Bearer {TOKEN}".encode())

    def _route(self):
        u = urlparse(self.path)
        return [unquote(p) for p in u.path.strip("/").split("/") if p], parse_qs(u.query)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_OPTIONS(self):
        self._send(204)

    def do_GET(self):
        parts, query = self._route()
        if parts == ["health"]:
            return self._send(200, {"ok": True, "version": VERSION})
        if not self._authed():
            return self._send(401, {"error": "token inválido"})
        if parts == ["status"]:
            global LAST_REQ
            LAST_REQ = time.time()
            for _ in range(80):  # depois de um tempo parado, espera a leitura nova (até ~8s)
                if time.time() - SNAP_TS <= FRESH:
                    break
                time.sleep(0.1)
            with LOCK:
                snap = dict(SNAP)
            cs = [{k: v for k, v in c.items() if k != "id"} for c in (snap["containers"] or [])] if snap["containers"] is not None else None
            return self._send(200, {"t": snap["t"], "host": snap["host"], "containers": cs,
                                    "docker_error": snap["docker_error"], "restart_enabled": ENABLE_RESTART,
                                    "version": VERSION})
        if parts == ["credit"]:
            return self._send(200, credit_status())
        if parts == ["gasto"]:
            return self._send(200, secretaria_gasto())
        if parts == ["placar"]:
            try:
                out = placar_ler()
            except sqlite3.Error as e:
                return self._send(500, {"error": "não consegui ler o placar: " + str(e)[:100]})
            return self._send(200, out)
        if len(parts) == 3 and parts[0] == "containers" and parts[2] == "logs":
            c = known_container(parts[1])
            if not c:
                return self._send(404, {"error": "container desconhecido"})
            try:
                tail = max(1, min(100, int(query.get("tail", ["25"])[0])))
            except ValueError:
                tail = 25
            try:
                st, data = docker("GET", f"/containers/{quote(c['id'])}/logs?stdout=1&stderr=1&tail={tail}")
                if st != 200:
                    return self._send(502, {"error": f"docker respondeu HTTP {st}"})
            except Exception as e:
                return self._send(502, {"error": str(e)[:140]})
            txt = ANSI_RE.sub("", demux_logs(data))
            return self._send(200, text=txt[-6000:])
        if parts == ["notes"]:
            with LOCK:
                notes = [read_note(p) for p in NOTES_DIR.glob("*.md") if ID_RE.match(p.stem)]
            return self._send(200, notes)
        if len(parts) == 2 and parts[0] == "notes" and ID_RE.match(parts[1]):
            path = NOTES_DIR / f"{parts[1]}.md"
            if not path.exists():
                return self._send(404, {"error": "não encontrada"})
            with LOCK:
                note = read_note(path)
            return self._send(200, note)  # envia fora do LOCK para um cliente lento não travar o resto
        self._send(404, {"error": "rota desconhecida"})

    def do_POST(self):
        parts, _ = self._route()
        if not self._authed():
            return self._send(401, {"error": "token inválido"})
        if len(parts) == 3 and parts[0] == "containers" and parts[2] == "restart":
            if not ENABLE_RESTART:
                return self._send(403, {"error": "reinício desativado no servidor (ENABLE_RESTART=1)"})
            c = known_container(parts[1])
            if not c:
                return self._send(404, {"error": "container desconhecido"})
            try:
                st, _ = docker("POST", f"/containers/{quote(c['id'])}/restart?t=10", timeout=40)
            except Exception as e:
                return self._send(502, {"error": str(e)[:140]})
            if st == 204:
                return self._send(200, {"ok": True})
            if st == 403:
                return self._send(403, {"error": "o docker-socket-proxy bloqueou (precisa de POST=1 e ALLOW_RESTARTS=1)"})
            return self._send(502, {"error": f"docker respondeu HTTP {st}"})
        if parts == ["placar"]:   # fim de uma partida de um joguinho
            length = int(self.headers.get("Content-Length") or 0)
            try:
                if not 0 < length <= 1000:
                    raise ValueError
                d = json.loads(self.rfile.read(length).decode("utf-8"))
                jogo, pid = d["jogo"], str(d["pid"])
                pontos, moedas = int(d["pontos"]), int(d["moedas"])
                if jogo not in JOGOS or not PID_RE.match(pid) or not 0 <= pontos <= 100000 or not 0 <= moedas <= JOGOS[jogo]:
                    raise ValueError
            except Exception:
                return self._send(400, {"error": "partida inválida"})
            try:
                with PLACAR_LOCK:
                    con = placar_db()
                    try:
                        # o app reenvia partidas guardadas offline: o pid evita contar duas vezes
                        con.execute("INSERT OR IGNORE INTO partidas (pid, jogo, pontos, moedas, quando) VALUES (?,?,?,?,?)",
                                    (pid, jogo, pontos, moedas, now_iso()))
                        con.commit()
                        out = placar_resumo(con)
                    finally:
                        con.close()
            except sqlite3.Error as e:
                return self._send(500, {"error": "não consegui gravar o placar: " + str(e)[:100]})
            return self._send(200, out)
        self._send(404, {"error": "rota desconhecida"})

    def do_PUT(self):
        parts, _ = self._route()
        if not self._authed():
            return self._send(401, {"error": "token inválido"})
        if parts == ["credit"]:   # saldo digitado à mão
            length = int(self.headers.get("Content-Length") or 0)
            try:
                if not 0 < length <= 1000:
                    raise ValueError
                remaining = float(json.loads(self.rfile.read(length).decode("utf-8"))["remaining"])
                if not 0 <= remaining <= 1_000_000:
                    raise ValueError
            except Exception:
                return self._send(400, {"error": "envie {\"remaining\": número entre 0 e 1000000}"})
            with LOCK:
                fd, tmp = tempfile.mkstemp(dir=NOTES_DIR, suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"remaining": round(remaining, 2), "as_of": now_iso()}, f)
                os.replace(tmp, manual_path())
            return self._send(200, read_manual())
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
        parts, _ = self._route()
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
    threading.Thread(target=stats_loop, daemon=True).start()
    print(f"Fazenda {VERSION} · notas em {NOTES_DIR.resolve()} · http://{HOST}:{PORT} · docker {DOCKER_HOST} · "
          f"reiniciar: {'sim' if ENABLE_RESTART else 'não'} · origens: {', '.join(ORIGINS)}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
