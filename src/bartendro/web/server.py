"""bartendro-web: run the bot's web app.

Examples:
    bartendro-web                    # hardware and database from the config file, port 8080
    bartendro-web --sim 15           # no hardware: 15 simulated dispensers (try it on a PC)
    bartendro-web --sim 3 --db test.db --port 8000
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from .. import config as config_mod
from ..bot import Bot
from ..db import open_db
from ..hw.connect import ConnectError, open_driver
from ..hw.driver import DriverError
from ..hw.router import RouterError

log = logging.getLogger("bartendro")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bartendro-web", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="bot config file (default: $BARTENDRO_CONFIG, ./bartendro.toml, "
                                     "/etc/bartendro/bartendro.toml)")
    ap.add_argument("--db", help="database file (default: [database] path from the config)")
    ap.add_argument("--sim", type=int, metavar="N", help="simulate N dispensers instead of the hardware")
    ap.add_argument("--host", default="0.0.0.0", help="address to listen on (default: all)")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--showcase", action="store_true",
                    help="start from a fresh showcase database (bundled drinks, 15 bottles, a demo party) "
                         "at --db, replacing what's there - for demo copies")
    ap.add_argument("--start-from", metavar="FILE",
                    help="start from a fresh copy of FILE (a bot database, plus the uploads folder next "
                         "to it: party logos) at --db, replacing what's there - for demo copies of a real bot")
    ap.add_argument("--banner", default="", help="a line shown on every page, e.g. 'Demo: simulated pumps'")
    ap.add_argument("--no-uploads", action="store_true", help="switch off party logo uploads")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        cfg = config_mod.load(args.config)
    except config_mod.ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    hw = cfg.hardware
    simulate = args.sim if args.sim is not None else hw.simulate
    db_path = args.db or cfg.database_path()
    if args.showcase and args.start_from:
        print("error: use --showcase or --start-from, not both", file=sys.stderr)
        return 2
    if args.showcase:
        from .. import showcase
        sessions = showcase.build(db_path)
    elif args.start_from:
        if not Path(args.start_from).is_file():
            print(f"error: {args.start_from} does not exist", file=sys.stderr)
            return 2
        shutil.copyfile(args.start_from, db_path)
        uploads = Path(args.start_from).resolve().parent / "uploads"
        if uploads.is_dir():
            shutil.copytree(uploads, Path(db_path).resolve().parent / "uploads", dirs_exist_ok=True)
        sessions = open_db(db_path)  # brings the copy's schema up to date
    else:
        sessions = open_db(db_path)
    log.info("%s: database %s", cfg.name, db_path)

    import uvicorn

    from . import create_app
    try:
        with open_driver(device=hw.serial_device or None, i2c_bus=hw.i2c_bus, simulate=simulate,
                         status_led=hw.status_led, ports=hw.ports) as driver:
            if simulate:
                driver.sim.time_scale = 1.0
            log.info("found %d dispenser(s)%s", driver.count(), " (simulated)" if simulate else "")
            if hw.dispensers is not None and driver.count() != hw.dispensers:
                log.warning("config says %d dispensers but %d were found", hw.dispensers, driver.count())
            bot = Bot(driver, sessions)
            bot.start()
            app = create_app(bot, cfg.name, banner=args.banner, allow_uploads=not args.no_uploads)
            uvicorn.run(app, host=args.host, port=args.port, proxy_headers=True, forwarded_allow_ips="*",
                        log_level="debug" if args.verbose else "info")
    except (ConnectError, RouterError, DriverError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
