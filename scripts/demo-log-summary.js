// Who used the hosted demo bot: paste into the browser console (or run with a browser tool) on
// the TrueNAS container-log page - Apps > bartendro-demo > Workloads > bartendro > View Logs,
// with "Tail Lines" set high (e.g. 20000) and Connect. Logs only go back to the last redeploy.
//
// Prints one row per IP address, times in Pacific time (PST/PDT, America/Los_Angeles): first and
// last request, request counts, and a guess at what it was:
//   person  - opened pages and the live connection (WebSocket) or changed something
//   scanner - asked for files the app doesn't have (wp-config.php, /.git/config, ...)
//   bot     - one or two hits on the front page
//   your LAN / your tailnet / Google (link preview) - 172.16.x.x (the Docker gateway), 100.64-127.x.x,
//   2607:f8b0:...
// Skips 127.0.0.1 (the container's own health check).
(() => {
  const TZ = "America/Los_Angeles";
  const fmt = new Intl.DateTimeFormat("en-US", { timeZone: TZ, month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", hour12: false, timeZoneName: "short" });
  const pacific = (utc) => fmt.format(new Date(utc.replace(" ", "T") + "Z"));
  const re = /^(\S+ \S+?)(?:\.\d+)?\+00:00INFO:\s+(\S+):\d+ - "(\S+) (\S+)[^"]*"\s*(\d{3})?/;
  const ips = {};
  let total = 0, logStart = null;
  for (const line of document.querySelector("main").innerText.split("\n")) {
    logStart = logStart || (line.match(/^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)/) || [])[1];
    const m = line.match(re);
    if (!m) continue;
    const [, ts, ip, method, path, status] = m;
    if (ip === "127.0.0.1") continue;
    total++;
    const e = (ips[ip] = ips[ip] || { first: ts, last: ts, requests: 0, pages: 0, live: 0, changes: 0, notFound: 0 });
    e.last = ts;
    e.requests++;
    if (method === "WebSocket") e.live++;
    else if (method === "POST" && status && status < "400") e.changes++;
    else if (method === "GET" && !/^\/(static|api|uploads)\//.test(path)) e.pages++;
    if (status === "404" || status === "405") e.notFound++;
  }
  const rows = Object.entries(ips).map(([ip, e]) => ({
    ip,
    kind: ip.startsWith("172.16.") ? "your LAN" : /^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./.test(ip) ? "your tailnet"
      : ip.startsWith("2607:f8b0:") ? "Google (link preview)"
      : e.notFound >= 1 && e.live === 0 ? "scanner" : e.live || e.changes ? "person" : "bot",
    first: pacific(e.first), last: pacific(e.last),
    requests: e.requests, pages: e.pages, live: e.live, changes: e.changes,
  })).sort((a, b) => b.requests - a.requests);
  console.log(`Log starts ${logStart ? pacific(logStart) : "?"}; ${total} requests from ${rows.length} addresses`);
  console.table(rows);
  return { logStart: logStart && pacific(logStart), total, rows };
})();
