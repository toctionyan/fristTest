import { Search } from "lucide-react";
import { getOrderId, money, statusText } from "../utils.js";

export function OrdersPane({ orders, selectedOrderId, query, setQuery, busy, onSearch, onSelect }) {
  return (
    <aside className="orders-pane">
      <form className="search-row" onSubmit={onSearch}>
        <Search size={16} />
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="订单号或商品" />
        <button type="submit" disabled={busy}>
          查询
        </button>
      </form>
      <div className="order-list">
        {orders.length ? orders.map((order) => {
          const id = getOrderId(order);
          return (
            <button className={`order-item ${id === selectedOrderId ? "active" : ""}`} key={id} onClick={() => onSelect(order)}>
              <span>{order.product_name || id}</span>
              <strong>{money(order.amount)}</strong>
              <small>{id} · {statusText(order.status)}</small>
            </button>
          );
        }) : <div className="empty-state">暂无订单</div>}
      </div>
    </aside>
  );
}
