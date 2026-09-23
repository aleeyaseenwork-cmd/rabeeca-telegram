// ---------- tab switching ----------
document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "subscribers") loadSubscribers();
    if (btn.dataset.tab === "plans") loadPanels();
  });
});

let currentPage = 1;
let currencySymbol = "$";

// ---------- overview ----------
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
}

function renderSubsTable(rows) {
  const tbody = document.getElementById("subsTableBody");
  tbody.innerHTML = "";
  if (rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;color:var(--text-muted)">No subscribers found</td></tr>`;
    return;
  }
  rows.forEach(r => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${r.telegram_username ? "@" + escapeHtml(r.telegram_username) : r.telegram_user_id}</td>
      <td>${escapeHtml(r.button_text || "-")}</td>
      <td>${escapeHtml(r.duration_type || "-")}</td>
      <td>${currencySymbol}${(r.price || 0).toFixed(2)}</td>
      <td><span class="badge ${r.status}">${r.status}</span></td>
      <td>
        <input type="checkbox" ${r.auto_renew ? "checked" : ""} onchange="toggleAutoRenew('${r.id}', this.checked)">
      </td>
      <td>${r.last_payment_status ? `<span class="badge ${r.last_payment_status}">${r.last_payment_status}</span>` : "-"}</td>
      <td>${r.expiry_date ? new Date(r.expiry_date).toLocaleDateString() : "-"}</td>
      <td>
        <select onchange="updateSubStatus('${r.id}', this.value)">
          <option value="active" ${r.status === "active" ? "selected" : ""}>Active</option>
          <option value="expired" ${r.status === "expired" ? "selected" : ""}>Expired</option>
          <option value="cancelled" ${r.status === "cancelled" ? "selected" : ""}>Cancelled</option>
        </select>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

async function toggleAutoRenew(id, checked) {
  await fetch(`/api/subscribers/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ auto_renew: checked ? 1 : 0 }),
  });
}

async function updateSubStatus(id, status) {
  await fetch(`/api/subscribers/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  loadSubscribers();
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
document.getElementById("downloadCsvBtn").addEventListener("click", () => {
  const search = document.getElementById("searchInput").value;
  const status = document.getElementById("statusFilter").value;
  const payStatus = document.getElementById("payStatusFilter").value;
  const params = new URLSearchParams({ search, status, pay_status: payStatus });
  window.location.href = "/api/subscribers/export.csv?" + params.toString();
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

function openPanelModal() {
  document.getElementById("panelModal").classList.remove("hidden");
}
function closePanelModal() {
  document.getElementById("panelModal").classList.add("hidden");
}

document.getElementById("newPanelBtn").addEventListener("click", () => {
  editingPanelId = null;
  document.getElementById("panelModalTitle").textContent = "New Panel";
  document.getElementById("panelName").value = "New Panel";
  document.getElementById("panelMessage").value = "Choose a plan below to get access.";
  renderButtonsEditor([
    { text: "1 Month", price: 25, duration_type: "monthly" },
  ]);
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
  document.getElementById("settingProvider").value = s.active_provider || "stripe";
  document.getElementById("settingReminderDays").value = s.reminder_days_before || 3;
  document.getElementById("settingAutoKick").checked = s.auto_kick_on_expiry === "1";
  document.getElementById("settingAdminIds").value = (s.admin_ids || []).join("\n");
  document.getElementById("settingsModal").classList.remove("hidden");
});
document.getElementById("settingsCancelBtn").addEventListener("click", () => {
  document.getElementById("settingsModal").classList.add("hidden");
});
document.getElementById("settingsSaveBtn").addEventListener("click", async () => {
  const admin_ids = document.getElementById("settingAdminIds").value
    .split("\n").map(s => s.trim()).filter(Boolean);
  await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      dashboard_title: document.getElementById("settingTitle").value,
      currency_symbol: document.getElementById("settingCurrency").value,
      active_provider: document.getElementById("settingProvider").value,
      reminder_days_before: document.getElementById("settingReminderDays").value,
      auto_kick_on_expiry: document.getElementById("settingAutoKick").checked ? "1" : "0",
      admin_ids,
    }),
  });
  document.getElementById("dashTitle").textContent = document.getElementById("settingTitle").value;
  document.getElementById("settingsModal").classList.add("hidden");
  loadStats();
});

// ---------- init ----------
loadStats();
