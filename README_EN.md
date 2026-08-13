# GitHub AI Insight

**English** | [简体中文](README.md)

> A Dockerised automation service that runs on a home NAS. Every day it searches GitHub for
> trending AI/LLM open-source projects, has an LLM analyse and score them, picks the highest
> scorer, renders a dark-theme HTML report, and pushes it to a WeChat Work group bot.

> **Note on language.** The service itself produces Chinese output — reports, push messages
> and the LLM prompts are all written in Chinese. This English README documents the design
> and operation; it does not mean the tool emits English.

Requirements live in [GitHub-AI-Insight-PRD.md](GitHub-AI-Insight-PRD.md) (Chinese),
visual spec in [DESIGN.md](DESIGN.md) (Chinese).

---

## Quick start

**Get it running locally before touching the NAS.** Local verification needs no API key at all:

```bash
python -m pip install -r requirements-dev.txt
python main.py --now --mock --open
```

Full local test guide: **[LOCAL_TESTING.md](LOCAL_TESTING.md)**.
NAS rollout: **[DEPLOY.md](DEPLOY.md)** (ZSpace NAS + Tailscale).

Real deployment:

```bash
cp .env.example .env
docker compose up -d
```

**Changing configuration** (swap model / API / push target): edit `.env`, then

```bash
docker compose restart
```

`.env` is mounted as a **file**, and the app reads it at process start, so `restart` is enough
— no need to rebuild with `up -d`. The four exceptions are `TIMEZONE` / `PUID` / `PGID` /
`HTTP_PORT`, which must take effect before the process starts; changing those still needs
`docker compose up -d`.

Check the logs afterwards to confirm the new config works:

```bash
docker compose logs -f --tail 30
```

On startup the service fires one minimal LLM request as a self-check. `启动自检通过 ✓` in the
log means the new model works; a misconfiguration prints the reason immediately, instead of
you finding out the next day via a fully degraded report.

---

## How it works

```
GitHub Search API  →  SQLite dedup  →  LLM analysis  →  pick top score  →  HTML + push + archive
  newborn + rising     by full_name    structured JSON   today ∪ backlog
  interleaved          skip if seen    4-dimension score
```

**Score weights**: utility 35% · problem-solving 30% · popularity 25% · NAS usability 10%

### Two search channels

GitHub Search can only filter on `created` — there is **no "recently gaining stars" filter**.
With a single narrow window, a project created two months ago that now has tens of thousands
of stars will never enter the candidate pool. It slid out of the window long ago; it is not
ranked low, it is simply not in the result set.

| Channel | Query | Catches |
|---|---|---|
| Newborn | `created:>=SEARCH_DAYS days stars:>=MIN_STARS` | Just-born projects |
| Rising | `created:>=RISING_DAYS days stars:>=RISING_MIN_STARS` | Market-validated ones we missed |

The two channels are **sampled alternately**, not merged and sorted by stars. Sorting would let
the giants from the rising channel permanently crowd out the newborn channel (projects a few
days old typically have only tens of stars), quietly degrading "discover new projects" into
"catch up on old ones". Set `RISING_ENABLED=false` to disable the second channel.

### Obsolescence veto

The price of widening the time window: deep in the all-time star ranking sit high-star corpses
— awesome lists, paper collections, superseded frameworks. They would still score full marks
on the 25% popularity dimension.

So there is a **veto bit** alongside the four scoring dimensions. Deliberately a veto and not a
weighted dimension: a weighted dimension means a dead repo can still win on the other three.

- **Hard filter**: repos with `archived == true` are dropped before dedup — no need to spend
  LLM money asking about a repo the author has declared dead.
- **Model verdict**: the LLM returns an `obsolete` boolean plus a reason in the same call.
  It votes true only when the project is superseded by a more mainstream one (**and it can name
  the successor**), supports only an abandoned stack, is fundamentally a tutorial / list /
  coursework rather than a runnable tool, or is archived / redirects users elsewhere.

Guarding against false positives is the hard part here:

- A missing field, a parse failure, or a degraded analysis **always resolves to false**. The
  system never draws a conclusion on behalf of a judgement that was never made.
- The parser does not use `bool(raw)` — models often write booleans as strings, and
  `bool("false")` is `True`, which would silently kill good projects.
- The prompt states explicitly that **"you haven't heard of it ≠ obsolete"** (the model has a
  knowledge cutoff, and the entire value of this system is surfacing things it hasn't heard of)
  and **"mature ≠ obsolete"**.

Vetoed projects are stored under their own `obsolete` status so false positives can be audited
later — "killed for being outdated" and "killed for scoring low" must stay distinguishable.

### The backlog pool

Five projects are analysed each day but only one is pushed. The other four fall out of the
GitHub search window (`SEARCH_DAYS` days) and would never appear again — but their full analysis
is still in the database. **Selection therefore takes the global maximum over
"today's candidates ∪ the backlog pool"**:

```
Day 1   85 / 84 / 83  →  push 85; 84 and 83 enter the backlog
Day 5   72 / 28 / 79  →  today's best (79) loses to 84 in the backlog  →  push 84
```

A pushed project leaves the pool immediately, so the backlog drains from the top down. Reusing a
backlog project **does not re-invoke the LLM** (the analysis already exists); it costs one GitHub
request to confirm the repo still exists, and the report is labelled "往期精选" (past pick).

If today's candidate list is empty the backlog is still consulted; only when both are empty does
the empty-result policy apply.

### Project statuses

Anything already in the database is excluded from fetching — a project with an existing analysis
is never sent to the LLM twice.

| Status | Meaning | Re-fetched | Competes from backlog |
|---|---|---|---|
| `pushed` | Delivered | No | No |
| `degraded` | Delivered using fallback data | No | No |
| `skipped` | Analysed, lost | No | **Yes** |
| `failed` | Push failed, never delivered | No | **Yes** |
| `rejected` | Below `REJECT_BELOW` | No | No |
| `obsolete` | Vetoed as outdated | No | No |
| `retry` | Analysis failed, worth another try | **Yes** | No |

`retry` is the only status that gets fetched again: the 50 assigned during degradation is a
placeholder, not a judgement about the project, and one network hiccup should not blacklist a
project forever.

When the global maximum (including the backlog) falls below `REJECT_BELOW`, **nothing is pushed
that day** — a quiet day beats pushing junk.

---

## Command line

```bash
python main.py                      # Resident: scheduler + report HTTP server
python main.py --now                # Run the pipeline once
python main.py --now --mock         # Full chain on fake data, zero network
python main.py --now --dry-run      # Real analysis, no WeChat push
python main.py --now --open         # Open the report in a browser afterwards
python main.py --serve              # Report HTTP server only
python main.py --show-config        # Print current config (secrets masked)
python main.py --test-llm           # Verify LLM key / endpoint / model
python main.py --list               # Print recent database records
```

The same commands work inside the container — worth running after swapping models:

```bash
docker compose exec github-ai-insight python main.py --test-llm
docker compose exec github-ai-insight python main.py --show-config
docker compose exec github-ai-insight python main.py --now   # run now, don't wait for the schedule
```

Temporary overrides that beat `.env`:

```bash
python main.py --now --model claude-sonnet-4-5 --candidates 10 --days 7
```

---

## Configuration

Everything loads from `.env`; template in [.env.example](.env.example). Key entries:

| Variable | Required | Default | Notes |
|------|------|------|------|
| `LLM_API_KEY` | Yes | — | Without it everything degrades to GitHub metadata summaries |
| `LLM_BASE_URL` | Yes | `https://api.openai.com/v1` | OpenAI-compatible endpoint; `.env.example` ships 6 provider presets |
| `LLM_MODEL` | Yes | `gpt-4o` | Model name |
| `LLM_PROVIDER` | No | `openai` | `openai` or `anthropic` |
| `GITHUB_TOKEN` | No | empty | Anonymous without it — 60 requests/hour |
| `WECHAT_WEBHOOK_URL` | No | empty | Push is skipped when empty |
| `REPORT_BASE_URL` | No | `http://localhost:8080/reports` | **Must be reachable from your phone on any network** — see [DEPLOY.md](DEPLOY.md) |
| `EXECUTION_TIME` | No | `12:00` | Interpreted in `TIMEZONE` |
| `TIMEZONE` | No | `Asia/Dubai` | |
| `CANDIDATE_COUNT` | No | `5` | Candidates analysed per run |
| `SEARCH_DAYS` | No | `3` | Newborn window. `.env.example` ships `15` — 3 days proved too narrow to fill the candidate list |
| `MIN_STARS` | No | `10` | Star floor |
| `RISING_ENABLED` | No | `true` | Enable the rising channel |
| `RISING_DAYS` | No | `90` | Rising channel window |
| `RISING_MIN_STARS` | No | `500` | Rising channel star floor |
| `REJECT_BELOW` | No | `65` | Below this, dropped outright — no backlog entry |
| `NOTIFY_EMPTY` | No | `false` | Push a notice when there are no candidates |
| `DATA_DIR` | No | `./data` | |
| `LOG_LEVEL` | No | `INFO` | |

---

## Layout

```
├── config.py              Config loading and validation (pydantic-settings)
├── models.py              Domain models: Repo / Analysis / Scores / Tldr / AnalyzedProject
├── db.py                  SQLite schema, dedup, UPSERT
├── github_client.py       Search API + README + rate-limit awareness
├── ai_analyzer.py         LLM calls + JSON parsing + scoring + degradation
├── report_generator.py    Jinja2 HTML rendering + Markdown archive
├── wechat_notifier.py     WeChat Work message construction and delivery
├── report_server.py       Built-in HTTP server (/reports and /health)
├── main.py                Orchestration + APScheduler + CLI
├── mock_data.py           Fake data and fake clients for local testing
├── templates/
│   └── report.html.j2     Self-contained dark report template
├── tests/                 286 unit and end-to-end tests
├── scripts/               One-command local verification
├── Dockerfile
├── docker-entrypoint.sh   PUID/PGID privilege drop (NAS permissions)
└── docker-compose.yml
```

Data produced:

```
data/
├── github_ai_insight.db          # WAL mode; the authoritative copy of the backlog
├── reports/YYYY-MM-DD-{owner}_{repo}.html
└── archive/
    ├── YYYY-MM/…md               # Projects that were pushed
    └── backlog/YYYY-MM/…md       # Projects that lost (text copy of the backlog)
```

---

## Degradation policy

No external failure interrupts the pipeline, and none causes the container to exit:

| Failure | Behaviour |
|------|------|
| LLM timeout / network error | Retry twice (5s, 15s), then degrade |
| LLM returns non-JSON | Fall back to the GitHub description, score 50, mark `degraded` |
| LLM key invalid / out of credit | No retry — degrade immediately and warn in the log |
| GitHub rate limit | Read `X-RateLimit-Reset`, wait, retry; give up if over 5 minutes |
| GitHub token invalid | Fall back to anonymous requests and continue |
| One search channel fails | Tolerated; **all** channels failing raises rather than returning empty |
| WeChat push fails | Retry 3 times (10s apart); the report and archive are written regardless |
| WeChat not configured | Skip the push, mark `skipped` — it will be pushed once configured |
| Nothing scores well today | Promote the highest-scoring backlog project (no new LLM call) |
| A backlog project's repo was deleted | Skip it and fall back to today's winner |
| No candidates after dedup | Consult the backlog; only skip silently when both are empty (`NOTIFY_EMPTY=true` pushes a "nothing new today" notice) |

Degraded reports carry an orange dashed warning banner at the top of the page.

---

## The report page

A self-contained single HTML file with **zero external requests** (no CDN, no remote fonts, no
JS) — it renders fine offline.

- Dark theme, background `#0A0A0B`, cards `#111113`
- Mobile first: single-column below 640px, touch targets ≥ 44px
- SVG score ring with a green/yellow/red gradient, plus four dimension bars
- `@media print` switches to black-on-white for archiving
- `prefers-reduced-motion` disables animation
- LLM output is HTML-escaped first, then rendered through an explicit Markdown extension
  allow-list (`attr_list` is deliberately excluded — its `{: onclick=... }` syntax can smuggle
  event attributes past the escaping)

Access: `http://{Tailscale_IP}:8080/reports` (see [DEPLOY.md](DEPLOY.md))

---

## NAS deployment notes

1. Set `PUID` / `PGID` to what `id` actually reports on the NAS, or `./data` will not be writable
2. Point `REPORT_BASE_URL` at a Tailscale address — a LAN IP only works on home WiFi and is a
   dead link over mobile data. The built-in HTTP server is Python's `http.server` with no
   authentication and must not be exposed to the public internet
3. Keep `TZ` and `TIMEZONE` in agreement
4. The health check probes SQLite connectivity; `healthy` in `docker compose ps` means fine

### Three SQLite caveats on a NAS

1. **`DATA_DIR` must be a local NAS path, never an SMB / NFS mount.**
   The database runs in WAL mode (far better power-loss recovery than the default journal),
   and WAL does not work on network filesystems. A `无法启用 WAL` warning in the startup log
   means the path landed on a network mount.
2. **Do not open the `.db` file with a GUI tool from your computer while the container runs.**
   SQLite's file locking over SMB is broken; reading while it writes risks corruption. Use
   `python main.py --list`, or `docker compose stop` first.
3. **This database is the authoritative copy of the backlog — back it up.**
   Losing projects exist only in the database (`archive/backlog/` holds text copies for manual
   recovery, but there is no import function). Lose the database, lose the whole backlog.
