import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App.jsx";
import { api } from "./api.js";

vi.mock("./api.js", () => ({
  newRequestId: (prefix = "ui") => `${prefix}-test-id`,
  api: {
    me: vi.fn(),
    devAccounts: vi.fn(),
    devLogin: vi.fn(),
    orders: vi.fn(),
    queryOrders: vi.fn(),
    order: vi.fn(),
    logistics: vi.fn(),
    orderActions: vi.fn(),
    threadMessages: vi.fn(),
    pending: vi.fn(),
    transactions: vi.fn(),
    reconcile: vi.fn(),
    chatTurn: vi.fn(),
    startTransaction: vi.fn()
  }
}));

const actor = { tenant_id: "tenant-a", user_id: "u001", source: "signed_session" };
const orders = [
  { order_id: "10001", product_name: "定制马克杯", amount: 99, status: "PAID" },
  { order_id: "10002", product_name: "无线鼠标", amount: 199, status: "SHIPPED" }
];

describe("full product journey", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    api.me.mockResolvedValue({ actor });
    api.orders.mockResolvedValue({ orders });
    api.order.mockImplementation(async (_token, orderId) => ({ order: orders.find((row) => row.order_id === orderId) }));
    api.logistics.mockResolvedValue({ logistics: null });
    api.orderActions.mockResolvedValue({ actions: [] });
    api.threadMessages.mockResolvedValue({ items: [] });
    api.pending.mockResolvedValue({ items: [] });
    api.transactions.mockResolvedValue({
      items: [{ draft_id: "draft-1", thread_id: "thread-other", target_summary: "跨线程退款", draft_state: "NEEDS_INPUT" }]
    });
  });

  it("keeps explicit order selection, exposes accessible chat, and navigates transaction threads", async () => {
    const user = userEvent.setup();
    render(<App />);

    const mouseText = await screen.findByText("无线鼠标");
    const mouseButton = mouseText.closest("button");
    await user.click(mouseButton);
    await waitFor(() => expect(mouseButton).toHaveClass("active"));
    expect(await screen.findByRole("heading", { name: "无线鼠标" })).toBeInTheDocument();

    await waitFor(() => expect(mouseButton).toHaveClass("active"));
    expect(screen.getByRole("log", { name: "对话记录" })).toHaveAttribute("aria-live", "polite");
    expect(screen.getByRole("textbox", { name: "输入问题" })).toHaveAttribute("maxlength", "8000");
    expect(screen.getByRole("button", { name: "新会话" })).toBeInTheDocument();

    const transaction = await screen.findByRole("button", { name: /跨线程退款/ });
    expect(transaction).toHaveTextContent("待补充");
    await user.click(transaction);
    await waitFor(() => expect(screen.getByText("thread-other")).toBeInTheDocument());
    expect(api.threadMessages).toHaveBeenCalledWith(undefined, "thread-other");
    expect(api.pending).toHaveBeenCalledWith("", "thread-other");
  });

  it("keeps the chat composer operable while order details are still loading", async () => {
    const user = userEvent.setup();
    let resolveOrder;
    api.order.mockImplementation(() => new Promise((resolve) => {
      resolveOrder = resolve;
    }));

    render(<App />);

    const input = await screen.findByRole("textbox", { name: "输入问题" });
    await user.type(input, "我买过什么？");
    expect(screen.getByRole("button", { name: "发送" })).toBeEnabled();

    resolveOrder?.({ order: orders[0] });
  });
});
