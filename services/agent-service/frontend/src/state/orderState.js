export const initialOrderState = {
  orders: [],
  selectedOrderId: "",
  selectedOrder: null,
  logistics: null,
  actions: [],
  loading: false
};

export function orderStateReducer(state, event) {
  switch (event.type) {
    case "loading":
      return { ...state, loading: true };
    case "orders_loaded":
      return { ...state, orders: event.orders, selectedOrderId: event.selectedOrderId };
    case "selection_requested":
      return { ...state, selectedOrderId: event.orderId, loading: true };
    case "details_loaded":
      if (state.selectedOrderId !== event.orderId) return state;
      return {
        ...state,
        selectedOrder: event.order,
        logistics: event.logistics,
        actions: event.actions,
        loading: false
      };
    case "details_failed":
      return state.selectedOrderId === event.orderId ? { ...state, loading: false } : state;
    case "empty":
      return { ...initialOrderState, orders: event.orders || [], loading: false };
    case "idle":
      return { ...state, loading: false };
    case "reset":
      return initialOrderState;
    default:
      throw new Error(`Unknown order state event: ${event.type}`);
  }
}
