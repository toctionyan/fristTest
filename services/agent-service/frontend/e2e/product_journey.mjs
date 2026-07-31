import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import path from "node:path";

const baseURL = process.env.PRODUCT_WEB_URL;
const artifactDir = process.env.PRODUCT_BROWSER_ARTIFACT_DIR;

function invariant(condition, message) {
  if (!condition) throw new Error(message);
}

function normalized(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

async function visibleAssistantEnvelope(message) {
  return {
    text: normalized(await message.innerText()),
    contracts: await message.locator("[data-contract-id]").evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute("data-contract-id")),
    ),
  };
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
  await page.getByRole("button", { name: /10003/ }).waitFor();
}

async function desktopJourney(browser) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await login(page);

  await page.getByRole("button", { name: /机械键盘.*10002/ }).click();
  await page.locator(".detail-pane").getByRole("heading", { name: "机械键盘" }).waitFor();
  const log = page.getByRole("log", { name: "对话记录" });
  await log.waitFor();
  invariant(await log.getAttribute("aria-live") === "polite", "chat log must announce updates politely");
  await page.getByRole("textbox", { name: "输入问题" }).fill("你好");
  const desktopChatResponse = page.waitForResponse((response) => response.url().endsWith("/api/chat/turn"));
  await page.getByRole("button", { name: "发送" }).click();
  const desktopChatPayload = await (await desktopChatResponse).json();
  invariant(desktopChatPayload?.type === "answer", "desktop chat did not return a public answer");
  const liveAgent = log.locator(".chat-message.agent").last();
  await liveAgent.waitFor();
  const liveEnvelope = await visibleAssistantEnvelope(liveAgent);
  invariant(liveEnvelope.text, "desktop assistant rendered an empty live bubble");
  invariant(
    await page.locator(".detail-pane").getByRole("heading", { name: "机械键盘" }).isVisible(),
    "explicit order selection reset after unrelated chat refresh",
  );

  await page.reload({ waitUntil: "networkidle" });
  await page.getByText("u001 · default").waitFor();
  const restoredLog = page.getByRole("log", { name: "对话记录" });
  const restoredAgent = restoredLog.locator(".chat-message.agent").last();
  await restoredAgent.waitFor();
  const restoredEnvelope = await visibleAssistantEnvelope(restoredAgent);
  invariant(
    JSON.stringify(restoredEnvelope) === JSON.stringify(liveEnvelope),
    `live/history assistant envelope mismatch: live=${JSON.stringify(liveEnvelope)} restored=${JSON.stringify(restoredEnvelope)}`,
  );

  await page.getByRole("button", { name: /无线鼠标.*10003/ }).click();
  const detail = page.locator(".detail-pane");
  await detail.getByRole("heading", { name: "无线鼠标" }).waitFor();
  await detail.getByRole("button", { name: /取消/ }).click();
  await page.getByRole("heading", { name: /取消.*订单|订单.*取消/ }).waitFor();
  await page.getByText("当前会话待办").waitFor();
  const originalThread = (await page.locator(".thread-tools strong").textContent())?.trim();
  invariant(originalThread, "original thread id is missing");

  await page.getByRole("button", { name: "新会话" }).click();
  const newThread = (await page.locator(".thread-tools strong").textContent())?.trim();
  invariant(newThread && newThread !== originalThread, "new conversation did not change thread identity");
  const transactionRows = page.getByRole("region", { name: "事务中心" }).locator(".transaction-row");
  await transactionRows.first().waitFor();
  await transactionRows.first().click();
  await page.waitForFunction(
    (thread) => document.querySelector(".thread-tools strong")?.textContent?.trim() === thread,
    originalThread,
  );
  await page.getByRole("heading", { name: /取消.*订单|订单.*取消/ }).waitFor();
  invariant(
    await page.locator(".pending-row").count() >= 1,
    "pending transaction was not restored after cross-thread navigation",
  );

  // Complete the restored Draft through the actual product controls.  API
  // smoke proves the boundary contract; this proves a user can operate it.
  const card = page.locator(".transaction-card");
  for (const select of await card.locator("select").all()) {
    const firstValue = await select.locator("option").evaluateAll((options) =>
      options.map((option) => option.value).find((value) => value && value !== "__custom__") || "",
    );
    if (firstValue) await select.selectOption(firstValue);
  }
  for (const field of await card.locator('textarea, input[type="text"], input[type="number"], input[type="date"]').all()) {
    if (!(await field.inputValue())) await field.fill("浏览器完整生命周期验证");
  }
  const inputResponse = page.waitForResponse((response) => response.url().endsWith("/api/transactions/input"));
  await card.getByRole("button", { name: /下一步|继续/ }).click();
  const inputPayload = await (await inputResponse).json();
  invariant(inputPayload?.type === "interaction_required", "browser Draft input did not reach authority interaction");
  await card.getByRole("button", { name: "确认提交" }).waitFor();
  const authorityResponse = page.waitForResponse((response) => response.url().endsWith("/api/transactions/authority"));
  await card.getByRole("button", { name: "确认提交" }).click();
  const authorityPayload = await (await authorityResponse).json();
  invariant(authorityPayload?.type !== "error", "browser authority submission returned an error");
  await page.waitForFunction(() =>
    Array.from(document.querySelectorAll(".transaction-row")).some((row) => row.textContent?.includes("已完成")),
  );
  await page.waitForFunction(() => document.querySelector(".pending-head strong")?.textContent?.trim() === "0");
  await card.getByText("办理结果", { exact: true }).waitFor();
  const terminalTitle = (await card.locator("h2").textContent())?.trim();
  invariant(terminalTitle && terminalTitle !== "待办理事务", "terminal receipt retained a pending transaction title");

  await page.screenshot({ path: path.join(artifactDir, "desktop-product-journey.png"), fullPage: true });
  await context.close();
  return {
    selectionStable: true,
    threadNavigation: true,
    pendingRestored: true,
    lifecycleCommitted: true,
    receiptVisible: true,
    terminalTitleAccurate: true,
    accessibleChat: true,
    liveHistoryEquivalent: true,
  };
}

async function mobileJourney(browser) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  await login(page);
  const positions = await page.evaluate(() => {
    const agent = document.querySelector(".agent-pane")?.getBoundingClientRect();
    const orders = document.querySelector(".orders-pane")?.getBoundingClientRect();
    const detail = document.querySelector(".detail-pane")?.getBoundingClientRect();
    return {
      agentY: agent?.y,
      ordersY: orders?.y,
      detailY: detail?.y,
      scrollWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
    };
  });
  invariant(positions.agentY < positions.ordersY && positions.ordersY < positions.detailY, "mobile content priority is not chat-first");
  invariant(positions.scrollWidth <= positions.viewportWidth + 1, "mobile layout has horizontal overflow");
  const input = page.getByRole("textbox", { name: "输入问题" });
  await input.scrollIntoViewIfNeeded();
  await input.fill("你好");
  const mobileChatResponse = page.waitForResponse((response) => response.url().endsWith("/api/chat/turn"));
  await page.getByRole("button", { name: "发送" }).click();
  const mobileChatPayload = await (await mobileChatResponse).json();
  invariant(mobileChatPayload?.type === "answer", "mobile chat did not return a public answer");
  const mobileAgent = page.getByRole("log", { name: "对话记录" }).locator(".chat-message.agent").last();
  await mobileAgent.waitFor();
  invariant((await visibleAssistantEnvelope(mobileAgent)).text, "mobile assistant rendered an empty live bubble");
  await page.screenshot({ path: path.join(artifactDir, "mobile-product-journey.png"), fullPage: true });
  await context.close();
  return { viewport: [390, 844], chatFirst: true, noHorizontalOverflow: true, chatOperable: true };
}

if (!baseURL || !artifactDir) throw new Error("PRODUCT_WEB_URL and PRODUCT_BROWSER_ARTIFACT_DIR are required");
await mkdir(artifactDir, { recursive: true });
const chromiumExecutable = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || process.env.CHROMIUM_EXECUTABLE_PATH;
const browser = await chromium.launch({
  headless: true,
  ...(chromiumExecutable ? { executablePath: chromiumExecutable } : {}),
});
try {
  const desktop = await desktopJourney(browser);
  const mobile = await mobileJourney(browser);
  process.stdout.write(JSON.stringify({
    status: "PASS",
    engine: "chromium",
    desktop,
    mobile,
    artifacts: ["desktop-product-journey.png", "mobile-product-journey.png"],
  }));
} finally {
  await browser.close();
}
