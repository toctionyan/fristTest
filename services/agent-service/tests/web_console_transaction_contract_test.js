'use strict';

/*
 * Executes the pure browser transaction renderer in a small VM harness.  It
 * is deliberately not a screenshot test: Chromium is policy-blocked in this
 * isolated runner, so this validates the product contract that must hold in
 * every renderer before browser visual testing is available.
 */
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const { pathToFileURL } = require('url');

const sourcePath = path.resolve(__dirname, '..', 'app', 'web', 'app.js');
let source = fs.readFileSync(sourcePath, 'utf8');
source = source.replace(/\ninit\(\);\s*$/m, '\n');
source += '\n;globalThis.__transactionContract = { transactionCardMarkup, isTerminalTransactionLifecycle, interactionLifecycleLabel, updateConversationControls, renderPresentationBlocks, friendlyDebugError, state };\n';

let transactionButtons = [];
const inertElement = () => ({
  classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
  addEventListener() {},
  appendChild() {},
  querySelectorAll(selector) { return selector === '[data-transaction-action]' ? transactionButtons : []; },
  querySelector() { return null; },
  innerHTML: '',
  textContent: '',
  value: '',
  disabled: false,
});
const sandbox = {
  console,
  JSON,
  Math,
  Date,
  Promise,
  URLSearchParams,
  localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
  document: {
    getElementById() { return inertElement(); },
    querySelectorAll(selector) { return selector === '[data-transaction-action]' ? transactionButtons : []; },
    querySelector() { return null; },
    createElement() { return inertElement(); },
  },
  crypto: { randomUUID() { return 'test-id'; } },
  fetch: async () => ({ ok: true, json: async () => ({}) }),
  alert() {},
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: sourcePath });
const api = sandbox.__transactionContract;

const base = {
  interaction_id: 'tx-1',
  title: '取消订单',
  target: '无线鼠标 · 订单 10003',
  summary: '订单当前可以取消。',
  details: [{ label: '取消原因', value: '口碑不好' }],
  fields: [{ name: 'reason', label: '取消原因', required: true, control: 'text', value: '' }],
  actions: [{ id: 'approve', label: '确认取消订单', style: 'primary' }],
};

const terminal = api.transactionCardMarkup({ ...base, lifecycle: 'committed' });
assert.ok(terminal.includes('data-lifecycle="committed"'));
assert.ok(!terminal.includes('data-transaction-action'), 'terminal card must not render active action buttons');
assert.ok(!terminal.includes('transaction-form'), 'terminal card must not render a form');
assert.ok(!terminal.includes('data-interaction-field'), 'terminal card must not render editable inputs');

const form = api.transactionCardMarkup({
  ...base,
  lifecycle: 'collecting_input',
  actions: [{ id: 'submit_input', label: '下一步', style: 'primary' }],
});
assert.ok(form.includes('data-transaction-action="submit_input"'));
assert.ok(form.includes('transaction-form'));

const choiceForm = api.transactionCardMarkup({
  ...base,
  lifecycle: 'collecting_input',
  fields: [{
    name: 'reason',
    label: '取消原因',
    required: true,
    control: 'choice_or_text',
    value: '',
    options: [{ value: 'bad_reputation', label: '口碑不好' }],
  }],
  actions: [{ id: 'submit_input', label: '继续', style: 'primary' }],
});
assert.ok(choiceForm.includes('transaction-choice-group'), 'choice_or_text should render as option cards');
assert.ok(choiceForm.includes('data-interaction-choice="reason"'));
assert.ok(choiceForm.includes('data-interaction-custom="reason"'));
assert.ok(choiceForm.includes('value="__custom__"'), 'custom reason escape hatch must be present');

const nextActions = api.renderPresentationBlocks([{
  type: 'next_actions',
  title: '可继续办理',
  summary: '请选择下一步。',
  target: '定制马克杯（订单 10004）',
  actions: [{ id: 'create_after_sales_request', action_id: 'create_after_sales_request', label: '申请售后', intent: '我要申请售后 10004', target: { resource_type: 'order', order_id: '10004' }, input_hints: { reason_code: 'WRONG_ITEM' } }],
}]);
assert.ok(nextActions.includes('data-presentation="next_actions"'));
assert.ok(nextActions.includes('data-transaction-start='));
assert.ok(!nextActions.includes('data-customer-action-intent='), 'new next action buttons must not carry natural-language intent as primary protocol');
assert.ok(!nextActions.includes('/console/business'), 'next action card must not call business writers directly');
assert.ok(source.includes("request('/transactions/start'"), 'structured customer actions must start transactions without chat routing');

for (const lifecycle of [
  'committed', 'commit_failed', 'commit_preflight_rejected', 'preflight_rejected',
  'preflight_failed', 'rejected', 'authority_rejected', 'expired', 'cancelled',
  'interaction_cancelled', 'superseded', 'interaction_superseded', 'input_target_missing',
]) {
  assert.strictEqual(api.isTerminalTransactionLifecycle(lifecycle), true, `${lifecycle} must be terminal`);
}
for (const lifecycle of ['collecting_input', 'awaiting_authority', 'submitting', 'ready', '']) {
  assert.strictEqual(api.isTerminalTransactionLifecycle(lifecycle), false, `${lifecycle} must remain nonterminal`);
}


// A server response can be ambiguous after a network/runtime failure.  The
// interaction is intentionally marked `submitting` until a refresh reconciles
// it, and the generic busy-state refresh must not re-enable its buttons.
const uncertainCard = { dataset: { lifecycle: 'submitting' } };
const uncertainButton = { disabled: false, closest(selector) { return selector === '.transaction-card' ? uncertainCard : null; } };
transactionButtons = [uncertainButton];
api.state.chatBusy = false;
api.state.resumeBusy = false;
api.updateConversationControls();
assert.strictEqual(uncertainButton.disabled, true, 'uncertain transaction must remain non-actionable after busy state clears');

const liveCard = { dataset: { lifecycle: 'awaiting_authority' } };
const liveButton = { disabled: true, closest(selector) { return selector === '.transaction-card' ? liveCard : null; } };
transactionButtons = [liveButton];
api.updateConversationControls();
assert.strictEqual(liveButton.disabled, false, 'fresh authority card should be actionable when the conversation is idle');

assert.ok(source.includes('正在加载这次聊天'), 'debug loader should be centered on inspecting one chat');
assert.ok(source.includes('/debug/thread-diagnostics?thread_id='), 'debug console must use backend structured diagnostics');
assert.ok(!source.includes('运行与事件'), 'global run/event button should not remain in the primary debug flow');
assert.ok(source.includes("els.threadLookupInput.value.trim() || els.debugThreadSelect.value"), 'empty manual thread id should fall back to selected thread');
assert.strictEqual(api.friendlyDebugError(new Error('thread not found')), '找不到该会话：请检查是否选错用户、thread_id 是否来自当前租户。');

(async () => {
  const debugModulePath = path.resolve(__dirname, '..', 'app', 'web', 'modules', 'debug_console.js');
  const debugConsole = await import(pathToFileURL(debugModulePath));
  const rendered = debugConsole.renderThreadDiagnostics({
    thread: { thread_id: 't-1' },
    summary: {
      severity: 'attention',
      title: '优先关注：事务卡',
      reason: 'preflight_rejected',
      impact: '该层出现业务拒绝或预检未通过，先确认是否符合业务规则。',
      related_messages: [{ index: 1, role: 'assistant' }],
      related_traces: [{ event_type: 'preflight_rejected' }],
    },
    flow_nodes: [
      { key: 'user_message', label: '用户消息', status: 'success', message_count: 2, trace_count: 0, summary: '本轮输入进入会话' },
      { key: 'transaction', label: '事务卡', status: 'attention', trace_count: 1, attention_count: 1, summary: 'preflight_rejected' },
    ],
    turns: [
      { index: 0, start_message: 0, badges: ['presentation next_actions'], messages: [{ index: 0, role: 'user', content: '我要退款', badges: [], raw: {} }, { index: 1, role: 'assistant', content: '请选择', badges: ['presentation next_actions'], raw: {} }] },
    ],
    evidence: {
      transaction: { key: 'transaction', label: '事务卡', traces: [{ event_type: 'preflight_rejected', status: 'attention', summary: '缺少原因', raw: {} }] },
      chat_api: { key: 'chat_api', label: '聊天入口', traces: [{ event_type: 'chat_start', status: 'success', summary: 'ok', raw: {} }] },
    },
    raw: { ok: true },
  }, { requestedId: 't-1', activeLayer: 'transaction' });
  assert.ok(rendered.bodyHtml.includes('诊断结论'));
  assert.ok(rendered.bodyHtml.includes('链路流程图'));
  assert.ok(rendered.bodyHtml.includes('第 1 轮'));
  assert.ok(rendered.bodyHtml.includes('节点证据：事务卡'));
  assert.ok(rendered.bodyHtml.includes('preflight_rejected'));
  assert.ok(!rendered.bodyHtml.includes('chat_start'), 'active layer filter should hide unrelated evidence');
  console.log('web_console_transaction_contract_test: PASS');
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
