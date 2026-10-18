#!/usr/bin/env python3
"""Exercise packaged authentication filters with the real Fail2Ban regex tool."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile


CLIENT4 = "198.51.100.23"
CLIENT6 = "2001:db8::23"
FOREIGN4 = "203.0.113.99"
FOREIGN6 = "2001:db8:ffff::99"


def cases(pack):
    paths = ["/Users/AuthenticateByName"]
    if pack == "emby":
        paths.append("/emby/Users/AuthenticateByName")
    for path in paths:
        for client in (CLIENT4, CLIENT6):
            for status in (401, 403, 200):
                yield f"{path} {client} status {status}", client, "-", f"POST {path} HTTP/1.1", status, "fixture", [client] if status != 200 else []
        yield "username cannot change ban address", CLIENT4, FOREIGN4, f"POST {path} HTTP/1.1", 401, "fixture", [CLIENT4]
        yield "escaped username cannot change ban address", CLIENT4, f"user-{FOREIGN4}\\x22\\x0aforged", f"POST {path} HTTP/1.1", 401, "fixture", [CLIENT4]
        yield "query cannot change ban address", CLIENT4, "-", f"POST {path}?Username={FOREIGN6} HTTP/1.1", 401, "fixture", [CLIENT4]
        yield "escaped query cannot change ban address", CLIENT6, "-", f"POST {path}?Username={FOREIGN4}\\x22\\x0a HTTP/1.1", 403, "fixture", [CLIENT6]
        forged = f'fake\\x0a{FOREIGN4} - - [18/Oct/2026:00:05:00 +0000] \\x22POST {path} HTTP/1.1\\x22 401 123'
        yield "user agent cannot change ban address", CLIENT6, "-", f"POST {path} HTTP/1.1", 401, forged, [CLIENT6]
        yield "successful login with forged user agent", CLIENT4, "-", f"POST {path} HTTP/1.1", 200, forged, []
        yield "unrelated request with login URL in query", CLIENT4, "-", f"POST /Items?next={path}&host={FOREIGN4} HTTP/1.1", 401, "fixture", []
        yield "server error", CLIENT4, "-", f"POST {path} HTTP/1.1", 500, "fixture", []
        if pack == "emby":
            yield "lowercase endpoint over HTTP/2", CLIENT6, "-", f"POST {path.lower()} HTTP/2.0", 401, "fixture", [CLIENT6]
            yield "similar URL is not the login endpoint", CLIENT4, "-", f"POST {path}Extra HTTP/1.1", 401, "fixture", []
            yield "GET is not an authentication submission", CLIENT4, "-", f"GET {path} HTTP/1.1", 401, "fixture", []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack", choices=("jellyfin", "emby"))
    parser.add_argument("--packages", required=True, type=Path, help="Directory containing unpacked de/ and en/ packages")
    parser.add_argument("--fail2ban-source", required=True, type=Path)
    args = parser.parse_args()
    source = args.fail2ban_source.resolve()
    environment = dict(os.environ, PYTHONPATH=str(source))
    project = "JellyfinServer" if args.pack == "jellyfin" else "embyserver"
    failures = 0
    checked = 0
    with tempfile.TemporaryDirectory(prefix="auth-filter-tests-") as temporary:
        log = Path(temporary) / "access.log"
        for language in ("de", "en"):
            definition = args.packages.resolve() / language / project / f"linuxserver_swag/config/fail2ban/filter.d/{args.pack}-auth.conf"
            for name, client, user, request, status, agent, expected in cases(args.pack):
                # nginx's default combined format; untrusted quote/control bytes are escaped.
                log.write_text(f'{client} - {user} [18/Oct/2026:00:05:00 +0000] "{request}" {status} 123 "-" "{agent}"\n', encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, str(source / "bin/fail2ban-regex"), "--config", str(source / "config"),
                     "--usedns=no", "--out=ip", str(log), str(definition)],
                    env=environment, capture_output=True, text=True, timeout=15,
                )
                actual = result.stdout.splitlines()
                checked += 1
                if result.returncode != 0 or actual != expected:
                    failures += 1
                    print(f"FAIL {language}: {name}: expected {expected}, received {actual}", file=sys.stderr)
                    if result.stderr:
                        print(result.stderr, file=sys.stderr)
    if failures:
        parser.exit(1, f"Authentication filter checks: {failures} of {checked} failed\n")
    print(f"Authentication filter checks: all {checked} passed using fail2ban-regex")


if __name__ == "__main__":
    main()
