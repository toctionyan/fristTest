import { useCallback, useRef, useState } from "react";
import { api, newRequestId } from "../api.js";
import { createThreadId, errorMessage, pickInteractionUpdate, storedThreadKey } from "../utils.js";

export function useTransaction({ token, actor, onMessage, onError }) {
  const [threadId, setThreadId] = useState("");
  const [interaction, setInteraction] = useState(null);
  const [interactionUpdate, setInteractionUpdate] = useState(null);
  const [pendingItems, setPendingItems] = useState([]);
  const [busy, setBusy] = useState(false);
  const threadIdRef = useRef("");

  const rememberThread = useCallback((nextThreadId, nextActor = actor) => {
    const normalized = String(nextThreadId || "").trim();
    threadIdRef.current = normalized;
    setThreadId(normalized);
    if (normalized && nextActor) localStorage.setItem(storedThreadKey(nextActor), normalized);
    return normalized;
  }, [actor]);

  const ensureThread = useCallback((nextActor = actor) => {
    if (!nextActor) return "";
    const key = storedThreadKey(nextActor);
    const existing = localStorage.getItem(key);
    const next = existing || createThreadId(nextActor);
    localStorage.setItem(key, next);
    return rememberThread(next, nextActor);
  }, [actor, rememberThread]);

  const switchThread = useCallback((nextThreadId) => {
    setInteraction(null);
    setInteractionUpdate(null);
    setPendingItems([]);
    return rememberThread(nextThreadId);
  }, [rememberThread]);

  const startNewThread = useCallback((nextActor = actor) => {
    if (!nextActor) return "";
    return switchThread(createThreadId(nextActor));
  }, [actor, switchThread]);

  const applyResponse = useCallback((response) => {
    if (response?.thread_id) {
      rememberThread(response.thread_id);
    }
    const blocks = Array.isArray(response?.blocks) ? response.blocks : [];
    const hasInteraction = Boolean(response?.interaction || response?.interaction_update);
    const rawText = response?.answer || response?.message || response?.error || "";
    const text = rawText || (!hasInteraction && !blocks.length
      ? "系统未返回可展示的结果，未创建或提交任何业务申请。请重新说明需要查询的事项。"
      : "");
    if (text || blocks.length) {
      onMessage?.({
        id: newRequestId("message"),
        role: response?.type === "error" ? "system" : "agent",
        text,
        blocks,
        presentationMode: response?.presentation_mode || (text && !rawText ? "notice" : null)
      });
    }
    const { live, update } = pickInteractionUpdate(response);
    if (live) {
      setInteraction(live);
      setInteractionUpdate(null);
    }
    if (update) {
      setInteractionUpdate(update);
      setInteraction((current) => {
        if (!update.interaction_id || update.interaction_id === current?.interaction_id) return null;
        return current;
      });
    }
  }, [onMessage, rememberThread]);

  const refreshPending = useCallback(async (authToken = token, currentThreadId = threadIdRef.current) => {
    if (!currentThreadId) return null;
    try {
      const payload = await api.pending(authToken, currentThreadId);
      setPendingItems(Array.isArray(payload.items) ? payload.items : []);
      if (payload.interaction) {
        setInteraction(payload.interaction);
        setInteractionUpdate(null);
      }
      return payload;
    } catch (err) {
      if (err?.status !== 404) onError?.(errorMessage(err));
      return null;
    }
  }, [onError, token]);

  const startAction = useCallback(async (action, explicitOrderId = "") => {
    const target = action?.target && typeof action.target === "object" ? action.target : {};
    const orderId = String(target.order_id || explicitOrderId || "").trim();
    const actionId = String(action?.action_id || "").trim();
    if (!orderId || !actionId) {
      onError?.("当前办理卡缺少经核验的动作或订单目标，未创建任何业务申请。");
      return;
    }
    setBusy(true);
    try {
      const response = await api.startTransaction(token, {
        thread_id: ensureThread(),
        action_id: actionId,
        target: { resource_type: "order", order_id: orderId },
        input_hints: action?.input_hints && typeof action.input_hints === "object" ? action.input_hints : {},
        client_request_id: newRequestId("start")
      });
      applyResponse(response);
      await refreshPending(token, response.thread_id);
    } catch (err) {
      onError?.(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }, [applyResponse, ensureThread, onError, refreshPending, token]);

  const reconcile = useCallback(async () => {
    if (!threadId) return;
    setBusy(true);
    try {
      const payload = await api.reconcile(token, threadId);
      setPendingItems(Array.isArray(payload.items) ? payload.items : []);
      if (payload.interaction) {
        setInteraction(payload.interaction);
        setInteractionUpdate(null);
      }
      onMessage?.({ id: newRequestId("reconcile"), role: "system", text: "事务状态已刷新。", blocks: [] });
    } catch (err) {
      onError?.(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }, [onError, onMessage, threadId, token]);

  const clearInteraction = useCallback(() => {
    setInteraction(null);
    setInteractionUpdate(null);
  }, []);

  return {
    threadId,
    interaction,
    interactionUpdate,
    pendingItems,
    busy,
    ensureThread,
    applyResponse,
    refreshPending,
    startAction,
    reconcile,
    clearInteraction,
    switchThread,
    startNewThread
  };
}
