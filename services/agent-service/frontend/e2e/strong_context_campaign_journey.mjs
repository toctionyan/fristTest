import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import { campaignDefinition } from "./in_app_context_campaign.mjs";

const baseURL = process.env.PRODUCT_WEB_URL;
const artifactDir = process.env.PRODUCT_BROWSER_ARTIFACT_DIR;
const modelMode = process.env.PRODUCT_BROWSER_MODEL_MODE;
const seed = Number(process.env.PRODUCT_BROWSER_CAMPAIGN_SEED || "20260715");
const phase = process.env.PRODUCT_BROWSER_CAMPAIGN_PHASE || "repair-retest";
const minimumTurnPassRate = Number(process.env.PRODUCT_BROWSER_CAMPAIGN_MIN_TURN_PASS_RATE || "0.8");
const minimumScenariosAtEight = Number(process.env.PRODUCT_BROWSER_CAMPAIGN_MIN_SCENARIOS_AT_EIGHT || "16");
const reportPath = path.join(artifactDir || ".", `strong-context-campaign-${seed}-${phase}.json`);

const GENERIC_FAILURES = [
  "系统未获得可继续办理的明确结果",
  "系统未能证明当前结果完整满足你的查询条件",
  "未获得可继续办理的明确结果",
  "未确认创建或提交任何业务申请",
  "请刷新后查看事务中心",
  "请重新说明需要处理的事项",
  "结果暂时无法完整展示",
];
const INTERNAL_MARKERS = [
  "Traceback", "Exception:", "tool_trace", "state_contract_violations",
  "projection_contract_violation", "registered_primary_presentation",
];
const TARGET_MARKERS = new Set([
  "10001", "10002", "10003", "10004",
  "蓝牙耳机", "机械键盘", "无线鼠标", "定制马克杯",
]);
const SUCCESS_MARKERS = ["申请成功", "退款成功", "开票成功", "取消成功", "已提交", "已取消订单"];

function invariant(condition, message) {
  if (!condition) throw new Error(message);
}

function normalized(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function evaluateTurn(text, expected = {}, tags = []) {
  const errors = [];
  if (!text) errors.push("empty_response");
  for (const marker of INTERNAL_MARKERS) {
    if (text.includes(marker)) errors.push(`internal_marker:${marker}`);
  }
  for (const marker of expected.requiredAll || []) {
    if (!text.includes(marker)) errors.push(`missing_required:${marker}`);
  }
  if ((expected.requiredAny || []).length && !expected.requiredAny.some((marker) => text.includes(marker))) {
    errors.push(`missing_any:${expected.requiredAny.join("|")}`);
  }
  for (const marker of expected.forbidden || []) {
    if (text.includes(marker)) errors.push(`forbidden:${marker}`);
  }
  const expectedOrderIds = [...new Set((expected.requiredAll || [])
    .filter((marker) => /^\d{5}$/.test(String(marker))))].sort();
  if (expectedOrderIds.length && !expected.allowAdditionalOrderIds) {
    const actualOrderIds = [...new Set(text.match(/\b\d{5}\b/g) || [])].sort();
    for (const orderId of actualOrderIds) {
      if (!expectedOrderIds.includes(orderId)) errors.push(`unexpected_order_id:${orderId}`);
    }
  }
  if (!expected.allowControlledFailure) {
    for (const marker of GENERIC_FAILURES) {
      if (text.includes(marker)) errors.push(`generic_failure:${marker}`);
    }
  }
  const critical = [];
  for (const error of errors) {
    const [kind, marker = ""] = error.split(":", 2);
    if (kind === "internal_marker") critical.push({ class: "p0_safety", marker });
    if (kind === "forbidden" && TARGET_MARKERS.has(marker)) critical.push({ class: "wrong_target", marker });
    if (kind === "forbidden" && SUCCESS_MARKERS.includes(marker)) {
      critical.push({ class: tags.some((tag) => ["transaction", "draft", "no-commit", "cancel"].includes(tag))
        ? "stale_transaction" : "p0_safety", marker });
    }
    if (kind === "forbidden" && tags.includes("thread-isolation") && TARGET_MARKERS.has(marker)) {
      critical.push({ class: "cross_thread_leakage", marker });
    }
  }
  return { pass: errors.length === 0, errors, critical };
}

async function persist(report) {
  await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
}

async function transcript(page) {
  return page.locator(".chat-message").evaluateAll((messages) => messages.map((message) => ({
    role: message.classList.contains("user") ? "user" : "agent",
    text: String(message.textContent || "").replace(/\s+/g, " ").trim(),
    contracts: Array.from(message.querySelectorAll("[data-contract-id]"))
      .map((node) => node.getAttribute("data-contract-id"))
      .filter(Boolean),
  })));
}

async function currentThreadId(page) {
  return normalized(await page.locator(".thread-tools strong").textContent().catch(() => ""));
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
  const login = page.getByRole("button", { name: "登录" });
  if (await login.count() === 1) {
    await page.getByLabel("账号").selectOption("customer_u001");
    await page.getByLabel("密码").fill("123456");
    await login.click();
  }
  await page.getByText("u001 · default").waitFor();
  await page.getByRole("textbox", { name: "输入问题" }).waitFor();
}

async function newThread(page) {
  const before = await currentThreadId(page);
  const button = page.getByRole("button", { name: "新会话" });
  invariant(await button.count() === 1, "new conversation button is not unique");
  await button.click();
  await page.waitForFunction(
    (previous) => {
      const current = document.querySelector(".thread-tools strong")?.textContent?.trim() || "";
      return Boolean(current && current !== previous);
    },
    before,
    { timeout: 30_000 },
  );
  return currentThreadId(page);
}

async function sendTurn(page, turn, turnIndex, tags) {
  const startedAt = new Date().toISOString();
  const agents = page.locator(".chat-message.agent");
  const before = await agents.count();
  const input = page.getByRole("textbox", { name: "输入问题" });
  const send = page.getByRole("button", { name: "发送" });
  try {
    invariant(await input.count() === 1 && await send.count() === 1, "chat composer is not unique");
    await input.fill(turn.prompt);
    await page.waitForFunction(
      () => {
        const candidate = Array.from(document.querySelectorAll("button"))
          .find((node) => node.textContent?.includes("发送"));
        return Boolean(candidate && !candidate.disabled);
      },
      undefined,
      { timeout: 30_000 },
    );
    const [response] = await Promise.all([
      page.waitForResponse((candidate) => candidate.url().endsWith("/api/chat/turn"), { timeout: 180_000 }),
      send.click(),
    ]);
    let publicPayload = null;
    try {
      publicPayload = await response.json();
    } catch {
      publicPayload = { type: "invalid_json", httpStatus: response.status() };
    }
    await page.waitForFunction(
      (count) => {
        const rows = Array.from(document.querySelectorAll(".chat-message.agent"));
        return rows.length > count && String(rows.at(-1)?.textContent || "").trim().length > 0;
      },
      before,
      { timeout: 180_000 },
    );
    const current = agents.last();
    const text = normalized(await current.innerText());
    const contracts = await current.locator("[data-contract-id]").evaluateAll((nodes) => nodes
      .map((node) => node.getAttribute("data-contract-id")).filter(Boolean));
    const verdict = evaluateTurn(text, turn.expected, tags);
    if (!response.ok()) verdict.errors.push(`http_status:${response.status()}`);
    if (publicPayload?.type !== "answer") verdict.errors.push(`public_type:${publicPayload?.type || "missing"}`);
    verdict.pass = verdict.errors.length === 0;
    return {
      turn: turnIndex,
      prompt: turn.prompt,
      expected: turn.expected,
      response: text,
      contracts,
      publicResponse: {
        type: publicPayload?.type || null,
        presentationMode: publicPayload?.presentation_mode || null,
        threadId: publicPayload?.thread_id || null,
        turnId: publicPayload?.turn_id || null,
      },
      ...verdict,
      startedAt,
      finishedAt: new Date().toISOString(),
    };
  } catch (error) {
    return {
      turn: turnIndex,
      prompt: turn.prompt,
      expected: turn.expected,
      response: "",
      contracts: [],
      publicResponse: null,
      pass: false,
      errors: [`browser_error:${String(error?.message || error)}`],
      critical: [],
      startedAt,
      finishedAt: new Date().toISOString(),
    };
  }
}

function summarize(report) {
  const turns = report.scenarios.flatMap((scenario) => scenario.turns);
  const passedTurns = turns.filter((turn) => turn.pass).length;
  const scenariosAtEight = report.scenarios.filter((scenario) => scenario.passTurns >= 8).length;
  const criticalFailures = report.scenarios.flatMap((scenario) => scenario.turns.flatMap((turn) =>
    turn.critical.map((failure) => ({ scenario: scenario.id, turn: turn.turn, prompt: turn.prompt, ...failure }))));
  const reloadFailures = report.scenarios.filter((scenario) => !scenario.reloadEquivalent).map((scenario) => scenario.id);
  const turnPassRate = turns.length ? Number((passedTurns / turns.length).toFixed(4)) : 0;
  return {
    completedScenarios: report.scenarios.length,
    totalTurns: turns.length,
    passedTurns,
    failedTurns: turns.length - passedTurns,
    turnPassRate,
    fullyPassedScenarios: report.scenarios.filter((scenario) => scenario.passTurns === 10).length,
    scenariosAtEight,
    reloadEquivalentScenarios: report.scenarios.length - reloadFailures.length,
    reloadFailures,
    criticalFailures,
    acceptance: {
      minimumTurnPassRate,
      minimumScenariosAtEight,
      requiresReloadEquivalent: 20,
      requiresZeroCriticalFailures: true,
      pass: report.scenarios.length === 20
        && turns.length === 200
        && turnPassRate >= minimumTurnPassRate
        && scenariosAtEight >= minimumScenariosAtEight
        && reloadFailures.length === 0
        && criticalFailures.length === 0,
    },
  };
}

async function runCampaign(browser) {
  const definition = campaignDefinition(seed);
  invariant(definition.scenarioCount === 20 && definition.totalTurns === 200, "campaign must be exactly 20x10");
  const report = {
    schemaVersion: 2,
    campaignId: `strong-context-${seed}-${phase}`,
    engine: "playwright-chromium-web-ui",
    modelMode,
    phase,
    seed,
    baseURL,
    definition: {
      scenarioCount: definition.scenarioCount,
      turnsPerScenario: definition.turnsPerScenario,
      totalTurns: definition.totalTurns,
      order: definition.order,
      scenarios: definition.scenarios,
    },
    startedAt: new Date().toISOString(),
    scenarios: [],
  };
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  page.setDefaultTimeout(180_000);
  try {
    await login(page);
    for (let index = 0; index < definition.scenarios.length; index += 1) {
      const scenario = definition.scenarios[index];
      const threadId = await newThread(page);
      const row = {
        index: index + 1,
        id: scenario.id,
        tags: scenario.tags,
        threadId,
        startedAt: new Date().toISOString(),
        turns: [],
      };
      for (let turnIndex = 0; turnIndex < scenario.turns.length; turnIndex += 1) {
        row.turns.push(await sendTurn(page, scenario.turns[turnIndex], turnIndex + 1, scenario.tags));
        report.activeScenario = row;
        report.updatedAt = new Date().toISOString();
        await persist(report);
      }
      const live = await transcript(page);
      await page.reload({ waitUntil: "networkidle" });
      await page.getByRole("textbox", { name: "输入问题" }).waitFor();
      await page.waitForFunction(
        (count) => document.querySelectorAll(".chat-message").length === count,
        live.length,
        { timeout: 30_000 },
      );
      const restored = await transcript(page);
      row.reloadEquivalent = JSON.stringify(live) === JSON.stringify(restored);
      row.transcript = restored;
      row.passTurns = row.turns.filter((turn) => turn.pass).length;
      row.finishedAt = new Date().toISOString();
      delete report.activeScenario;
      report.scenarios.push(row);
      report.summary = summarize(report);
      report.updatedAt = new Date().toISOString();
      await persist(report);
      process.stdout.write(`${JSON.stringify({ progress: index + 1, scenario: scenario.id, passTurns: row.passTurns, reloadEquivalent: row.reloadEquivalent })}\n`);
    }
    report.finishedAt = new Date().toISOString();
    report.summary = summarize(report);
    await page.screenshot({ path: path.join(artifactDir, `strong-context-campaign-${seed}-${phase}.png`), fullPage: true });
    await persist(report);
    return report;
  } finally {
    await context.close();
  }
}

invariant(baseURL && artifactDir, "PRODUCT_WEB_URL and PRODUCT_BROWSER_ARTIFACT_DIR are required");
invariant(modelMode === "configured", `campaign requires configured model, received ${modelMode}`);
invariant(Number.isInteger(seed), "campaign seed must be an integer");
await mkdir(artifactDir, { recursive: true });
const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined, headless: true });
try {
  const report = await runCampaign(browser);
  process.stdout.write(`${JSON.stringify({ status: report.summary.acceptance.pass ? "PASS" : "FAIL", artifact: reportPath, summary: report.summary })}\n`);
  if (!report.summary.acceptance.pass) process.exitCode = 1;
} finally {
  await browser.close();
}
