import { useState } from "react";
import { Bot, Loader2, MessageSquare, Send } from "lucide-react";
import { api, newRequestId } from "../api.js";
import { validatePresentationBlock } from "../contracts/presentationRegistry.js";
import { errorMessage, money, statusText } from "../utils.js";

function ContractViolation({ block, fallbackMissing = [] }) {
  const isControlledViolation = block?.type === "projection_contract_violation";
  return (
    <div className="notice warning" role="alert">
      <strong>{isControlledViolation ? (block?.title || "结果暂时无法完整展示") : "结果暂时无法完整展示"}</strong>
      <p>{isControlledViolation ? (block?.content || "系统已获取结果，但缺少必要展示信息；为避免误导，未显示不完整内容。") : "系统已获取结果，但当前展示合同无效；为避免误导，未显示不完整内容。"}</p>
    </div>
  );
}

function CommerceOrderList({ block }) {
  return (
    <div className="mini-list commerce-order-list" data-contract-id={block.contract_id}>
      {block.items.map((item) => (
        <div className="mini-row commerce-order-row" key={item.order_id}>
          <div className="commerce-order-identity">
            <span className="commerce-order-title">{item.product_name}</span>
            <small>订单 {item.order_id}</small>
          </div>
          <div className="commerce-order-meta">
            <strong>{item.status ? statusText(item.status) : "状态暂不可用"}</strong>
            <small>{item.amount === null || item.amount === undefined || item.amount === "" ? "金额暂不可用" : money(item.amount)}</small>
          </div>
        </div>
      ))}
    </div>
  );
}

function CommerceLogisticsOverview({ block }) {
  return (
    <section className="mini-list commerce-logistics-overview" data-contract-id={block.contract_id} aria-label={block.title}>
      <strong className="block-heading">{block.title}</strong>
      <p className="muted block-summary">{block.summary}</p>
      {block.items.map((item) => (
        <div className="commerce-logistics-row" key={item.order_id}>
          <div>
            <strong>{item.product_name}</strong>
            <small>订单 {item.order_id}</small>
          </div>
          <div className="commerce-logistics-state">
            <strong>{statusText(item.status)}</strong>
            <small>{item.latest}</small>
            <small>{item.estimate || "预计时间暂不可用"}</small>
          </div>
        </div>
      ))}
    </section>
  );
}

function CommerceBusinessStatusList({ block }) {
  return (
    <section className="mini-list commerce-business-status-list" data-contract-id={block.contract_id} aria-label={block.title}>
      <strong className="block-heading">{block.title}</strong>
      <p className="muted block-summary">{block.summary}</p>
      {block.target_order_id || block.target_label ? (
        <p className="muted">
          {block.target_product_name || block.target_label || "业务对象"}
          {block.target_order_id ? `（订单 ${block.target_order_id}）` : ""}
        </p>
      ) : null}
      {block.items.map((item) => (
        <div className="mini-row commerce-status-row" key={`${item.record_kind}:${item.record_reference}`}>
          <div>
            <strong>{item.record_kind} {item.record_reference}</strong>
            <small>{item.order_id ? `关联订单 ${item.order_id}` : "关联订单暂不可用"}</small>
          </div>
          <div className="commerce-order-meta">
            <strong>{statusText(item.status)}</strong>
            <small>{item.updated_at || "更新时间暂不可用"}</small>
          </div>
        </div>
      ))}
    </section>
  );
}

function CommerceNextActions({ block, onStartAction, busy }) {
  return (
    <section className="next-actions-card" data-contract-id={block.contract_id} aria-label={block.title}>
      <strong className="block-heading">{block.title}</strong>
      <p>{block.summary}</p>
      <p className="muted">{block.target_product_name}（订单 {block.target_order_id}）</p>
      <div className="action-row">
        {block.actions.map((action) => (
          <button
            className="ghost-button"
            type="button"
            key={action.action_id}
            disabled={busy || !onStartAction}
            onClick={() => onStartAction?.(action)}
          >
            {action.label}
          </button>
        ))}
      </div>
    </section>
  );
}

function CommerceEligibilityDecision({ block, onStartAction, busy }) {
  return (
    <section className={`eligibility-card ${block.eligible ? "eligible" : "ineligible"}`} data-contract-id={block.contract_id} aria-label={block.title}>
      <strong className="block-heading">{block.title}</strong>
      <p>{block.summary}</p>
      <p className="muted">{block.target_product_name}（订单 {block.target_order_id}）</p>
      {block.actions.length ? (
        <div className="action-row">
          {block.actions.map((action) => (
            <button
              className="ghost-button"
              type="button"
              key={action.action_id}
              disabled={busy || !onStartAction}
              onClick={() => onStartAction?.(action)}
            >
              {action.label}
            </button>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function CommerceAdvisory({ block }) {
  return (
    <section className="advisory-card" data-contract-id={block.contract_id} aria-label={block.title}>
      <strong className="block-heading">{block.title}</strong>
      <p>{block.summary}</p>
      <p className="muted">{block.target_product_name}（订单 {block.target_order_id}）</p>
      {block.items.map((item, index) => (
        <details key={`${item.title}-${index}`}>
          <summary>{item.title}</summary>
          <p>{item.content}</p>
          <small>{item.source}</small>
        </details>
      ))}
    </section>
  );
}

function RuntimeResourceList({ block }) {
  return (
    <section className="mini-list runtime-resource-list" data-contract-id={block.contract_id} aria-label={block.title}>
      <strong className="block-heading">{block.title}</strong>
      <p className="muted block-summary">{block.summary}</p>
      {block.items.map((item) => (
        <div className="mini-row" key={`${item.resource_type || "resource"}:${item.resource_id}`}>
          <div>
            <strong>{item.resource_label}</strong>
            <small>{item.resource_type || "资源"} · {item.resource_id}</small>
          </div>
          <div className="commerce-order-meta">
            <strong>{item.state || "状态暂不可用"}</strong>
            <small>{item.summary || "暂无补充说明"}</small>
          </div>
        </div>
      ))}
    </section>
  );
}

function RuntimeTransactionStatus({ block }) {
  const data = block.data || {};
  const draft = data.draft || {};
  const receipt = data.receipt || {};
  return (
    <div className="notice info" data-contract-id={block.contract_id}>
      <strong>{block.summary}</strong>
      {draft.draft_state ? <p>当前状态：{draft.draft_state}</p> : null}
      {receipt.receipt_id ? <p>回执：{receipt.receipt_id}</p> : null}
    </div>
  );
}

function RuntimeInteractionTimeline({ block }) {
  return (
    <div className="notice info interaction-timeline" data-contract-id={block.contract_id}>
      <strong>待处理办理事项</strong>
      <p>{block.summary}</p>
      {block.target ? <small>对象：{block.target}</small> : null}
      <small>当前状态：{block.lifecycle}</small>
      <small>{block.next_step}</small>
    </div>
  );
}

function BlockList({ blocks = [], onStartAction, busy }) {
  if (!blocks.length) return <ContractViolation fallbackMissing={["registered_primary_presentation"]} />;
  return (
    <div className="blocks">
      {blocks.map((block, index) => {
        const key = `${block?.contract_id || block?.type || "block"}-${index}`;
        if (block?.type === "projection_contract_violation") {
          return <ContractViolation block={block} key={key} />;
        }
        if (block?.type === "notice") {
          return (
            <div className={`notice ${block.tone || "info"}`} key={key}>
              {block.content || "系统提示暂不可用。"}
            </div>
          );
        }
        const validation = validatePresentationBlock(block);
        if (!validation.valid) return <ContractViolation block={block} fallbackMissing={validation.missingSemantics} key={key} />;
        switch (validation.rendererId) {
          case "commerce_order_list_renderer":
            return <CommerceOrderList block={block} key={key} />;
          case "commerce_logistics_overview_renderer":
            return <CommerceLogisticsOverview block={block} key={key} />;
          case "commerce_business_status_list_renderer":
            return <CommerceBusinessStatusList block={block} key={key} />;
          case "commerce_next_actions_renderer":
            return <CommerceNextActions block={block} onStartAction={onStartAction} busy={busy} key={key} />;
          case "commerce_eligibility_decision_renderer":
            return <CommerceEligibilityDecision block={block} onStartAction={onStartAction} busy={busy} key={key} />;
          case "commerce_advisory_renderer":
            return <CommerceAdvisory block={block} key={key} />;
          case "runtime_resource_list_renderer":
            return <RuntimeResourceList block={block} key={key} />;
          case "runtime_transaction_status_renderer":
            return <RuntimeTransactionStatus block={block} key={key} />;
          case "runtime_interaction_timeline_renderer":
            return <RuntimeInteractionTimeline block={block} key={key} />;
          default:
            return <ContractViolation block={block} fallbackMissing={["registered_channel_renderer"]} key={key} />;
        }
      })}
    </div>
  );
}

export function ChatPanel({ token, threadId, ensureThread, messages, setMessages, applyResponse, refreshPending, busy, setBusy, onError, onStartAction, interactionSlot = null }) {
  const [chatInput, setChatInput] = useState("");

  async function sendChat(event) {
    event.preventDefault();
    const message = chatInput.trim();
    if (!message) return;
    setChatInput("");
    setMessages((current) => [...current, { id: newRequestId("user"), role: "user", text: message, blocks: [] }]);
    setBusy(true);
    onError("");
    try {
      const response = await api.chatTurn(token, { thread_id: ensureThread(), message });
      applyResponse(response);
      await refreshPending(token, response.thread_id || threadId);
    } catch (err) {
      onError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="conversation-shell">
      <div className="chat-log" role="log" aria-live="polite" aria-label="对话记录">
        {messages.length ? messages.map((message) => {
          const text = String(message.text || "").trim();
          const blocks = Array.isArray(message.blocks) ? message.blocks : [];
          const isCustomerTurn = message.role === "user";
          const structuredMode = ["structured", "transaction_status"].includes(message.presentationMode);
          return (
            <div className={`chat-message ${message.role}`} key={message.id}>
              <div className="avatar">{isCustomerTurn ? <MessageSquare size={15} /> : <Bot size={15} />}</div>
              <div>
                {text && (!structuredMode || !blocks.length) ? <p>{text}</p> : null}
                {blocks.length ? <BlockList blocks={blocks} onStartAction={onStartAction} busy={busy} /> : null}
                {!isCustomerTurn && !text && !blocks.length ? <ContractViolation /> : null}
              </div>
            </div>
          );
        }) : (
          <div className="empty-state">可以直接询问订单、退款、售后或发票问题</div>
        )}
      </div>

      {interactionSlot ? <div className="live-interaction-dock">{interactionSlot}</div> : null}
      <form className="chat-box" onSubmit={sendChat}>
        <label className="sr-only" htmlFor="agent-chat-input">输入问题</label>
        <input id="agent-chat-input" aria-label="输入问题" maxLength={8000} value={chatInput} onChange={(event) => setChatInput(event.target.value)} placeholder="输入问题" />
        <button className="primary-button" type="submit" disabled={busy || !chatInput.trim()}>
          {busy ? <Loader2 className="spin" size={16} /> : <Send size={16} />}
          发送
        </button>
      </form>
    </section>
  );
}
