"""Command-line tools for the gateway administrator (FR6).

Usage:
    python -m ollama_iam_gateway.cli init-db
    python -m ollama_iam_gateway.cli create-admin --username admin
    python -m ollama_iam_gateway.cli list-users

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

    sub.add_parser("list-users", help="list all users")
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
        if args.command == "list-users":
            return _cmd_list_users(settings, args)
    except UserServiceError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 2  # unreachable: argparse enforces a command


if __name__ == "__main__":
    sys.exit(main())
