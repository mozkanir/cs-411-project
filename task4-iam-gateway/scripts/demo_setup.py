"""Prepare a clean, repeatable demo for the presentation.

    python scripts/demo_setup.py

Creates (in the current folder, all git-ignored):
  demo.db        fresh database with 3 users and 2 API keys
  demo_env.ps1   PowerShell variables: $env:IAM_DB_URL, $GW, $BOB, $VERA
  body.json      a chat request for qwen2.5:0.5b
  delete.json    a delete request for a model that does not exist

Then follow docs/DEMO.md.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ollama_iam_gateway.config import Settings  # noqa: E402
from ollama_iam_gateway.data.db import init_db, make_engine, make_session_factory  # noqa: E402
from ollama_iam_gateway.data.models import Role  # noqa: E402
from ollama_iam_gateway.iam.apikey_service import ApiKeyService  # noqa: E402
from ollama_iam_gateway.iam.user_service import UserService  # noqa: E402

USERS = [  # username, password, role
    ("admin", "admin-demo-1", Role.ADMIN),
    ("bob", "bob-demo-1", Role.USER),
    ("vera", "vera-demo-1", Role.VIEWER),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen2.5:0.5b")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()

    db_file = Path("demo.db")
    if db_file.exists():
        db_file.unlink()
    settings = Settings(db_url="sqlite:///./demo.db")
    engine = make_engine(settings.db_url)
    init_db(engine)

    keys = {}
    with make_session_factory(engine)() as db:
        users = UserService(db, settings)
        admin = None
        for name, password, role in USERS:
            u = users.create_user(name, password, role, actor=admin)
            admin = admin or u
            if role != Role.ADMIN:
                keys[name] = ApiKeyService(db).create(u, "demo-terminal").raw_key
    engine.dispose()

    Path("demo_env.ps1").write_text(
        '$env:IAM_DB_URL = "sqlite:///./demo.db"\n'
        f'$GW = "http://127.0.0.1:{args.port}"\n'
        f'$BOB = "{keys["bob"]}"\n'
        f'$VERA = "{keys["vera"]}"\n', encoding="utf-8")
    Path("body.json").write_text(json.dumps({
        "model": args.model,
        "messages": [{"role": "user", "content": "In one sentence: why should a shared AI server have logins?"}],
    }), encoding="ascii")
    # A model name that does not exist: even with an admin key nothing is deleted.
    Path("delete.json").write_text(json.dumps({"model": "demo-model-that-does-not-exist"}),
                                   encoding="ascii")

    print("Demo ready.\n")
    print(f"  {'user':<6} {'password':<14} role")
    for name, password, role in USERS:
        print(f"  {name:<6} {password:<14} {role.value}")
    print("\nFiles: demo.db, demo_env.ps1 (API keys of bob and vera), body.json, delete.json")
    print("Next:  . .\\demo_env.ps1   then   python -m ollama_iam_gateway.cli serve")


if __name__ == "__main__":
    main()
