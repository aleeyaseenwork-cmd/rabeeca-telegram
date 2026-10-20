// ---------- icons (dark mode toggle swaps these two) ----------
const ICON_SUN = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>';
const ICON_MOON = '<svg viewBox="0 0 24 24"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';

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
    tbody.innerHTML = `<tr><td colspan="13" style="text-align:center;color:var(--text-muted)">No subscribers found</td></tr>`;
    return;
  }
  rows.forEach(r => {
    const tr = document.createElement("tr");
    if (r.banned) tr.className = "row-banned";
    tr.innerHTML = `
      <td class="mono">${escapeHtml(r.telegram_user_id)}</td>
      <td>${r.telegram_username ? "@" + escapeHtml(r.telegram_username) : "-"}</td>
      <td>${escapeHtml(r.button_text || "-")}</td>
      <td><span class="badge ${r.duration_type}">${escapeHtml(r.duration_type || "-")}${r.duration_type === "custom" && r.custom_days ? " (" + r.custom_days + "d)" : ""}</span></td>
      <td>${currencySymbol}${(r.price || 0).toFixed(2)}</td>
      <td><span class="badge ${r.status}">${r.status}</span>${r.banned ? ' <span class="badge banned">Banned</span>' : ""}</td>
      <td>
        <input type="checkbox" class="auto-renew-checkbox" ${r.auto_renew ? "checked" : ""} onchange="toggleAutoRenew('${r.id}', this.checked)">
      </td>
      <td>${r.cancelled ? '<span class="badge cancelled">Yes</span>' : "-"}</td>
      <td class="mono">${r.purchase_count || 0}</td>
      <td>${r.link_sent ? '<span class="link-yes">Yes</span>' : '<span class="link-no">No</span>'}</td>
      <td>${r.last_payment_status ? `<span class="badge ${r.last_payment_status}">${r.last_payment_status}</span>` : "-"}</td>
      <td>${r.expiry_date ? new Date(r.expiry_date).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "-"}</td>
      <td>
        <div class="row-actions">
          <button title="View" onclick="viewSubscriber('${r.id}')">${ICON_EYE}</button>
          <button title="Edit" onclick="openEditModal('${r.id}')">${ICON_PENCIL}</button>
          <button title="More" onclick="openRowMenu('${r.id}')">${ICON_DOTS}</button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

const ICON_EYE = '<svg viewBox="0 0 24 24"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
const ICON_PENCIL = '<svg viewBox="0 0 24 24"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';
const ICON_DOTS = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="1.8" fill="currentColor" stroke="none"/><circle cx="12" cy="5" r="1.8" fill="currentColor" stroke="none"/><circle cx="12" cy="19" r="1.8" fill="currentColor" stroke="none"/></svg>';

// ---------- row actions modal (3-dots) ----------
let rowMenuSubId = null;
const rowMenuModal = document.getElementById("rowMenuModal");

function openRowMenu(id) {
  const r = allSubscriberRows.find(x => x.id === id);
  if (!r) return;
  rowMenuSubId = id;
  document.getElementById("rowMenuWho").textContent =
    String(r.telegram_user_id) + (r.telegram_username ? " · @" + r.telegram_username : "");
  rowMenuModal.classList.remove("hidden");
}

function closeRowMenu() {
  rowMenuModal.classList.add("hidden");
  rowMenuSubId = null;
}

// closes the modal first, then runs the chosen action for that member
function rowMenuAction(fn) {
  const r = allSubscriberRows.find(x => x.id === rowMenuSubId);
  closeRowMenu();
  if (r) fn(r);
}
document.getElementById("rowMenuRemind").addEventListener("click", () => rowMenuAction(r => remindOne(r.id)));
document.getElementById("rowMenuBan").addEventListener("click", () => rowMenuAction(r => banFromRow(r.telegram_user_id)));
document.getElementById("rowMenuDelete").addEventListener("click", () => rowMenuAction(r => deleteSubscriber(r.id)));
document.getElementById("rowMenuCloseBtn").addEventListener("click", closeRowMenu);

// outside click (press AND release on the dark backdrop) and Esc close it too
let rowMenuPressedBackdrop = false;
rowMenuModal.addEventListener("mousedown", (e) => { rowMenuPressedBackdrop = (e.target === rowMenuModal); });
rowMenuModal.addEventListener("click", (e) => {
  if (e.target === rowMenuModal && rowMenuPressedBackdrop) closeRowMenu();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !rowMenuModal.classList.contains("hidden")) closeRowMenu();
});

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
    ["Cancelled", r.cancelled ? "Yes" : "No"],
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
const DURATION_DAYS = { weekly: 7, monthly: 30, quarterly: 90, semiannual: 180, yearly: 365, onetime: null }; // onetime = lifetime, no days

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
          ${p.buttons.map(b => `<span>${escapeHtml(b.text)} · ${currencySymbol}${Number(b.price).toFixed(2)} · ${escapeHtml(b.duration_type)}</span>`).join("")}
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

// Plans saved before the per-plan "auto_renew" flag existed have no such key; the bot
// treats those as the old global auto_billing_enabled setting, so the editor shows
// (and, on save, keeps) that same value for them and for newly added rows.
let autoBillingFallback = true;
async function refreshAutoBillingFallback() {
  try {
    const res = await fetch("/api/settings");
    if (res.ok) {
      const s = await res.json();
      autoBillingFallback = (s.auto_billing_enabled ?? "1") === "1";
    }
  } catch (e) { /* keep the last known value */ }
}

document.getElementById("newPanelBtn").addEventListener("click", async () => {
  await refreshAutoBillingFallback();
  editingPanelId = null;
  document.getElementById("panelModalTitle").textContent = "New Panel";
  document.getElementById("panelName").value = "New Panel";
  document.getElementById("panelMessage").value = "Choose a plan below to get access.";
  renderButtonsEditor([{ text: "1 Month", price: 25, duration_type: "monthly" }]);
  openPanelModal();
});

async function editPanel(p) {
  await refreshAutoBillingFallback();
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
  buttons.forEach(b => addButtonRow(b.text, b.price, b.duration_type, b.custom_days, b.auto_renew));
}

function addButtonRow(text = "", price = 0, duration = "monthly", customDays = null, autoRenew = undefined) {
  const autoRenewChecked = (autoRenew === undefined || autoRenew === null)
    ? autoBillingFallback
    : (autoRenew === true || autoRenew === 1 || autoRenew === "1" || autoRenew === "true");
  const row = document.createElement("div");
  row.className = "button-row";
  row.innerHTML = `
    <input type="text" class="btn-text" placeholder="Button text" value="${escapeHtml(text)}">
    <input type="number" class="btn-price" placeholder="Price" value="${price}" step="0.01">
    <select class="btn-duration">
      <option value="weekly" ${duration === "weekly" ? "selected" : ""}>Weekly</option>
      <option value="monthly" ${duration === "monthly" ? "selected" : ""}>Monthly</option>
      <option value="quarterly" ${duration === "quarterly" ? "selected" : ""}>3 Months</option>
      <option value="semiannual" ${duration === "semiannual" ? "selected" : ""}>6 Months</option>
      <option value="yearly" ${duration === "yearly" ? "selected" : ""}>Yearly</option>
      <option value="onetime" ${duration === "onetime" ? "selected" : ""}>Lifetime</option>
      <option value="custom" ${duration === "custom" ? "selected" : ""}>Custom days</option>
    </select>
    <input type="number" class="btn-custom-days" placeholder="Days" min="1" value="${customDays || 30}" style="width:70px" ${duration === "custom" ? "" : "hidden"}>
    <button type="button" class="remove-btn" onclick="this.parentElement.remove()">✕</button>
    <label class="btn-autorenew" title="Recurring subscription (weekly, monthly, 3 months, 6 months, yearly). Lifetime and custom-days plans are always a one-off payment.">
      <input type="checkbox" class="btn-auto-renew" ${autoRenewChecked ? "checked" : ""}> Auto-renew
    </label>
  `;
  const durationSel = row.querySelector(".btn-duration");
  const daysInput = row.querySelector(".btn-custom-days");
  durationSel.addEventListener("change", () => {
    daysInput.hidden = durationSel.value !== "custom";
  });
  document.getElementById("buttonsEditor").appendChild(row);
}

document.getElementById("addButtonBtn").addEventListener("click", () => addButtonRow());
document.getElementById("panelCancelBtn").addEventListener("click", closePanelModal);

document.getElementById("panelSaveBtn").addEventListener("click", async () => {
  const name = document.getElementById("panelName").value.trim() || "Untitled Panel";
  const message_text = document.getElementById("panelMessage").value.trim();
  const buttons = Array.from(document.querySelectorAll("#buttonsEditor .button-row")).map(row => {
    const duration_type = row.querySelector(".btn-duration").value;
    const btn = {
      text: row.querySelector(".btn-text").value.trim(),
      price: parseFloat(row.querySelector(".btn-price").value) || 0,
      duration_type,
      auto_renew: row.querySelector(".btn-auto-renew").checked,
    };
    if (duration_type === "custom") {
      btn.custom_days = parseInt(row.querySelector(".btn-custom-days").value, 10) || 30;
    }
    return btn;
  });

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

  const isPanels = table === "panels";
  document.getElementById("devSaveBtn").classList.toggle("hidden", !isPanels);
  document.getElementById("devHint").textContent = isPanels
    ? "Edit the JSON directly and click Save — this fully replaces your panels (packages/buttons) with whatever's in the box, including adding or removing panels."
    : "Read-only preview. This is for quickly copying data into ChatGPT/Claude.";
}

document.getElementById("devSaveBtn").addEventListener("click", async () => {
  let parsed;
  try {
    parsed = JSON.parse(document.getElementById("devJson").value);
  } catch (e) {
    alert("That's not valid JSON: " + e.message);
    return;
  }
  if (!Array.isArray(parsed)) {
    alert("Panels JSON must be an array of panel objects.");
    return;
  }

  const existingRes = await fetch("/api/panels");
  const existing = await existingRes.json();
  const existingIds = new Set(existing.map(p => p.id));
  const newIds = new Set(parsed.filter(p => p.id).map(p => p.id));

  // remove panels that were deleted from the JSON
  for (const id of existingIds) {
    if (!newIds.has(id)) {
      await fetch(`/api/panels/${id}`, { method: "DELETE" });
    }
  }

  // create or update every panel in the JSON
  for (const p of parsed) {
    const body = {
      name: p.name || "Untitled Panel",
      message_text: p.message_text || "",
      buttons: p.buttons || [],
      active: p.active !== undefined ? p.active : 1,
    };
    if (p.id && existingIds.has(p.id)) {
      await fetch(`/api/panels/${p.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } else {
      await fetch("/api/panels", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    }
  }

  alert("Panels saved.");
  loadDevTable();
  if (document.getElementById("tab-plans").classList.contains("active")) loadPanels();
});

// ---------- settings ----------
document.getElementById("settingsBtn").addEventListener("click", async () => {
  const res = await fetch("/api/settings");
  const s = await res.json();
  document.getElementById("settingTitle").value = s.dashboard_title || "";
  document.getElementById("settingCurrency").value = s.currency_symbol || "$";
  const activeProviders = (s.active_providers || "stripe").split(",").map(p => p.trim());
  document.getElementById("providerStripe").checked = activeProviders.includes("stripe");
  document.getElementById("providerCcbill").checked = activeProviders.includes("ccbill");
  document.getElementById("providerVerotel").checked = activeProviders.includes("verotel");
  document.getElementById("settingGroupId").value = s.group_chat_id || "";
  loadBotLink();
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
  if (document.getElementById("providerVerotel").checked) providers.push("verotel");
  await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      dashboard_title: document.getElementById("settingTitle").value,
      currency_symbol: document.getElementById("settingCurrency").value,
      active_providers: providers.join(","),
      group_chat_id: document.getElementById("settingGroupId").value.trim(),
      reminder_days_before: document.getElementById("settingReminderDays").value,
      auto_kick_on_expiry: document.getElementById("settingAutoKick").checked ? "1" : "0",
      admin_ids,
    }),
  });
  document.getElementById("dashTitle").textContent = document.getElementById("settingTitle").value;
  document.getElementById("settingsModal").classList.add("hidden");
  allPanels = [];
  await loadStats();       // also re-reads the currency symbol
  loadSubscribers();
  loadPanels();
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


// ---------- reminders ----------
async function remindOne(id) {
  const res = await fetch(`/api/subscribers/${id}/remind`, { method: "POST" });
  alert(res.ok ? "Reminder sent." : "Couldn't send it — the user may have blocked the bot or never started it.");
}

document.getElementById("remindExpiringBtn").addEventListener("click", async () => {
  if (!confirm("Send a one-time reminder to everyone whose plan is about to end?")) return;
  const res = await fetch("/api/subscribers/remind-expiring", { method: "POST" });
  const data = await res.json();
  alert(`Reminders sent: ${data.sent}` + (data.failed ? `\nCouldn't reach: ${data.failed}` : ""));
});

// ---------- bans ----------
async function loadBans() {
  const res = await fetch("/api/bans");
  const bans = await res.json();
  const list = document.getElementById("bansList");
  if (!bans.length) { list.innerHTML = '<div class="ban-empty">Nobody is banned.</div>'; return; }
  list.innerHTML = bans.map(b => `
    <div class="ban-row">
      <div>
        <div class="ban-id">${escapeHtml(b.telegram_user_id)}</div>
        <div class="ban-reason">${escapeHtml(b.reason || "No reason given")} · ${new Date(b.banned_at).toLocaleDateString()}</div>
      </div>
      <button class="btn-outline" onclick="unban('${escapeHtml(b.telegram_user_id)}')">Unban</button>
    </div>`).join("");
}

async function unban(id) {
  if (!confirm(`Unban ${id}? They'll be able to use the bot again.`)) return;
  const res = await fetch(`/api/bans/${id}`, { method: "DELETE" });
  let data = {};
  try { data = await res.json(); } catch (e) { /* ignore */ }
  loadBans();
  loadSubscribers();
  loadStats();
  if (data.restored && !data.notified) {
    alert("Unbanned and their access was restored, but the Telegram message couldn't be delivered (they may have blocked the bot).");
  }
}

async function banUser(telegramId, reason, kickNow) {
  const res = await fetch("/api/bans", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ telegram_user_id: telegramId, reason, kick_now: kickNow }),
  });
  const data = await res.json();
  return { ok: res.ok, error: data.error };
}

document.getElementById("bansBtn").addEventListener("click", () => {
  document.getElementById("banMsg").textContent = "";
  document.getElementById("bansModal").classList.remove("hidden");
  loadBans();
});
document.getElementById("bansCloseBtn").addEventListener("click", () => {
  document.getElementById("bansModal").classList.add("hidden");
});
document.getElementById("banAddBtn").addEventListener("click", async () => {
  const id = document.getElementById("banIdInput").value.trim();
  const msg = document.getElementById("banMsg");
  const result = await banUser(id, document.getElementById("banReasonInput").value.trim(),
                               document.getElementById("banKickNow").checked);
  if (!result.ok) { msg.style.color = "var(--failed)"; msg.textContent = result.error || "Could not ban."; return; }
  msg.style.color = "var(--success)"; msg.textContent = "Banned.";
  document.getElementById("banIdInput").value = "";
  document.getElementById("banReasonInput").value = "";
  loadBans(); loadSubscribers(); loadStats();
});

async function banFromRow(telegramId) {
  if (!confirm(`Ban Telegram ID ${telegramId} permanently? This also cancels their subscription.`)) return;
  const result = await banUser(telegramId, "Banned from subscriber list", true);
  if (!result.ok) { alert(result.error || "Could not ban."); return; }
  loadSubscribers(); loadStats();
}

// ---------- revenue by date range ----------
function isoDate(d) { return d.toISOString().slice(0, 10); }

async function calcRange() {
  const start = document.getElementById("rangeStart").value;
  const end = document.getElementById("rangeEnd").value;
  const out = document.getElementById("rangeResult");
  if (!start || !end) { out.textContent = "Pick a start and end date."; return; }
  const res = await fetch(`/api/stats/revenue-range?start=${start}&end=${end}`);
  const data = await res.json();
  if (!res.ok) { out.textContent = data.error; return; }
  out.innerHTML = `<strong>${data.currency_symbol}${data.revenue.toFixed(2)}</strong>` +
    `<span class="range-meta">from ${data.count} payment${data.count === 1 ? "" : "s"}, ${start} to ${end}</span>`;
}
document.getElementById("rangeBtn").addEventListener("click", calcRange);

document.querySelectorAll(".range-preset").forEach(btn => {
  btn.addEventListener("click", () => {
    const now = new Date();
    let start, end = now;
    if (btn.dataset.preset === "this-month") start = new Date(now.getFullYear(), now.getMonth(), 1);
    if (btn.dataset.preset === "last-month") {
      start = new Date(now.getFullYear(), now.getMonth() - 1, 1);
      end = new Date(now.getFullYear(), now.getMonth(), 0);
    }
    if (btn.dataset.preset === "last-30") { start = new Date(now); start.setDate(start.getDate() - 30); }
    // build YYYY-MM-DD from local parts so the timezone can't shift the day
    const fmt = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    document.getElementById("rangeStart").value = fmt(start);
    document.getElementById("rangeEnd").value = fmt(end);
    calcRange();
  });
});

// ---------- bot link (for the website) ----------
async function loadBotLink() {
  const input = document.getElementById("settingBotLink");
  input.value = "loading...";
  try {
    const res = await fetch("/api/bot-link");
    const data = await res.json();
    input.value = res.ok ? data.link : (data.error || "unavailable");
  } catch (e) { input.value = "unavailable"; }
}
document.getElementById("copyBotLinkBtn").addEventListener("click", async () => {
  const v = document.getElementById("settingBotLink").value;
  if (!v.startsWith("https://")) return;
  await navigator.clipboard.writeText(v);
  const btn = document.getElementById("copyBotLinkBtn");
  btn.textContent = "Copied"; setTimeout(() => btn.textContent = "Copy", 1500);
});

// ---------- init ----------
function renderCurrentDate() {
  const el = document.getElementById("currentDate");
  if (el) el.textContent = new Date().toLocaleDateString(undefined, { weekday: "short", year: "numeric", month: "short", day: "numeric" });
}
renderCurrentDate();
loadStats();
loadSubscribers();


// ---------- Broadcast ----------
const BC_MAX_BYTES = 50 * 1024 * 1024;
const bcModal = document.getElementById("broadcastModal");
let bcTimer = null;

function bcNote(msg, kind) {
  const el = document.getElementById("bcNote");
  el.textContent = msg || "";
  el.className = "hint" + (kind === "error" ? " bc-error-note" : kind === "ok" ? " bc-ok-note" : "");
}

// the API stores UTC without a "Z"
function bcWhen(iso) {
  if (!iso) return "-";
  const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

const BC_AUDIENCE = { active: "Active", expired: "Expired", all: "All" };
const BC_STATUS_BADGE = { scheduled: "pending", sending: "pending", sent: "successful", cancelled: "cancelled", failed: "failed", interrupted: "failed" };

function bcRenderList(list) {
  const box = document.getElementById("bcList");
  if (!list.length) { box.innerHTML = '<p class="hint">No broadcasts yet.</p>'; return; }
  box.innerHTML = list.map(b => {
    const preview = (b.message_text || "").trim().slice(0, 70) || (b.media_name ? "(file only)" : "(empty)");
    const when = b.status === "scheduled" ? "Scheduled for " + bcWhen(b.scheduled_at) : bcWhen(b.started_at || b.scheduled_at);
    const counts = b.status === "scheduled"
      ? "Waiting to send"
      : `Sent ${b.sent_count || 0} \u00b7 Failed ${b.failed_count || 0}` + (b.total ? ` of ${b.total}` : "");
    return `
      <div class="bc-row">
        <div class="bc-main">
          <div class="bc-title">${escapeHtml(preview)}${(b.message_text || "").trim().length > 70 ? "..." : ""}</div>
          <div class="bc-meta">${escapeHtml(BC_AUDIENCE[b.audience] || b.audience)} \u00b7 ${escapeHtml(when)}${b.media_type ? " \u00b7 " + escapeHtml(b.media_type) + ": " + escapeHtml(b.media_name || "") : ""}</div>
          <div class="bc-meta">${counts}</div>
          ${b.error ? `<div class="bc-error">${escapeHtml(b.error)}</div>` : ""}
        </div>
        <div class="bc-side">
          <span class="badge ${BC_STATUS_BADGE[b.status] || "cancelled"}">${escapeHtml(b.status)}</span>
          ${b.status === "scheduled" ? `<button class="secondary" onclick="bcCancel('${b.id}')">Cancel</button>` : ""}
        </div>
      </div>`;
  }).join("");
}

async function bcLoadList() {
  try {
    const res = await fetch("/api/broadcasts");
    if (res.ok) bcRenderList(await res.json());
  } catch (e) { /* keep what is shown */ }
}

async function bcCancel(id) {
  if (!confirm("Cancel this scheduled broadcast?")) return;
  const res = await fetch(`/api/broadcasts/${id}/cancel`, { method: "POST" });
  if (!res.ok) {
    let data = {};
    try { data = await res.json(); } catch (e) { /* ignore */ }
    alert(data.error || "Could not cancel it.");
  }
  bcLoadList();
}

function bcOpen() {
  bcNote("");
  const now = new Date();
  const pad = n => String(n).padStart(2, "0");
  document.getElementById("bcWhen").min =
    `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`;
  bcModal.classList.remove("hidden");
  bcLoadList();
  clearInterval(bcTimer);
  bcTimer = setInterval(bcLoadList, 10000);   // keeps sent/failed counts fresh while it's open
}

function bcClose() {
  bcModal.classList.add("hidden");
  clearInterval(bcTimer);
  bcTimer = null;
}

document.getElementById("broadcastBtn").addEventListener("click", bcOpen);
document.getElementById("bcCloseBtn").addEventListener("click", bcClose);

document.getElementById("bcMode").addEventListener("change", () => {
  const schedule = document.getElementById("bcMode").value === "schedule";
  document.getElementById("bcScheduleRow").classList.toggle("hidden", !schedule);
  document.getElementById("bcSendBtn").textContent = schedule ? "Schedule" : "Send now";
});

document.getElementById("bcFile").addEventListener("change", () => {
  const input = document.getElementById("bcFile");
  const file = input.files[0];
  const row = document.getElementById("bcMediaTypeRow");
  if (!file) { row.classList.add("hidden"); return; }
  if (file.size > BC_MAX_BYTES) {
    bcNote("That file is over 50 MB. Telegram bots can't send files that large.", "error");
    input.value = "";
    row.classList.add("hidden");
    return;
  }
  bcNote("");
  document.getElementById("bcMediaType").value =
    file.type.startsWith("image/") ? "photo" : file.type.startsWith("video/") ? "video" : "document";
  row.classList.remove("hidden");
});

document.getElementById("bcSendBtn").addEventListener("click", async () => {
  const btn = document.getElementById("bcSendBtn");
  const message = document.getElementById("bcMessage").value.trim();
  const file = document.getElementById("bcFile").files[0];
  const audience = document.getElementById("bcAudience").value;
  const mode = document.getElementById("bcMode").value;

  if (!message && !file) { bcNote("Write a message or attach a file.", "error"); return; }
  if (message.length > (file ? 1024 : 4096)) {
    bcNote(file ? "With a file attached the message is its caption: 1024 characters at most." : "The message is too long (4096 characters at most).", "error");
    return;
  }
  let when = null;
  if (mode === "schedule") {
    const raw = document.getElementById("bcWhen").value;
    when = raw ? new Date(raw) : null;
    if (!when || isNaN(when.getTime())) { bcNote("Pick a date and time.", "error"); return; }
    if (when.getTime() < Date.now() - 60000) { bcNote("Pick a time in the future.", "error"); return; }
  }

  btn.disabled = true;
  try {
    bcNote("Checking the audience...");
    const cres = await fetch(`/api/broadcasts/audience-count?audience=${audience}`);
    const cdata = await cres.json();
    if (!cres.ok) { bcNote(cdata.error || "Could not count the audience.", "error"); return; }
    const count = cdata.count;
    if (!count && mode === "now") { bcNote("Nobody is in that audience.", "error"); return; }

    const question = mode === "now"
      ? `Send to ${count} members?`
      : `Send to ${count} members on ${when.toLocaleString()}?\n\n(The audience is checked again at send time.)`;
    if (!confirm(question)) { bcNote(""); return; }

    const fd = new FormData();
    fd.append("message", message);
    fd.append("audience", audience);
    fd.append("mode", mode);
    if (when) fd.append("scheduled_at", when.toISOString());
    if (file) {
      fd.append("media_type", document.getElementById("bcMediaType").value);
      fd.append("file", file);
      bcNote("Uploading...");
    }
    const res = await fetch("/api/broadcasts", { method: "POST", body: fd });
    let data = {};
    try { data = await res.json(); } catch (e) { /* ignore */ }
    if (!res.ok) { bcNote(data.error || "Could not queue the broadcast.", "error"); return; }

    document.getElementById("bcMessage").value = "";
    document.getElementById("bcFile").value = "";
    document.getElementById("bcMediaTypeRow").classList.add("hidden");
    bcNote(mode === "now" ? "Queued. The bot sends it within a minute." : "Scheduled.", "ok");
    bcLoadList();
  } catch (e) {
    bcNote("Something went wrong. Please try again.", "error");
  } finally {
    btn.disabled = false;
  }
});
