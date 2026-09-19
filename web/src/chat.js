import { createChartView, element } from "./chart-view.js";
import "./style.css";

const root = document.querySelector("#app");
root.innerHTML = `
  <div class="studio">
    <aside class="sidebar">
      <a class="brand" href="/" aria-label="Foundry Charts home"><span class="brand-mark">▥</span><span>Foundry<span class="brand-sub">CHARTS STUDIO</span></span></a>
      <div class="sidebar-section"><span class="eyebrow">WORKSPACE</span><div class="workspace-active">◈ &nbsp; Sales exploration <span class="count">01</span></div></div>
      <div class="sidebar-section"><span class="eyebrow">YOUR DATA</span><p class="data-label">◉ &nbsp; Global sales · 2025</p><p class="muted">A fictional dataset. Real exploration.</p></div>
      <div class="sidebar-bottom"><span class="status-dot"></span><span id="connection-status">Checking gateway…</span><p>Powered by Microsoft Foundry</p></div>
    </aside>
    <main class="main">
      <header class="topbar"><div><span class="eyebrow">ANALYTICS WORKSPACE</span><h1>Sales studio</h1></div><button id="clear-chat" class="button subtle" type="button">＋ New conversation</button></header>
      <section class="welcome" id="welcome"><span class="badge">YOUR DATA, IN FOCUS</span><h2>A clearer picture starts<br>with a good question.</h2><p>Ask about sales. Compare performance. Follow your curiosity.<br>Every chart is interactive, every number is grounded in your data.</p>
        <div class="suggestions">
          <button type="button" data-prompt="Show revenue by region as a bar chart."><span class="suggestion-icon">▥</span><strong>The big picture</strong><span>Compare revenue across regions →</span></button>
          <button type="button" data-prompt="Show monthly revenue split by region as a line chart."><span class="suggestion-icon">⌁</span><strong>Find the trend</strong><span>See how revenue changed over time →</span></button>
          <button type="button" data-prompt="Show a donut chart of orders by channel."><span class="suggestion-icon">◉</span><strong>Explore the mix</strong><span>Break down orders by sales channel →</span></button>
        </div>
      </section>
      <section id="conversation" class="conversation" aria-label="Conversation" aria-live="polite"></section>
      <div class="composer-wrap"><form id="chat-form" class="composer"><label class="sr-only" for="message">Ask about your sales data</label><textarea id="message" rows="1" maxlength="8000" placeholder="Ask about your sales data…" required></textarea><button id="send" type="submit" aria-label="Send message">↑</button></form><p class="composer-hint"><span>↵ Send &nbsp; · &nbsp; Shift + ↵ New line</span><span>Synthetic 2025 sales · AI can make mistakes</span></p></div>
    </main>
  </div>`;

const conversation = root.querySelector("#conversation");
const input = root.querySelector("#message");
const submit = root.querySelector("#send");
let previousResponseId;
let sending = false;
let generation = 0;
const charts = [];

async function post(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    credentials: "same-origin",
  });
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) throw new Error("The gateway returned an unexpected response.");
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status}).`);
  return result;
}

function message(role, text) {
  const block = element("div", `message ${role}`);
  block.append(element("div", "message-avatar", role === "user" ? "YOU" : "◈"));
  const body = element("div", "message-body");
  body.append(element("p", "message-name", role === "user" ? "You" : "Foundry analyst"));
  const content = element("p", "message-text", text);
  body.append(content);
  block.append(body);
  conversation.append(block);
  return { block, body, content };
}

async function sendMessage(text) {
  if (sending || !text.trim()) return;
  sending = true;
  submit.disabled = true;
  const current = generation;
  root.querySelector("#welcome").hidden = true;
  message("user", text);
  input.value = "";
  const reply = message("assistant", "Looking into your data…");
  reply.block.classList.add("pending");
  reply.block.scrollIntoView({ block: "end", behavior: "smooth" });
  try {
    const result = await post("/api/chat", { message: text, ...(previousResponseId ? { previous_response_id: previousResponseId } : {}) });
    if (generation !== current) return;
    previousResponseId = result.response_id;
    reply.content.textContent = result.text || (result.charts.length ? "Here’s your chart. Select a mark to explore further." : "No chart was returned.");
    for (const bundle of result.charts) {
      const chart = createChartView(bundle, { onDrill: (request) => post("/api/chart", { request }) });
      charts.push(chart);
      reply.body.append(chart.element);
    }
  } catch (error) {
    if (generation === current) {
      reply.content.textContent = error.message;
      reply.content.classList.add("error");
    }
  } finally {
    reply.block.classList.remove("pending");
    if (generation === current) {
      sending = false;
      submit.disabled = false;
      input.focus();
    }
  }
}

root.querySelector("#chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  void sendMessage(input.value.trim());
});
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    void sendMessage(input.value.trim());
  }
});
root.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => void sendMessage(button.dataset.prompt));
});
root.querySelector("#clear-chat").addEventListener("click", () => {
  generation++;
  previousResponseId = undefined;
  charts.splice(0).forEach((chart) => chart.destroy());
  conversation.replaceChildren();
  root.querySelector("#welcome").hidden = false;
  sending = false;
  submit.disabled = false;
  input.focus();
});
fetch("/health").then((response) => {
  if (!response.ok) throw new Error("Health check failed");
  root.querySelector("#connection-status").textContent = "Gateway connected";
}).catch(() => {
  root.querySelector("#connection-status").textContent = "Gateway unavailable";
  root.querySelector(".status-dot").classList.add("offline");
});
