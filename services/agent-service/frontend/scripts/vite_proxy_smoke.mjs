import http from "node:http";
import { spawn } from "node:child_process";

const agentPort = 18000;
const vitePort = 15173;
let agentRequests = 0;
const agent = http.createServer((req, res) => {
  agentRequests += 1;
  if (req.url === "/api/session/me") {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ actor: { user_id: "proxy-agent" } }));
    return;
  }
  res.writeHead(404).end();
});

function waitFor(url, timeoutMs = 15000) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const probe = () => {
      http.get(url, (res) => {
        res.resume();
        if (res.statusCode && res.statusCode < 500) return resolve();
        retry();
      }).on("error", retry);
    };
    const retry = () => {
      if (Date.now() - started > timeoutMs) return reject(new Error(`timeout waiting for ${url}`));
      setTimeout(probe, 120);
    };
    probe();
  });
}

await new Promise((resolve) => agent.listen(agentPort, "127.0.0.1", resolve));
const vite = spawn(process.platform === "win32" ? "npx.cmd" : "npx", ["vite", "--host", "127.0.0.1", "--port", String(vitePort)], {
  env: { ...process.env, VITE_AGENT_DEV_TARGET: `http://127.0.0.1:${agentPort}` },
  stdio: "ignore"
});
try {
  await waitFor(`http://127.0.0.1:${vitePort}/`);
  const payload = await new Promise((resolve, reject) => {
    http.get(`http://127.0.0.1:${vitePort}/api/session/me`, (res) => {
      let text = "";
      res.setEncoding("utf8");
      res.on("data", (part) => { text += part; });
      res.on("end", () => resolve({ status: res.statusCode, text }));
    }).on("error", reject);
  });
  if (payload.status !== 200 || !payload.text.includes("proxy-agent") || agentRequests < 1) {
    throw new Error(`Vite /api proxy did not reach Agent target: ${JSON.stringify(payload)}`);
  }
  console.log("Vite proxy smoke passed");
} finally {
  vite.kill("SIGTERM");
  await new Promise((resolve) => agent.close(resolve));
}
