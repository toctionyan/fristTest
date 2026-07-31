from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator

UserRole = Literal["customer", "operator", "developer", "admin"]
AuthorityType = Literal["ui_confirmed", "ui_rejected"]


class ChatRequest(BaseModel):
    thread_id: str = Field(..., description="会话 ID")
    user_id: str = Field("u001", description="业务用户 ID")
    role: UserRole = Field("customer", description="当前访问角色")
    message: str
    tenant_id: str | None = Field(None, description="租户 ID；生产环境由 token/session 解析，客户端传入会被覆盖")
    actor_permissions: list[str] = Field(default_factory=list, description="认证后的权限集合；由服务端覆盖", exclude=True)


class ActionAuthorityRequest(BaseModel):
    """Independent, structured final authority for an already-previewed action."""

    thread_id: str
    user_id: str = Field("u001", description="授权人 ID")
    role: UserRole = Field("customer", description="授权人角色")
    decision: Literal["approved", "rejected"]
    authority_type: AuthorityType
    offer_handle: str = Field(..., min_length=1, description="服务端生成的动作草稿句柄")
    action_id: str = Field(..., min_length=1, description="界面展示并确认的动作 ID")
    target_handle: str = Field(..., min_length=1, description="界面展示并确认的目标对象句柄")
    confirmation_id: str = Field(..., min_length=1, description="服务端签发的一次性授权令牌")
    confirmation_version: int = Field(..., ge=1, description="授权卡片对应的确认版本")
    conversation_revision: int = Field(..., ge=1, description="授权卡片创建时的会话版本")
    client_request_id: str = Field(..., min_length=1, description="前端本次授权请求唯一 ID")
    comment: str = ""
    tenant_id: str | None = Field(None, description="租户 ID；生产环境由 token/session 解析，客户端传入会被覆盖")
    actor_permissions: list[str] = Field(default_factory=list, description="认证后的权限集合；由服务端覆盖", exclude=True)

    @model_validator(mode="after")
    def validate_decision_authority_pair(self):
        expected = "ui_confirmed" if self.decision == "approved" else "ui_rejected"
        if self.authority_type != expected:
            raise ValueError("decision 与 authority_type 必须匹配")
        return self


class ActionInputRequest(BaseModel):
    """Structured input submission for a transaction interaction.

    This is intentionally not a chat message.  The payload is the opaque form
    envelope rendered by a client plus named business field values.  The server
    validates it against the persisted action draft and current user/session.
    """

    thread_id: str
    user_id: str = Field("u001", description="提交人 ID")
    role: UserRole = Field("customer", description="提交人角色")
    interaction_mode: Literal["submit_input", "cancel_interaction"]
    offer_handle: str = Field(..., min_length=1)
    action_id: str = Field(..., min_length=1)
    target_handle: str = Field(..., min_length=1)
    form_id: str = Field(..., min_length=1)
    form_version: int = Field(..., ge=1)
    form_step: int = Field(1, ge=1, description="当前事务表单步骤；由服务端持久化契约校验")
    conversation_revision: int = Field(..., ge=1)
    client_request_id: str = Field(..., min_length=1)
    input_values: dict[str, Any] = Field(default_factory=dict)
    tenant_id: str | None = Field(None, description="租户 ID；生产环境由 token/session 解析，客户端传入会被覆盖")
    actor_permissions: list[str] = Field(default_factory=list, description="认证后的权限集合；由服务端覆盖", exclude=True)

    @model_validator(mode="after")
    def validate_input_mode(self):
        if self.interaction_mode == "submit_input" and not isinstance(self.input_values, dict):
            raise ValueError("input_values 必须是对象")
        return self


class TransactionStartRequest(BaseModel):
    """Structured customer transaction start.

    This starts a draft/interaction only; it never commits a business write.
    """

    thread_id: str
    user_id: str = Field("u001", description="提交人 ID")
    role: UserRole = Field("customer", description="提交人角色")
    action_id: str = Field(..., min_length=1)
    target: dict[str, Any] = Field(default_factory=dict)
    input_hints: dict[str, Any] = Field(default_factory=dict)
    client_request_id: str = Field(..., min_length=1)
    tenant_id: str | None = Field(None, description="租户 ID；生产环境由 token/session 解析，客户端传入会被覆盖")
    actor_permissions: list[str] = Field(default_factory=list, description="认证后的权限集合；由服务端覆盖", exclude=True)


class ChatResponse(BaseModel):
    type: Literal["answer", "interaction_required", "error"]
    thread_id: str
    answer: str | None = None
    message: str | None = None
    # Generic, client-neutral transaction interaction contract.  New clients
    # should use this rather than action-specific confirmation fields.
    interaction: dict[str, Any] | None = None
    interaction_update: dict[str, Any] | None = None
    presentation_mode: Literal["narrative", "structured", "interaction", "transaction_status", "notice"] | None = None
    blocks: list[dict[str, Any]] = Field(default_factory=list, description="面向前端的安全展示块；不含内部句柄或授权令牌")
    state: dict[str, Any] | None = None
    sources: list[dict[str, Any]] = []
    error: str | None = None
