# ExpenseTracker MCP Server

`ExpenseTracker` is a FastMCP-based remote server for personal expense tracking. It exposes tools and resources that let an MCP client add expenses, search and summarize spending, manage budgets, track recurring payments, and generate reports from a SQLite-backed data store.

It now also includes a built-in web application served by the same backend, with Google sign-in, a personalized dashboard, expense capture forms, budget management, recurring spend tracking, and profile settings.

This project is useful from two angles:

- For users: it acts like a finance assistant that can record and analyze expenses through MCP tools.
- For developers: it is a compact reference implementation of a stateful FastMCP server using SQLite, async database access, and structured resources.

## What This App Does

The server provides:

- Expense CRUD operations
- Bulk expense import
- Search and filtered listing
- Monthly and yearly reporting
- Budget setup and tracking
- Recurring expense templates
- Statistical summaries and daily breakdowns
- JSON resources for categories and quick dashboard-style views
- CSV export for a selected date range
- A premium web UI with Google sign-in and personalized planning

## User Guide

### Core Use Cases

You can use this server through any MCP-compatible client to:

- Add day-to-day expenses with category, subcategory, note, tags, payment mode, and currency
- Review spending between two dates
- Find a specific expense by ID or search by keyword
- See category-wise summaries for a week, month, or custom period
- Monitor budgets by category
- Track recurring items like rent, EMI, subscriptions, or utility bills
- Export filtered expense data as CSV
- Pull quick resources such as `today`, `this_month`, and `budgets/status`

Or open the built-in web app in a browser and:

- Sign in with Google
- Set up a personal profile with name, city, income, and savings goal
- Review a polished monthly dashboard with trends and highlights
- Add expenses, budgets, and recurring bills from one interface

### Data Captured Per Expense

Each expense can store:

- `date`
- `amount`
- `category`
- `subcategory`
- `note`
- `tags`
- `payment_mode`
- `currency`
- `created_at`

### Categories

The server reads categories from [`categories.json`](/Users/Sanjoy%20Chattopadhyay/PycharmProjects/MCP/Build-local-server/categories.json). It includes broad groups such as:

- `food`
- `transport`
- `housing`
- `utilities`
- `health`
- `education`
- `family_kids`
- `entertainment`
- `shopping`
- `subscriptions`
- `business`
- `travel`
- `investments`
- `misc`

Each category includes subcategories so clients can guide users toward consistent tagging.

### Main Tools Available

#### Expense Management

- `add_expense`
- `update_expense`
- `delete_expense`
- `bulk_add_expenses`
- `get_expense`
- `list_expenses`
- `search_expenses`

#### Reports and Insights

- `summarize`
- `monthly_report`
- `yearly_report`
- `compare_months`
- `spending_trends`
- `top_expenses`
- `payment_mode_summary`
- `expense_stats`
- `daily_breakdown`
- `export_csv`

#### Budgets

- `set_budget`
- `get_budgets`
- `delete_budget`

#### Recurring Expenses

- `add_recurring`
- `list_recurring`
- `log_recurring`
- `delete_recurring`

### Resources Available

- `expense:///categories`
- `expense:///summary/today`
- `expense:///summary/this_month`
- `expense:///recurring/due_soon`
- `expense:///budgets/status`
- `expense:///stats/all_time`

### Typical User Flows

1. Add expenses throughout the day with `add_expense`.
2. Use `list_expenses` or `search_expenses` to review entries.
3. Use `summarize` or `monthly_report` to understand where money went.
4. Set category limits with `set_budget`.
5. Track predictable bills with `add_recurring` and `log_recurring`.
6. Export records with `export_csv` when needed.

## Developer Guide

### Tech Stack

- Python 3.13+
- [FastMCP](https://github.com/jlowin/fastmcp)
- SQLite
- `aiosqlite`
- `uv` for dependency management

### Project Files

- [`main.py`](/Users/Sanjoy%20Chattopadhyay/PycharmProjects/MCP/Build-local-server/main.py): current FastMCP server implementation
- [`categories.json`](/Users/Sanjoy%20Chattopadhyay/PycharmProjects/MCP/Build-local-server/categories.json): category and subcategory definitions
- [`pyproject.toml`](/Users/Sanjoy%20Chattopadhyay/PycharmProjects/MCP/Build-local-server/pyproject.toml): project metadata and dependencies
- [`build-local-server.py`](/Users/Sanjoy%20Chattopadhyay/PycharmProjects/MCP/Build-local-server/build-local-server.py): earlier local/server variant kept in the repo

### Local Setup

1. Install Python `3.13` or newer.
2. Install dependencies:

```bash
uv sync
```

3. Start the server:

```bash
uv run python main.py
```

By default, the server starts with:

- transport: `http`
- host: `0.0.0.0`
- port: `8000`

Open [http://localhost:8000](http://localhost:8000) for the web app.
The MCP endpoint remains available at [http://localhost:8000/mcp](http://localhost:8000/mcp).

### Data Storage Behavior

The server stores its SQLite database inside the project so it stays standalone locally:

- database file: `data/expenses.db`

That behavior is defined in [`main.py`](/Users/Sanjoy%20Chattopadhyay/PycharmProjects/MCP/Build-local-server/main.py). On startup, the server initializes required tables if they do not already exist:

- `expenses`
- `budgets`
- `recurring`

This makes local setup easy, and the app will also copy forward an older temp-directory database if one exists. For hosted deployment, you still need persistent disk storage because SQLite is file-based.

- local data remains self-contained with the project
- hosted deployment should use a platform with persistent disk storage
- serverless platforms with ephemeral filesystems are not a good fit for long-lived SQLite data

### Architecture Notes

The current implementation follows a simple structure:

- `FastMCP("ExpenseTracker")` creates the MCP app
- `init_db()` performs synchronous schema initialization
- async tool handlers use `aiosqlite`
- `_rows()` is a small helper that converts query results into dictionaries
- resources expose lightweight read-only snapshots in JSON form

This design keeps the server straightforward while still supporting a useful amount of functionality.

### Development Tips

- Update [`categories.json`](/Users/Sanjoy%20Chattopadhyay/PycharmProjects/MCP/Build-local-server/categories.json) to extend supported categories without changing code.
- If you need persistent storage for deployment, change `DB_PATH` in [`main.py`](/Users/Sanjoy%20Chattopadhyay/PycharmProjects/MCP/Build-local-server/main.py).
- If you add new tools, keep return shapes consistent so MCP clients can present results cleanly.
- Consider adding request validation if this is going to be exposed to less-trusted clients.
- Consider adding tests around reporting, recurring date advancement, and budget calculations.

## Example Capabilities

These are the kinds of requests an MCP client can make through this server:

- "Add an expense of 250 INR for lunch under food/dining_out."
- "Show my expenses from 2026-04-01 to 2026-04-05."
- "Summarize spending by category for this month."
- "Set a food budget of 8000 for 2026-04."
- "Show recurring expenses due soon."
- "Export my transport expenses for March as CSV."

## Current Limitations

- Google sign-in requires a configured OAuth Web Client ID
- No automated tests are included yet
- Hosted deployments need persistent storage for SQLite
- Schema migrations are implicit rather than versioned
- Some validation rules are light and rely on client discipline

## Roadmap Ideas

- Add automated tests
- Add input validation and stricter enums
- Support persistent deployment configuration through environment variables
- Add pagination for large result sets
- Add import from CSV files, not just export
- Add authentication for hosted/public deployment

## License

No license file is currently included in this repository. Add one if you plan to distribute or open-source the project more broadly.
