"""`agent-harness` CLI entry point.

Subcommands:
  init          — create data dir + DB.
  gen-token     — generate and persist auth token (no-op if one exists).
  gen-openapi   — dump the FastAPI OpenAPI spec to a file (no server needed).
  serve         — start the FastAPI app via uvicorn.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path

from .config import config_path, get_settings, load_toml, reset_settings_cache, write_toml
from .db import init_db


def _ensure_init() -> None:
    s = get_settings()
    s.resolve_paths()
    init_db()


def cmd_init(_args: argparse.Namespace) -> int:
    _ensure_init()
    s = get_settings()
    print(f"Initialised at {s.home}")
    print(f"  db:   {s.db_path}")
    print(f"  logs: {s.logs_dir}")
    print(f"  cfg:  {config_path()}")
    return 0


def cmd_gen_token(args: argparse.Namespace) -> int:
    _ensure_init()
    toml = load_toml()
    if toml.get("auth_token") and not args.force:
        print("auth_token already set; use --force to overwrite")
        return 0
    token = secrets.token_urlsafe(24)
    toml["auth_token"] = token
    write_toml(toml)
    reset_settings_cache()
    print(token)
    return 0


def cmd_gen_openapi(args: argparse.Namespace) -> int:
    from .main import create_app

    app = create_app()
    spec = app.openapi()
    out = Path(args.path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, indent=2))
    print(f"wrote {out}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    _ensure_init()
    s = get_settings()
    ssl_kwargs: dict[str, str] = {}
    if s.ssl_certfile and s.ssl_keyfile:
        ssl_kwargs["ssl_certfile"] = str(s.ssl_certfile)
        ssl_kwargs["ssl_keyfile"] = str(s.ssl_keyfile)
    uvicorn.run(
        "agent_harness.main:app",
        host=args.host or s.host,
        port=args.port or s.port,
        reload=args.reload,
        **ssl_kwargs,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-harness")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)

    p = sub.add_parser("gen-token")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_gen_token)

    p = sub.add_parser("gen-openapi")
    p.add_argument("path")
    p.set_defaults(func=cmd_gen_openapi)

    p = sub.add_parser("serve")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
