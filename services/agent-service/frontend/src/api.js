const API_PREFIX = "/api";

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request(path, { method = "GET", body, token } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const response = await fetch(`${API_PREFIX}${path}`, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof payload === "object" && payload !== null ? payload.detail || payload.error : payload;
    const message = typeof detail === "string" ? detail : detail?.message || `请求失败 (${response.status})`;
    throw new ApiError(message, response.status, detail);
  }
  return payload;
}

export function newRequestId(prefix = "ui") {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export const api = {
  me: (token) => request("/session/me", { token }),
  devAccounts: () => request("/session/dev-accounts"),
  devLogin: (payload) => request("/session/dev-login", { method: "POST", body: payload }),
  actions: (token) => request("/actions", { token }),
  orders: (token) => request("/orders", { token }),
  queryOrders: (token, payload) => request("/orders/query", { method: "POST", token, body: payload }),
  order: (token, orderId) => request(`/orders/${encodeURIComponent(orderId)}`, { token }),
  logistics: (token, orderId) => request(`/orders/${encodeURIComponent(orderId)}/logistics`, { token }),
  orderActions: (token, orderId) => request(`/orders/${encodeURIComponent(orderId)}/actions`, { token }),
  chatTurn: (token, payload) => request("/chat/turn", { method: "POST", token, body: payload }),
  startTransaction: (token, payload) => request("/transactions/start", { method: "POST", token, body: payload }),
  submitInput: (token, payload) => request("/transactions/input", { method: "POST", token, body: payload }),
  submitAuthority: (token, payload) => request("/transactions/authority", { method: "POST", token, body: payload }),
  threadMessages: (token, threadId, limit = 100) => request(`/threads/${encodeURIComponent(threadId)}/messages?limit=${encodeURIComponent(limit)}`, { token }),
  threads: (token) => request("/threads", { token }),
  transactions: (token, threadId) => request(`/transactions${threadId ? `?thread_id=${encodeURIComponent(threadId)}` : ""}`, { token }),
  transaction: (token, draftId) => request(`/transactions/${encodeURIComponent(draftId)}`, { token }),
  transactionReceipt: (token, draftId) => request(`/transactions/${encodeURIComponent(draftId)}/receipt`, { token }),
  pending: (token, threadId) => request(`/threads/${encodeURIComponent(threadId)}/pending`, { token }),
  reconcile: (token, threadId) => request(`/threads/${encodeURIComponent(threadId)}/reconcile`, { method: "POST", token })
};
