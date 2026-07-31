import { Package } from "lucide-react";
import { money, statusText } from "../utils.js";

export function OrderDetail({ selectedOrder, selectedOrderKey, logistics, actions, busy, onStartAction }) {
  return (
    <section className="detail-pane">
      <div className="section-head">
        <div>
          <div className="eyebrow">订单详情</div>
          <h2>{selectedOrder?.product_name || "请选择订单"}</h2>
        </div>
        {selectedOrder ? <span className="status-pill">{statusText(selectedOrder.status)}</span> : null}
      </div>

      {selectedOrder ? (
        <>
          <div className="facts-grid">
            <div>
              <span>订单号</span>
              <strong>{selectedOrderKey}</strong>
            </div>
            <div>
              <span>金额</span>
              <strong>{money(selectedOrder.amount)}</strong>
            </div>
            <div>
              <span>版本</span>
              <strong>{selectedOrder.version || 1}</strong>
            </div>
            <div>
              <span>收货人</span>
              <strong>{selectedOrder.receiver_name || selectedOrder.consignee || "-"}</strong>
            </div>
          </div>

          <div className="logistics-band">
            <Package size={18} />
            <div>
              <strong>{logistics?.status || selectedOrder.logistics_status || "暂无物流更新"}</strong>
              <span>{logistics?.latest_event || logistics?.latest_trace || selectedOrder.shipping_address || ""}</span>
            </div>
          </div>

          <div className="action-strip">
            {actions.length ? actions.map((action) => (
              <button key={action.action_id || action.id} onClick={() => onStartAction(action)} disabled={busy}>
                {action.label}
              </button>
            )) : <span className="muted">当前订单暂无可办理动作</span>}
          </div>
        </>
      ) : (
        <div className="empty-state">暂无订单</div>
      )}
    </section>
  );
}
