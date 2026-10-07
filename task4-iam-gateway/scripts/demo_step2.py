"""Manual walkthrough of the IAM service layer (step 2).

Run from the task4-iam-gateway folder:
    python scripts/demo_step2.py

Uses a separate database file (demo_step2.db) that is recreated each run.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ollama_iam_gateway.config import Settings  # noqa: E402
from ollama_iam_gateway.data.db import init_db, make_engine, make_session_factory  # noqa: E402
from ollama_iam_gateway.data.models import Role  # noqa: E402
from ollama_iam_gateway.iam.apikey_service import ApiKeyService  # noqa: E402
from ollama_iam_gateway.iam.audit_service import AuditService  # noqa: E402
from ollama_iam_gateway.iam.auth_service import AuthService  # noqa: E402
from ollama_iam_gateway.iam.errors import IAMError  # noqa: E402
from ollama_iam_gateway.iam.user_service import UserService  # noqa: E402

DB_FILE = Path("demo_step2.db")


def step(title):
    print(f"\n=== {title} ===")


def attempt(label, fn):
    try:
        result = fn()
        print(f"  OK    {label}")
        return result
    except IAMError as exc:
        print(f"  ERROR {label}: {exc}")


def main():
    if DB_FILE.exists():
        DB_FILE.unlink()
    settings = Settings(db_url=f"sqlite:///{DB_FILE}")
    engine = make_engine(settings.db_url)
    init_db(engine)
    db = make_session_factory(engine)()
    users, auth = UserService(db, settings), AuthService(db, settings)
    keys, audit = ApiKeyService(db), AuditService(db)

    step("1. Create users")
    admin = users.create_user("admin", "admin-pass-1", Role.ADMIN)
    alice = users.create_user("alice", "alice-pass-1", Role.USER, actor=admin)
    attempt("alice tries to create a user", lambda: users.create_user(
        "eve", "eve-pass-123", Role.ADMIN, actor=alice))
    for u in users.list_users():
        print(f"  {u.username:<6} role={u.role.value}")

    step("2. Login")
    r = attempt("alice logs in with the right password",
                lambda: auth.login("alice", "alice-pass-1", "127.0.0.1"))
    print(f"  cookie token (sent to browser): {r.raw_session_token[:12]}...")
    print(f"  stored in DB (sha256)        : {r.session.token_hash[:12]}...")
    attempt("unknown user", lambda: auth.login("nobody", "x", "127.0.0.1"))

    step("3. Lockout after 5 wrong passwords")
    for i in range(1, 6):
        attempt(f"wrong password #{i}", lambda: auth.login("alice", "wrong", "127.0.0.1"))
    attempt("right password while locked", lambda: auth.login("alice", "alice-pass-1"))
    users.unlock(admin, alice.id)
    attempt("right password after admin unlock", lambda: auth.login("alice", "alice-pass-1"))

    step("4. API keys")
    new = keys.create(alice, "my-laptop")
    print(f"  full key (shown once): {new.raw_key}")
    print(f"  stored hash          : {new.key.key_hash[:16]}...")
    print(f"  resolve(valid key)   -> user={keys.resolve(new.raw_key).user.username}")
    print(f"  resolve(fake key)    -> {keys.resolve('olg_12345678_' + 'x' * 43)}")
    keys.revoke(alice, new.key.id)
    print(f"  resolve(after revoke)-> {keys.resolve(new.raw_key)}")

    step("5. Admin disables alice")
    token = auth.login("alice", "alice-pass-1").raw_session_token
    users.set_active(admin, alice.id, False)
    print(f"  alice's session still valid? {auth.sessions.validate(token) is not None}")
    attempt("alice logs in while disabled", lambda: auth.login("alice", "alice-pass-1"))
    attempt("admin disables himself", lambda: users.set_active(admin, admin.id, False))
    attempt("admin demotes the last admin", lambda: users.set_role(admin, admin.id, Role.USER))

    step("6. Audit log (oldest first)")
    for e in reversed(audit.list_recent(limit=50)):
        print(f"  {e.timestamp:%H:%M:%S} {e.event_type:<15} {e.username or '-':<8} {e.detail or ''}")
    db.close()
    engine.dispose()


if __name__ == "__main__":
    main()
