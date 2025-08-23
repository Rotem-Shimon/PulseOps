#!/usr/bin/env python3
"""
collector.py  — minimal NOC-style collector (LIVE / REPLAY+FAULTS)

Design:
- Two modes via env MODE=[live|replay] (default: replay).
- LIVE pulls Open-Meteo (no API key). Resilient: timeouts, retries, simple breaker.
- REPLAY replays a JSONL file in a loop and injects faults to simulate incidents.
- Writes to OpenSearch @ http://localhost:9200 (security on, no SSL).

Indexed fields:
  timestamp (date), temperature (float), windspeed (float),
  status (keyword), source (keyword), latency_ms (float), error (keyword)
"""

from __future__ import annotations
import os, time, json, random, signal, sys
from datetime import datetime
from typing import Dict, Optional, Generator
import requests
from requests import Response
from opensearchpy import OpenSearch, RequestsHttpConnection

# -------- Config (env) --------
MODE              = os.getenv("MODE", "replay").strip().lower()   # live | replay
OS_HOST           = os.getenv("OPENSEARCH_HOST", "localhost")
OS_PORT           = int(os.getenv("OPENSEARCH_PORT", "9200"))
OS_USER           = os.getenv("OPENSEARCH_USER", "admin")
OS_PASS           = os.getenv("OPENSEARCH_PASS", "admin")
INDEX             = os.getenv("INDEX_NAME", "pulseops-weather")

OPEN_METEO_URL    = "https://api.open-meteo.com/v1/forecast"
CITY_LAT          = float(os.getenv("CITY_LAT", "32.0853"))
CITY_LON          = float(os.getenv("CITY_LON", "34.7818"))

LOOP_DELAY_SEC    = int(os.getenv("LOOP_DELAY_SEC", "60"))        # live pacing
REPLAY_DELAY_MS   = int(os.getenv("REPLAY_DELAY_MS", "500"))       # replay pacing
REPLAY_FILE       = os.getenv("REPLAY_FILE", "data/replay/weather.jsonl")
HTTP_TIMEOUT_SEC  = int(os.getenv("HTTP_TIMEOUT_SEC", "5"))
MAX_RETRIES       = int(os.getenv("MAX_RETRIES", "3"))
CB_FAIL_THRESHOLD = int(os.getenv("CB_FAIL_THRESHOLD", "5"))
CB_SLEEP_SEC      = int(os.getenv("CB_SLEEP_SEC", "30"))
FAULT_EVERY_N     = int(os.getenv("FAULT_EVERY_N", "20"))         # 0=off
FAULT_PROB        = float(os.getenv("FAULT_PROB", "0.05"))        # 0..1

# -------- Small helpers --------
def safe_float(x) -> Optional[float]:
    try:
        return None if x is None else float(x)
    except Exception:
        return None

def on_exit():
    print("\n[sys] Exiting."); sys.exit(0)

def install_signals():
    for s in (signal.SIGINT, signal.SIGTERM): signal.signal(s, lambda *_: on_exit())

# -------- OpenSearch --------
def os_client() -> OpenSearch:
    """HTTP only (matches your compose) + basic auth (admin/admin)."""
    return OpenSearch(
        hosts=[{"host": OS_HOST, "port": OS_PORT, "scheme": "http"}],
        http_auth=(OS_USER, OS_PASS),
        use_ssl=False, verify_certs=False,
        connection_class=RequestsHttpConnection,
        timeout=10, max_retries=1, retry_on_timeout=False
    )

def os_wait(cli: OpenSearch, max_wait=120):
    """Wait until OS responds to ping (compose may still warm up)."""
    t0 = time.time()
    while time.time() - t0 <= max_wait:
        try:
            if cli.ping(): print("[os] ready"); return
        except Exception: pass
        time.sleep(2)
    print("[os] WARNING: not reachable, proceeding anyway")

def os_ensure_index(cli: OpenSearch):
    """Create index with stable mapping if missing."""
    if cli.indices.exists(index=INDEX): return
    mapping = {
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        "mappings": {"properties": {
            "timestamp": {"type": "date"},
            "temperature": {"type": "float"},
            "windspeed": {"type": "float"},
            "status": {"type": "keyword"},
            "source": {"type": "keyword"},
            "latency_ms": {"type": "float"},
            "error": {"type": "keyword"},
        }}
    }
    cli.indices.create(index=INDEX, body=mapping)
    print(f"[os] created index {INDEX}")

def os_index(cli: OpenSearch, doc: Dict):
    try: cli.index(index=INDEX, body=doc)
    except Exception as e: print(f"[os] index error: {e}")

# -------- Producers --------
def live_once() -> Dict:
    """Single live fetch with timeout; never raises."""
    params = {"latitude": CITY_LAT, "longitude": CITY_LON, "current_weather": True}
    t0 = time.time()
    try:
        r: Response = requests.get(OPEN_METEO_URL, params=params, timeout=HTTP_TIMEOUT_SEC)
        lat = (time.time() - t0) * 1000.0
        if r.status_code == 200:
            cw = r.json().get("current_weather", {})
            return {"timestamp": datetime.utcnow().isoformat(),
                    "temperature": safe_float(cw.get("temperature")),
                    "windspeed": safe_float(cw.get("windspeed")),
                    "status": "ok" if cw else "no_current_weather",
                    "source": "live", "latency_ms": lat}
        return {"timestamp": datetime.utcnow().isoformat(), "temperature": None, "windspeed": None,
                "status": f"bad_status_{r.status_code}", "source": "live", "latency_ms": lat}
    except Exception as e:
        lat = (time.time() - t0) * 1000.0
        return {"timestamp": datetime.utcnow().isoformat(), "temperature": None, "windspeed": None,
                "status": "error", "source": "live", "latency_ms": lat, "error": str(e)[:200]}

def live_stream() -> Generator[Dict, None, None]:
    """Retries + basic circuit breaker; yields forever."""
    fails = 0
    while True:
        rec = None
        for attempt in range(1, MAX_RETRIES + 1):
            rec = live_once()
            if rec["status"] in ("ok", "no_current_weather"): fails = 0; yield rec; break
            backoff = min(2 ** (attempt - 1), 8) + random.random()
            print(f"[live] fail #{attempt}/{MAX_RETRIES} ({rec['status']}); sleep {backoff:.1f}s")
            time.sleep(backoff)
        if rec and rec["status"] not in ("ok", "no_current_weather"):
            fails += 1; yield rec
            if fails >= CB_FAIL_THRESHOLD:
                print(f"[live] circuit open ({fails}); sleeping {CB_SLEEP_SEC}s"); time.sleep(CB_SLEEP_SEC); fails = 0
        time.sleep(LOOP_DELAY_SEC)

def replay_cycle(path: str) -> Generator[Dict, None, None]:
    """Iterate the JSONL once; refresh timestamp to 'now'."""
    if not os.path.exists(path):
        yield {"timestamp": datetime.utcnow().isoformat(), "temperature": 27.5, "windspeed": 10.0,
               "status": "replay_file_missing", "source": "replay", "latency_ms": 0.0,
               "error": f"missing: {path}"}; return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not (line := line.strip()): continue
            try: rec = json.loads(line)
            except Exception: rec = {"status": "parse_error"}
            yield {"timestamp": datetime.utcnow().isoformat(),
                   "temperature": safe_float(rec.get("temperature")),
                   "windspeed": safe_float(rec.get("windspeed")),
                   "status": str(rec.get("status") or "ok"),
                   "source": "replay",
                   "latency_ms": safe_float(rec.get("latency_ms")) or 0.0}

def inject_fault(doc: Dict, i: int) -> Dict:
    """Fault every N or with probability — masks values and bumps latency."""
    if (FAULT_EVERY_N > 0 and i % FAULT_EVERY_N == 0) or (random.random() < FAULT_PROB):
        d = dict(doc)
        d["temperature"] = None; d["windspeed"] = None
        d["status"] = random.choice(["bad_status_500", "bad_status_429", "error"])
        d["latency_ms"] = (safe_float(doc.get("latency_ms")) or 0.0) + random.randint(300, 1200)
        return d
    return doc

def replay_stream(path: str) -> Generator[Dict, None, None]:
    """Infinite loop: replay file + inject realistic incidents."""
    i = 1
    while True:
        for doc in replay_cycle(path):
            yield inject_fault(doc, i); i += 1
            time.sleep(REPLAY_DELAY_MS / 1000.0)

# -------- Main --------
def main():
    install_signals()
    print(f"[boot] MODE={MODE} | OS=http://{OS_HOST}:{OS_PORT} | INDEX={INDEX}")
    cli = os_client(); os_wait(cli); os_ensure_index(cli)
    gen = live_stream() if MODE == "live" else replay_stream(REPLAY_FILE)
    for doc in gen:
        os_index(cli, doc)
        print(f"[{doc['source']}] {doc}")

if __name__ == "__main__":
    main()
