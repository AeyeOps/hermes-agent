#!/usr/bin/env python3
"""Idempotently upsert AEyeOps portal A records in Cloudflare.

Secrets are read from environment or a host-local keys file. Values are never
printed. This helper is intentionally generic and public-fork safe.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.cloudflare.com/client/v4"


def load_env_file(path: str | None) -> None:
    if not path:
        return
    p = Path(path).expanduser()
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def cf_request(method: str, path: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        API + path,
        method=method,
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            out = json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            out = json.load(exc)
        except Exception:
            out = {"success": False, "errors": [{"message": str(exc)}]}
    if not out.get("success"):
        message = "; ".join(err.get("message", "unknown") for err in out.get("errors", []))
        raise RuntimeError(message or "Cloudflare API request failed")
    return out


def public_ip() -> str:
    with urllib.request.urlopen("https://api.ipify.org", timeout=20) as resp:
        ip = resp.read().decode("ascii").strip()
    ipaddress.ip_address(ip)
    return ip


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--zone-name", default=os.environ.get("CLOUDFLARE_ZONE_NAME") or os.environ.get("AEX_CLOUDFLARE_ZONE_NAME") or "")
    ap.add_argument("--zone-id", default=os.environ.get("CLOUDFLARE_ZONE_ID") or os.environ.get("AEX_CLOUDFLARE_ZONE_ID") or "")
    ap.add_argument("--keys-file", default=os.environ.get("AEX_CLOUDFLARE_KEYS_FILE") or str(Path.home() / ".config/secrets/keys.env"))
    ap.add_argument("--origin-ip", default=os.environ.get("AEX_PORTAL_ORIGIN_IP") or "auto")
    ap.add_argument("--proxied", choices=("true", "false", "source"), default=os.environ.get("AEX_CLOUDFLARE_PROXIED", "false"))
    ap.add_argument("--ttl", type=int, default=int(os.environ.get("AEX_CLOUDFLARE_TTL", "1")))
    ap.add_argument("records", nargs="+", help="FQDNs to upsert as A records")
    args = ap.parse_args()

    load_env_file(args.keys_file)
    token = os.environ.get("CLOUDFLARE_API_TOKEN") or os.environ.get("CF_API_TOKEN") or os.environ.get("CLOUDFLARE_TOKEN")
    if not token:
      print(json.dumps({"changed": False, "skipped": True, "reason": "missing_cloudflare_token"}))
      return 0

    zone_name = args.zone_name or ".".join(args.records[0].rstrip(".").split(".")[-2:])
    zone_id = args.zone_id
    if not zone_id:
        qs = urllib.parse.urlencode({"name": zone_name})
        zones = cf_request("GET", f"/zones?{qs}", token)["result"]
        if not zones:
            raise RuntimeError(f"Cloudflare zone not found: {zone_name}")
        zone_id = zones[0]["id"]

    ip = public_ip() if args.origin_ip == "auto" else args.origin_ip
    ipaddress.ip_address(ip)

    changed = []
    unchanged = []
    for name in args.records:
        fqdn = name.rstrip(".")
        qs = urllib.parse.urlencode({"type": "A", "name": fqdn})
        existing = cf_request("GET", f"/zones/{zone_id}/dns_records?{qs}", token)["result"]
        if args.proxied == "source" and existing:
            proxied = bool(existing[0].get("proxied", False))
        else:
            proxied = args.proxied == "true"
        payload = {"type": "A", "name": fqdn, "content": ip, "ttl": args.ttl, "proxied": proxied}
        if existing:
            rec = existing[0]
            if rec.get("content") == ip and bool(rec.get("proxied", False)) == proxied and int(rec.get("ttl", args.ttl) or 1) == args.ttl:
                unchanged.append(fqdn)
                continue
            cf_request("PUT", f"/zones/{zone_id}/dns_records/{rec['id']}", token, payload)
        else:
            cf_request("POST", f"/zones/{zone_id}/dns_records", token, payload)
        changed.append(fqdn)

    print(json.dumps({"changed": changed, "unchanged": unchanged, "origin_ip": "<redacted>", "zone": zone_name}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"success": False, "error": str(exc)}), file=sys.stderr)
        raise SystemExit(1)
