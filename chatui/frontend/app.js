const template = document.getElementById("chat-panel-template");
const blockedListEl = document.getElementById("blocked-list");
const verdictListEl = document.getElementById("verdict-list");

const ACCENTS = { a: "var(--accent-a)", b: "var(--accent-b)" };
const knownSessions = {}; // session_id -> { label, accent }

class ChatPanel {
  constructor(slotId, accent, title) {
    this.accent = accent;
    this.title = title;

    const node = template.content.cloneNode(true);
    this.root = node.querySelector(".chat-panel");
    this.root.dataset.accent = accent;

    document.getElementById(slotId).appendChild(node);

    this.titleEl = this.root.querySelector(".chat-title");
    this.sessionIdEl = this.root.querySelector(".session-id");
    this.newSessionBtn = this.root.querySelector(".new-session-btn");
    this.pipelineEl = this.root.querySelector(".pipeline");
    this.captionEl = this.root.querySelector(".pipeline-caption");
    this.bannerEl = this.root.querySelector(".banner");
    this.messagesEl = this.root.querySelector(".messages");
    this.formEl = this.root.querySelector(".chat-form");
    this.inputEl = this.root.querySelector(".chat-input");
    this.sendBtn = this.root.querySelector(".send-btn");

    this.titleEl.textContent = title;
    this.formEl.addEventListener("submit", (e) => this.handleSubmit(e));
    this.newSessionBtn.addEventListener("click", () => this.newSession());

    this.newSession();
  }

  newSession() {
    this.sessionId = crypto.randomUUID();
    knownSessions[this.sessionId] = { label: this.title, accent: this.accent };
    this.sessionIdEl.textContent = `id: ${this.sessionId.slice(0, 8)}`;
    this.sessionIdEl.title = this.sessionId;
    this.messagesEl.innerHTML = "";
    this.bannerEl.classList.add("hidden");
    this.setPipeline("idle", "Idle");
  }

  addMessage(role, text) {
    const div = document.createElement("div");
    div.className = `msg ${role}`;
    div.textContent = text;
    this.messagesEl.appendChild(div);
    this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
  }

  setPipeline(state, caption, errorNode) {
    this.pipelineEl.classList.remove("idle", "active", "success", "error");
    this.pipelineEl.classList.add(state);
    this.pipelineEl.querySelectorAll(".pipe-node").forEach((n) => {
      n.classList.toggle("error-node", errorNode && n.dataset.node === errorNode);
    });
    this.captionEl.textContent = caption;
  }

  async handleSubmit(e) {
    e.preventDefault();
    const text = this.inputEl.value.trim();
    if (!text) return;

    this.addMessage("user", text);
    this.inputEl.value = "";
    this.sendBtn.disabled = true;
    this.setPipeline("active", "Sending to LiteLLM...");

    const start = performance.now();
    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: this.sessionId, message: text }),
      });
      const data = await resp.json();
      const elapsed = data.latency_ms ?? Math.round(performance.now() - start);

      if (!resp.ok) {
        const blocked = /Blocked by AIJudge/i.test(data.error || "");
        this.setPipeline(
          "error",
          blocked ? `Blocked by Judge (${elapsed}ms)` : `Error (${elapsed}ms)`,
          blocked ? "litellm" : "backend"
        );
        this.addMessage("error", data.error || `Request failed (${resp.status})`);
        this.bannerEl.classList.toggle("hidden", !blocked);
        if (blocked) this.bannerEl.textContent = "This session has been blocked by AIJudge.";
      } else {
        this.setPipeline("success", `Round trip: ${elapsed}ms`);
        this.addMessage("assistant", data.reply);
      }
    } catch (err) {
      this.setPipeline("error", "Network error", "backend");
      this.addMessage("error", err.message);
    } finally {
      this.sendBtn.disabled = false;
      this.inputEl.focus();
    }
  }

  isBlocked(blockedUsers) {
    const blocked = blockedUsers.includes(this.sessionId);
    this.bannerEl.classList.toggle("hidden", !blocked);
    if (blocked) this.bannerEl.textContent = "This session has been blocked by AIJudge.";
  }
}

const panelA = new ChatPanel("panel-a-slot", "a", "Session A");
const panelB = new ChatPanel("panel-b-slot", "b", "Session B");
const panels = [panelA, panelB];

function sessionLabel(userId) {
  const known = knownSessions[userId];
  if (known) return { text: known.label, color: ACCENTS[known.accent] };
  return { text: (userId || "unknown").slice(0, 8), color: "var(--muted)" };
}

function renderStatus(status) {
  for (const panel of panels) panel.isBlocked(status.blocked_users);

  blockedListEl.innerHTML = "";
  if (status.blocked_users.length === 0) {
    blockedListEl.innerHTML = '<li class="empty">None yet</li>';
  } else {
    for (const userId of status.blocked_users) {
      const { text, color } = sessionLabel(userId);
      const li = document.createElement("li");
      li.innerHTML = `<span class="session-swatch" style="background:${color}"></span>${text}`;
      li.title = userId;
      blockedListEl.appendChild(li);
    }
  }

  verdictListEl.innerHTML = "";
  if (status.recent_verdicts.length === 0) {
    verdictListEl.innerHTML = '<li class="empty">None yet</li>';
  } else {
    for (const v of status.recent_verdicts) {
      const { text, color } = sessionLabel(v.user_id);
      const li = document.createElement("li");
      li.className = "verdict-item";
      const tagClass = ["safe", "suspicious", "bad"].includes(v.verdict) ? v.verdict : "suspicious";
      li.innerHTML = `
        <span class="tag ${tagClass}">${v.verdict || "unknown"}</span>
        <div class="reason">${v.reason || ""}</div>
        <div class="user"><span class="session-swatch" style="background:${color}"></span>${text} &middot; ${new Date(v.timestamp).toLocaleTimeString()}</div>
      `;
      verdictListEl.appendChild(li);
    }
  }
}

async function pollStatus() {
  try {
    const resp = await fetch("/api/status");
    const status = await resp.json();
    renderStatus(status);
  } catch (err) {
    // Backend not reachable yet; ignore and retry on next tick.
  }
}

pollStatus();
setInterval(pollStatus, 3000);
