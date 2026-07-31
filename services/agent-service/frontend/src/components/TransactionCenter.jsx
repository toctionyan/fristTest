import { Clock3, FileCheck2, RefreshCw, ShieldAlert } from "lucide-react";
import { lifecycleText } from "../utils.js";

function receiptText(item) {
  const receipt = item.latest_receipt;
  if (!receipt) return null;
  if (receipt.receipt_state === "SUCCESS") return "已完成";
  if (receipt.receipt_state === "FAILED") return "提交失败";
  return receipt.receipt_state || "已处理";
}

export function TransactionCenter({ items = [], loading, onRefresh, onSelect }) {
  return (
    <section className="transaction-center" aria-label="事务中心">
      <div className="transaction-center-head">
        <div>
          <div className="eyebrow">事务中心</div>
          <strong>办理记录</strong>
        </div>
        <button className="icon-button" onClick={onRefresh} disabled={loading} title="刷新办理记录">
          <RefreshCw size={16} className={loading ? "spin" : ""} />
        </button>
      </div>
      {!items.length ? <p className="muted transaction-empty">没有待办或历史办理记录</p> : (
        <div className="transaction-list">
          {items.map((item) => {
            const unknown = item.draft_state === "SUBMISSION_UNKNOWN" || item.draft_state === "RECONCILIATION_REQUIRED";
            return (
              <button className="transaction-row" key={item.draft_id} onClick={() => onSelect?.(item)}>
                <span className="transaction-icon">{unknown ? <ShieldAlert size={16} /> : item.latest_receipt ? <FileCheck2 size={16} /> : <Clock3 size={16} />}</span>
                <span className="transaction-content">
                  <strong>{item.target_summary || item.action_id || "业务办理"}</strong>
                  <small>{unknown ? "提交结果确认中，请勿重复操作" : lifecycleText(item.draft_state)}</small>
                </span>
                <span className="transaction-state">{receiptText(item) || lifecycleText(item.draft_state)}</span>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}
