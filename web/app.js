const state = {
  user: null,
  dashboard: null,
  categories: {},
  publicConfig: null,
};

const MCP_URL = "https://academic-gold-weasel.fastmcp.app/mcp";
const IS_FILE_MODE = window.location.protocol === "file:";

function getTheme() {
  return document.documentElement.getAttribute("data-theme") || "dark";
}

function applySystemTheme() {
  const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  refreshGoogleSignInButton();
}

function refreshGoogleSignInButton() {
  const clientId = state.publicConfig?.auth?.google_client_id || "";
  if (!clientId || !window.google?.accounts?.id || !nodes.googleSignInButton) {
    return;
  }
  const isDark = getTheme() === "dark";
  window.google.accounts.id.renderButton(nodes.googleSignInButton, {
    theme: isDark ? "filled_blue" : "outline",
    size: "large",
    shape: "pill",
    text: "continue_with",
    width: 320,
  });
}

const nodes = {
  authView: document.getElementById("authView"),
  appView: document.getElementById("appView"),
  googleAuthStatus: document.getElementById("googleAuthStatus"),
  googleSignInButton: document.getElementById("googleSignInButton"),
  googleFallbackHelp: document.getElementById("googleFallbackHelp"),
  toast: document.getElementById("toast"),
  welcomeTitle: document.getElementById("welcomeTitle"),
  activeMonthLabel: document.getElementById("activeMonthLabel"),
  statSpent: document.getElementById("statSpent"),
  statTransactions: document.getElementById("statTransactions"),
  statBudget: document.getElementById("statBudget"),
  statBudgetLeft: document.getElementById("statBudgetLeft"),
  statIncome: document.getElementById("statIncome"),
  statSavings: document.getElementById("statSavings"),
  trendChart: document.getElementById("trendChart"),
  categoryList: document.getElementById("categoryList"),
  highlightList: document.getElementById("highlightList"),
  recentExpenses: document.getElementById("recentExpenses"),
  expenseHistory: document.getElementById("expenseHistory"),
  budgetList: document.getElementById("budgetList"),
  recurringList: document.getElementById("recurringList"),
  expenseForm: document.getElementById("expenseForm"),
  budgetForm: document.getElementById("budgetForm"),
  recurringForm: document.getElementById("recurringForm"),
  profileForm: document.getElementById("profileForm"),
  profileEmail: document.getElementById("profileEmail"),
  expenseSearch: document.getElementById("expenseSearch"),
  expenseSearchBtn: document.getElementById("expenseSearchBtn"),
  refreshBtn: document.getElementById("refreshBtn"),
  logoutBtn: document.getElementById("logoutBtn"),
  copyPublicMcpBtn: document.getElementById("copyPublicMcpBtn"),
  copyAppMcpBtn: document.getElementById("copyAppMcpBtn"),
  fileWarning: document.getElementById("fileWarning"),
};

const tabs = [...document.querySelectorAll(".tab")];
const panels = {
  overview: document.getElementById("overviewTab"),
  expenses: document.getElementById("expensesTab"),
  budgets: document.getElementById("budgetsTab"),
  recurring: document.getElementById("recurringTab"),
  profile: document.getElementById("profileTab"),
};

function showToast(message, kind = "success") {
  nodes.toast.textContent = message;
  nodes.toast.className = `toast ${kind}`;
  setTimeout(() => {
    nodes.toast.className = "toast hidden";
  }, 3200);
}

function disableInteractiveUiForFileMode() {
  document.querySelectorAll("input, textarea, select, button").forEach((element) => {
    if (element.id === "copyPublicMcpBtn" || element.id === "copyAppMcpBtn") {
      return;
    }
    element.disabled = true;
  });
}

async function api(path, options = {}) {
  if (IS_FILE_MODE) {
    throw new Error("Open http://localhost:8000 while the server is running. Direct file mode cannot call the app API.");
  }

  const headers = { ...(options.headers || {}) };
  if (!headers["Content-Type"] && options.body && typeof options.body === "string") {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(path, {
    credentials: "same-origin",
    headers,
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || "Something went wrong.");
  }
  return data;
}

function formatMoney(value) {
  const currency = (state.user?.currency || "INR").toUpperCase();
  try {
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency,
      maximumFractionDigits: 0,
    }).format(Number(value || 0));
  } catch {
    return `${currency} ${Number(value || 0).toFixed(0)}`;
  }
}

async function copyText(text, message) {
  try {
    await navigator.clipboard.writeText(text);
    showToast(message);
  } catch {
    showToast("Copy failed. You can still copy the MCP URL manually.", "danger");
  }
}

function formatDate(value) {
  return new Date(value).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

function todayValue() {
  return new Date().toISOString().slice(0, 10);
}

function currentMonthValue() {
  return new Date().toISOString().slice(0, 7);
}

function greetingForHour() {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

function optionMarkup(options) {
  return options.map((item) => `<option value="${item}">${item}</option>`).join("");
}

function populateSimpleCategorySelect(selectId) {
  const select = document.getElementById(selectId);
  select.innerHTML = optionMarkup(Object.keys(state.categories));
}

function populateCategorySelect(selectId, subSelectId) {
  const select = document.getElementById(selectId);
  const subSelect = document.getElementById(subSelectId);
  const categories = Object.keys(state.categories);
  select.innerHTML = optionMarkup(categories);
  const refreshSubcategories = () => {
    const selected = select.value;
    const subcategories = state.categories[selected] || ["other"];
    subSelect.innerHTML = optionMarkup(subcategories);
  };
  select.addEventListener("change", refreshSubcategories);
  refreshSubcategories();
}

function activateTab(name) {
  tabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === name));
  Object.entries(panels).forEach(([key, panel]) => {
    panel.classList.toggle("hidden", key !== name);
    panel.classList.toggle("active", key === name);
  });
}

function renderTrend(trend) {
  if (!trend.length) {
    nodes.trendChart.innerHTML = '<p class="empty-state">No spending history for this period yet.</p>';
    return;
  }
  const max = Math.max(...trend.map((item) => Number(item.total || 0)), 1);
  nodes.trendChart.innerHTML = trend
    .map((item) => {
      const height = Math.max((Number(item.total) / max) * 170, 10);
      const label = new Date(item.date).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
      return `
        <div class="trend-bar-wrap">
          <div class="trend-bar-value">${formatMoney(item.total)}</div>
          <div class="trend-bar" style="height:${height}px"></div>
          <div class="trend-bar-label">${label}</div>
        </div>
      `;
    })
    .join("");
}

function renderCategories(categories) {
  if (!categories.length) {
    nodes.categoryList.innerHTML = '<p class="empty-state">Start adding expenses to see category balance.</p>';
    return;
  }
  nodes.categoryList.innerHTML = categories
    .map(
      (item, index) => `
        <div class="ranked-item">
          <div>
            <div class="ranked-label">#${index + 1}</div>
            <div class="expense-title">${item.category}</div>
          </div>
          <strong>${formatMoney(item.total)}</strong>
        </div>
      `
    )
    .join("");
}

function renderHighlights(highlights) {
  nodes.highlightList.innerHTML = (highlights.length ? highlights : ["A few smart observations will appear here once spending data grows."])
    .map((item) => `<article class="insight-item"><p>${item}</p></article>`)
    .join("");
}

function expenseMarkup(item, actions = "") {
  return `
    <article class="expense-item">
      <div class="expense-head">
        <div>
          <div class="expense-title">${item.note || item.category}</div>
          <div class="expense-meta">${item.category}${item.subcategory ? ` / ${item.subcategory}` : ""} - ${formatDate(item.date)}</div>
        </div>
        <strong>${formatMoney(item.amount)}</strong>
      </div>
      <div class="pill-row">
        ${item.payment_mode ? `<span class="pill">${item.payment_mode}</span>` : ""}
        ${item.tags ? item.tags.split(",").filter(Boolean).map((tag) => `<span class="pill">${tag.trim()}</span>`).join("") : ""}
      </div>
      ${actions}
    </article>
  `;
}

function renderRecentExpenses(items) {
  nodes.recentExpenses.innerHTML = items.length
    ? items.map((item) => expenseMarkup(item)).join("")
    : '<p class="empty-state">Your latest expenses will appear here.</p>';
}

function renderExpenseHistory(items) {
  nodes.expenseHistory.innerHTML = items.length
    ? items
        .map(
          (item) =>
            expenseMarkup(
              item,
              `<div class="pill-row">
                <button class="mini-button danger" data-delete-expense="${item.id}" type="button">Delete</button>
              </div>`
            )
        )
        .join("")
    : '<p class="empty-state">No expenses found for this filter.</p>';
}

function renderBudgets(items) {
  nodes.budgetList.innerHTML = items.length
    ? items
        .map((item) => {
          const pct = Math.min(Number(item.pct_used || 0), 100);
          const over = Number(item.remaining) < 0;
          return `
            <article class="budget-item">
              <div class="budget-head">
                <div>
                  <div class="budget-label">${item.category}</div>
                  <div class="budget-title">${formatMoney(item.spent)} of ${formatMoney(item.budget)}</div>
                </div>
                <button class="mini-button danger" data-delete-budget="${item.id}" type="button">Remove</button>
              </div>
              <div class="progress-track">
                <div class="progress-fill ${over ? "over" : ""}" style="width:${pct}%"></div>
              </div>
              <div class="budget-meta">${over ? `${formatMoney(Math.abs(item.remaining))} over` : `${formatMoney(item.remaining)} left`}</div>
            </article>
          `;
        })
        .join("")
    : '<p class="empty-state">No budgets set for this month yet.</p>';
}

function renderRecurring(items) {
  nodes.recurringList.innerHTML = items.length
    ? items
        .map(
          (item) => `
            <article class="recurring-item">
              <div class="recurring-head">
                <div>
                  <div class="recurring-title">${item.description}</div>
                  <div class="expense-meta">${item.category}${item.subcategory ? ` / ${item.subcategory}` : ""} - next ${formatDate(item.next_due)}</div>
                </div>
                <strong>${formatMoney(item.amount)}</strong>
              </div>
              <div class="pill-row">
                <span class="pill">${item.frequency}</span>
                ${item.payment_mode ? `<span class="pill">${item.payment_mode}</span>` : ""}
                <button class="mini-button success" data-log-recurring="${item.id}" type="button">Log now</button>
                <button class="mini-button danger" data-delete-recurring="${item.id}" type="button">Deactivate</button>
              </div>
            </article>
          `
        )
        .join("")
    : '<p class="empty-state">No recurring expenses created yet.</p>';
}

function fillProfileForm(user) {
  nodes.profileForm.full_name.value = user.full_name || "";
  nodes.profileForm.city.value = user.city || "";
  nodes.profileForm.monthly_income.value = user.monthly_income || "";
  nodes.profileForm.savings_goal.value = user.savings_goal || "";
  nodes.profileForm.currency.value = user.currency || "INR";
  nodes.profileEmail.value = user.email || "";
}

function renderDashboard(payload) {
  state.dashboard = payload;
  state.user = payload.user;
  nodes.welcomeTitle.textContent = `${greetingForHour()}, ${state.user.full_name || "there"}`;
  nodes.activeMonthLabel.textContent = payload.month;
  nodes.statSpent.textContent = formatMoney(payload.stats.spent);
  nodes.statTransactions.textContent = `${payload.stats.transactions} transactions`;
  nodes.statBudget.textContent = formatMoney(payload.stats.budgeted);
  nodes.statBudgetLeft.textContent = `${formatMoney(payload.stats.budget_left)} left`;
  nodes.statIncome.textContent = formatMoney(payload.stats.monthly_income);
  nodes.statSavings.textContent = `${formatMoney(payload.stats.savings_left)} available`;
  renderTrend(payload.trend);
  renderCategories(payload.categories);
  renderHighlights(payload.highlights);
  renderRecentExpenses(payload.recent_expenses);
  renderBudgets(payload.budgets);
  fillProfileForm(payload.user);
}

async function loadExpenses(search = "") {
  const data = await api(`/api/expenses?month=${currentMonthValue()}${search ? `&search=${encodeURIComponent(search)}` : ""}`);
  renderExpenseHistory(data.expenses);
}

async function loadRecurring() {
  const data = await api("/api/recurring");
  renderRecurring(data.items);
}

async function loadDashboard() {
  const payload = await api(`/api/dashboard?month=${currentMonthValue()}`);
  renderDashboard(payload);
}

function showGoogleUnavailable(message) {
  nodes.googleAuthStatus.textContent = message;
  nodes.googleAuthStatus.classList.remove("hidden");
  nodes.googleSignInButton.innerHTML = "";
}

async function waitForGoogleLibrary() {
  for (let index = 0; index < 20; index += 1) {
    if (window.google?.accounts?.id) {
      return true;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return false;
}

async function handleGoogleCredentialResponse(response) {
  try {
    await api("/api/auth/google", {
      method: "POST",
      body: JSON.stringify({ credential: response.credential }),
    });
    nodes.authView.classList.add("hidden");
    nodes.appView.classList.remove("hidden");
    await Promise.all([loadDashboard(), loadExpenses(), loadRecurring()]);
    showToast("Signed in with Google.");
  } catch (error) {
    showGoogleUnavailable(error.message);
    showToast(error.message, "danger");
  }
}

async function initializeGoogleAuth() {
  const clientId = state.publicConfig?.auth?.google_client_id || "";
  if (!clientId) {
    showGoogleUnavailable("Google sign-in is not configured yet. Add GOOGLE_CLIENT_ID to enable it.");
    return;
  }

  const hasLibrary = await waitForGoogleLibrary();
  if (!hasLibrary) {
    showGoogleUnavailable("Google Sign-In could not load. Refresh and try again.");
    return;
  }

  window.google.accounts.id.initialize({
    client_id: clientId,
    callback: handleGoogleCredentialResponse,
    auto_select: false,
    cancel_on_tap_outside: true,
    ux_mode: "popup",
  });

  refreshGoogleSignInButton();

  nodes.googleAuthStatus.classList.add("hidden");
  nodes.googleFallbackHelp.textContent = "Use the same Google account across local and deployed versions of the app.";
}

async function bootstrap() {
  if (IS_FILE_MODE) {
    nodes.fileWarning.classList.remove("hidden");
    nodes.appView.classList.add("hidden");
    disableInteractiveUiForFileMode();
    showToast("Use http://localhost:8000 instead of opening index.html directly.", "danger");
    return;
  }

  try {
    const [configData, categoryData] = await Promise.all([api("/api/public-config"), api("/api/categories")]);
    state.publicConfig = configData;
    state.categories = categoryData.categories;
    populateCategorySelect("expenseCategory", "expenseSubcategory");
    populateSimpleCategorySelect("budgetCategory");
    populateCategorySelect("recurringCategory", "recurringSubcategory");
  } catch (error) {
    showToast(error.message, "danger");
    return;
  }

  nodes.expenseForm.date.value = todayValue();
  nodes.budgetForm.month.value = currentMonthValue();
  nodes.recurringForm.next_due.value = todayValue();

  await initializeGoogleAuth();

  try {
    const me = await api("/api/me");
    state.user = me.user;
    nodes.authView.classList.add("hidden");
    nodes.appView.classList.remove("hidden");
    await Promise.all([loadDashboard(), loadExpenses(), loadRecurring()]);
  } catch {
    nodes.authView.classList.remove("hidden");
    nodes.appView.classList.add("hidden");
  }
}

nodes.expenseForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(nodes.expenseForm);
  try {
    await api("/api/expenses", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(form.entries())),
    });
    nodes.expenseForm.reset();
    nodes.expenseForm.date.value = todayValue();
    document.getElementById("expenseCategory").dispatchEvent(new Event("change"));
    await Promise.all([loadDashboard(), loadExpenses()]);
    showToast("Expense added.");
  } catch (error) {
    showToast(error.message, "danger");
  }
});

nodes.budgetForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(nodes.budgetForm);
  try {
    await api("/api/budgets", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(form.entries())),
    });
    await loadDashboard();
    showToast("Budget saved.");
  } catch (error) {
    showToast(error.message, "danger");
  }
});

nodes.recurringForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(nodes.recurringForm);
  try {
    await api("/api/recurring", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(form.entries())),
    });
    nodes.recurringForm.reset();
    nodes.recurringForm.next_due.value = todayValue();
    document.getElementById("recurringCategory").dispatchEvent(new Event("change"));
    await Promise.all([loadRecurring(), loadDashboard()]);
    showToast("Recurring expense created.");
  } catch (error) {
    showToast(error.message, "danger");
  }
});

nodes.profileForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(nodes.profileForm);
  try {
    const data = await api("/api/profile", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(form.entries())),
    });
    state.user = data.user;
    fillProfileForm(data.user);
    await loadDashboard();
    showToast("Profile updated.");
  } catch (error) {
    showToast(error.message, "danger");
  }
});

nodes.expenseSearchBtn.addEventListener("click", async () => {
  try {
    await loadExpenses(nodes.expenseSearch.value.trim());
  } catch (error) {
    showToast(error.message, "danger");
  }
});

nodes.refreshBtn.addEventListener("click", async () => {
  try {
    await Promise.all([loadDashboard(), loadExpenses(nodes.expenseSearch.value.trim()), loadRecurring()]);
    showToast("Dashboard refreshed.");
  } catch (error) {
    showToast(error.message, "danger");
  }
});

nodes.logoutBtn.addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" });
  window.location.reload();
});

nodes.copyPublicMcpBtn.addEventListener("click", async () => {
  await copyText(MCP_URL, "MCP URL copied for your chatbot setup.");
});

nodes.copyAppMcpBtn.addEventListener("click", async () => {
  await copyText(MCP_URL, "Hosted MCP endpoint copied.");
});

nodes.expenseHistory.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-delete-expense]");
  if (!button) return;
  try {
    await api(`/api/expenses/${button.dataset.deleteExpense}`, { method: "DELETE" });
    await Promise.all([loadDashboard(), loadExpenses(nodes.expenseSearch.value.trim())]);
    showToast("Expense deleted.");
  } catch (error) {
    showToast(error.message, "danger");
  }
});

nodes.budgetList.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-delete-budget]");
  if (!button) return;
  try {
    await api(`/api/budgets/${button.dataset.deleteBudget}`, { method: "DELETE" });
    await loadDashboard();
    showToast("Budget removed.");
  } catch (error) {
    showToast(error.message, "danger");
  }
});

nodes.recurringList.addEventListener("click", async (event) => {
  const logButton = event.target.closest("[data-log-recurring]");
  const deleteButton = event.target.closest("[data-delete-recurring]");
  try {
    if (logButton) {
      await api(`/api/recurring/${logButton.dataset.logRecurring}/log`, {
        method: "POST",
        body: JSON.stringify({ date: todayValue() }),
      });
      await Promise.all([loadRecurring(), loadDashboard(), loadExpenses(nodes.expenseSearch.value.trim())]);
      showToast("Recurring expense logged.");
    }
    if (deleteButton) {
      await api(`/api/recurring/${deleteButton.dataset.deleteRecurring}`, { method: "DELETE" });
      await loadRecurring();
      showToast("Recurring expense deactivated.");
    }
  } catch (error) {
    showToast(error.message, "danger");
  }
});

tabs.forEach((tab) => {
  tab.addEventListener("click", () => activateTab(tab.dataset.tab));
});

window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applySystemTheme);

bootstrap();
