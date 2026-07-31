import { useCallback, useEffect, useState } from "react";
import { AlertCircle, Clock3, Loader2, LogOut, RefreshCw, ShoppingBag } from "lucide-react";
import { ChatPanel } from "./components/ChatPanel.jsx";
import { LoginView } from "./components/LoginView.jsx";
import { OrderDetail } from "./components/OrderDetail.jsx";
import { OrdersPane } from "./components/OrdersPane.jsx";
import { TransactionCard } from "./components/TransactionCard.jsx";
import { TransactionCenter } from "./components/TransactionCenter.jsx";
import { useOrders } from "./hooks/useOrders.js";
import { useSession } from "./hooks/useSession.js";
import { useTransaction } from "./hooks/useTransaction.js";
import { lifecycleText } from "./utils.js";
import { api } from "./api.js";

export default function App() {
  const session = useSession();
  const [query, setQuery] = useState("");
  const [messages, setMessages] = useState([]);
  const [error, setError] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [transactionItems, setTransactionItems] = useState([]);
  const [transactionsLoading, setTransactionsLoading] = useState(false);
  const setSessionError = session.setError;

  const showError = useCallback((message) => {
    setError(message || "");
    setSessionError(message || "");
  }, [setSessionError]);

  const appendMessage = useCallback((message) => {
    setMessages((current) => [...current, message]);
  }, []);

  const orders = useOrders(session.token, showError);
  const transaction = useTransaction({
    token: session.token,
    actor: session.actor,
    onMessage: appendMessage,
    onError: showError
  });

  const loadThreadMessages = useCallback(async (authToken, currentThreadId) => {
    if (!currentThreadId) {
      setMessages([]);
      return;
    }
    try {
      const payload = await api.threadMessages(authToken, currentThreadId);
      setMessages(Array.isArray(payload.items) ? payload.items : []);
    } catch (err) {
      // A brand-new locally generated thread does not exist until its first
      // turn.  Treat that as an empty transcript, not as a UI failure.
      if (err?.status === 404) {
        setMessages([]);
        return;
      }
      showError(err.message || "无法加载会话记录");
    }
  }, [showError]);

  const loadTransactions = useCallback(async () => {
    if (!session.actor) return;
    setTransactionsLoading(true);
    try {
      const payload = await api.transactions(session.token || undefined);
      setTransactionItems(payload.items || []);
    } catch (err) {
      showError(err.message || "无法加载办理记录");
    } finally {
      setTransactionsLoading(false);
    }
  }, [session.actor, session.token, showError]);

  useEffect(() => {
    async function bootData() {
      if (!session.actor) return;
      const authToken = session.token || undefined;
      const thread = transaction.ensureThread(session.actor);
      await loadThreadMessages(authToken, thread);
      // Session refresh may legitimately retrigger boot after the page is
      // already interactive.  A late boot must initialize an empty selection,
      // never overwrite a choice the user made while its request was running.
      await orders.loadOrders(authToken);
      await transaction.refreshPending(authToken, thread);
      await loadTransactions();
    }
    bootData();
  }, [loadThreadMessages, loadTransactions, orders.loadOrders, session.actor, session.token, transaction.ensureThread, transaction.refreshPending]);

  async function handleLogin(username, password) {
    await session.login(username, password);
  }

  async function switchConversation(threadId) {
    const nextThread = transaction.switchThread(threadId);
    await loadThreadMessages(session.token || undefined, nextThread);
    await transaction.refreshPending(session.token || undefined, nextThread);
  }

  async function startNewConversation() {
    const nextThread = transaction.startNewThread(session.actor);
    setMessages([]);
    await transaction.refreshPending(session.token || undefined, nextThread);
  }

  async function startProductAction(action, orderId = "") {
    await transaction.startAction(action, orderId);
    await loadTransactions();
  }

  function logout() {
    session.logout();
    orders.resetOrders();
    transaction.clearInteraction();
    setMessages([]);
    setError("");
  }

  async function searchOrders(event) {
    event.preventDefault();
    setError("");
    await orders.queryOrders(query);
  }

  const busy = transaction.busy || orders.loading || chatBusy;
  // Loading an order detail is independent from the conversation composer.
  // Coupling it to chatBusy can leave a fully rendered chat unable to submit
  // while a background order refresh is slow or superseded.
  const conversationBusy = transaction.busy || chatBusy;
  const displayError = error || session.error;

  if (session.loading) {
    return (
      <main className="loading-shell">
        <Loader2 className="spin" size={24} />
        <span>正在载入</span>
      </main>
    );
  }

  if (!session.actor) {
    return <LoginView onLogin={handleLogin} sessionError={session.error} />;
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="topbar-title">
          <ShoppingBag size={22} />
          <div>
            <h1>Agent 客户自助</h1>
            <p>{session.actor.user_id} · {session.actor.tenant_id || "default"}</p>
          </div>
        </div>
        <div className="topbar-actions">
          <button className="icon-button" onClick={() => transaction.refreshPending()} title="刷新待办">
            <RefreshCw size={17} />
          </button>
          <button className="icon-button" onClick={logout} title="退出">
            <LogOut size={17} />
          </button>
        </div>
      </header>

      {displayError ? (
        <div className="error-banner">
          <AlertCircle size={17} />
          {displayError}
        </div>
      ) : null}

      <div className="workspace">
        <OrdersPane
          orders={orders.orders}
          selectedOrderId={orders.selectedOrderId}
          query={query}
          setQuery={setQuery}
          busy={busy}
          onSearch={searchOrders}
          onSelect={orders.selectOrder}
        />

        <OrderDetail
          selectedOrder={orders.selectedOrder}
          selectedOrderKey={orders.selectedOrderKey}
          logistics={orders.logistics}
          actions={orders.actions}
          busy={busy}
          onStartAction={(action) => startProductAction(action, orders.selectedOrderKey)}
        />

        <section className="agent-pane">
          <div className="thread-tools">
            <div>
              <div className="eyebrow">会话</div>
              <strong>{transaction.threadId}</strong>
            </div>
            <div className="thread-actions">
              <button className="ghost-button" onClick={startNewConversation} disabled={busy}>
                新会话
              </button>
              <button className="ghost-button" onClick={transaction.reconcile} disabled={busy || !transaction.threadId}>
                <Clock3 size={16} />
                对账
              </button>
            </div>
          </div>

          <div className="pending-band">
            <div className="pending-head">
              <span>当前会话待办</span>
              <strong>{transaction.pendingItems.length}</strong>
            </div>
            {transaction.pendingItems.length ? transaction.pendingItems.map((item) => (
              <div className="pending-row" key={item.interaction_id}>
                <span>{item.title}</span>
                <small>{lifecycleText(item.lifecycle)}</small>
              </div>
            )) : <small className="muted">当前会话没有待办</small>}
          </div>

          <TransactionCenter
            items={transactionItems}
            loading={transactionsLoading}
            onRefresh={loadTransactions}
            onSelect={(item) => item.thread_id ? switchConversation(item.thread_id) : undefined}
          />

          <ChatPanel
            token={session.token}
            threadId={transaction.threadId}
            ensureThread={transaction.ensureThread}
            messages={messages}
            setMessages={setMessages}
            applyResponse={transaction.applyResponse}
            refreshPending={transaction.refreshPending}
            busy={conversationBusy}
            setBusy={setChatBusy}
            onError={showError}
            onStartAction={(action) => startProductAction(action)}
            interactionSlot={(transaction.interaction || transaction.interactionUpdate) ? (
              <TransactionCard
                interaction={transaction.interaction}
                update={transaction.interactionUpdate}
                threadId={transaction.threadId}
                token={session.token}
                onResponse={async (response) => {
                  transaction.applyResponse(response);
                  await transaction.refreshPending(session.token, response.thread_id || transaction.threadId);
                  await loadTransactions();
                }}
                onClear={transaction.clearInteraction}
                onError={showError}
              />
            ) : null}
          />
        </section>
      </div>
    </main>
  );
}
