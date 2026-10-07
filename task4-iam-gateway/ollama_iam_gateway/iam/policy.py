"""PolicyEngine: the Policy Decision Point (PDP).

Answers one question: may a user with role R call METHOD PATH on Ollama?

    request ──> required_permission(method, path)   (ROUTE_RULES)
            ──> permission in ROLE_PERMISSIONS[role]?
            ──> Decision(allowed, permission, reason)

Rules
  * The route table mirrors Ollama's server/routes.go (GenerateRoutes),
    checked against Ollama v0.40.0 (36 method+path routes in 32 rules).
  * Default deny: a path/method that matches no rule is refused for every
    role, so endpoints added in future Ollama versions stay blocked until
    someone classifies them here.
  * Suspicious paths (dot segments, encoded slashes, empty segments,
    backslashes, control characters) are refused before matching, so the
    path we check is the path Ollama will route.
  * This module has no I/O and no state: easy to test and to reason about.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import unquote

from ..data.models import Role


class Permission(str, enum.Enum):
    PUBLIC = "public"
    MODELS_READ = "models:read"
    INFERENCE = "inference"
    TOOLS_WEB = "tools:web"
    MODELS_MANAGE = "models:manage"
    CLOUD_ACCOUNT = "cloud:account"
    IAM_MANAGE = "iam:manage"


# ---- role -> permissions (hierarchical: each role adds to the one below) --
_VIEWER = {Permission.PUBLIC, Permission.MODELS_READ}
_USER = _VIEWER | {Permission.INFERENCE, Permission.TOOLS_WEB}
_ADMIN = _USER | {Permission.MODELS_MANAGE, Permission.CLOUD_ACCOUNT, Permission.IAM_MANAGE}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset(_VIEWER),
    Role.USER: frozenset(_USER),
    Role.ADMIN: frozenset(_ADMIN),
}


# ---- route -> permission ----------------------------------------------
ANY = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})


@dataclass(frozen=True)
class RouteRule:
    methods: frozenset[str]
    pattern: str            # "/a/b", "/a/:param" (one segment), "/a/*" (one or more)
    permission: Permission

    @property
    def segments(self) -> tuple[str, ...]:
        return _split(self.pattern)


def _rule(methods: Iterable[str] | frozenset[str], pattern: str, perm: Permission) -> RouteRule:
    return RouteRule(frozenset(m.upper() for m in methods), pattern, perm)


P = Permission
ROUTE_RULES: tuple[RouteRule, ...] = (
    # public (health checks, no login)
    _rule({"GET", "HEAD"}, "/", P.PUBLIC),
    _rule({"GET", "HEAD"}, "/api/version", P.PUBLIC),
    # models:read
    _rule({"GET", "HEAD"}, "/api/tags", P.MODELS_READ),
    _rule({"POST"}, "/api/show", P.MODELS_READ),
    _rule({"GET"}, "/api/ps", P.MODELS_READ),
    _rule({"GET"}, "/api/status", P.MODELS_READ),
    _rule({"GET"}, "/v1/models", P.MODELS_READ),
    _rule({"GET"}, "/v1/models/:model", P.MODELS_READ),
    # inference (native + OpenAI + Anthropic compatible + Codex proxy)
    _rule({"POST"}, "/api/generate", P.INFERENCE),
    _rule({"POST"}, "/api/chat", P.INFERENCE),
    _rule({"POST"}, "/api/embed", P.INFERENCE),
    _rule({"POST"}, "/api/embeddings", P.INFERENCE),
    _rule({"POST"}, "/v1/chat/completions", P.INFERENCE),
    _rule({"POST"}, "/v1/completions", P.INFERENCE),
    _rule({"POST"}, "/v1/embeddings", P.INFERENCE),
    _rule({"POST"}, "/v1/responses", P.INFERENCE),
    _rule({"POST"}, "/v1/responses/compact", P.INFERENCE),
    _rule({"POST"}, "/v1/audio/transcriptions", P.INFERENCE),
    _rule({"POST"}, "/v1/messages", P.INFERENCE),
    _rule(ANY, "/api/codex/*", P.INFERENCE),
    # tools:web (uses the server owner's ollama.com account)
    _rule({"POST"}, "/api/experimental/web_search", P.TOOLS_WEB),
    _rule({"POST"}, "/api/experimental/web_fetch", P.TOOLS_WEB),
    _rule({"GET"}, "/api/experimental/model-recommendations", P.TOOLS_WEB),
    # models:manage (disk, network, destructive)
    _rule({"POST"}, "/api/pull", P.MODELS_MANAGE),
    _rule({"POST"}, "/api/push", P.MODELS_MANAGE),
    _rule({"DELETE"}, "/api/delete", P.MODELS_MANAGE),
    _rule({"POST"}, "/api/create", P.MODELS_MANAGE),
    _rule({"POST"}, "/api/copy", P.MODELS_MANAGE),
    _rule({"POST", "HEAD"}, "/api/blobs/:digest", P.MODELS_MANAGE),
    # cloud:account (the machine-wide ollama.com identity)
    _rule({"POST"}, "/api/me", P.CLOUD_ACCOUNT),
    _rule({"POST"}, "/api/signout", P.CLOUD_ACCOUNT),
    _rule({"DELETE"}, "/api/user/keys/:encodedKey", P.CLOUD_ACCOUNT),
)
del P


# ---- path handling ----------------------------------------------------
class UnsafePath(ValueError):
    pass


def _split(path: str) -> tuple[str, ...]:
    return tuple(s for s in path.split("/") if s)


def normalize_path(raw_path: str) -> tuple[str, ...]:
    """Return the decoded path segments, or raise UnsafePath.

    We never "fix" a strange path (e.g. resolve ".."): we refuse it. That
    guarantees the path we authorize is the path Ollama will route.
    """
    if not raw_path or not raw_path.startswith("/"):
        raise UnsafePath("path must start with '/'")
    if "?" in raw_path or "#" in raw_path:
        raise UnsafePath("query/fragment must not be part of the path")
    if raw_path == "/":
        return ()
    raw_segments = raw_path[1:].split("/")
    if any(s == "" for s in raw_segments):
        raise UnsafePath("empty segment or trailing slash")
    out = []
    for raw in raw_segments:
        seg = unquote(raw)
        if seg in (".", ".."):
            raise UnsafePath("dot segment")
        if "/" in seg or "\\" in seg:
            raise UnsafePath("encoded slash or backslash")
        if any(ord(c) < 0x20 or ord(c) == 0x7F for c in seg):
            raise UnsafePath("control character")
        out.append(seg)
    return tuple(out)


def _matches(rule_segments: tuple[str, ...], path_segments: tuple[str, ...]) -> bool:
    if rule_segments and rule_segments[-1] == "*":
        head = rule_segments[:-1]
        return (len(path_segments) > len(head)
                and _matches(head, path_segments[:len(head)]))
    if len(rule_segments) != len(path_segments):
        return False
    return all(r.startswith(":") or r == p for r, p in zip(rule_segments, path_segments))


# ---- decision ---------------------------------------------------------
@dataclass(frozen=True)
class Decision:
    allowed: bool
    permission: Optional[Permission]   # None = no rule matched / unsafe path
    reason: str


class PolicyEngine:
    def __init__(self, rules: tuple[RouteRule, ...] = ROUTE_RULES,
                 role_permissions: dict[Role, frozenset[Permission]] = ROLE_PERMISSIONS) -> None:
        self.rules = rules
        self.role_permissions = role_permissions

    def required_permission(self, method: str, path: str) -> Optional[Permission]:
        """Permission needed for this request; None = not allowed for anyone."""
        try:
            segments = normalize_path(path)
        except UnsafePath:
            return None
        method = (method or "").upper()
        for rule in self.rules:
            if method in rule.methods and _matches(rule.segments, segments):
                return rule.permission
        return None

    def is_public(self, method: str, path: str) -> bool:
        return self.required_permission(method, path) == Permission.PUBLIC

    def has_permission(self, role: Optional[Role], permission: Permission) -> bool:
        if permission == Permission.PUBLIC:
            return True
        return role is not None and permission in self.role_permissions.get(role, frozenset())

    def decide(self, role: Optional[Role], method: str, path: str) -> Decision:
        """Full decision with a reason (the reason goes into the audit log)."""
        try:
            normalize_path(path)
        except UnsafePath as exc:
            return Decision(False, None, f"unsafe path: {exc}")
        permission = self.required_permission(method, path)
        if permission is None:
            return Decision(False, None, "no matching rule (default deny)")
        if self.has_permission(role, permission):
            return Decision(True, permission, f"role grants {permission.value}")
        who = role.value if role else "anonymous"
        return Decision(False, permission, f"role '{who}' lacks {permission.value}")

    def is_allowed(self, role: Optional[Role], method: str, path: str) -> bool:
        return self.decide(role, method, path).allowed

    def table(self) -> list[tuple[str, str, str, str]]:
        """(methods, pattern, permission, roles) rows, for docs and the CLI."""
        rows = []
        for r in self.rules:
            roles = [role.value for role in Role if self.has_permission(role, r.permission)]
            methods = "ANY" if r.methods == ANY else ",".join(sorted(r.methods))
            rows.append((methods, r.pattern, r.permission.value,
                         "everyone" if r.permission == Permission.PUBLIC else ",".join(roles)))
        return rows
