"""Mint a development bearer token: uv run python scripts/mint_token.py --tenant northwind --role analyst"""

from __future__ import annotations

import argparse
import time

import jwt

from app.bootstrap.settings import get_settings


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--role", choices=["analyst", "ops_lead", "admin"], default="analyst")
    ap.add_argument("--user", default="dev-user")
    ap.add_argument("--ttl", type=int, default=3600)
    a = ap.parse_args()
    s = get_settings()
    claims = {"sub": a.user, "tenant": a.tenant, "role": a.role, "exp": int(time.time()) + a.ttl}
    print(jwt.encode(claims, s.jwt_secret.get_secret_value(), algorithm=s.jwt_algorithm))


if __name__ == "__main__":
    main()
