"""so101-dashboard - web dashboard for the SO-101 (needs the 08_so101_framework installed).

    so101-dashboard                 open http://127.0.0.1:8000, connect the arm from the page
    so101-dashboard --sim           start with the simulated arm (no hardware)
    so101-dashboard --lan           also reachable from a phone/tablet (prints a secret link)
"""
from __future__ import annotations

import argparse
import sys


def main(argv=None):
    from so101 import profile as P
    from so101.session import stdout_utf8
    from .server import serve
    stdout_utf8()
    ap = argparse.ArgumentParser(prog="so101-dashboard", description=__doc__.split("\n\n")[0])
    ap.add_argument("--profile", help="framework profile (default: $SO101_PROFILE or my_so101)")
    ap.add_argument("--sim", action="store_true", help="start with the simulated arm (no hardware needed)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--lan", action="store_true", help="listen on the network; prints a secret link")
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    a = ap.parse_args(argv)
    return serve(P.load(a.profile), port=a.port, lan=a.lan, sim=a.sim, open_browser=not a.no_browser)


if __name__ == "__main__":
    sys.exit(main())
