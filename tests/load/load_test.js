import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Trend } from "k6/metrics";

const loginDuration = new Trend("login_duration", true);
const chatDuration = new Trend("chat_duration", true);
const login429s = new Counter("login_429s");
const chat429s = new Counter("chat_429s");

const baseUrl = (__ENV.BASE_URL || "http://localhost:8080").replace(/\/$/, "");

export const options = {
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
    login_duration: ["p(95)<500"],
    chat_duration: ["p(95)<2500"],
    login_429s: ["count>0"],
    chat_429s: ["count>0"],
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

  loginDuration.add(response.timings.duration);

  if (response.status === 429) {
    login429s.add(1);
  }

  check(response, {
    "login returns 401 or 429": (r) => r.status === 401 || r.status === 429,
    "login does not return 5xx": (r) => r.status < 500,
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

  chatDuration.add(response.timings.duration);

  if (response.status === 429) {
    chat429s.add(1);
  }

  check(response, {
    "chat returns 200 or 429": (r) => r.status === 200 || r.status === 429,
    "chat does not return 5xx": (r) => r.status < 500,
  });
}

export function handleSummary(data) {
  const metric = (name) => data.metrics[name]?.values || {};
  const login = metric("login_duration");
  const chat = metric("chat_duration");
  const loginRateLimits = metric("login_429s").count || 0;
  const chatRateLimits = metric("chat_429s").count || 0;

  return {
    stdout: `\nLoad baseline\nlogin_p95=${login["p(95)"] || "n/a"}ms\nchat_p95=${chat["p(95)"] || "n/a"}ms\nlogin_429s=${loginRateLimits}\nchat_429s=${chatRateLimits}\n`,
  };
}
