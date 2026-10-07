import pytest

from ollama_iam_gateway import cli
from ollama_iam_gateway.data.models import Role
from ollama_iam_gateway.iam.policy import (ROLE_PERMISSIONS, Permission as P,
                                           PolicyEngine, UnsafePath, normalize_path)

engine = PolicyEngine()
V, U, A = Role.VIEWER, Role.USER, Role.ADMIN

# Every route registered in Ollama v0.40.0 server/routes.go GenerateRoutes()
# (36 method+path pairs), with the permission from the requirements table.
OLLAMA_ROUTES = [
    ("HEAD", "/", P.PUBLIC), ("GET", "/", P.PUBLIC),
    ("HEAD", "/api/version", P.PUBLIC), ("GET", "/api/version", P.PUBLIC),
    ("GET", "/api/status", P.MODELS_READ),
    ("GET", "/api/codex/v1/responses", P.INFERENCE),      # r.Any(/api/codex/*path)
    ("POST", "/api/pull", P.MODELS_MANAGE),
    ("POST", "/api/push", P.MODELS_MANAGE),
    ("HEAD", "/api/tags", P.MODELS_READ), ("GET", "/api/tags", P.MODELS_READ),
    ("POST", "/api/show", P.MODELS_READ),
    ("DELETE", "/api/delete", P.MODELS_MANAGE),
    ("POST", "/api/me", P.CLOUD_ACCOUNT),
    ("POST", "/api/signout", P.CLOUD_ACCOUNT),
    ("DELETE", "/api/user/keys/c3NoLWVkMjU1MTk", P.CLOUD_ACCOUNT),
    ("POST", "/api/create", P.MODELS_MANAGE),
    ("POST", "/api/blobs/sha256:abc123", P.MODELS_MANAGE),
    ("HEAD", "/api/blobs/sha256:abc123", P.MODELS_MANAGE),
    ("POST", "/api/copy", P.MODELS_MANAGE),
    ("POST", "/api/experimental/web_search", P.TOOLS_WEB),
    ("POST", "/api/experimental/web_fetch", P.TOOLS_WEB),
    ("GET", "/api/experimental/model-recommendations", P.TOOLS_WEB),
    ("GET", "/api/ps", P.MODELS_READ),
    ("POST", "/api/generate", P.INFERENCE),
    ("POST", "/api/chat", P.INFERENCE),
    ("POST", "/api/embed", P.INFERENCE),
    ("POST", "/api/embeddings", P.INFERENCE),
    ("POST", "/v1/chat/completions", P.INFERENCE),
    ("POST", "/v1/completions", P.INFERENCE),
    ("POST", "/v1/embeddings", P.INFERENCE),
    ("GET", "/v1/models", P.MODELS_READ),
    ("GET", "/v1/models/qwen2.5:0.5b", P.MODELS_READ),
    ("POST", "/v1/responses", P.INFERENCE),
    ("POST", "/v1/responses/compact", P.INFERENCE),
    ("POST", "/v1/audio/transcriptions", P.INFERENCE),
    ("POST", "/v1/messages", P.INFERENCE),
]


def test_route_list_is_complete():
    # 35 fixed routes + the codex catch-all = 36 (32 rules; 4 rules cover 2 methods)
    assert len(OLLAMA_ROUTES) == 36
    assert len(set((m, p) for m, p, _ in OLLAMA_ROUTES)) == 36


@pytest.mark.parametrize("method,path,perm", OLLAMA_ROUTES)
def test_every_ollama_route_is_classified(method, path, perm):
    assert engine.required_permission(method, path) == perm


# expected access per permission: (viewer, user, admin)
MATRIX = {
    P.PUBLIC: (True, True, True),
    P.MODELS_READ: (True, True, True),
    P.INFERENCE: (False, True, True),
    P.TOOLS_WEB: (False, True, True),
    P.MODELS_MANAGE: (False, False, True),
    P.CLOUD_ACCOUNT: (False, False, True),
}


@pytest.mark.parametrize("method,path,perm", OLLAMA_ROUTES)
def test_role_matrix(method, path, perm):
    expected = MATRIX[perm]
    assert tuple(engine.is_allowed(r, method, path) for r in (V, U, A)) == expected
    assert engine.is_allowed(None, method, path) is (perm == P.PUBLIC)


def test_roles_are_hierarchical():
    assert ROLE_PERMISSIONS[V] < ROLE_PERMISSIONS[U] < ROLE_PERMISSIONS[A]
    assert engine.has_permission(A, P.IAM_MANAGE)
    assert not engine.has_permission(U, P.IAM_MANAGE)


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/new-endpoint"),      # future Ollama route: blocked until classified
    ("GET", "/api/chat"),              # right path, wrong method
    ("POST", "/api/tags"),
    ("GET", "/api/delete"),
    ("PUT", "/api/pull"),
    ("OPTIONS", "/api/chat"),
    ("POST", "/API/CHAT"),             # Ollama routes are case-sensitive
    ("POST", "/api/chat/extra"),
    ("GET", "/api/codex"),             # wildcard needs at least one segment
    ("GET", "/v1/models/a/b"),         # :model is exactly one segment
    ("TRACE", "/"),
])
def test_default_deny(method, path):
    d = engine.decide(A, method, path)
    assert not d.allowed and d.permission is None
    assert "default deny" in d.reason


@pytest.mark.parametrize("path", [
    "/api/chat/../delete",
    "/api/./delete",
    "/api/%2e%2e/api/delete",
    "/api/chat%2f..%2fdelete",
    "/api/chat%2F..%2Fdelete",
    "//api/delete",
    "/api/delete/",
    "/api\\delete",
    "/api/delete%5c",
    "/api/delete%00",
    "/api/tags?x=1",
    "api/tags",
    "",
])
def test_unsafe_paths_are_refused_even_for_admin(path):
    d = engine.decide(A, "DELETE", path)
    assert not d.allowed
    with pytest.raises(UnsafePath):
        normalize_path(path)


def test_percent_encoding_is_decoded_like_ollama_does():
    # gin routes on the decoded path, so we must check the decoded path too
    assert engine.required_permission("GET", "/v1/models/qwen2.5%3A0.5b") == P.MODELS_READ
    assert engine.required_permission("POST", "/api/%63hat") == P.INFERENCE
    assert engine.required_permission("DELETE", "/api/%64elete") == P.MODELS_MANAGE
    assert not engine.is_allowed(U, "DELETE", "/api/%64elete")


def test_method_case_insensitive_and_public_helper():
    assert engine.is_allowed(U, "post", "/api/chat")
    assert engine.is_public("GET", "/api/version")
    assert not engine.is_public("GET", "/api/tags")


def test_decision_reasons():
    assert engine.decide(V, "POST", "/api/chat").reason == "role 'viewer' lacks inference"
    assert engine.decide(None, "GET", "/api/tags").reason == "role 'anonymous' lacks models:read"
    assert engine.decide(A, "POST", "/api/pull").reason == "role grants models:manage"


def test_table_for_docs():
    rows = engine.table()
    assert len(rows) == 32
    assert ("DELETE", "/api/delete", "models:manage", "admin") in rows
    assert ("GET,HEAD", "/", "public", "everyone") in rows


def test_cli_show_policy_and_check_access(capsys):
    assert cli.main(["show-policy"]) == 0
    assert "default deny" in capsys.readouterr().out
    assert cli.main(["check-access", "--role", "user", "POST", "/api/chat"]) == 0
    assert capsys.readouterr().out.startswith("ALLOW")
    assert cli.main(["check-access", "--role", "user", "DELETE", "/api/delete"]) == 3
    assert "lacks models:manage" in capsys.readouterr().out
