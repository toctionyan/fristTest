import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api.js";
import { useOrders } from "./useOrders.js";

vi.mock("../api.js", () => ({
  api: {
    orders: vi.fn(),
    queryOrders: vi.fn(),
    order: vi.fn(),
    logistics: vi.fn(),
    orderActions: vi.fn()
  }
}));

const rows = [
  { order_id: "10001", product_name: "定制马克杯", amount: 99 },
  { order_id: "10002", product_name: "无线鼠标", amount: 199 }
];

describe("useOrders product state", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.orders.mockResolvedValue({ orders: rows });
    api.order.mockImplementation(async (_token, orderId) => ({ order: rows.find((row) => row.order_id === orderId) }));
    api.logistics.mockResolvedValue({ logistics: null });
    api.orderActions.mockResolvedValue({ actions: [] });
  });

  it("keeps the loader identity and explicit selection stable", async () => {
    const onError = vi.fn();
    const { result } = renderHook(() => useOrders("token", onError));
    await act(async () => {
      await result.current.loadOrders("token", { keepSelection: false });
    });
    const stableLoader = result.current.loadOrders;

    await act(async () => {
      await result.current.selectOrder(rows[1]);
    });

    expect(result.current.selectedOrderId).toBe("10002");
    expect(result.current.selectedOrder?.product_name).toBe("无线鼠标");
    expect(result.current.loadOrders).toBe(stableLoader);
  });

  it("does not let a late boot response overwrite an explicit selection", async () => {
    let releaseOrders;
    api.orders.mockImplementationOnce(() => new Promise((resolve) => {
      releaseOrders = resolve;
    }));
    const { result } = renderHook(() => useOrders("token", vi.fn()));

    let boot;
    await act(async () => {
      boot = result.current.loadOrders("token");
      await result.current.selectOrder(rows[1]);
    });
    await act(async () => {
      releaseOrders({ orders: rows });
      await boot;
    });

    expect(result.current.selectedOrderId).toBe("10002");
    expect(result.current.selectedOrder?.product_name).toBe("无线鼠标");
  });
});
