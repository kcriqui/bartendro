# Development workflow

## Two PCs, everything on the NAS

Kevin works from his home PC and his office PC, so **everything this project creates lives on the
NAS** (`P:\Claude\bartendro` = `\\TRUENAS\Projects\Claude\bartendro` =
`/mnt/HDD-pool/Projects/Claude/bartendro` on the NAS), not in a PC's local folders:
- Plans: `docs/plans/<date>-<topic>.md`. Plan mode writes to `~/.claude/plans/` on the local PC -
  copy the approved plan into `docs/plans/` and delete the local copy.
- Notes, decisions, findings: the `docs/` files listed in CLAUDE.md - not Claude's local memory
  (the local memory folder stays empty on purpose).
- Scratch / demo databases, renders, logs: `build/` (git-ignored, but on the NAS). Preview servers
  in `.claude/launch.json` use relative paths into `build/`.

**Per-PC setup (once on each PC):** Python 3.11+, `pip install -e ".[dev]"` in the repo (puts
`bartendro-hw` / `-db` / `-web` on PATH, plus pytest, pytest-cov, ruff, vulture), `gh auth login`.
If git complains about "dubious ownership" on the share:
`git config --global --add safe.directory '%(prefix)///TRUENAS/Projects/Claude/bartendro'`.

## Tests and code quality

- `pytest` (no hardware needed; 124 tests). `bartendro-hw --sim N ...` for the hardware CLI.
- **Before committing, run pytest and stop if anything fails.** A `| tail` pipe hides the exit code -
  that once let a failing commit through.
- Lint: `py -m ruff check src tests scripts` (config in pyproject; must stay clean). `ruff format`
  isn't applied (it would reflow ~35 files). Review ruff's `--unsafe-fixes` by hand: SIM118 turned
  `col in row.keys()` into `col in row`, which is wrong for a `sqlite3.Row` (searches the values).
- Coverage: `pytest --cov=bartendro --cov-report=term-missing` (86% overall; the real-hardware
  paths in router.py / status_led.py / connect.py can only run on a Pi; driver.py's error paths are
  the riskiest gap).
- Dead code: `vulture src/bartendro` - mostly false alarms (routes, SQLAlchemy events, migrations).

## Local preview

`bartendro-web --sim 15 --start-from deploy/demo-bot.db --db <scratch>/demo.db --port 8093
--no-uploads --dev` - restart it after Python changes; `--dev` makes template edits live (without it
templates are read once); static files are always live.
- `.claude/launch.json` has this as `bartendro-demo-copy` (port 8093, db in `build/sim-test/`).
- Watching the pumps: the **Sim** link in the top bar opens `/sim` in a second window (simulated
  pumps only).
- `?party=<id>` previews a party via a cookie shared by every localhost port; `?party=0` clears it.
- The built-in browser pane throttles animations while it's hidden.
- Visual work: show it in the preview (or a `build/` scratch page served with `py -m http.server`),
  let Kevin react, iterate. He likes side-by-side options to pick from (fonts, art).

## Windows gotchas (this PC)

- Use `py`, not `python3` (the Windows store stub hangs).
- Bash heredocs mangle backslash escapes (`\n`, `\b` in Windows paths): write scripts to a file, or
  use the Edit tool for strings with escapes.
- Time local servers with `127.0.0.1`, not `localhost` (name lookup adds ~0.3 s).
- Scripts printing non-ASCII to the console show "�": set `PYTHONIOENCODING=utf-8`.
- Files on the NAS share written by a container can show up here a minute late (SMB caching).

## Working with Kevin

- **Commit / push / redeploy only when he asks** (he usually says "commit, push and redeploy").
- **Never run pumps on real hardware without his go-ahead** (the CLI asks before pumping unless `-y`).
- Never type passwords. TrueNAS web UI: Kevin logs in himself; keep his TrueNAS tab open until he
  closes it.
- Never touch `P:\Claude\starlight-ebay-template` (his eBay tools) from this project.
- Times: Pacific (PDT in summer, PST in winter).
- Gmail: I can only create drafts (he sends them), and they land in kevin@starlightphotonics.com,
  not kcriqui@gmail.com. Gmail can wrap links in a google.com/url redirect if a draft is edited -
  check links before he sends.
- The repo (github.com/kcriqui/bartendro) is **public**: it contains the demo URL, NAS / tailnet
  names and the bot database (`deploy/demo-bot.db`, Kevin's choice). Never commit secrets.

## NAS access

See Kevin's global CLAUDE.md for details: **SSH as root** with
`MSYS_NO_PATHCONV=1 ssh -i ~/.ssh/id_truenas -o IdentitiesOnly=yes root@truenas '<cmd>'`, and the
TrueNAS REST API for apps / cron / datasets. Tailscale is the container `ix-tailscale-tailscale-1`
(`docker exec ix-tailscale-tailscale-1 tailscale funnel status`). Funnel 443 = the bartendro demo.
Project datasets use NFSv4 ACLs: containers writing there must run as Kevin's uid 3000; `chmod` is
refused even for root.

## Redeploying the hosted demo

**Easiest (2026-10-31, works): the TrueNAS REST API** - no login needed. `POST /app/redeploy` with
body `"bartendro-demo"` rebuilds the image from GitHub (`pull_policy: build`) and restarts it
(~1 min); poll `GET /core/get_jobs?id=<job>` until SUCCESS. Call it through `nas_cron.call()` in
`P:\Claude\SLP-eBay-tools\tools\nas_cron.py` (reads the bearer token from `truenas_key.json`
there; import it, don't touch anything else in that project). Then check with curl
(`http://truenas:8077/...`) and the public URL.

Fallback, in the TrueNAS web UI with Claude in Chrome (Kevin logs in): Apps > select the
`bartendro-demo` row (the list opens on bambuddy and the first click often doesn't take - click
the name again, or take a screenshot and click the row) > Details > Edit > Save without changes =
rebuild from GitHub (~40-50 s). TrueNAS's YAML editor auto-indents: paste config as one-line JSON.

More in [hosting.md](hosting.md).
