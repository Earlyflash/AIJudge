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
    this.tokenTotalEl = this.root.querySelector(".token-total");
    this.tokenSplitEl = this.root.querySelector(".token-split");
    this.fastLatencyEl = this.root.querySelector(".fast-latency");
    this.fastScoreEl = this.root.querySelector(".fast-score");
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
    this.tokens = { prompt: 0, completion: 0, total: 0 };
    this.renderTokens();
    this.renderFast(null, null);
    this.sessionIdEl.textContent = `id: ${this.sessionId.slice(0, 8)}`;
    this.sessionIdEl.title = this.sessionId;
    this.messagesEl.innerHTML = "";
    this.bannerEl.classList.add("hidden");
    this.setPipeline("idle", "Idle");
  }

  renderTokens() {
    const f = (n) => new Intl.NumberFormat().format(n);
    this.tokenTotalEl.textContent = `Tokens: ${f(this.tokens.total)}`;
    this.tokenSplitEl.textContent = `${f(this.tokens.prompt)} in / ${f(this.tokens.completion)} out`;
  }

  // summary: this session's entry from /api/status (or null before its first request)
  renderFast(summary, threshold) {
    if (!summary || !summary.fast_checks) {
      this.fastLatencyEl.textContent = "Fast rules: -- ms";
      this.fastScoreEl.textContent = `Suspicion: 0/${threshold ?? 5}`;
      return;
    }
    this.fastLatencyEl.textContent =
      `Fast rules: ${fmtMs(summary.fast_latency_last_ms)} last · ${fmtMs(summary.fast_latency_avg_ms)} avg · ${fmtMs(summary.fast_latency_max_ms)} max (${summary.fast_checks})`;
    this.fastScoreEl.textContent = `Suspicion: ${summary.score}/${threshold ?? 5}`;
    this.fastScoreEl.classList.toggle("hot", threshold != null && summary.score >= threshold);
  }

  addMessage(role, text, usage, fastMs) {
    const div = document.createElement("div");
    div.className = `msg ${role}`;
    div.textContent = text;
    if (usage || fastMs != null) {
      const meta = document.createElement("div");
      meta.className = "msg-tokens";
      const parts = [];
      if (usage) parts.push(`${usage.prompt_tokens} in / ${usage.completion_tokens} out · ${usage.total_tokens} tokens`);
      if (fastMs != null) parts.push(`fast rules ${fmtMs(fastMs)}`);
      meta.textContent = parts.join(" · ");
      div.appendChild(meta);
    }
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
        this.addMessage("error", data.error || `Request failed (${resp.status})`, null, data.fast_check_ms);
        this.bannerEl.classList.toggle("hidden", !blocked);
        if (blocked) this.bannerEl.textContent = "This session has been blocked by AIJudge.";
      } else {
        this.setPipeline("success", `Round trip: ${elapsed}ms`);
        const usage = data.usage;
        if (usage) {
          this.tokens.prompt += usage.prompt_tokens;
          this.tokens.completion += usage.completion_tokens;
          this.tokens.total += usage.total_tokens;
          this.renderTokens();
        }
        this.addMessage("assistant", data.reply, usage, data.fast_check_ms);
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

// Fast-rule checks take well under a millisecond, so show more precision
// than a whole-ms round trip would.
function fmtMs(ms) {
  if (ms == null) return "--";
  return ms < 1 ? `${ms.toFixed(3)} ms` : ms < 100 ? `${ms.toFixed(2)} ms` : `${Math.round(ms)} ms`;
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
  for (const panel of panels) {
    panel.isBlocked(status.blocked_users);
    panel.renderFast((status.sessions || {})[panel.sessionId] || null, status.slow_review_threshold);
  }

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
        <div class="user">chat ${v.chat_tokens ?? 0} tok &middot; judge ${v.judge_tokens ?? 0} tok${v.fast_latency_ms != null ? ` &middot; fast ${fmtMs(v.fast_latency_ms)}` : ""}</div>
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
