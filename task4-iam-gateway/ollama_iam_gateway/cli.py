"""Command-line tools for the gateway administrator (FR6).

Usage:
    python -m ollama_iam_gateway.cli init-db
    python -m ollama_iam_gateway.cli create-admin --username admin
    python -m ollama_iam_gateway.cli create-user --username bob --role user
    python -m ollama_iam_gateway.cli create-key --username bob --name laptop
    python -m ollama_iam_gateway.cli serve
    python -m ollama_iam_gateway.cli list-users
    python -m ollama_iam_gateway.cli show-policy
    python -m ollama_iam_gateway.cli check-access --role user POST /api/delete

The database location comes from IAM_DB_URL (default sqlite:///./iam.db)
or the --db option.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from dataclasses import replace
from typing import Optional, Sequence

from .config import Settings
from .data.db import init_db, make_engine, make_session_factory
from .data.models import Role
from .iam.errors import IAMError as UserServiceError
from .iam.apikey_service import ApiKeyService
from .iam.policy import PolicyEngine
from .iam.user_service import UserService


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("Password: ")
    second = getpass.getpass("Repeat password: ")
    if first != second:
        raise UserServiceError("Passwords do not match.")
    return first


def _cmd_init_db(settings: Settings, _args: argparse.Namespace) -> int:
    init_db(make_engine(settings.db_url))
    print(f"Database ready: {settings.db_url}")
    return 0


def _cmd_create_user(settings: Settings, args: argparse.Namespace, role: Role) -> int:
    engine = make_engine(settings.db_url)
    init_db(engine)
    with make_session_factory(engine)() as db:
        service = UserService(db, settings)
        password = _read_password(args.password_stdin)
        user = service.create_user(args.username, password, role)
        print(f"Created {role.value} '{user.username}' (id={user.id}).")
    return 0


def _cmd_create_key(settings: Settings, args: argparse.Namespace) -> int:
    engine = make_engine(settings.db_url)
    init_db(engine)
    with make_session_factory(engine)() as db:
        user = UserService(db, settings).get_by_username(args.username)
        if user is None:
            raise UserServiceError(f"User '{args.username}' not found.")
        new = ApiKeyService(db).create(user, args.name)
        print(f"API key for '{user.username}' ({user.role.value}), name '{args.name}':")
        print(new.raw_key)
        print("Store it now: it is not saved anywhere and cannot be shown again.")
    return 0


def _cmd_serve(settings: Settings, args: argparse.Namespace) -> int:
    import uvicorn

    from .main import create_app

    host = args.host or settings.host
    port = args.port or settings.port
    print(f"Ollama IAM Gateway on http://{host}:{port}  ->  Ollama at {settings.ollama_url}")
    print(f"Database: {settings.db_url}")
    uvicorn.run(create_app(settings), host=host, port=port, log_level="info")
    return 0


def _cmd_list_users(settings: Settings, _args: argparse.Namespace) -> int:
    engine = make_engine(settings.db_url)
    init_db(engine)
    with make_session_factory(engine)() as db:
        users = UserService(db, settings).list_users()
        if not users:
            print("No users.")
            return 0
        print(f"{'ID':>3}  {'USERNAME':<20} {'ROLE':<7} {'ACTIVE':<6} CREATED (UTC)")
        for u in users:
            print(f"{u.id:>3}  {u.username:<20} {u.role.value:<7} "
                  f"{'yes' if u.is_active else 'no':<6} {u.created_at:%Y-%m-%d %H:%M}")
    return 0


def _cmd_show_policy(_settings: Settings, _args: argparse.Namespace) -> int:
    rows = PolicyEngine().table()
    print(f"{'METHODS':<12} {'PATH':<42} {'PERMISSION':<14} ROLES")
    for methods, pattern, perm, roles in rows:
        print(f"{methods:<12} {pattern:<42} {perm:<14} {roles}")
    print(f"\n{len(rows)} rules. Anything else: denied for everyone (default deny).")
    return 0


def _cmd_check_access(_settings: Settings, args: argparse.Namespace) -> int:
    role = None if args.role == "anonymous" else Role(args.role)
    d = PolicyEngine().decide(role, args.method, args.path)
    print(f"{'ALLOW' if d.allowed else 'DENY '}  {args.role} {args.method.upper()} {args.path}  ({d.reason})")
    return 0 if d.allowed else 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ollama_iam_gateway.cli",
                                     description="Ollama IAM Gateway admin tools")
    parser.add_argument("--db", help="database URL (overrides IAM_DB_URL)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create the database tables")

    p_admin = sub.add_parser("create-admin", help="create an admin user (first-time setup)")
    p_admin.add_argument("--username", required=True)
    p_admin.add_argument("--password-stdin", action="store_true",
                         help="read the password from stdin instead of prompting")

    p_user = sub.add_parser("create-user", help="create a user with any role")
    p_user.add_argument("--username", required=True)
    p_user.add_argument("--role", required=True, choices=[r.value for r in Role])
    p_user.add_argument("--password-stdin", action="store_true",
                        help="read the password from stdin instead of prompting")

    p_key = sub.add_parser("create-key", help="create an API key for a user and print it once")
    p_key.add_argument("--username", required=True)
    p_key.add_argument("--name", default="cli")

    p_serve = sub.add_parser("serve", help="run the gateway")
    p_serve.add_argument("--host", help="listen address (default IAM_HOST or 127.0.0.1)")
    p_serve.add_argument("--port", type=int, help="listen port (default IAM_PORT or 8080)")

    sub.add_parser("list-users", help="list all users")

    sub.add_parser("show-policy", help="print the endpoint -> permission -> role table")

    p_check = sub.add_parser("check-access", help="ask the policy engine for one decision")
    p_check.add_argument("--role", required=True,
                         choices=["anonymous"] + [r.value for r in Role])
    p_check.add_argument("method")
    p_check.add_argument("path")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    if args.db:
        settings = replace(settings, db_url=args.db)

    try:
        if args.command == "init-db":
            return _cmd_init_db(settings, args)
        if args.command == "create-admin":
            return _cmd_create_user(settings, args, Role.ADMIN)
        if args.command == "create-user":
            return _cmd_create_user(settings, args, Role(args.role))
        if args.command == "create-key":
            return _cmd_create_key(settings, args)
        if args.command == "serve":
            return _cmd_serve(settings, args)
        if args.command == "list-users":
            return _cmd_list_users(settings, args)
        if args.command == "show-policy":
            return _cmd_show_policy(settings, args)
        if args.command == "check-access":
            return _cmd_check_access(settings, args)
    except UserServiceError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 2  # unreachable: argparse enforces a command


if __name__ == "__main__":
    sys.exit(main())
