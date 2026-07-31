import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TransactionCenter } from "./TransactionCenter.jsx";

describe("TransactionCenter", () => {
  it("shows cross-thread transaction state and allows a safe refresh", async () => {
    const refresh = vi.fn();
    const select = vi.fn();
    render(
      <TransactionCenter
        onRefresh={refresh}
        onSelect={select}
        items={[{
          draft_id: "draft:1",
          thread_id: "older-thread",
          action_id: "create_refund",
          draft_state: "SUBMISSION_UNKNOWN",
          target_summary: "订单 10001 退款"
        }]}
      />
    );
    expect(screen.getByText("订单 10001 退款")).toBeInTheDocument();
    expect(screen.getByText("提交结果确认中，请勿重复操作")).toBeInTheDocument();
    await userEvent.click(screen.getByTitle("刷新办理记录"));
    expect(refresh).toHaveBeenCalledOnce();
    await userEvent.click(screen.getByText("订单 10001 退款"));
    expect(select).toHaveBeenCalledWith(expect.objectContaining({ draft_id: "draft:1" }));
  });
});
