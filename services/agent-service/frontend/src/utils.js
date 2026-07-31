import { ApiError } from "./api.js";

export const TOKEN_KEY = "agent.product.token";
export const THREAD_PREFIX = "agent.product.thread";

export function actorKey(actor) {
  return `${actor?.tenant_id || "default"}:${actor?.user_id || "anonymous"}`;
}

export function storedThreadKey(actor) {
  return `${THREAD_PREFIX}:${actorKey(actor)}`;
}

export function createThreadId(actor) {
  const tenant = String(actor?.tenant_id || "default").replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 32);
  const user = String(actor?.user_id || "user").replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 32);
  return `${tenant}-${user}-${Date.now()}`;
}

export function normalizeOrders(payload) {
  if (Array.isArray(payload?.orders)) return payload.orders;
  if (Array.isArray(payload?.data)) return payload.data;
  return [];
}

export function getOrderId(order) {
  return String(order?.order_id || order?.id || "");
}

export function money(value) {
  const amount = Number(value || 0);
  return amount.toLocaleString("zh-CN", { style: "currency", currency: "CNY" });
}

export function statusText(value) {
  const map = {
    PAID: "已支付",
    PENDING_PAYMENT: "待支付",
    SHIPPED: "已发货",
    DELIVERED: "已签收",
    CANCELLED: "已取消",
    REFUNDING: "退款中",
    AFTER_SALES: "售后中"
  };
  return map[String(value || "")] || String(value || "未知");
}

export function lifecycleText(value) {
  const map = {
    collecting_input: "待补充",
    NEEDS_INPUT: "待补充",
    awaiting_authority: "待确认",
    AWAITING_AUTHORIZATION: "待确认",
    committed: "已提交",
    COMMITTED: "已提交",
    commit_failed: "提交失败",
    FAILED_RETRYABLE: "可重试",
    FAILED_FINAL: "办理失败",
    expired: "已失效",
    EXPIRED: "已失效",
    queued: "排队中",
    submission_unknown: "结果确认中",
    SUBMISSION_UNKNOWN: "结果确认中",
    RECONCILIATION_REQUIRED: "等待对账"
  };
  return map[String(value || "")] || String(value || "处理中");
}

export function errorMessage(error) {
  if (error instanceof ApiError) return error.message;
  return error?.message || "请求失败，请稍后重试。";
}

export function pickInteractionUpdate(response) {
  if (response?.interaction) return { live: response.interaction, update: null };
  if (response?.interaction_update) return { live: null, update: response.interaction_update };
  return { live: null, update: null };
}
