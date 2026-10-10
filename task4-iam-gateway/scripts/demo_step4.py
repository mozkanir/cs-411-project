"""End-to-end demo of the gateway against your REAL Ollama (step 4).

What it does:
  1. creates a fresh demo database (demo_step4.db) with 3 users + API keys
  2. starts the gateway on http://127.0.0.1:8081 (in this process)
  3. sends real requests as viewer / user / admin / nobody and prints results
  4. prints the audit log

Requirements: Ollama running on 127.0.0.1:11434 with a small model pulled.
    python scripts/demo_step4.py
    python scripts/demo_step4.py --model qwen2.5:0.5b --ollama-url http://127.0.0.1:11434
"""
import argparse
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from ollama_iam_gateway.config import Settings  # noqa: E402
from ollama_iam_gateway.data.models import Role  # noqa: E402
from ollama_iam_gateway.iam.apikey_service import ApiKeyService  # noqa: E402
from ollama_iam_gateway.iam.audit_service import AuditService  # noqa: E402
from ollama_iam_gateway.iam.user_service import UserService  # noqa: E402
from ollama_iam_gateway.main import create_app  # noqa: E402

DB_FILE = Path("demo_step4.db")
PORT = 8081
BASE = f"http://127.0.0.1:{PORT}"


def show(label, r):
    body = r.text.strip().replace("\n", " ")
    print(f"  {r.status_code}  {label:<46} {body[:90]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen2.5:0.5b")
    ap.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    args = ap.parse_args()

    if DB_FILE.exists():
        DB_FILE.unlink()
    settings = Settings(db_url=f"sqlite:///{DB_FILE}", ollama_url=args.ollama_url)
    app = create_app(settings)

    keys = {}
    with app.state.session_factory() as db:
        users = UserService(db, settings)
        for name, role in [("admin", Role.ADMIN), ("bob", Role.USER), ("vera", Role.VIEWER)]:
            u = users.create_user(name, "password123", role)
            keys[name] = ApiKeyService(db).create(u, "demo").raw_key

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    print(f"Gateway running on {BASE} -> Ollama {args.ollama_url}\n")

    H = lambda who: {"Authorization": f"Bearer {keys[who]}"}  # noqa: E731
    c = httpx.Client(base_url=BASE, timeout=None, trust_env=False)

    print("=== Authentication ===")
    show("nobody   GET  /  (public)", c.get("/"))
    show("nobody   GET  /api/tags  (no key)", c.get("/api/tags"))
    show("nobody   GET  /api/tags  (fake key)",
         c.get("/api/tags", headers={"Authorization": "Bearer olg_12345678_" + "x" * 43}))

    print("\n=== Authorization (role matrix) ===")
    show("vera(viewer) GET  /api/tags", c.get("/api/tags", headers=H("vera")))
    show("vera(viewer) POST /api/chat", c.post("/api/chat", headers=H("vera"),
                                               json={"model": args.model, "messages": []}))
    show("bob(user)    DELETE /api/delete", c.request("DELETE", "/api/delete", headers=H("bob"),
                                                      json={"model": "does-not-exist"}))
    show("bob(user)    POST /api/pull", c.post("/api/pull", headers=H("bob"),
                                               json={"model": "llama3", "stream": False}))
    show("admin        DELETE /api/delete (missing model)",
         c.request("DELETE", "/api/delete", headers=H("admin"), json={"model": "does-not-exist"}))
    show("admin        GET  /api/something-new", c.get("/api/something-new", headers=H("admin")))
    show("admin        DELETE /api/chat/%2e%2e/delete",
         c.request("DELETE", "/api/chat/%2e%2e/delete", headers=H("admin")))

    print(f"\n=== Streaming chat as bob(user) with {args.model} ===")
    body = {"model": args.model, "messages": [
        {"role": "user", "content": "In one short sentence: what is an API gateway?"}]}
    t0 = time.time()
    with c.stream("POST", "/api/chat", headers=H("bob"), json=body) as r:
        print(f"  status {r.status_code}, first bytes after {time.time() - t0:.2f}s:\n  ", end="")
        chunks = 0
        for line in r.iter_lines():
            if not line:
                continue
            chunks += 1
            msg = json.loads(line)
            if "error" in msg:
                print(f"ERROR from Ollama: {msg['error']}", end="")
            print(msg.get("message", {}).get("content", ""), end="", flush=True)
        print(f"\n  ({chunks} streamed chunks, {time.time() - t0:.2f}s total)")

    print("\n=== OpenAI-compatible endpoint as bob(user) ===")
    r = c.post("/v1/chat/completions", headers=H("bob"), json={
        "model": args.model, "messages": [{"role": "user", "content": "Say hi in 3 words."}]})
    try:
        print(f"  {r.status_code}  {r.json()['choices'][0]['message']['content'].strip()}")
    except Exception:
        show("/v1/chat/completions", r)

    time.sleep(0.3)  # let the last audit rows be written
    print("\n=== Audit log ===")
    with app.state.session_factory() as db:
        for e in reversed(AuditService(db).list_recent(limit=100)):
            if e.event_type in ("USER_CREATED", "APIKEY_CREATED"):
                continue
            print(f"  {e.timestamp:%H:%M:%S} {e.event_type:<14} {e.username or '-':<6} "
                  f"{e.method or '':<6} {e.path or '':<26} {str(e.status_code or ''):<4} "
                  f"{e.model or '':<14} {e.detail or ''}")

    server.should_exit = True
    time.sleep(0.3)


if __name__ == "__main__":
    main()
