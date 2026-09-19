const kpiRowEl = document.getElementById("kpi-row");
const stackbarEl = document.getElementById("stackbar");
const stackbarLegendEl = document.getElementById("stackbar-legend");
const rulesListEl = document.getElementById("rules-list");
const rawSuspiciousEl = document.getElementById("raw-suspicious");
const rawNinoFormatEl = document.getElementById("raw-nino-format");
const rawNinoIntentEl = document.getElementById("raw-nino-intent");
const blockedListEl = document.getElementById("blocked-list");
const verdictListEl = document.getElementById("verdict-list");
const resetBlocklistBtn = document.getElementById("reset-blocklist-btn");

let rulesRendered = false;
let lastBlockedCount = 0;

function fmtNumber(n) {
  return new Intl.NumberFormat().format(n || 0);
}

function renderKpis(data) {
  const t = data.totals;
  const tiles = [
    { label: "Total Requests", value: fmtNumber(t.total_requests) },
    {
      label: `Requests / sec (last ${data.window_seconds}s)`,
      value: data.requests_per_second.toFixed(3),
      sub: `${fmtNumber(data.requests_last_window)} in the window`,
    },
    { label: "Unique Sessions", value: fmtNumber(t.unique_sessions) },
    {
      label: "Blocked Sessions",
      value: fmtNumber(t.blocked_sessions),
      status: t.blocked_sessions > 0 ? "bad" : "safe",
    },
    {
      label: "Total Tokens",
      value: fmtNumber(t.total_tokens),
      sub: `${fmtNumber(t.total_prompt_tokens)} prompt / ${fmtNumber(t.total_completion_tokens)} completion`,
    },
    {
      label: "Judge Overhead Tokens",
      value: fmtNumber(t.judge_overhead_tokens),
      sub: "spent on LLM-as-judge calls",
    },
  ];

  kpiRowEl.innerHTML = tiles
    .map(
      (tile) => `
      <div class="kpi-tile${tile.status ? ` status-${tile.status}` : ""}">
        <div class="kpi-label">${tile.label}</div>
        <div class="kpi-value">${tile.value}</div>
        ${tile.sub ? `<div class="kpi-sub">${tile.sub}</div>` : ""}
      </div>
    `
    )
    .join("");
}

function renderStackbar(data) {
  const counts = data.totals.verdict_counts;
  const total = (counts.safe || 0) + (counts.suspicious || 0) + (counts.bad || 0);

  if (total === 0) {
    stackbarEl.innerHTML = "";
    stackbarLegendEl.innerHTML = '<li class="empty">No requests judged yet</li>';
    return;
  }

  const order = ["safe", "suspicious", "bad"];
  stackbarEl.innerHTML = order
    .map((k) => {
      const pct = ((counts[k] || 0) / total) * 100;
      return pct > 0 ? `<div class="seg ${k}" style="width:${pct}%" title="${k}: ${counts[k]}"></div>` : "";
    })
    .join("");

  stackbarLegendEl.innerHTML = order
    .map((k) => {
      const pct = (((counts[k] || 0) / total) * 100).toFixed(1);
      return `<li><span class="swatch ${k}"></span>${k} <span class="count">${counts[k] || 0} (${pct}%)</span></li>`;
    })
    .join("");
}

function renderRulesOnce(data) {
  if (rulesRendered) return;
  rulesRendered = true;

  rulesListEl.innerHTML = data.rules
    .map(
      (r) => `
      <li>
        <div class="rule-head">
          <span class="rule-name">${r.name}</span>
          <span class="rule-mode">${r.mode}</span>
        </div>
        <div class="rule-desc">${r.description}</div>
      </li>
    `
    )
    .join("");

  rawSuspiciousEl.innerHTML = data.suspicious_patterns.map((p) => `<li>${p}</li>`).join("");
  rawNinoFormatEl.textContent = data.nino_format_pattern;
  rawNinoIntentEl.textContent = data.nino_intent_pattern;
}

function renderBlocked(data) {
  lastBlockedCount = data.blocked_users.length;
  resetBlocklistBtn.disabled = lastBlockedCount === 0;

  if (lastBlockedCount === 0) {
    blockedListEl.innerHTML = '<li class="empty">None yet</li>';
    return;
  }
  blockedListEl.innerHTML = data.blocked_users
    .map((id) => `<li title="${id}">${id.slice(0, 8)}</li>`)
    .join("");
}

resetBlocklistBtn.addEventListener("click", async () => {
  const count = lastBlockedCount;
  const noun = count === 1 ? "session" : "sessions";
  if (!confirm(`Unblock all ${count} currently blocked ${noun}? They'll be able to send messages again immediately.`)) {
    return;
  }
  resetBlocklistBtn.disabled = true;
  try {
    await fetch("/api/blocklist/reset", { method: "POST" });
  } catch (err) {
    alert(`Failed to reset blocklist: ${err.message}`);
  }
  pollStats();
});

function renderVerdicts(data) {
  if (data.recent_verdicts.length === 0) {
    verdictListEl.innerHTML = '<li class="empty">None yet</li>';
    return;
  }
  verdictListEl.innerHTML = data.recent_verdicts
    .map((v) => {
      const tagClass = ["safe", "suspicious", "bad"].includes(v.verdict) ? v.verdict : "suspicious";
      const time = v.timestamp ? new Date(v.timestamp).toLocaleTimeString() : "";
      return `
        <li class="verdict-item">
          <span class="tag ${tagClass}">${v.verdict || "unknown"}</span>
          <div class="reason">${v.reason || ""}</div>
          <div class="meta">${(v.user_id || "unknown").slice(0, 8)} &middot; ${time}</div>
        </li>
      `;
    })
    .join("");
}

async function pollStats() {
  try {
    const resp = await fetch("/api/judge-stats");
    const data = await resp.json();
    renderKpis(data);
    renderStackbar(data);
    renderRulesOnce(data);
    renderBlocked(data);
    renderVerdicts(data);
  } catch (err) {
    // Backend not reachable yet; ignore and retry on next tick.
  }
}

pollStats();
setInterval(pollStats, 3000);
