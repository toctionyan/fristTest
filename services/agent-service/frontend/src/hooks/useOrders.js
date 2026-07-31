import { useCallback, useMemo, useReducer, useRef } from "react";
import { api } from "../api.js";
import { initialOrderState, orderStateReducer } from "../state/orderState.js";
import { errorMessage, getOrderId, normalizeOrders } from "../utils.js";

export function useOrders(token, onError) {
  const [state, dispatch] = useReducer(orderStateReducer, initialOrderState);
  const selectedOrderIdRef = useRef("");
  const detailRequestRef = useRef(0);

  const selectedOrderKey = useMemo(() => getOrderId(state.selectedOrder), [state.selectedOrder]);

  const loadOrderDetails = useCallback(async (orderId, authToken = token) => {
    if (!orderId) return;
    const requestId = ++detailRequestRef.current;
    selectedOrderIdRef.current = orderId;
    dispatch({ type: "selection_requested", orderId });
    try {
      const [orderPayload, logisticsPayload, actionPayload] = await Promise.all([
        api.order(authToken, orderId),
        api.logistics(authToken, orderId).catch((err) => ({ logistics: null, warning: err })),
        api.orderActions(authToken, orderId).catch((err) => ({ actions: [], warning: err }))
      ]);
      if (requestId !== detailRequestRef.current) return;
      dispatch({
        type: "details_loaded",
        orderId,
        order: orderPayload.order || null,
        logistics: logisticsPayload.logistics || null,
        actions: Array.isArray(actionPayload.actions) ? actionPayload.actions : []
      });
      if (logisticsPayload.warning) onError?.(`物流暂不可用：${errorMessage(logisticsPayload.warning)}`);
      if (actionPayload.warning) onError?.(`订单动作暂不可用：${errorMessage(actionPayload.warning)}`);
    } catch (err) {
      if (requestId === detailRequestRef.current) dispatch({ type: "details_failed", orderId });
      onError?.(errorMessage(err));
    }
  }, [onError, token]);

  const loadOrders = useCallback(async (authToken = token, { keepSelection = true } = {}) => {
    dispatch({ type: "loading" });
    try {
      const payload = await api.orders(authToken);
      const rows = normalizeOrders(payload);
      const retained = keepSelection && rows.some((row) => getOrderId(row) === selectedOrderIdRef.current)
        ? selectedOrderIdRef.current
        : "";
      const nextId = retained || getOrderId(rows[0]);
      selectedOrderIdRef.current = nextId;
      dispatch({ type: "orders_loaded", orders: rows, selectedOrderId: nextId });
      if (nextId) {
        await loadOrderDetails(nextId, authToken);
      } else {
        dispatch({ type: "empty", orders: rows });
      }
      return rows;
    } catch (err) {
      dispatch({ type: "idle" });
      onError?.(errorMessage(err));
      return [];
    }
  }, [loadOrderDetails, onError, token]);

  const queryOrders = useCallback(async (keyword) => {
    dispatch({ type: "loading" });
    try {
      const payload = keyword?.trim()
        ? await api.queryOrders(token, { product_keyword: keyword.trim() })
        : await api.orders(token);
      const rows = normalizeOrders(payload);
      const firstId = getOrderId(rows[0]);
      selectedOrderIdRef.current = firstId;
      dispatch({ type: "orders_loaded", orders: rows, selectedOrderId: firstId });
      if (firstId) await loadOrderDetails(firstId, token);
      else dispatch({ type: "empty", orders: rows });
      return rows;
    } catch (err) {
      dispatch({ type: "idle" });
      onError?.(errorMessage(err));
      return [];
    }
  }, [loadOrderDetails, onError, token]);

  const selectOrder = useCallback(async (order) => {
    const orderId = getOrderId(order);
    if (!orderId) return;
    selectedOrderIdRef.current = orderId;
    await loadOrderDetails(orderId, token);
  }, [loadOrderDetails, token]);

  const resetOrders = useCallback(() => {
    detailRequestRef.current += 1;
    selectedOrderIdRef.current = "";
    dispatch({ type: "reset" });
  }, []);

  return {
    ...state,
    selectedOrderKey,
    loadOrders,
    loadOrderDetails,
    queryOrders,
    selectOrder,
    resetOrders
  };
}
