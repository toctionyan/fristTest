import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const baseURL = process.env.PRODUCT_WEB_URL;
const artifactDir = process.env.PRODUCT_BROWSER_ARTIFACT_DIR;
const modelMode = process.env.PRODUCT_BROWSER_MODEL_MODE;
const scenario = process.env.PRODUCT_BROWSER_SCENARIO || "full";

const reportedRegressionTurns = [
  ["我都买了什么", { label: "reported order inventory", requiredAll: ["10001", "10002", "10003", "10004"] }],
  ["哪些在路上", {
    label: "reported logistics follow-up",
    requiredAll: ["10001"],
    requiredAny: ["蓝牙耳机", "运输中", "已发货"],
    forbidden: ["10003", "无线鼠标", "待发货"],
  }],
  ["可以退货退款吗？", {
    label: "reported eligibility follow-up",
    requiredAll: ["10001"],
    requiredAny: ["退款", "退货", "资格", "不能", "暂不"],
    forbidden: ["已提交", "申请成功", "10002", "10003", "10004"],
  }],
];

// A clarification is a paused goal, not a completed conversation.  These
// journeys reproduce the customer-reported failure through the public page:
// the short answer must resume the original capability, while an explicit
// topic switch must retire it instead of hijacking the new request.
const clarificationResumeTurns = [
  ["我都买了什么", { label: "clarification inventory", requiredAll: ["10001", "10002", "10003", "10004"] }],
  ["可以退货退款吗？", {
    label: "clarification requested for ambiguous refund eligibility",
    requiredAny: ["哪", "哪个", "具体", "请明确", "订单"],
    forbidden: ["已提交", "申请成功"],
  }],
  ["鼠标", {
    label: "short answer resumes refund eligibility",
    requiredAll: ["10003"],
    requiredAny: ["无线鼠标", "退款", "资格", "可以继续申请"],
    forbidden: ["10001", "10002", "10004", "请重新说明需要处理的事项"],
  }],
];

const clarificationAbandonTurns = [
  ["我都买了什么", { label: "abandon inventory", requiredAll: ["10001", "10002", "10003", "10004"] }],
  ["可以退货退款吗？", {
    label: "abandon setup clarification",
    requiredAny: ["哪", "哪个", "具体", "请明确", "订单"],
  }],
  ["先不问退款了，查订单10004能不能开发票", {
    label: "explicit new request abandons suspended refund goal",
    requiredAll: ["10004"],
    requiredAny: ["发票", "开票"],
    forbidden: ["10001", "10002", "10003", "退款资格", "申请退款"],
  }],
];

function invariant(condition, message) {
  if (!condition) throw new Error(message);
}

function normalized(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function assertCustomerSafe(text, label) {
  const forbidden = [
    "registered_primary_presentation",
    "registered_channel_renderer",
    "projection_contract_violation",
    "Traceback",
    "Exception:",
    "tool_trace",
    "state_contract_violations",
  ];
  for (const marker of forbidden) {
    invariant(!text.includes(marker), `${label}: leaked internal marker ${marker}: ${text}`);
  }
}

function assertSemantic(result, expectation) {
  const text = result.text;
  const label = expectation.label;
  invariant(text.length > 0, `${label}: assistant rendered an empty live bubble`);
  assertCustomerSafe(text, label);
  for (const marker of expectation.requiredAll || []) {
    invariant(text.includes(marker), `${label}: missing required semantic ${marker}: ${text}`);
  }
  if ((expectation.requiredAny || []).length) {
    invariant(
      expectation.requiredAny.some((marker) => text.includes(marker)),
      `${label}: none of the accepted semantics were visible: ${expectation.requiredAny.join(", ")}; actual=${text}`,
    );
  }
  if ((expectation.acceptableAnyGroups || []).length) {
    invariant(
      expectation.acceptableAnyGroups.some((group) => group.every((marker) => text.includes(marker))),
      `${label}: none of the accepted semantic groups matched: ${JSON.stringify(expectation.acceptableAnyGroups)}; actual=${text}`,
    );
  }
  for (const marker of expectation.forbidden || []) {
    invariant(!text.includes(marker), `${label}: forbidden semantic ${marker} was visible: ${text}`);
  }
  if (!expectation.allowControlledFailure) {
    for (const marker of [
      "结果暂时无法完整展示",
      "未能证明当前结果",
      "未获得可用于继续办理",
      "未获得可继续办理的明确结果",
      "未确认创建或提交任何业务申请",
      "请重新说明需要处理的事项",
    ]) {
      invariant(!text.includes(marker), `${label}: degraded instead of answering: ${text}`);
    }
  }
}

async function login(page) {
  const authToken = process.env.PRODUCT_BROWSER_AUTH_TOKEN || "";
  if (authToken) {
    await page.addInitScript(
      ({ key, token }) => localStorage.setItem(key, token),
      { key: "agent.product.token", token: authToken },
    );
  }
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await page.getByRole("heading", { name: "Agent 客户自助" }).waitFor();
  const loginButton = page.getByRole("button", { name: "登录" });
  if (await loginButton.count() === 1) {
    await page.getByLabel("账号").selectOption("customer_u001");
    await page.getByLabel("密码").fill("123456");
    await loginButton.click();
  }
  await page.getByText("u001 · default").waitFor();
  await page.getByRole("button", { name: /10004/ }).waitFor();
}

async function transcript(page) {
  return page.locator(".chat-message").evaluateAll((messages) => messages.map((message) => ({
    role: message.classList.contains("user") ? "user" : "agent",
    text: String(message.textContent || "").replace(/\s+/g, " ").trim(),
    contracts: Array.from(message.querySelectorAll("[data-contract-id]")).map((node) => node.getAttribute("data-contract-id")),
  })));
}

async function sendTurn(page, prompt, expectation) {
  const agents = page.locator(".chat-message.agent");
  const before = await agents.count();
  const input = page.getByRole("textbox", { name: "输入问题" });
  const send = page.getByRole("button", { name: "发送" });
  await input.fill(prompt);
  await send.waitFor({ state: "visible" });
  await page.waitForFunction(
    () => {
      const button = Array.from(document.querySelectorAll("button")).find((node) => node.textContent?.includes("发送"));
      return Boolean(button && !button.disabled);
    },
    undefined,
    { timeout: 30_000 },
  );
  const [response] = await Promise.all([
    page.waitForResponse(
      (candidate) => candidate.url().endsWith("/api/chat/turn"),
      { timeout: 120_000 },
    ),
    send.click(),
  ]);
  invariant(response.ok(), `${expectation.label}: chat HTTP status ${response.status()}`);
  const payload = await response.json();
  invariant(payload?.type === "answer", `${expectation.label}: public response type is ${payload?.type}`);
  await page.waitForFunction(
    (count) => document.querySelectorAll(".chat-message.agent").length === count + 1,
    before,
    { timeout: 120_000 },
  );
  const current = agents.last();
  const text = normalized(await current.innerText());
  const contracts = await current.locator("[data-contract-id]").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-contract-id")));
  const result = { prompt, text, contracts, payloadMode: payload.presentation_mode || null };
  assertSemantic(result, expectation);
  return result;
}

async function assertReloadEquivalent(page, liveTranscript, label) {
  await page.reload({ waitUntil: "networkidle" });
  await page.getByRole("log", { name: "对话记录" }).waitFor();
  await page.waitForFunction(
    (count) => document.querySelectorAll(".chat-message").length === count,
    liveTranscript.length,
    { timeout: 30_000 },
  );
  const restored = await transcript(page);
  invariant(
    JSON.stringify(restored) === JSON.stringify(liveTranscript),
    `${label}: live/history transcript mismatch: live=${JSON.stringify(liveTranscript)} restored=${JSON.stringify(restored)}`,
  );
}

async function runJourney(browser) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const diagnostics = { console: [], pageErrors: [], requests: [] };
  page.on("console", (message) => diagnostics.console.push({ type: message.type(), text: message.text() }));
  page.on("pageerror", (error) => diagnostics.pageErrors.push(String(error?.stack || error)));
  page.on("request", (request) => {
    if (request.url().includes("/api/chat/")) diagnostics.requests.push({ method: request.method(), url: request.url() });
  });
  page.setDefaultTimeout(120_000);
  try {
    await login(page);

  if (scenario === "reported") {
    const results = [];
    for (const [prompt, expectation] of reportedRegressionTurns) {
      results.push(await sendTurn(page, prompt, expectation));
    }
    const live = await transcript(page);
    await assertReloadEquivalent(page, live, "targeted reported regression reload");
    await page.screenshot({ path: path.join(artifactDir, "strong-context-reported-regression.png"), fullPage: true });
    await context.close();
    return {
      firstThreadTurns: 0,
      secondThreadTurns: 0,
      reportedRegressionTurns: reportedRegressionTurns.length,
      reloadEquivalent: true,
      results,
    };
  }

  if (scenario === "clarification-resume") {
    const results = [];
    for (const [prompt, expectation] of clarificationResumeTurns) {
      results.push(await sendTurn(page, prompt, expectation));
    }
    const resumedLive = await transcript(page);
    await assertReloadEquivalent(page, resumedLive, "clarification resume reload");

    await page.getByRole("button", { name: "新会话" }).click();
    for (const [prompt, expectation] of clarificationAbandonTurns) {
      results.push(await sendTurn(page, prompt, expectation));
    }
    const abandonedLive = await transcript(page);
    await assertReloadEquivalent(page, abandonedLive, "clarification abandon reload");
    await page.screenshot({ path: path.join(artifactDir, "strong-context-clarification-resume.png"), fullPage: true });
    await context.close();
    return {
      firstThreadTurns: clarificationResumeTurns.length,
      secondThreadTurns: clarificationAbandonTurns.length,
      reportedRegressionTurns: 0,
      reloadEquivalent: true,
      results,
    };
  }

  const firstThread = normalized(await page.locator(".thread-tools strong").textContent());
  const results = [];
  const turns = [
    ["我买过什么？", { label: "order inventory", requiredAll: ["10001", "10002", "10003", "10004"] }],
    ["可以退货退款吗？", {
      label: "visible collection member eligibility",
      acceptableAnyGroups: [
        ["10001", "10002", "10003", "10004", "退款"],
        ["哪", "订单"],
        ["具体", "订单"],
      ],
    }],
    ["哪些还在路上？", {
      label: "visible collection filter",
      requiredAll: ["10001"],
      requiredAny: ["蓝牙耳机", "运输中", "已发货"],
      forbidden: ["10003", "无线鼠标", "待发货"],
    }],
    ["其中最贵的是哪个？", { label: "collection superlative", requiredAll: ["10001"], requiredAny: ["蓝牙耳机", "199"] }],
    ["它现在是什么状态？", { label: "single object pronoun", requiredAny: ["10001", "蓝牙耳机", "运输中", "已发货"] }],
    ["它可以退货退款吗？先不要提交。", {
      label: "refund eligibility",
      requiredAll: ["10001"],
      requiredAny: ["未签收", "不能", "暂不", "资格"],
      forbidden: ["知识库资料不足", "已提交", "申请成功"],
    }],
    ["先不办理。那无线鼠标什么时候发货？", {
      label: "topic switch logistics",
      requiredAll: ["10003"],
      requiredAny: ["无线鼠标", "待发货", "备货"],
      forbidden: ["10001", "10002", "10004", "蓝牙耳机", "机械键盘", "定制马克杯"],
    }],
    ["回到刚才的蓝牙耳机，它是哪一个订单？", { label: "return to prior object", requiredAll: ["10001"], requiredAny: ["蓝牙耳机", "订单"] }],
    ["订单10004能开发票吗？我只问发票，不要退款，也不要售后。", {
      label: "invoice intent isolation",
      requiredAll: ["10004"],
      requiredAny: ["发票", "开票"],
      forbidden: ["申请退款", "申请售后", "退款政策", "售后政策", "物流说明", "未发货订单通常"],
    }],
    ["刚才我要给哪个订单开票？", { label: "invoice goal recall", requiredAll: ["10004"], requiredAny: ["发票", "开票", "定制马克杯"] }],
  ];
  for (const [prompt, expectation] of turns) {
    results.push(await sendTurn(page, prompt, expectation));
  }

  const firstLive = await transcript(page);
  await assertReloadEquivalent(page, firstLive, "long thread reload");

  await page.getByRole("button", { name: "新会话" }).click();
  const secondThread = normalized(await page.locator(".thread-tools strong").textContent());
  invariant(firstThread && secondThread && firstThread !== secondThread, "new conversation did not isolate thread identity");

  results.push(await sendTurn(page, "它现在能退吗？", {
    label: "fresh thread ambiguity",
    requiredAny: ["请明确", "请提供", "订单号", "哪笔", "指定"],
    forbidden: ["10001", "10002", "10003", "10004", "蓝牙耳机", "无线鼠标"],
    allowControlledFailure: true,
  }));
  results.push(await sendTurn(page, "订单10003现在是什么状态？", {
    label: "fresh thread explicit target",
    requiredAll: ["10003"],
    requiredAny: ["无线鼠标", "待发货"],
  }));
  results.push(await sendTurn(page, "它是什么商品？", {
    label: "fresh thread immediate pronoun",
    requiredAny: ["无线鼠标", "10003"],
  }));

  const secondLive = await transcript(page);
  await assertReloadEquivalent(page, secondLive, "fresh thread reload");

  // Exact customer-reported regression.  This must run as one real browser
  // conversation: the logistics answer establishes a visible one-order set,
  // and the next eligibility question must bind to that set instead of being
  // routed to an unrelated action or a generic controlled-failure response.
  await page.getByRole("button", { name: "新会话" }).click();
  const thirdThread = normalized(await page.locator(".thread-tools strong").textContent());
  invariant(
    thirdThread && thirdThread !== firstThread && thirdThread !== secondThread,
    "reported-regression conversation did not isolate thread identity",
  );
  for (const [prompt, expectation] of reportedRegressionTurns) {
    results.push(await sendTurn(page, prompt, expectation));
  }
  const thirdLive = await transcript(page);
  await assertReloadEquivalent(page, thirdLive, "reported regression reload");

  await page.getByRole("button", { name: "新会话" }).click();
  for (const [prompt, expectation] of clarificationResumeTurns) {
    results.push(await sendTurn(page, prompt, expectation));
  }
  const fourthLive = await transcript(page);
  await assertReloadEquivalent(page, fourthLive, "clarification resume reload");

  await page.getByRole("button", { name: "新会话" }).click();
  for (const [prompt, expectation] of clarificationAbandonTurns) {
    results.push(await sendTurn(page, prompt, expectation));
  }
  const fifthLive = await transcript(page);
  await assertReloadEquivalent(page, fifthLive, "clarification abandon reload");
  await page.screenshot({ path: path.join(artifactDir, "strong-context-product-journey.png"), fullPage: true });
    await context.close();
    return {
      firstThreadTurns: turns.length,
      secondThreadTurns: 3 + clarificationResumeTurns.length + clarificationAbandonTurns.length,
      reportedRegressionTurns: reportedRegressionTurns.length,
      reloadEquivalent: true,
      results,
    };
  } catch (error) {
    const input = page.getByRole("textbox", { name: "输入问题" });
    const send = page.getByRole("button", { name: "发送" });
    diagnostics.failure = {
      error: String(error?.stack || error),
      url: page.url(),
      inputCount: await input.count().catch(() => 0),
      inputValue: await input.inputValue().catch(() => ""),
      sendCount: await send.count().catch(() => 0),
      sendDisabled: await send.isDisabled().catch(() => null),
      transcript: await transcript(page).catch(() => []),
    };
    await page.screenshot({ path: path.join(artifactDir, "strong-context-failure.png"), fullPage: true }).catch(() => {});
    await writeFile(
      path.join(artifactDir, "strong-context-failure.json"),
      `${JSON.stringify(diagnostics, null, 2)}\n`,
      "utf8",
    ).catch(() => {});
    await context.close().catch(() => {});
    throw error;
  }
}

if (!baseURL || !artifactDir) throw new Error("PRODUCT_WEB_URL and PRODUCT_BROWSER_ARTIFACT_DIR are required");
invariant(modelMode === "configured", `strong context journey requires configured model, received ${modelMode}`);
invariant(["full", "reported", "clarification-resume"].includes(scenario), `unsupported strong-context scenario: ${scenario}`);
await mkdir(artifactDir, { recursive: true });
const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined, headless: true });
try {
  const journey = await runJourney(browser);
  process.stdout.write(JSON.stringify({
    status: "PASS",
    engine: "chromium",
    modelMode,
    scenario,
    journey,
    artifacts: [
      scenario === "full"
        ? "strong-context-product-journey.png"
        : scenario === "clarification-resume"
          ? "strong-context-clarification-resume.png"
          : "strong-context-reported-regression.png",
    ],
  }));
} finally {
  await browser.close();
}
