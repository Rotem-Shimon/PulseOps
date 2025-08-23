# PulseOps — NOC Demo ⚡

One-command **OpenSearch + Dashboards + Python collector** that streams weather metrics and visualizes them. Built to impress fast.

## Run (LIVE) 🚀
Windows (PowerShell)
    
    $env:MODE="live"; docker compose up -d --build
    docker compose down   # stop

macOS / Linux

    MODE=live docker compose up -d --build
    docker compose down   # stop

> 💡 If `MODE` isn’t set, the stack defaults to **replay**.

## Open it 🔌
- Dashboards → http://localhost:5601  
- API → http://localhost:9200

## First-time UI (10s) 🧭
Management → **Index patterns** → **Create** → pattern `pulseops-weather*`, time field `timestamp` → **Discover** (Last 15 minutes).

## What’s inside 🧰
- **OpenSearch** (data) + **Dashboards** (UI) + **collector** (Python)  
- Index: `pulseops-weather` → `timestamp`, `status`, `latency_ms`, `temperature`, `windspeed`

## Screenshots 🖼️
Place files under `docs/screenshots/`:
- `docs/screenshots/discover-live.png` — Discover (Live data)
- `docs/screenshots/dashboard-overview.png` — NOC Dashboard (overview)

## Project layout 🗂️
PulseOps/
├─ docker-compose.yml
├─ .gitignore
├─ LICENSE
├─ README.md
├─ collector/
│  ├─ collector.py
│  ├─ Dockerfile
│  └─ requirements.txt
└─ docs/
   └─ screenshots/


## Quick fixes 🧯
No data? `docker compose ps` → `docker compose logs -f collector`  
Docker hiccup? Restart Docker Desktop (Windows tip: `wsl --shutdown`).

MIT © PulseOps
