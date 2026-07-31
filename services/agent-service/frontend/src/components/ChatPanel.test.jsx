import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChatPanel } from "./ChatPanel.jsx";

const noop = () => {};

function fullCoverage(sourcePopulation, count) {
  return {
    mode: "full",
    source_population: sourcePopulation,
    status: "complete",
    resolved_member_count: count,
    presented_member_count: count,
    presented_population_proof: "same_member_identity_set",
  };
}

const canonicalOrderList = {
  type: "order_list",
  role: "primary",
  contract_id: "commerce.order_list@1",
  contract_version: 1,
  contract_owner: "ecommerce_overlay",
  projection_boundary: "ecommerce_order_list_projector",
  producer: "ecommerce.orders.list.capability",
  title: "订单（2）",
  summary: "已找到 2 笔订单。",
  degradation: { level: "none", missing_optional_semantics: [] },
  coverage: fullCoverage("resolved_order_collection", 2),
  items: [
    { order_id: "10002", product_name: "机械键盘", status: "已签收", amount: 399, reference_handle: "artifact:order:10002" },
    { order_id: "10004", product_name: "定制马克杯", status: "已签收", amount: 59, reference_handle: "artifact:order:10004" },
  ],
};

const canonicalLogisticsOverview = {
  type: "logistics_overview",
  role: "primary",
  contract_id: "commerce.logistics_overview@1",
  contract_version: 1,
  contract_owner: "ecommerce_overlay",
  projection_boundary: "ecommerce_logistics_overview_projector",
  producer: "ecommerce.orders.logistics.capability",
  title: "物流总览",
  summary: "共 2 笔订单：1 笔待发货、1 笔运输中。",
  degradation: { level: "none", missing_optional_semantics: [] },
  coverage: {
    ...fullCoverage("requested_result_population", 2),
    presented_population_proof: "business_matched_member_identity_set",
  },
  query_scope: {
    source_population_count: 4,
    matched_population_count: 2,
    presented_population_count: 2,
    applied_conditions: { delivery_status: "运输中_or_待发货" },
  },
  items: [
    {
      order_id: "10003",
      product_name: "无线鼠标",
      status: "待发货",
      latest: "商家正在备货",
      estimate: "预计 24 小时内发货",
    },
    {
      order_id: "10001",
      product_name: "蓝牙耳机",
      status: "运输中",
      latest: "已到达 Phoenix 分拨中心",
      estimate: "预计 2 天内送达",
    },
  ],
};

const canonicalBusinessStatusList = {
  type: "business_status_list",
  role: "primary",
  contract_id: "commerce.business_status_list@1",
  contract_version: 1,
  contract_owner: "ecommerce_overlay",
  projection_boundary: "ecommerce_business_status_list_projector",
  producer: "ecommerce.related_resource_status.capability",
  title: "退款记录",
  summary: "共 1 条退款记录。",
  target_label: "订单 10001",
  degradation: { level: "none", missing_optional_semantics: [] },
  coverage: fullCoverage("resolved_related_resource_collection", 1),
  items: [
    {
      record_reference: "refund-10001",
      record_kind: "退款",
      status: "处理中",
      updated_at: "2026-07-06 10:00",
      order_id: "10001",
    },
  ],
};

const canonicalTransactionStatus = {
  type: "transaction_status",
  role: "primary",
  contract_id: "runtime.transaction_status@1",
  contract_version: 1,
  contract_owner: "runtime_transaction_projection",
  projection_boundary: "runtime_transaction_status_projector",
  producer: "runtime.transaction_status.outcome",
  summary: "退款申请正在处理中。",
  data: {
    draft: { draft_state: "COMMITTING" },
    receipt: { receipt_id: "receipt-10001" },
  },
  degradation: { level: "none", missing_optional_semantics: [] },
  coverage: {
    mode: "not_collection",
    source_population: "runtime_transaction_outcome",
    status: "not_applicable",
  },
};

const canonicalInteractionTimeline = {
  type: "interaction_timeline",
  role: "primary",
  contract_id: "runtime.interaction_timeline@1",
  contract_version: 1,
  contract_owner: "runtime_interaction_projection",
  projection_boundary: "runtime_interaction_timeline_projector",
  producer: "runtime.transaction_interaction",
  interaction_id: "interaction-refund-10004",
  lifecycle: "collecting_input",
  summary: "申请退款需要补充退款原因。",
  target: "定制马克杯（订单 10004）",
  next_step: "请在下方办理卡补充退款原因。",
  read_only: true,
  degradation: { level: "none", missing_optional_semantics: [] },
  coverage: {
    mode: "not_collection",
    source_population: "runtime_transaction_interaction",
    status: "not_applicable",
  },
};

const canonicalNextActions = {
  type: "next_actions",
  role: "primary",
  contract_id: "commerce.next_actions@1",
  contract_version: 1,
  contract_owner: "ecommerce_overlay",
  projection_boundary: "ecommerce_next_actions_projector",
  producer: "ecommerce.eligibility_or_consultation.capability",
  title: "你可以继续办理",
  summary: "该订单支持申请退款。",
  degradation: { level: "none", missing_optional_semantics: [] },
  coverage: {
    mode: "not_collection",
    source_population: "verified_single_target",
    status: "not_applicable",
  },
  target_order_id: "10001",
  target_product_name: "蓝牙耳机",
  actions: [
    {
      action_id: "create_refund",
      label: "申请退款",
      target: { resource_type: "order", order_id: "10001" },
    },
  ],
};

const canonicalBlockedEligibility = {
  type: "eligibility_decision",
  role: "primary",
  contract_id: "commerce.eligibility_decision@1",
  contract_version: 1,
  contract_owner: "ecommerce_overlay",
  projection_boundary: "ecommerce_eligibility_decision_projector",
  producer: "ecommerce.eligibility.capability",
  title: "退款资格暂未通过",
  summary: "订单已发货但尚未签收，当前不能申请退款。",
  target_order_id: "10001",
  target_product_name: "蓝牙耳机",
  eligibility_kind: "退款资格",
  decision: "BLOCKED",
  eligible: false,
  actions: [],
  degradation: { level: "none", missing_optional_semantics: [] },
  coverage: {
    mode: "not_collection",
    source_population: "verified_single_target",
    status: "not_applicable",
  },
};

const canonicalInvoiceAdvisory = {
  type: "advisory",
  role: "primary",
  contract_id: "commerce.advisory@1",
  contract_version: 1,
  contract_owner: "ecommerce_overlay",
  projection_boundary: "ecommerce_advisory_projector",
  producer: "ecommerce.order.consultation.capability",
  title: "订单政策咨询",
  summary: "已支付且未全额退款的订单可以申请电子发票。",
  target_order_id: "10004",
  target_product_name: "定制马克杯",
  question: "订单10004能开发票吗",
  knowledge_available: true,
  items: [{
    title: "开票政策",
    content: "已支付且未全额退款的订单可以申请电子发票。",
    source: "内置开票政策",
  }],
  degradation: { level: "none", missing_optional_semantics: [] },
  coverage: {
    mode: "not_collection",
    source_population: "verified_single_target_consultation",
    status: "not_applicable",
  },
};

function renderPanel(messages, overrides = {}) {
  return render(
    <ChatPanel
      token="token"
      threadId="thread-a"
      ensureThread={() => "thread-a"}
      messages={messages}
      setMessages={noop}
      applyResponse={noop}
      refreshPending={vi.fn()}
      busy={false}
      setBusy={noop}
      onError={noop}
      {...overrides}
    />,
  );
}

function structuredMessage(id, block) {
  return { id, role: "assistant", presentationMode: "structured", blocks: block ? [block] : [] };
}

describe("ChatPanel canonical presentation", () => {
  it("renders a narrative primary expression once even when legacy blocks exist", () => {
    renderPanel([
      {
        id: "a-1",
        role: "assistant",
        presentationMode: "narrative",
        text: "您好，欢迎来到电商客服。",
        blocks: [{ type: "text", content: "您好，欢迎来到电商客服。" }],
      },
    ]);

    expect(screen.getAllByText("您好，欢迎来到电商客服。")).toHaveLength(1);
  });

  it("renders the canonical commerce order-list contract without field fallbacks", () => {
    renderPanel([
      {
        id: "a-2",
        role: "assistant",
        presentationMode: "structured",
        text: "您目前共有 2 笔订单。",
        blocks: [canonicalOrderList],
      },
    ]);

    expect(screen.queryByText("您目前共有 2 笔订单。")).not.toBeInTheDocument();
    expect(screen.getByText("机械键盘")).toBeInTheDocument();
    expect(screen.getByText("订单 10002")).toBeInTheDocument();
    expect(screen.getByText("定制马克杯")).toBeInTheDocument();
    expect(screen.getByText("订单 10004")).toBeInTheDocument();
  });

  it("does not silently truncate a full-coverage collection in the browser", () => {
    const sixOrders = [
      ...canonicalOrderList.items,
      { order_id: "10005", product_name: "智能手表", status: "已发货", amount: 1299, reference_handle: "artifact:order:10005" },
      { order_id: "10006", product_name: "运动水杯", status: "待发货", amount: 79, reference_handle: "artifact:order:10006" },
      { order_id: "10007", product_name: "降噪耳机", status: "运输中", amount: 899, reference_handle: "artifact:order:10007" },
      { order_id: "10008", product_name: "机械键盘 Pro", status: "已签收", amount: 599, reference_handle: "artifact:order:10008" },
    ];
    const block = {
      ...canonicalOrderList,
      title: "订单（6）",
      summary: "已找到 6 笔订单。",
      items: sixOrders,
      coverage: fullCoverage("resolved_order_collection", 6),
    };

    renderPanel([structuredMessage("a-many-orders", block)]);

    for (const item of sixOrders) {
      expect(screen.getByText(item.product_name)).toBeInTheDocument();
      expect(screen.getByText(`订单 ${item.order_id}`)).toBeInTheDocument();
    }
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders all canonical logistics fields instead of an empty unknown block", () => {
    renderPanel([structuredMessage("a-logistics", canonicalLogisticsOverview)]);

    expect(screen.getByText("无线鼠标")).toBeInTheDocument();
    expect(screen.getByText("订单 10003")).toBeInTheDocument();
    expect(screen.getByText("商家正在备货")).toBeInTheDocument();
    expect(screen.getByText("预计 24 小时内发货")).toBeInTheDocument();
    expect(screen.getByText("蓝牙耳机")).toBeInTheDocument();
    expect(screen.getByText("已到达 Phoenix 分拨中心")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders a canonical business status list", () => {
    renderPanel([structuredMessage("a-status", canonicalBusinessStatusList)]);

    expect(screen.getByText("退款 refund-10001")).toBeInTheDocument();
    expect(screen.getByText("关联订单 10001")).toBeInTheDocument();
    expect(screen.getByText("处理中")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps the verified query target visible for an empty business status list", () => {
    renderPanel([structuredMessage("a-empty-status", {
      ...canonicalBusinessStatusList,
      title: "发票记录",
      summary: "暂未找到业务记录。",
      target_order_id: "10004",
      target_product_name: "定制马克杯",
      target_label: "定制马克杯（订单 10004）",
      coverage: fullCoverage("resolved_related_resource_collection", 0),
      items: [],
    })]);

    expect(screen.getByText("暂未找到业务记录。")).toBeInTheDocument();
    expect(screen.getByText("定制马克杯（订单 10004）")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders a canonical runtime transaction status through its registered contract", () => {
    renderPanel([structuredMessage("a-transaction", canonicalTransactionStatus)]);

    expect(screen.getByText("退款申请正在处理中。")).toBeInTheDocument();
    expect(screen.getByText("当前状态：COMMITTING")).toBeInTheDocument();
    expect(screen.getByText("回执：receipt-10001")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders a read-only interaction timeline event in the chat transcript", () => {
    renderPanel([structuredMessage("a-interaction", canonicalInteractionTimeline)]);

    expect(screen.getByText("待处理办理事项")).toBeInTheDocument();
    expect(screen.getByText("申请退款需要补充退款原因。")).toBeInTheDocument();
    expect(screen.getByText("对象：定制马克杯（订单 10004）")).toBeInTheDocument();
    expect(screen.getByText("请在下方办理卡补充退款原因。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /确认|下一步|暂不/ })).not.toBeInTheDocument();
  });

  it("places the live interaction control after the transcript instead of above it", () => {
    const { container } = renderPanel([], { interactionSlot: <div data-testid="live-interaction">办理卡</div> });
    const transcript = container.querySelector(".chat-log");
    const liveInteraction = screen.getByTestId("live-interaction");
    const composer = container.querySelector(".chat-box");

    expect(transcript).not.toBeNull();
    expect(composer).not.toBeNull();
    expect(transcript.compareDocumentPosition(liveInteraction) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(liveInteraction.compareDocumentPosition(composer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("dispatches a canonical next-action without guessing target fields", () => {
    const onStartAction = vi.fn();
    renderPanel([structuredMessage("a-actions", canonicalNextActions)], { onStartAction });

    fireEvent.click(screen.getByRole("button", { name: "申请退款" }));
    expect(onStartAction).toHaveBeenCalledWith(canonicalNextActions.actions[0]);
  });

  it("renders a blocked eligibility decision even when no action is allowed", () => {
    renderPanel([structuredMessage("a-blocked-eligibility", canonicalBlockedEligibility)]);

    expect(screen.getByText("退款资格暂未通过")).toBeInTheDocument();
    expect(screen.getByText("订单已发货但尚未签收，当前不能申请退款。")).toBeInTheDocument();
    expect(screen.getByText("蓝牙耳机（订单 10001）")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "申请退款" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders invoice consultation as read-only evidence without refund or after-sales actions", () => {
    renderPanel([structuredMessage("a-invoice-advisory", canonicalInvoiceAdvisory)]);

    expect(screen.getByText("订单政策咨询")).toBeInTheDocument();
    expect(screen.getAllByText("已支付且未全额退款的订单可以申请电子发票。")).toHaveLength(2);
    expect(screen.getByText("定制马克杯（订单 10004）")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "申请退款" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "申请售后" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not render a partial legacy order row when its contract is invalid", () => {
    renderPanel([
      {
        id: "a-3",
        role: "assistant",
        presentationMode: "structured",
        blocks: [
          {
            type: "order_list",
            contract_id: "commerce.order_list@0",
            contract_version: 0,
            items: [{ title: "机械键盘", status: "已签收" }],
          },
        ],
      },
    ]);

    expect(screen.getByRole("alert")).toHaveTextContent("结果暂时无法完整展示");
    expect(screen.queryByText("机械键盘")).not.toBeInTheDocument();
  });

  it("rejects a collection whose coverage claims do not match its visible members", () => {
    const mismatched = {
      ...canonicalLogisticsOverview,
      coverage: {
        ...canonicalLogisticsOverview.coverage,
        presented_member_count: 1,
      },
    };
    renderPanel([structuredMessage("a-coverage-mismatch", mismatched)]);

    expect(screen.getByRole("alert")).toHaveTextContent("结果暂时无法完整展示");
    expect(screen.getByRole("alert")).not.toHaveTextContent("coverage_population_mismatch");
    expect(screen.queryByText("无线鼠标")).not.toBeInTheDocument();
  });

  it("turns legacy logistics blocks into a visible contract violation instead of a blank bubble", () => {
    renderPanel([
      {
        id: "a-legacy-logistics",
        role: "assistant",
        presentationMode: "structured",
        blocks: [{ type: "logistics_overview", title: "物流总览", summary: "旧结构" }],
      },
    ]);

    expect(screen.getByRole("alert")).toHaveTextContent("当前展示合同无效");
    expect(screen.getByRole("alert")).not.toHaveTextContent("registered_presentation_contract");
    expect(screen.queryByText("旧结构")).not.toBeInTheDocument();
  });

  it("turns an empty structured response into a visible contract violation", () => {
    renderPanel([structuredMessage("a-empty", null)]);
    expect(screen.getByRole("alert")).toHaveTextContent("结果暂时无法完整展示");
    expect(screen.getByRole("alert")).not.toHaveTextContent("registered_primary_presentation");
  });

  it("renders notice-mode released blocks in the live transcript instead of an empty bubble", () => {
    renderPanel([{
      id: "a-live-notice",
      role: "agent",
      text: "",
      presentationMode: "notice",
      blocks: [{
        type: "notice",
        tone: "warning",
        content: "系统未能证明当前结果完整满足你的查询条件，因此未展示可能范围不正确的结果。",
      }],
    }]);

    expect(screen.getByText("系统未能证明当前结果完整满足你的查询条件，因此未展示可能范围不正确的结果。")).toBeInTheDocument();
  });
});

describe("ChatPanel module-generic resource projection", () => {
  it("renders a non-ecommerce module list through the registered generic contract", () => {
    const block = {
      type: "resource_list",
      role: "primary",
      contract_id: "runtime.resource_list@1",
      contract_version: 1,
      contract_owner: "runtime_generic_resource_projection",
      projection_boundary: "module_resource_list_projector",
      producer: "module.query_capability",
      title: "支持工单（2）",
      summary: "已找到 2 条支持工单。",
      coverage: {
        mode: "full",
        source_population: "module_verified_resource_collection",
        status: "complete",
        resolved_member_count: 2,
        presented_member_count: 2,
        presented_population_proof: "same_member_identity_set",
      },
      items: [
        { resource_type: "support_ticket", resource_id: "T-1001", resource_label: "登录异常", state: "处理中", summary: "已分配给支持团队" },
        { resource_type: "support_ticket", resource_id: "T-1002", resource_label: "账单咨询", state: "待回复", summary: "等待补充信息" },
      ],
    };

    renderPanel([structuredMessage("a-support-tickets", block)]);

    expect(screen.getByText("支持工单（2）")).toBeInTheDocument();
    expect(screen.getByText("登录异常")).toBeInTheDocument();
    expect(screen.getByText("support_ticket · T-1001")).toBeInTheDocument();
    expect(screen.getByText("账单咨询")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
