# Hosting a demo bot for people outside your network

A fully working copy of the web app with **simulated pumps**, reachable from anywhere through
Tailscale Funnel. Visitors only need a browser; Tailscale runs on the NAS. The demo starts from
a copy of the bot database committed as `deploy/demo-bot.db` and resets on every restart.
To update the demo's drinks and bottles: copy `build/bartendro.db` over `deploy/demo-bot.db`, push,
redeploy. (`bartendro-web --showcase` instead starts from the bundled drinks + 15 demo bottles.)
There's no login: anyone with the link can use everything, admin included; logo uploads are off.

## 1. Run the container on TrueNAS

Apps > Discover Apps > three-dots menu > **Install via YAML**, name `bartendro-demo`, paste
[`deploy/truenas-demo.yaml`](../deploy/truenas-demo.yaml). TrueNAS builds the image from GitHub
(a minute or two) and starts it on port **8077**: check `http://<nas>:8077` on your network.

Without the TrueNAS UI (NAS shell):

```
docker build -t bartendro-demo https://github.com/kcriqui/bartendro.git#modernize
docker run -d --name bartendro-demo --restart unless-stopped -p 8077:8080 bartendro-demo
```

New code: push to `modernize`, then in TrueNAS edit the app and save (it rebuilds:
`pull_policy: build`), or `docker build ...` again and recreate the container.

## 2. Make it public with Tailscale Funnel

One-time, in the Tailscale admin console (you, not a script - these are tailnet security settings):
- DNS > enable **HTTPS Certificates**.
- Access controls: allow Funnel for the NAS (the console offers to add the `funnel` node
  attribute the first time you run the command below; accept it).

Then, in a shell where the NAS's `tailscale` command works (TrueNAS: Apps > tailscale > Shell):

```
tailscale funnel --bg 8077            # if the Tailscale app uses host networking
tailscale funnel --bg http://<nas-lan-ip>:8077   # otherwise
tailscale funnel status               # shows the public https://<nas>.<tailnet>.ts.net URL
```

Share that URL. Stop sharing: `tailscale funnel --https=443 off` (or `tailscale funnel reset`).

Funnel hostnames appear in public certificate logs, so expect the odd crawler, not just the
people you send the link to. Restart the app to undo whatever visitors changed.

## Who has been using it

TrueNAS: Apps > bartendro-demo > Workloads > bartendro > View Logs, set Tail Lines to 20000,
Connect. Then paste [`scripts/demo-log-summary.js`](../scripts/demo-log-summary.js) into the browser
console (F12 > Console) on that page: one row per IP with first/last visit in Pacific time, request
counts, and person / scanner / bot / your LAN / your tailnet / Google. The log only goes back to the
last redeploy. Visitors' real IPs show (Funnel passes them on). Scanners show up within hours of
any Funnel URL going live (certificate transparency logs) - they only ever get 404s.

## Watching for trouble

The demo writes its own access log (one JSON line per request, rotated at 5 MB x 5) to
`build/demo-logs/` in the repo folder on the NAS (`/mnt/HDD-pool/Projects/Claude/bartendro/build/demo-logs`
mounted on `/logs`; the container runs as Kevin's NAS user 3000:3000 so it may write there - see
`deploy/truenas-demo.yaml`). Unlike the container log it survives redeploys.

`py scripts/check_demo_log.py` reports what's new since its last run (Pacific times): ALERT for a
probe that got something other than a 404, server errors, changes (POSTs) from outside your LAN or
tailnet, request floods, or a log that stopped; scanners that only get 404s are just counted.
`--all` checks the whole log, `--dry-run` doesn't remember the run.

A scheduled Claude task, **bartendro-demo-watch** (Claude app > Scheduled, 8 am and 8 pm), runs it and
sends a desktop notification on ALERT. It runs only while the Claude app is open on Kevin's home PC
(missed runs happen at the next launch).

