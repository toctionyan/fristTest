from typing import Literal

UserRole = Literal["customer", "operator", "developer", "admin"]

ALL_ROLES: tuple[UserRole, ...] = ("customer", "operator", "developer", "admin")


class PermissionDenied(Exception):
    def __init__(self, permission: str, role: str):
        super().__init__(
            f"role {role!r} is not allowed to use permission {permission!r}"
        )
        self.permission = permission
        self.role = role


ROLE_PERMISSIONS: dict[UserRole, set[str]] = {
    "customer": {
        "chat:use",
        "business:read_own",
        "business:write_own",
        "after_sales:create_own",
        "refund:create_own",
        "invoice:create_own",
        "invoice:read_own",
        "complaint:create_own",
        "approval:customer_confirm",
        "human:transfer",
    },
    "operator": {
        "chat:use",
        "business:read_any",
        "business:write_any",
        "after_sales:review",
        "refund:review",
        "invoice:review",
        "complaint:handle",
        "complaint:review",
        "support:handoff:review",
        "approval:operator_review",
        "human:accept",
    },
    "developer": {
        "chat:use",
        "debug:read",
        "trace:read",
        "documents:read",
        "eval:run",
    },
    "admin": {
        "chat:use",
        "business:read_own",
        "business:read_any",
        "business:write_own",
        "business:write_any",
        "after_sales:create_own",
        "after_sales:review",
        "refund:create_own",
        "refund:review",
        "invoice:create_own",
        "invoice:read_own",
        "invoice:review",
        "complaint:create_own",
        "complaint:handle",
        "complaint:review",
        "support:handoff:review",
        "approval:customer_confirm",
        "approval:operator_review",
        "human:transfer",
        "human:accept",
        "debug:read",
        "trace:read",
        "documents:read",
        "documents:write",
        "eval:run",
    },
}


def normalize_role(role: str | None) -> UserRole:
    normalized = (role or "customer").strip().lower()
    # 兼容 Spring Security / 若依 常见 ROLE_ADMIN、ROLE_USER 写法。
    if normalized.startswith("role_"):
        normalized = normalized[5:]
    aliases = {
        "user": "customer",
        "member": "customer",
        "staff": "operator",
        "ops": "operator",
        "dev": "developer",
        "root": "admin",
        "super_admin": "admin",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in ALL_ROLES:
        return "customer"
    return normalized  # type: ignore[return-value]


def can(role: str | None, permission: str) -> bool:
    normalized = normalize_role(role)
    return permission in ROLE_PERMISSIONS[normalized]


def require_permission(role: str | None, permission: str) -> None:
    if not can(role, permission):
        raise PermissionDenied(permission=permission, role=normalize_role(role))
