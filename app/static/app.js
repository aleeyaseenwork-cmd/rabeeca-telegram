// ---------- icons (dark mode toggle swaps these two) ----------
const ICON_SUN = '<svg viewBox="0 0 24 24"><path d="M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10zm0-5a1 1 0 0 1 1 1v2a1 1 0 1 1-2 0V3a1 1 0 0 1 1-1zm0 18a1 1 0 0 1 1 1v2a1 1 0 1 1-2 0v-2a1 1 0 0 1 1-1zM4 11a1 1 0 0 1 0 2H2a1 1 0 1 1 0-2h2zm18 0a1 1 0 0 1 0 2h-2a1 1 0 1 1 0-2h2zM5.6 4.2a1 1 0 0 1 1.4 0l1.4 1.4a1 1 0 1 1-1.4 1.4L5.6 5.6a1 1 0 0 1 0-1.4zm11 11a1 1 0 0 1 1.4 0l1.4 1.4a1 1 0 0 1-1.4 1.4l-1.4-1.4a1 1 0 0 1 0-1.4zm1.4-12.4a1 1 0 0 1 0 1.4l-1.4 1.4a1 1 0 1 1-1.4-1.4l1.4-1.4a1 1 0 0 1 1.4 0zm-11 11a1 1 0 0 1 0 1.4L5.6 18.4A1 1 0 1 1 4.2 17l1.4-1.4a1 1 0 0 1 1.4 0z"/></svg>';
const ICON_MOON = '<svg viewBox="0 0 24 24"><path d="M12.7 2a9.5 9.5 0 1 0 9.3 11.4 8 8 0 0 1-9.3-11.4z"/></svg>';

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('theme', theme);
  document.getElementById('darkModeBtn').innerHTML = theme === 'dark' ? ICON_SUN : ICON_MOON;
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
let allPanels = [];

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

// ---------- refresh (re-fetch, no page reload) ----------
document.getElementById("refreshBtn").addEventListener("click", () => {
  loadStats();
  loadSubscribers();
});

// ---------- subscribers table ----------
async function loadSubscribers() {
  const search = document.getElementById("searchInput").value;
  const status = document.getElementById("statusFilter").value;
  const payStatus = document.getElementById("payStatusFilter").value;
  const duration = document.getElementById("durationFilter").value;
  const pageSize = document.getElementById("pageSizeSelect").value;

  const params = new URLSearchParams({
    search, status, pay_status: payStatus,
    page: currentPage, page_size: pageSize,
  });
  const res = await fetch("/api/subscribers?" + params.toString());
  const data = await res.json();

  let rows = data.rows;
  if (duration) rows = rows.filter(r => r.duration_type === duration);
  rows = sortRows(rows, document.getElementById("sortSelect").value);

  renderSubsTable(rows);
  renderPagination(data.total, data.page, data.page_size);
  syncParentAutoRenewCheckbox(rows);
}

function sortRows(rows, mode) {
  const copy = [...rows];
  switch (mode) {
    case "oldest":
      return copy.sort((a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0));
    case "purchases_high":
      return copy.sort((a, b) => (b.purchase_count || 0) - (a.purchase_count || 0));
    case "purchases_low":
      return copy.sort((a, b) => (a.purchase_count || 0) - (b.purchase_count || 0));
    case "link_not_sent":
      return copy.sort((a, b) => (a.link_sent ? 1 : 0) - (b.link_sent ? 1 : 0));
    case "expiring_soon":
      return copy.sort((a, b) => new Date(a.expiry_date || "9999-12-31") - new Date(b.expiry_date || "9999-12-31"));
    case "newest":
    default:
      return copy.sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));
  }
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
      <td><span class="badge ${r.duration_type}">${escapeHtml(r.duration_type || "-")}${r.duration_type === "custom" && r.custom_days ? " (" + r.custom_days + "d)" : ""}</span></td>
      <td>${currencySymbol}${(r.price || 0).toFixed(2)}</td>
      <td><span class="badge ${r.status}">${r.status}</span></td>
      <td>
        <input type="checkbox" class="auto-renew-checkbox" ${r.auto_renew ? "checked" : ""} onchange="toggleAutoRenew('${r.id}', this.checked)">
      </td>
      <td class="mono">${r.purchase_count || 0}</td>
      <td>${r.link_sent ? '<span class="link-yes">Yes</span>' : '<span class="link-no">No</span>'}</td>
      <td>${r.last_payment_status ? `<span class="badge ${r.last_payment_status}">${r.last_payment_status}</span>` : "-"}</td>
      <td>${r.expiry_date ? new Date(r.expiry_date).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "-"}</td>
      <td>
        <div class="row-actions">
          <button title="View" onclick="viewSubscriber('${r.id}')">${ICON_EYE}</button>
          <button title="Edit" onclick="openEditModal('${r.id}')">${ICON_PENCIL}</button>
          <button title="More" onclick="toggleRowMenu(event, '${r.id}')">${ICON_DOTS}</button>
          <div class="row-menu hidden" id="rowMenu-${r.id}">
            <button class="danger" onclick="deleteSubscriber('${r.id}')">Delete</button>
          </div>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

const ICON_EYE = '<svg viewBox="0 0 24 24" width="16" height="16"><path d="M12 5c-5 0-9.3 3.1-11 7 1.7 3.9 6 7 11 7s9.3-3.1 11-7c-1.7-3.9-6-7-11-7zm0 12a5 5 0 1 1 0-10 5 5 0 0 1 0 10zm0-8a3 3 0 1 0 0 6 3 3 0 0 0 0-6z"/></svg>';
const ICON_PENCIL = '<svg viewBox="0 0 24 24" width="16" height="16"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg>';
const ICON_DOTS = '<svg viewBox="0 0 24 24" width="16" height="16"><path d="M12 8a2 2 0 1 0 0-4 2 2 0 0 0 0 4zm0 6a2 2 0 1 0 0-4 2 2 0 0 0 0 4zm0 6a2 2 0 1 0 0-4 2 2 0 0 0 0 4z"/></svg>';

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

async function toggleAutoRenew(id, checked) {
  await fetch(`/api/subscribers/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ auto_renew: checked ? 1 : 0 }),
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

document.getElementById("searchInput").addEventListener("input", debounce(() => { currentPage = 1; loadSubscribers(); }, 350));
document.getElementById("statusFilter").addEventListener("change", () => { currentPage = 1; loadSubscribers(); });
document.getElementById("payStatusFilter").addEventListener("change", () => { currentPage = 1; loadSubscribers(); });
document.getElementById("durationFilter").addEventListener("change", () => { currentPage = 1; loadSubscribers(); });
document.getElementById("sortSelect").addEventListener("change", () => loadSubscribers());
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

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ---------- edit subscriber modal ----------
let editingSubId = null;
const DURATION_DAYS = { weekly: 7, monthly: 30, onetime: null }; // onetime = lifetime, no days

function populatePlanOptions(selectedPlanValue) {
  const sel = document.getElementById("editPlanSelect");
  sel.innerHTML = "";
  const seen = new Set();
  allPanels.forEach(p => {
    p.buttons.forEach(b => {
      const key = b.text + "|" + b.duration_type + "|" + b.price;
      if (seen.has(key)) return;
      seen.add(key);
      const opt = document.createElement("option");
      opt.value = key;
      opt.textContent = `${b.text} (${b.duration_type}, ${currencySymbol}${b.price})`;
      opt.dataset.text = b.text;
      opt.dataset.duration = b.duration_type;
      opt.dataset.price = b.price;
      sel.appendChild(opt);
    });
  });
  const customOpt = document.createElement("option");
  customOpt.value = "__custom__";
  customOpt.textContent = "Custom plan...";
  sel.appendChild(customOpt);
  if (selectedPlanValue) sel.value = selectedPlanValue;
}

document.getElementById("editPlanSelect").addEventListener("change", (e) => {
  const isCustom = e.target.value === "__custom__";
  document.getElementById("editCustomPlanFields").classList.toggle("hidden", !isCustom);
  if (!isCustom) {
    const opt = e.target.selectedOptions[0];
    document.getElementById("editPrice").value = opt.dataset.price;
    document.getElementById("editDurationSelect").value = opt.dataset.duration;
    recalcExpiryFromDuration();
  }
});

function recalcExpiryFromDuration() {
  const duration = document.getElementById("editDurationSelect").value;
  const customDaysField = document.getElementById("editCustomDaysField");
  customDaysField.classList.toggle("hidden", duration !== "custom");

  if (duration === "onetime") {
    document.getElementById("editExpiryDate").value = "2126-01-01";
    return;
  }
  let days = DURATION_DAYS[duration];
  if (duration === "custom") {
    days = parseInt(document.getElementById("editCustomDays").value, 10) || 1;
  }
  const d = new Date();
  d.setDate(d.getDate() + days);
  document.getElementById("editExpiryDate").value = d.toISOString().slice(0, 10);
}
document.getElementById("editDurationSelect").addEventListener("change", recalcExpiryFromDuration);
document.getElementById("editCustomDays").addEventListener("input", recalcExpiryFromDuration);

document.getElementById("editStatusSelect").addEventListener("change", (e) => {
  document.getElementById("editKickField").classList.toggle("hidden", e.target.value !== "expired");
});

async function openEditModal(id) {
  const r = allSubscriberRows.find(x => x.id === id);
  if (!r) return;
  editingSubId = id;

  document.getElementById("editUserId").textContent = r.telegram_user_id;
  document.getElementById("editUsername").textContent = r.telegram_username ? "@" + r.telegram_username : "-";

  if (allPanels.length === 0) {
    const res = await fetch("/api/panels");
    allPanels = await res.json();
  }
  const matchKey = `${r.button_text}|${r.duration_type}|${r.price}`;
  populatePlanOptions(matchKey);
  const isKnownPlan = Array.from(document.getElementById("editPlanSelect").options).some(o => o.value === matchKey);
  if (!isKnownPlan) {
    document.getElementById("editPlanSelect").value = "__custom__";
    document.getElementById("editCustomPlanFields").classList.remove("hidden");
    document.getElementById("editCustomName").value = r.button_text || "";
  } else {
    document.getElementById("editCustomPlanFields").classList.add("hidden");
  }

  document.getElementById("editPrice").value = r.price || 0;
  document.getElementById("editDurationSelect").value = r.duration_type || "monthly";
  document.getElementById("editCustomDays").value = r.custom_days || 1;
  document.getElementById("editCustomDaysField").classList.toggle("hidden", r.duration_type !== "custom");
  document.getElementById("editExpiryDate").value = r.expiry_date ? r.expiry_date.slice(0, 10) : "";
  document.getElementById("editStatusSelect").value = r.status;
  document.getElementById("editKickField").classList.toggle("hidden", r.status !== "expired");
  document.getElementById("editKickNow").checked = false;
  document.getElementById("editAutoRenew").checked = !!r.auto_renew;
  document.getElementById("editPurchaseCount").value = r.purchase_count || 0;
  document.getElementById("editLinkSent").checked = !!r.link_sent;
  document.getElementById("editLastPayment").textContent = r.last_payment_status || "-";

  document.getElementById("editModal").classList.remove("hidden");
}

document.getElementById("editCancelBtn").addEventListener("click", () => {
  document.getElementById("editModal").classList.add("hidden");
});

document.getElementById("editSaveBtn").addEventListener("click", async () => {
  const planSel = document.getElementById("editPlanSelect");
  const isCustomPlan = planSel.value === "__custom__";
  const button_text = isCustomPlan
    ? (document.getElementById("editCustomName").value.trim() || "Custom plan")
    : planSel.selectedOptions[0].dataset.text;

  const duration_type = document.getElementById("editDurationSelect").value;
  const custom_days = duration_type === "custom" ? (parseInt(document.getElementById("editCustomDays").value, 10) || 1) : null;

  const payload = {
    button_text,
    duration_type,
    custom_days,
    price: parseFloat(document.getElementById("editPrice").value) || 0,
    expiry_date: new Date(document.getElementById("editExpiryDate").value).toISOString(),
    status: document.getElementById("editStatusSelect").value,
    kick_now: document.getElementById("editKickNow").checked,
    auto_renew: document.getElementById("editAutoRenew").checked ? 1 : 0,
    purchase_count: parseInt(document.getElementById("editPurchaseCount").value, 10) || 0,
    link_sent: document.getElementById("editLinkSent").checked ? 1 : 0,
  };

  await fetch(`/api/subscribers/${editingSubId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  document.getElementById("editModal").classList.add("hidden");
  loadSubscribers();
  loadStats();
});

// ---------- import modal ----------
let importFile = null;

document.getElementById("importBtnHeader").addEventListener("click", () => {
  importFile = null;
  document.getElementById("importFileName").textContent = "";
  document.getElementById("importConfirmBtn").disabled = true;
  document.getElementById("importModal").classList.remove("hidden");
});
document.getElementById("importCancelBtn").addEventListener("click", () => {
  document.getElementById("importModal").classList.add("hidden");
});

const dropZone = document.getElementById("dropZone");
const importFileInput = document.getElementById("importFileInput");

dropZone.addEventListener("click", () => importFileInput.click());
dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("dragover"); });
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("dragover");
  if (e.dataTransfer.files.length) setImportFile(e.dataTransfer.files[0]);
});
importFileInput.addEventListener("change", (e) => {
  if (e.target.files.length) setImportFile(e.target.files[0]);
});

function setImportFile(file) {
  importFile = file;
  document.getElementById("importFileName").textContent = "Selected: " + file.name;
  document.getElementById("importConfirmBtn").disabled = false;
}

document.getElementById("importConfirmBtn").addEventListener("click", async () => {
  if (!importFile) return;
  const formData = new FormData();
  formData.append("file", importFile);
  const res = await fetch("/api/subscribers/import.csv", { method: "POST", body: formData });
  const data = await res.json();
  document.getElementById("importModal").classList.add("hidden");
  if (res.ok) {
    alert(`Imported ${data.imported} subscriber(s)${data.skipped ? `, skipped ${data.skipped}` : ""}.`);
    loadSubscribers();
    loadStats();
  } else {
    alert("Import failed: " + (data.error || "unknown error"));
  }
});

// ---------- plans / panels ----------
async function loadPanels() {
  const res = await fetch("/api/panels");
  const panels = await res.json();
  allPanels = panels;
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
