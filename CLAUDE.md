# Bartendro (Kevin's fork) - Python 3 rewrite

Python 3 rewrite of the Bartendro cocktail robot's Raspberry Pi software (FastAPI web app +
hardware driver), keeping the original router board, dispensers and AVR firmware. Fork of
partyrobotics/bartendro: `origin` = github.com/kcriqui/bartendro (public), `upstream` =
partyrobotics. **Work on branch `modernize`.** The repo lives on the NAS: `P:\Claude\bartendro` =
`\\TRUENAS\Projects\Claude\bartendro`; Kevin works from two PCs, so everything (plans, notes,
scratch in `build/`) stays here, not in local memory.

## Rules

- Commit / push / redeploy only when Kevin asks. Run `pytest` first and stop if anything fails.
- Never run pumps on real hardware without Kevin's go-ahead.
- Never type passwords (Kevin logs in to TrueNAS himself). Never commit secrets - the repo is public.
- Never touch `P:\Claude\starlight-ebay-template` from here.
- Keep code Python 3.11-compatible (Pi OS Bookworm). Target hardware: Pi 3B+ / Pi 4 only.
- Don't bring back the static GitHub Pages demo unasked. Don't merge PR #1 unasked.
- Record new decisions and findings in the docs below, not here.

## Read as needed

| File | What's in it |
|---|---|
| [docs/status.md](docs/status.md) | Goal, milestones, **current state, next steps, backlog** (start here) |
| [docs/architecture.md](docs/architecture.md) | Code map, web app features (menus, parties, robots, LED sign), speed / single-process design, schema-change procedure |
| [docs/recipes.md](docs/recipes.md) | Bundled recipes, Kevin's recipe rules (pumped vs by hand, ice, specific spirits), naming convention, small-pour ideas |
| [docs/development.md](docs/development.md) | Two-PC setup, tests / lint / coverage, local preview, Windows gotchas, working with Kevin, NAS access, redeploying the demo |
| [docs/hosting.md](docs/hosting.md) | Hosted demo (TrueNAS + Tailscale Funnel), access log, `check_demo_log.py`, the scheduled watcher, what the logs have shown |
| [docs/project-notes.md](docs/project-notes.md) | Decisions and findings: touchscreens, mini-router firmware, demo history, bot database merge, robot art, Bar2D2, Pi performance, code review |
| [docs/pi-setup.md](docs/pi-setup.md) | Setting up a real Pi, first hardware test, what to check on real hardware |
| [docs/plans/](docs/plans/) | Approved plans |
| [Davids-ideas.md](Davids-ideas.md) | David's UI feedback checklist (backlog, not started) |
