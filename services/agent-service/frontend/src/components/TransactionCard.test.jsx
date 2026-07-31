import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TransactionCard } from "./TransactionCard.jsx";

function interaction(field) {
  return {
    interaction_id: "h_offer:1",
    lifecycle: "collecting_input",
    title: "申请售后",
    target: "订单 10001",
    current_step: 1,
    total_steps: 1,
    fields: [field],
    control: {
      offer_handle: "h_offer:1",
      action_id: "create_after_sales_request",
      target_handle: "h_order:10001",
      form_id: "form-1",
      form_version: 1,
      conversation_revision: 1
    }
  };
}

const noop = () => {};

describe("TransactionCard", () => {
  it("does not show custom choice for choice_or_text without allow_custom", () => {
    render(
      <TransactionCard
        interaction={interaction({
          name: "reason_code",
          label: "问题类型",
          control: "choice_or_text",
          options: [{ value: "WRONG_ITEM", label: "发错商品" }],
          allow_custom: false,
          step: 1
        })}
        threadId="thread-a"
        token="token"
        onResponse={noop}
        onClear={noop}
        onError={noop}
      />
    );

    expect(screen.queryByRole("option", { name: "其他" })).not.toBeInTheDocument();
  });

  it("keeps a stable custom text input when allow_custom is enabled", async () => {
    render(
      <TransactionCard
        interaction={interaction({
          name: "reason",
          label: "原因",
          control: "choice_or_text",
          options: [{ value: "duplicate", label: "重复下单" }],
          allow_custom: true,
          placeholder: "请输入原因",
          step: 1
        })}
        threadId="thread-a"
        token="token"
        onResponse={noop}
        onClear={noop}
        onError={noop}
      />
    );

    await userEvent.selectOptions(screen.getByRole("combobox"), "__custom__");
    expect(screen.getByPlaceholderText("请输入原因")).toBeInTheDocument();
  });

  it("renders terminal updates as read-only cards", () => {
    const onClear = vi.fn();
    render(
      <TransactionCard
        update={{ lifecycle: "committed", title: "申请发票", message: "已提交" }}
        threadId="thread-a"
        token="token"
        onResponse={noop}
        onClear={onClear}
        onError={noop}
      />
    );

    expect(screen.getAllByText("已提交").length).toBeGreaterThan(0);
    expect(screen.getByText("办理结果")).toBeInTheDocument();
    expect(screen.queryByText("待办理事务")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "收起" })).toBeInTheDocument();
  });

  it("does not retain a pending title after the lifecycle is terminal", () => {
    render(
      <TransactionCard
        interaction={{ ...interaction({}), title: "待办理事务" }}
        update={{ lifecycle: "committed", message: "业务动作已提交。" }}
        threadId="thread-a"
        token="token"
        onResponse={noop}
        onClear={noop}
        onError={noop}
      />
    );

    expect(screen.getByRole("heading", { name: "办理完成" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "待办理事务" })).not.toBeInTheDocument();
  });
});
