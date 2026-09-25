// ---------- dark mode ----------
function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('theme', theme);
  document.getElementById('darkModeBtn').textContent = theme === 'dark' ? '☀️' : '🌙';
}
document.getElementById('darkModeBtn').addEventListener('click', () => {
  const current = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  applyTheme(current === 'dark' ? 'light' : 'dark');
});
applyTheme(document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light');

// ---------- tab switching ----------
document.querySelectorAll(".segmented-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".segmented-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "subscribers") loadSubscribers();
    if (btn.dataset.tab === "plans") loadPanels();
  });
});

let currentPage = 1;
let currencySymbol = "$";

// ---------- overview stats ----------
async function loadStats() {
  const res = await fetch("/api/stats");
  const data = await res.json();
  currencySymbol = data.currency_symbol || "$";
  document.getElementById("statTotalRevenue").textContent = currencySymbol + data.total_revenue.toFixed(2);
  document.getElementById("statMonthRevenue").textContent = currencySymbol + data.month_revenue.toFixed(2);
  document.getElementById("statActiveSubs").textContent = data.active_subs;
  document.getElementById("statExpiringSoon").textContent = data.expiring_soon;
}

// ---------- subscribers table ----------
async function loadSubscribers() {
  const search = document.getElementById("searchInput").value;
  const status = document.getElementById("statusFilter").value;
  const payStatus = document.getElementById("payStatusFilter").value;
  const pageSize = document.getElementById("pageSizeSelect").value;

  const params = new URLSearchParams({
    search, status, pay_status: payStatus,
    page: currentPage, page_size: pageSize,
  });
  const res = await fetch("/api/subscribers?" + params.toString());
  const data = await res.json();
  renderSubsTable(data.rows);
  renderPagination(data.total, data.page, data.page_size);
  syncParentAutoRenewCheckbox(data.rows);
}

let allSubscriberRows = [];

function renderSubsTable(rows) {
  allSubscriberRows = rows;
  const tbody = document.getElementById("subsTableBody");
  tbody.innerHTML = "";
  if (rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="11" style="text-align:center;color:var(--text-muted)">No subscribers found</td></tr>`;
    return;
  }
  rows.forEach(r => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="mono">${escapeHtml(r.telegram_user_id)}</td>
      <td>${r.telegram_username ? "@" + escapeHtml(r.telegram_username) : "-"}</td>
      <td>${escapeHtml(r.button_text || "-")}</td>
      <td><span class="badge ${r.duration_type}">${escapeHtml(r.duration_type || "-")}</span></td>
      <td>${currencySymbol}${(r.price || 0).toFixed(2)}</td>
      <td><span class="badge ${r.status}">${r.status}</span></td>
      <td>
        <input type="checkbox" class="auto-renew-checkbox" ${r.auto_renew ? "checked" : ""} onchange="toggleAutoRenew('${r.id}', this.checked)">
      </td>
      <td class="mono">${r.purchase_count || 0}</td>
      <td>${r.link_sent ? '<span class="link-yes">✓ Yes</span>' : '<span class="link-no">✕ No</span>'}</td>
      <td>${r.last_payment_status ? `<span class="badge ${r.last_payment_status}">${r.last_payment_status}</span>` : "-"}</td>
      <td>${r.expiry_date ? new Date(r.expiry_date).toLocaleDateString() : "-"}</td>
      <td>
        <div class="row-actions">
          <button title="View" onclick="viewSubscriber('${r.id}')">👁</button>
          <button title="Edit status" onclick="cycleStatus('${r.id}', '${r.status}')">✏️</button>
          <button title="More" onclick="toggleRowMenu(event, '${r.id}')">⋮</button>
          <div class="row-menu hidden" id="rowMenu-${r.id}">
            <button class="danger" onclick="deleteSubscriber('${r.id}')">🗑 Delete</button>
          </div>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

document.addEventListener("click", (e) => {
  if (!e.target.closest(".row-actions")) {
    document.querySelectorAll(".row-menu").forEach(m => m.classList.add("hidden"));
  }
});

function toggleRowMenu(e, id) {
  e.stopPropagation();
  const menu = document.getElementById("rowMenu-" + id);
  document.querySelectorAll(".row-menu").forEach(m => { if (m !== menu) m.classList.add("hidden"); });
  menu.classList.toggle("hidden");
}

function viewSubscriber(id) {
  const r = allSubscriberRows.find(x => x.id === id);
  if (!r) return;
  const rows = [
    ["User ID", r.telegram_user_id],
    ["Username", r.telegram_username ? "@" + r.telegram_username : "-"],
    ["Plan", r.button_text || "-"],
    ["Duration", r.duration_type || "-"],
    ["Price", currencySymbol + (r.price || 0).toFixed(2)],
    ["Status", r.status],
    ["Auto-renew", r.auto_renew ? "Yes" : "No"],
    ["Purchases", r.purchase_count || 0],
    ["Invite link sent", r.link_sent ? "Yes" : "No"],
    ["Last payment", r.last_payment_status || "-"],
    ["Start date", r.start_date ? new Date(r.start_date).toLocaleString() : "-"],
    ["Expiry", r.expiry_date ? new Date(r.expiry_date).toLocaleString() : "-"],
    ["Provider", r.provider || "-"],
  ];
  document.getElementById("viewModalBody").innerHTML = rows.map(
    ([k, v]) => `<div class="vd-row"><span>${k}</span><strong>${escapeHtml(String(v))}</strong></div>`
  ).join("");
  document.getElementById("viewModal").classList.remove("hidden");
}
document.getElementById("viewCloseBtn").addEventListener("click", () => {
  document.getElementById("viewModal").classList.add("hidden");
});

const STATUS_CYCLE = { active: "expired", expired: "cancelled", cancelled: "active" };
async function cycleStatus(id, current) {
  const next = STATUS_CYCLE[current] || "active";
  await updateSubStatus(id, next);
}

async function toggleAutoRenew(id, checked) {
  await fetch(`/api/subscribers/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ auto_renew: checked ? 1 : 0 }),
  });
  loadSubscribers();
}

async function updateSubStatus(id, status) {
  await fetch(`/api/subscribers/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  loadSubscribers();
}

async function deleteSubscriber(id) {
  if (!confirm("Delete this subscriber? This can't be undone.")) return;
  await fetch(`/api/subscribers/${id}`, { method: "DELETE" });
  loadSubscribers();
  loadStats();
}

// parent checkbox: tick/untick auto-renew for everyone currently loaded
document.getElementById("autoRenewAllCheckbox").addEventListener("change", async (e) => {
  const value = e.target.checked;
  await fetch("/api/subscribers/auto-renew-all", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  });
  loadSubscribers();
});

function syncParentAutoRenewCheckbox(rows) {
  const parent = document.getElementById("autoRenewAllCheckbox");
  if (rows.length === 0) { parent.checked = false; parent.indeterminate = false; return; }
  const allOn = rows.every(r => r.auto_renew);
  const allOff = rows.every(r => !r.auto_renew);
  parent.checked = allOn;
  parent.indeterminate = !allOn && !allOff;
}

function renderPagination(total, page, pageSize) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const el = document.getElementById("pagination");
  el.innerHTML = "";
  for (let i = 1; i <= pages; i++) {
    const b = document.createElement("button");
    b.textContent = i;
    if (i === page) b.classList.add("active");
    b.addEventListener("click", () => { currentPage = i; loadSubscribers(); });
    el.appendChild(b);
  }
}

document.getElementById("searchInput").addEventListener("input", debounce(() => { currentPage = 1; loadSubscribers(); }, 350));
document.getElementById("statusFilter").addEventListener("change", () => { currentPage = 1; loadSubscribers(); });
document.getElementById("payStatusFilter").addEventListener("change", () => { currentPage = 1; loadSubscribers(); });
document.getElementById("pageSizeSelect").addEventListener("change", () => { currentPage = 1; loadSubscribers(); });

function currentExportParams() {
  const search = document.getElementById("searchInput").value;
  const status = document.getElementById("statusFilter").value;
  const payStatus = document.getElementById("payStatusFilter").value;
  return new URLSearchParams({ search, status, pay_status: payStatus });
}
document.getElementById("exportBtnHeader").addEventListener("click", () => {
  window.location.href = "/api/subscribers/export.csv?" + currentExportParams().toString();
});
document.getElementById("importBtnHeader").addEventListener("click", () => {
  document.getElementById("importFileInput").click();
});
document.getElementById("importFileInput").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch("/api/subscribers/import.csv", { method: "POST", body: formData });
  const data = await res.json();
  if (res.ok) {
    alert(`Imported ${data.imported} subscriber(s)${data.skipped ? `, skipped ${data.skipped}` : ""}.`);
    loadSubscribers();
    loadStats();
  } else {
    alert("Import failed: " + (data.error || "unknown error"));
  }
  e.target.value = "";
});

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ---------- plans / panels ----------
async function loadPanels() {
  const res = await fetch("/api/panels");
  const panels = await res.json();
  const list = document.getElementById("panelsList");
  list.innerHTML = "";
  panels.forEach(p => {
    const div = document.createElement("div");
    div.className = "panel-card";
    div.innerHTML = `
      <div class="panel-card-main">
        <h3>${escapeHtml(p.name)}</h3>
        <p>${escapeHtml(p.message_text)}</p>
        <div class="panel-buttons-preview">
          ${p.buttons.map(b => `<span>${escapeHtml(b.text)} (${escapeHtml(b.duration_type)})</span>`).join("")}
        </div>
      </div>
      <div class="panel-card-actions">
        <button class="secondary" onclick='editPanel(${JSON.stringify(p).replace(/'/g, "&apos;")})'>Edit</button>
        <button class="secondary" onclick="deletePanel('${p.id}')">Delete</button>
      </div>
    `;
    list.appendChild(div);
  });
}

async function deletePanel(id) {
  if (!confirm("Delete this panel?")) return;
  await fetch(`/api/panels/${id}`, { method: "DELETE" });
  loadPanels();
}

let editingPanelId = null;

function openPanelModal() { document.getElementById("panelModal").classList.remove("hidden"); }
function closePanelModal() { document.getElementById("panelModal").classList.add("hidden"); }

document.getElementById("newPanelBtn").addEventListener("click", () => {
  editingPanelId = null;
  document.getElementById("panelModalTitle").textContent = "New Panel";
  document.getElementById("panelName").value = "New Panel";
  document.getElementById("panelMessage").value = "Choose a plan below to get access.";
  renderButtonsEditor([{ text: "1 Month", price: 25, duration_type: "monthly" }]);
  openPanelModal();
});

function editPanel(p) {
  editingPanelId = p.id;
  document.getElementById("panelModalTitle").textContent = "Edit Panel";
  document.getElementById("panelName").value = p.name;
  document.getElementById("panelMessage").value = p.message_text;
  renderButtonsEditor(p.buttons);
  openPanelModal();
}

function renderButtonsEditor(buttons) {
  const editor = document.getElementById("buttonsEditor");
  editor.innerHTML = "";
  buttons.forEach(b => addButtonRow(b.text, b.price, b.duration_type));
}

function addButtonRow(text = "", price = 0, duration = "monthly") {
  const row = document.createElement("div");
  row.className = "button-row";
  row.innerHTML = `
    <input type="text" class="btn-text" placeholder="Button text" value="${escapeHtml(text)}">
    <input type="number" class="btn-price" placeholder="Price" value="${price}" step="0.01">
    <select class="btn-duration">
      <option value="weekly" ${duration === "weekly" ? "selected" : ""}>Weekly</option>
      <option value="monthly" ${duration === "monthly" ? "selected" : ""}>Monthly</option>
      <option value="onetime" ${duration === "onetime" ? "selected" : ""}>One-time</option>
    </select>
    <button type="button" class="remove-btn" onclick="this.parentElement.remove()">✕</button>
  `;
  document.getElementById("buttonsEditor").appendChild(row);
}

document.getElementById("addButtonBtn").addEventListener("click", () => addButtonRow());
document.getElementById("panelCancelBtn").addEventListener("click", closePanelModal);

document.getElementById("panelSaveBtn").addEventListener("click", async () => {
  const name = document.getElementById("panelName").value.trim() || "Untitled Panel";
  const message_text = document.getElementById("panelMessage").value.trim();
  const buttons = Array.from(document.querySelectorAll("#buttonsEditor .button-row")).map(row => ({
    text: row.querySelector(".btn-text").value.trim(),
    price: parseFloat(row.querySelector(".btn-price").value) || 0,
    duration_type: row.querySelector(".btn-duration").value,
  }));

  if (editingPanelId) {
    await fetch(`/api/panels/${editingPanelId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, message_text, buttons, active: 1 }),
    });
  } else {
    await fetch("/api/panels", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, message_text, buttons }),
    });
  }
  closePanelModal();
  loadPanels();
});

// ---------- developer view ----------
document.getElementById("devViewBtn").addEventListener("click", () => {
  document.getElementById("devModal").classList.remove("hidden");
  loadDevTable();
});
document.getElementById("devCloseBtn").addEventListener("click", () => {
  document.getElementById("devModal").classList.add("hidden");
});
document.getElementById("devTableSelect").addEventListener("change", loadDevTable);

async function loadDevTable() {
  const table = document.getElementById("devTableSelect").value;
  const res = await fetch(`/api/dev/table/${table}`);
  const data = await res.json();
  document.getElementById("devJson").value = JSON.stringify(data, null, 2);
}

// ---------- settings ----------
document.getElementById("settingsBtn").addEventListener("click", async () => {
  const res = await fetch("/api/settings");
  const s = await res.json();
  document.getElementById("settingTitle").value = s.dashboard_title || "";
  document.getElementById("settingCurrency").value = s.currency_symbol || "$";
  const activeProviders = (s.active_providers || "stripe").split(",").map(p => p.trim());
  document.getElementById("providerStripe").checked = activeProviders.includes("stripe");
  document.getElementById("providerCcbill").checked = activeProviders.includes("ccbill");
  document.getElementById("settingReminderDays").value = s.reminder_days_before || 3;
  document.getElementById("settingAutoKick").checked = s.auto_kick_on_expiry === "1";
  document.getElementById("settingAdminIds").value = (s.admin_ids || []).join("\n");
  document.getElementById("currentPasswordInput").value = "";
  document.getElementById("newPasswordInput").value = "";
  document.getElementById("passwordChangeMsg").textContent = "";
  document.getElementById("settingsModal").classList.remove("hidden");
});
document.getElementById("settingsCancelBtn").addEventListener("click", () => {
  document.getElementById("settingsModal").classList.add("hidden");
});
document.getElementById("settingsSaveBtn").addEventListener("click", async () => {
  const admin_ids = document.getElementById("settingAdminIds").value
    .split("\n").map(s => s.trim()).filter(Boolean);
  const providers = [];
  if (document.getElementById("providerStripe").checked) providers.push("stripe");
  if (document.getElementById("providerCcbill").checked) providers.push("ccbill");
  await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      dashboard_title: document.getElementById("settingTitle").value,
      currency_symbol: document.getElementById("settingCurrency").value,
      active_providers: providers.join(","),
      reminder_days_before: document.getElementById("settingReminderDays").value,
      auto_kick_on_expiry: document.getElementById("settingAutoKick").checked ? "1" : "0",
      admin_ids,
    }),
  });
  document.getElementById("dashTitle").textContent = document.getElementById("settingTitle").value;
  document.getElementById("settingsModal").classList.add("hidden");
  loadStats();
});

document.getElementById("changePasswordBtn").addEventListener("click", async () => {
  const current_password = document.getElementById("currentPasswordInput").value;
  const new_password = document.getElementById("newPasswordInput").value;
  const msg = document.getElementById("passwordChangeMsg");
  const res = await fetch("/api/change-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current_password, new_password }),
  });
  const data = await res.json();
  if (res.ok) {
    msg.style.color = "var(--success)";
    msg.textContent = "Password updated.";
    document.getElementById("currentPasswordInput").value = "";
    document.getElementById("newPasswordInput").value = "";
  } else {
    msg.style.color = "var(--failed)";
    msg.textContent = data.error || "Could not update password.";
  }
});

// ---------- init ----------
function renderCurrentDate() {
  const el = document.getElementById("currentDate");
  if (el) el.textContent = new Date().toLocaleDateString(undefined, { weekday: "short", year: "numeric", month: "short", day: "numeric" });
}
renderCurrentDate();
loadStats();
loadSubscribers();
