import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Trend } from "k6/metrics";
import { textSummary } from "./vendor/k6-summary.js";

const loginDuration = new Trend("login_duration", true);
const chatDuration = new Trend("chat_duration", true);
const login429s = new Counter("login_429s");
const chat429s = new Counter("chat_429s");

const baseUrl = (__ENV.BASE_URL || "https://localhost:8443").replace(/\/$/, "");

export const options = {
  insecureSkipTLSVerify: true,
  scenarios: {
    login_burst: {
      executor: "shared-iterations",
      vus: 10,
      iterations: 20,
      maxDuration: "30s",
      exec: "loginScenario",
    },
    chat_probe: {
      executor: "shared-iterations",
      vus: 5,
      iterations: 15,
      maxDuration: "30s",
      startTime: "1s",
      exec: "chatScenario",
    },
  },
  thresholds: {
    login_duration: ["p(95)<5000"],
    chat_duration: ["p(95)<1000"],
    login_429s: ["count>0"],
    checks: ["rate>0.9"],
  },
};

export function loginScenario() {
  probeLogin();
  sleep(0.1);
}

export function chatScenario() {
  probeChat();
  sleep(0.1);
}

function probeLogin() {
  const payload = JSON.stringify({
    email: "load-test@example.com",
    password: "not-a-real-password",
  });

  const response = http.post(`${baseUrl}/auth/login`, payload, {
    headers: { "Content-Type": "application/json" },
    tags: { endpoint: "login" },
  });

  if (response.status === 429) {
    login429s.add(1);
  } else if (response.status > 0 && response.status < 500) {
    loginDuration.add(response.timings.duration);
  }

  check(response, {
    "login returns 401 or 429": (r) => r.status === 401 || r.status === 429,
    "login does not return 5xx": (r) => r.status < 500 && r.status !== 0,
  });
}

function probeChat() {
  const payload = JSON.stringify({
    message: "Where is room 301?",
    metadata: { load_test: true },
  });

  const response = http.post(`${baseUrl}/api/chat`, payload, {
    headers: { "Content-Type": "application/json" },
    tags: { endpoint: "chat" },
  });

  if (response.status === 429) {
    chat429s.add(1);
  } else if (response.status > 0 && response.status < 500) {
    chatDuration.add(response.timings.duration);
  }

  const isSuccess = response.status === 200 || response.status === 201 || response.status === 429;
  if (!isSuccess) {
    console.log(`[Chat Debug] Status: ${response.status}, Body: ${response.body}`);
  }

  check(response, {
    "chat returns 20x or 429": (r) => r.status === 200 || r.status === 201 || r.status === 429,
    "chat does not return 5xx": (r) => r.status < 500 && r.status !== 0,
  });
}

export function handleSummary(data) {
  const metric = (name) => data.metrics[name]?.values || {};
  const login = metric("login_duration");
  const chat = metric("chat_duration");
  const loginRateLimits = metric("login_429s").count || 0;
  const chatRateLimits = metric("chat_429s").count || 0;

  return {
    stdout:
      `\nLoad baseline\n` +
      `login_p95=${login["p(95)"] ?? "n/a"}ms\n` +
      `chat_p95=${chat["p(95)"] ?? "n/a"}ms\n` +
      `login_429s=${loginRateLimits}\n` +
      `chat_429s=${chatRateLimits}\n\n` +
      textSummary(data, { indent: " ", enableColors: false }),
  };
}
