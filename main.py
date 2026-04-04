from fastmcp import FastMCP
import os
import json
import csv
import io
import aiosqlite
import tempfile
from datetime import datetime, date
from typing import Optional

TEMP_DIR = tempfile.gettempdir()
DB_PATH = os.path.join(TEMP_DIR, "expenses.db")
CATEGORIES_PATH = os.path.join(os.path.dirname(__file__), "categories.json")

print(f"Database path: {DB_PATH}")

mcp = FastMCP("ExpenseTracker")


# ---------------------------------------------------------------------------
# Database initialisation
# ---------------------------------------------------------------------------

def init_db():
    import sqlite3
    with sqlite3.connect(DB_PATH) as c:
        c.execute("PRAGMA journal_mode=WAL")

        # Core expenses table
        c.execute("""
            CREATE TABLE IF NOT EXISTS expenses(
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT    NOT NULL,
                amount      REAL    NOT NULL,
                category    TEXT    NOT NULL,
                subcategory TEXT    DEFAULT '',
                note        TEXT    DEFAULT '',
                tags        TEXT    DEFAULT '',
                payment_mode TEXT   DEFAULT '',
                currency    TEXT    DEFAULT 'INR',
                created_at  TEXT    DEFAULT (datetime('now'))
            )
        """)

        # Budgets table – monthly spending caps per category
        c.execute("""
            CREATE TABLE IF NOT EXISTS budgets(
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                month     TEXT NOT NULL,   -- YYYY-MM
                category  TEXT NOT NULL,
                amount    REAL NOT NULL,
                UNIQUE(month, category)
            )
        """)

        # Recurring templates
        c.execute("""
            CREATE TABLE IF NOT EXISTS recurring(
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                description  TEXT NOT NULL,
                amount       REAL NOT NULL,
                category     TEXT NOT NULL,
                subcategory  TEXT DEFAULT '',
                payment_mode TEXT DEFAULT '',
                frequency    TEXT NOT NULL,  -- monthly | weekly | yearly
                next_due     TEXT NOT NULL,  -- YYYY-MM-DD
                active       INTEGER DEFAULT 1
            )
        """)

        # Verify write access
        c.execute("INSERT OR IGNORE INTO expenses(date,amount,category) VALUES('2000-01-01',0,'_test')")
        c.execute("DELETE FROM expenses WHERE category='_test'")
        print("Database initialised with write access.")


init_db()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def _rows(db_path, query, params=()):
    async with aiosqlite.connect(db_path) as c:
        cur = await c.execute(query, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in await cur.fetchall()]


# ===========================================================================
# TOOLS – Expense CRUD
# ===========================================================================

@mcp.tool()
async def add_expense(
    date: str,
    amount: float,
    category: str,
    subcategory: str = "",
    note: str = "",
    tags: str = "",
    payment_mode: str = "",
    currency: str = "INR"
):
    """Add a new expense.

    Args:
        date: ISO date YYYY-MM-DD
        amount: Positive numeric amount
        category: Main category (see categories resource)
        subcategory: Optional subcategory
        note: Free-text description
        tags: Comma-separated tags e.g. 'work,reimbursable'
        payment_mode: cash | upi | card | netbanking | emi | other
        currency: 3-letter currency code, default INR
    """
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            cur = await c.execute(
                """INSERT INTO expenses(date,amount,category,subcategory,note,tags,payment_mode,currency)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (date, amount, category, subcategory, note, tags, payment_mode, currency)
            )
            eid = cur.lastrowid
            await c.commit()
            return {"status": "success", "id": eid, "message": f"Expense #{eid} added."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def update_expense(
    expense_id: int,
    date: str = "",
    amount: float = 0,
    category: str = "",
    subcategory: str = "",
    note: str = "",
    tags: str = "",
    payment_mode: str = "",
    currency: str = ""
):
    """Update one or more fields of an existing expense by its ID.
    Only fields with non-empty / non-zero values are updated.
    """
    fields, params = [], []
    if date:           fields.append("date=?");         params.append(date)
    if amount > 0:     fields.append("amount=?");       params.append(amount)
    if category:       fields.append("category=?");     params.append(category)
    if subcategory:    fields.append("subcategory=?");  params.append(subcategory)
    if note:           fields.append("note=?");         params.append(note)
    if tags:           fields.append("tags=?");         params.append(tags)
    if payment_mode:   fields.append("payment_mode=?"); params.append(payment_mode)
    if currency:       fields.append("currency=?");     params.append(currency)

    if not fields:
        return {"status": "error", "message": "No fields to update."}

    params.append(expense_id)
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            cur = await c.execute(
                f"UPDATE expenses SET {', '.join(fields)} WHERE id=?", params
            )
            await c.commit()
            if cur.rowcount == 0:
                return {"status": "error", "message": f"No expense found with id={expense_id}"}
            return {"status": "success", "message": f"Expense #{expense_id} updated."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def delete_expense(expense_id: int):
    """Permanently delete an expense by its ID."""
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            cur = await c.execute("DELETE FROM expenses WHERE id=?", (expense_id,))
            await c.commit()
            if cur.rowcount == 0:
                return {"status": "error", "message": f"No expense found with id={expense_id}"}
            return {"status": "success", "message": f"Expense #{expense_id} deleted."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def bulk_add_expenses(expenses: list):
    """Add multiple expenses in one call.

    Each item in the list must be a dict with keys:
      date, amount, category, and optionally subcategory, note, tags, payment_mode, currency.

    Example:
      [{"date":"2026-04-01","amount":500,"category":"food","subcategory":"groceries"},
       {"date":"2026-04-02","amount":250,"category":"transport","note":"uber"}]
    """
    inserted, errors = [], []
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            for i, e in enumerate(expenses):
                try:
                    cur = await c.execute(
                        """INSERT INTO expenses(date,amount,category,subcategory,note,tags,payment_mode,currency)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (
                            e["date"], float(e["amount"]), e["category"],
                            e.get("subcategory",""), e.get("note",""),
                            e.get("tags",""), e.get("payment_mode",""),
                            e.get("currency","INR")
                        )
                    )
                    inserted.append(cur.lastrowid)
                except Exception as ex:
                    errors.append({"index": i, "error": str(ex)})
            await c.commit()
        return {"status": "success", "inserted_ids": inserted, "errors": errors}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ===========================================================================
# TOOLS – Querying & Listing
# ===========================================================================

@mcp.tool()
async def list_expenses(
    start_date: str,
    end_date: str,
    category: str = "",
    payment_mode: str = "",
    min_amount: float = 0,
    max_amount: float = 0,
    tags: str = "",
    limit: int = 200
):
    """List expenses with rich filters.

    Args:
        start_date: YYYY-MM-DD (inclusive)
        end_date:   YYYY-MM-DD (inclusive)
        category:   Filter by category (optional)
        payment_mode: Filter by payment mode (optional)
        min_amount: Minimum amount filter (0 = no filter)
        max_amount: Maximum amount filter (0 = no filter)
        tags:       Filter expenses that contain this tag
        limit:      Max rows to return (default 200)
    """
    query = """
        SELECT id, date, amount, category, subcategory, note, tags, payment_mode, currency
        FROM expenses
        WHERE date BETWEEN ? AND ?
    """
    params: list = [start_date, end_date]

    if category:
        query += " AND category=?"; params.append(category)
    if payment_mode:
        query += " AND payment_mode=?"; params.append(payment_mode)
    if min_amount > 0:
        query += " AND amount>=?"; params.append(min_amount)
    if max_amount > 0:
        query += " AND amount<=?"; params.append(max_amount)
    if tags:
        query += " AND (',' || tags || ',') LIKE ?"; params.append(f"%,{tags},%")

    query += " ORDER BY date DESC, id DESC LIMIT ?"
    params.append(limit)

    try:
        return await _rows(DB_PATH, query, params)
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def get_expense(expense_id: int):
    """Get a single expense record by its ID."""
    try:
        rows = await _rows(DB_PATH,
            "SELECT * FROM expenses WHERE id=?", (expense_id,))
        if not rows:
            return {"status": "error", "message": f"No expense with id={expense_id}"}
        return rows[0]
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def search_expenses(keyword: str, start_date: str = "", end_date: str = "", limit: int = 100):
    """Full-text search across note, subcategory, and tags fields.

    Args:
        keyword:    Search term (case-insensitive)
        start_date: Optional YYYY-MM-DD lower bound
        end_date:   Optional YYYY-MM-DD upper bound
        limit:      Max rows (default 100)
    """
    query = """
        SELECT id, date, amount, category, subcategory, note, tags, payment_mode
        FROM expenses
        WHERE (note LIKE ? OR subcategory LIKE ? OR tags LIKE ? OR category LIKE ?)
    """
    kw = f"%{keyword}%"
    params: list = [kw, kw, kw, kw]

    if start_date:
        query += " AND date>=?"; params.append(start_date)
    if end_date:
        query += " AND date<=?"; params.append(end_date)

    query += " ORDER BY date DESC LIMIT ?"
    params.append(limit)

    try:
        return await _rows(DB_PATH, query, params)
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ===========================================================================
# TOOLS – Summaries & Reports
# ===========================================================================

@mcp.tool()
async def summarize(
    start_date: str,
    end_date: str,
    category: str = "",
    group_by_subcategory: bool = False
):
    """Category-wise spending summary.

    Args:
        start_date: YYYY-MM-DD
        end_date:   YYYY-MM-DD
        category:   Narrow to one category (optional)
        group_by_subcategory: If True, break down within each category by subcategory
    """
    try:
        if group_by_subcategory:
            select = "category, subcategory, SUM(amount) AS total, COUNT(*) AS count"
            group  = "GROUP BY category, subcategory"
        else:
            select = "category, SUM(amount) AS total, COUNT(*) AS count"
            group  = "GROUP BY category"

        query = f"""
            SELECT {select} FROM expenses
            WHERE date BETWEEN ? AND ?
        """
        params: list = [start_date, end_date]
        if category:
            query += " AND category=?"; params.append(category)
        query += f" {group} ORDER BY total DESC"
        return await _rows(DB_PATH, query, params)
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def monthly_report(year: int, month: int):
    """Full spending report for a calendar month.

    Returns:
      - total spent
      - category breakdown
      - daily totals
      - top 5 largest expenses
      - budget status (if budgets set)
    """
    m = f"{year:04d}-{month:02d}"
    start = f"{m}-01"
    # last day
    import calendar
    last = calendar.monthrange(year, month)[1]
    end = f"{m}-{last:02d}"

    try:
        async with aiosqlite.connect(DB_PATH) as c:
            # Total
            cur = await c.execute(
                "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date BETWEEN ? AND ?",
                (start, end)
            )
            total = (await cur.fetchone())[0]

            # Category breakdown
            cur = await c.execute("""
                SELECT category, SUM(amount) AS total, COUNT(*) AS count
                FROM expenses WHERE date BETWEEN ? AND ?
                GROUP BY category ORDER BY total DESC
            """, (start, end))
            cols = [d[0] for d in cur.description]
            categories = [dict(zip(cols, r)) for r in await cur.fetchall()]

            # Daily totals
            cur = await c.execute("""
                SELECT date, SUM(amount) AS daily_total, COUNT(*) AS txns
                FROM expenses WHERE date BETWEEN ? AND ?
                GROUP BY date ORDER BY date
            """, (start, end))
            cols = [d[0] for d in cur.description]
            daily = [dict(zip(cols, r)) for r in await cur.fetchall()]

            # Top 5 expenses
            cur = await c.execute("""
                SELECT id, date, amount, category, subcategory, note
                FROM expenses WHERE date BETWEEN ? AND ?
                ORDER BY amount DESC LIMIT 5
            """, (start, end))
            cols = [d[0] for d in cur.description]
            top5 = [dict(zip(cols, r)) for r in await cur.fetchall()]

            # Budget status
            cur = await c.execute(
                "SELECT category, amount AS budget FROM budgets WHERE month=?", (m,)
            )
            budget_rows = await cur.fetchall()
            budget_status = []
            for cat, budget in budget_rows:
                cur2 = await c.execute(
                    "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date BETWEEN ? AND ? AND category=?",
                    (start, end, cat)
                )
                spent = (await cur2.fetchone())[0]
                budget_status.append({
                    "category": cat,
                    "budget": budget,
                    "spent": spent,
                    "remaining": budget - spent,
                    "pct_used": round(spent / budget * 100, 1) if budget > 0 else None
                })

        return {
            "month": m,
            "total_spent": round(total, 2),
            "category_breakdown": categories,
            "daily_totals": daily,
            "top_5_expenses": top5,
            "budget_status": budget_status
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def yearly_report(year: int):
    """Annual spending summary with month-by-month and category breakdown."""
    start = f"{year:04d}-01-01"
    end   = f"{year:04d}-12-31"
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            cur = await c.execute(
                "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date BETWEEN ? AND ?",
                (start, end)
            )
            total = (await cur.fetchone())[0]

            cur = await c.execute("""
                SELECT strftime('%Y-%m', date) AS month,
                       SUM(amount) AS total, COUNT(*) AS txns
                FROM expenses WHERE date BETWEEN ? AND ?
                GROUP BY month ORDER BY month
            """, (start, end))
            cols = [d[0] for d in cur.description]
            monthly = [dict(zip(cols, r)) for r in await cur.fetchall()]

            cur = await c.execute("""
                SELECT category, SUM(amount) AS total, COUNT(*) AS txns
                FROM expenses WHERE date BETWEEN ? AND ?
                GROUP BY category ORDER BY total DESC
            """, (start, end))
            cols = [d[0] for d in cur.description]
            by_category = [dict(zip(cols, r)) for r in await cur.fetchall()]

        return {
            "year": year,
            "total_spent": round(total, 2),
            "monthly_breakdown": monthly,
            "category_breakdown": by_category
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def compare_months(month1: str, month2: str):
    """Compare spending between two months (format YYYY-MM).

    Returns side-by-side totals and per-category diff.
    """
    import calendar

    def month_range(m):
        y, mo = map(int, m.split("-"))
        last = calendar.monthrange(y, mo)[1]
        return f"{m}-01", f"{m}-{last:02d}"

    try:
        async with aiosqlite.connect(DB_PATH) as c:
            results = {}
            for label, m in [("month1", month1), ("month2", month2)]:
                s, e = month_range(m)
                cur = await c.execute("""
                    SELECT category, SUM(amount) AS total
                    FROM expenses WHERE date BETWEEN ? AND ?
                    GROUP BY category
                """, (s, e))
                rows = await cur.fetchall()
                results[label] = {r[0]: r[1] for r in rows}
                cur2 = await c.execute(
                    "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date BETWEEN ? AND ?", (s, e))
                results[f"{label}_total"] = (await cur2.fetchone())[0]

        all_cats = sorted(set(list(results["month1"].keys()) + list(results["month2"].keys())))
        diff = []
        for cat in all_cats:
            v1 = results["month1"].get(cat, 0)
            v2 = results["month2"].get(cat, 0)
            diff.append({
                "category": cat,
                month1: round(v1, 2),
                month2: round(v2, 2),
                "change": round(v2 - v1, 2),
                "change_pct": round((v2 - v1) / v1 * 100, 1) if v1 > 0 else None
            })

        return {
            month1: {"total": round(results["month1_total"], 2)},
            month2: {"total": round(results["month2_total"], 2)},
            "total_change": round(results["month2_total"] - results["month1_total"], 2),
            "category_diff": diff
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def spending_trends(months: int = 6, category: str = ""):
    """Show spending trend over the last N months.

    Args:
        months:   How many months back to look (default 6)
        category: Narrow to one category (optional)
    """
    from datetime import date
    import calendar

    today = date.today()
    data = []
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            for i in range(months - 1, -1, -1):
                # Go back i months
                y = today.year
                m = today.month - i
                while m <= 0:
                    m += 12; y -= 1
                last = calendar.monthrange(y, m)[1]
                s = f"{y:04d}-{m:02d}-01"
                e = f"{y:04d}-{m:02d}-{last:02d}"
                label = f"{y:04d}-{m:02d}"

                if category:
                    cur = await c.execute(
                        "SELECT COALESCE(SUM(amount),0),COUNT(*) FROM expenses WHERE date BETWEEN ? AND ? AND category=?",
                        (s, e, category)
                    )
                else:
                    cur = await c.execute(
                        "SELECT COALESCE(SUM(amount),0),COUNT(*) FROM expenses WHERE date BETWEEN ? AND ?",
                        (s, e)
                    )
                row = await cur.fetchone()
                data.append({"month": label, "total": round(row[0], 2), "txns": row[1]})
        return {"category": category or "all", "trend": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def top_expenses(start_date: str, end_date: str, n: int = 10, category: str = ""):
    """Return the N largest expenses in a date range.

    Args:
        start_date: YYYY-MM-DD
        end_date:   YYYY-MM-DD
        n:          How many to return (default 10)
        category:   Optionally filter by category
    """
    query = """
        SELECT id, date, amount, category, subcategory, note, payment_mode
        FROM expenses WHERE date BETWEEN ? AND ?
    """
    params: list = [start_date, end_date]
    if category:
        query += " AND category=?"; params.append(category)
    query += " ORDER BY amount DESC LIMIT ?"
    params.append(n)
    try:
        return await _rows(DB_PATH, query, params)
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def payment_mode_summary(start_date: str, end_date: str):
    """Summarize spending by payment mode (cash, UPI, card, etc.)."""
    try:
        return await _rows(DB_PATH, """
            SELECT payment_mode, SUM(amount) AS total, COUNT(*) AS txns
            FROM expenses WHERE date BETWEEN ? AND ?
            GROUP BY payment_mode ORDER BY total DESC
        """, (start_date, end_date))
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def export_csv(start_date: str, end_date: str, category: str = ""):
    """Export expenses to CSV text for the given date range.

    Returns a plain-text CSV string you can save to a file.
    """
    query = """
        SELECT id, date, amount, category, subcategory, note, tags, payment_mode, currency
        FROM expenses WHERE date BETWEEN ? AND ?
    """
    params: list = [start_date, end_date]
    if category:
        query += " AND category=?"; params.append(category)
    query += " ORDER BY date, id"

    try:
        rows = await _rows(DB_PATH, query, params)
        if not rows:
            return {"status": "ok", "csv": "", "message": "No data found."}
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
        return {"status": "ok", "csv": buf.getvalue()}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ===========================================================================
# TOOLS – Budget Management
# ===========================================================================

@mcp.tool()
async def set_budget(month: str, category: str, amount: float):
    """Set or update the monthly budget for a category.

    Args:
        month:    YYYY-MM  e.g. '2026-04'
        category: Expense category name
        amount:   Budget cap in your default currency
    """
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            await c.execute(
                "INSERT INTO budgets(month,category,amount) VALUES(?,?,?) ON CONFLICT(month,category) DO UPDATE SET amount=excluded.amount",
                (month, category, amount)
            )
            await c.commit()
        return {"status": "success", "message": f"Budget set: {category} → {amount} for {month}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def get_budgets(month: str):
    """Get all budgets set for a given month (YYYY-MM) with actual vs budget."""
    import calendar
    y, m = map(int, month.split("-"))
    last = calendar.monthrange(y, m)[1]
    start, end = f"{month}-01", f"{month}-{last:02d}"

    try:
        budgets = await _rows(DB_PATH,
            "SELECT category, amount AS budget FROM budgets WHERE month=? ORDER BY category",
            (month,))
        result = []
        async with aiosqlite.connect(DB_PATH) as c:
            for b in budgets:
                cur = await c.execute(
                    "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date BETWEEN ? AND ? AND category=?",
                    (start, end, b["category"])
                )
                spent = (await cur.fetchone())[0]
                result.append({
                    "category": b["category"],
                    "budget": b["budget"],
                    "spent": round(spent, 2),
                    "remaining": round(b["budget"] - spent, 2),
                    "pct_used": round(spent / b["budget"] * 100, 1) if b["budget"] > 0 else None,
                    "over_budget": spent > b["budget"]
                })
        return result
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def delete_budget(month: str, category: str):
    """Delete a budget entry for a given month and category."""
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            cur = await c.execute(
                "DELETE FROM budgets WHERE month=? AND category=?", (month, category))
            await c.commit()
            if cur.rowcount == 0:
                return {"status": "error", "message": "Budget not found."}
        return {"status": "success", "message": f"Budget for {category} in {month} deleted."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ===========================================================================
# TOOLS – Recurring Expenses
# ===========================================================================

@mcp.tool()
async def add_recurring(
    description: str,
    amount: float,
    category: str,
    subcategory: str = "",
    payment_mode: str = "",
    frequency: str = "monthly",
    next_due: str = ""
):
    """Register a recurring expense template (subscription, rent, EMI, etc.).

    Args:
        description: Label e.g. 'Netflix subscription'
        amount:      Amount per occurrence
        category:    Expense category
        subcategory: Optional subcategory
        payment_mode: Payment mode
        frequency:   monthly | weekly | yearly
        next_due:    YYYY-MM-DD for next occurrence (defaults to today)
    """
    if not next_due:
        next_due = date.today().isoformat()
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            cur = await c.execute(
                """INSERT INTO recurring(description,amount,category,subcategory,payment_mode,frequency,next_due)
                   VALUES(?,?,?,?,?,?,?)""",
                (description, amount, category, subcategory, payment_mode, frequency, next_due)
            )
            rid = cur.lastrowid
            await c.commit()
        return {"status": "success", "id": rid, "message": f"Recurring #{rid} added."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def list_recurring(active_only: bool = True):
    """List all recurring expense templates."""
    query = "SELECT * FROM recurring"
    if active_only:
        query += " WHERE active=1"
    query += " ORDER BY next_due"
    try:
        return await _rows(DB_PATH, query)
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def log_recurring(recurring_id: int, date_override: str = ""):
    """Post an actual expense entry from a recurring template and advance next_due.

    Args:
        recurring_id: ID of the recurring template
        date_override: YYYY-MM-DD to use instead of today
    """
    from datetime import timedelta
    import calendar

    use_date = date_override or date.today().isoformat()
    try:
        rows = await _rows(DB_PATH,
            "SELECT * FROM recurring WHERE id=?", (recurring_id,))
        if not rows:
            return {"status": "error", "message": "Recurring not found."}
        r = rows[0]

        # Advance next_due
        nd = datetime.strptime(r["next_due"], "%Y-%m-%d").date()
        if r["frequency"] == "weekly":
            nd += timedelta(weeks=1)
        elif r["frequency"] == "monthly":
            m = nd.month + 1
            y = nd.year + (1 if m > 12 else 0)
            m = m if m <= 12 else m - 12
            last = calendar.monthrange(y, m)[1]
            nd = nd.replace(year=y, month=m, day=min(nd.day, last))
        elif r["frequency"] == "yearly":
            nd = nd.replace(year=nd.year + 1)

        async with aiosqlite.connect(DB_PATH) as c:
            cur = await c.execute(
                """INSERT INTO expenses(date,amount,category,subcategory,note,payment_mode)
                   VALUES(?,?,?,?,?,?)""",
                (use_date, r["amount"], r["category"], r["subcategory"],
                 r["description"], r["payment_mode"])
            )
            eid = cur.lastrowid
            await c.execute(
                "UPDATE recurring SET next_due=? WHERE id=?",
                (nd.isoformat(), recurring_id)
            )
            await c.commit()
        return {"status": "success", "expense_id": eid, "next_due": nd.isoformat()}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def delete_recurring(recurring_id: int):
    """Deactivate (soft-delete) a recurring template."""
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            await c.execute(
                "UPDATE recurring SET active=0 WHERE id=?", (recurring_id,))
            await c.commit()
        return {"status": "success", "message": f"Recurring #{recurring_id} deactivated."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ===========================================================================
# TOOLS – Stats & Insights
# ===========================================================================

@mcp.tool()
async def expense_stats(start_date: str, end_date: str):
    """Descriptive statistics for expenses in a date range.

    Returns: total, count, average, median, min, max, std_dev,
             active days, avg per day.
    """
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            cur = await c.execute(
                "SELECT amount FROM expenses WHERE date BETWEEN ? AND ? ORDER BY amount",
                (start_date, end_date)
            )
            amounts = [r[0] for r in await cur.fetchall()]

        if not amounts:
            return {"status": "ok", "message": "No expenses found.", "count": 0}

        n = len(amounts)
        total = sum(amounts)
        mean = total / n
        median = (amounts[n // 2] if n % 2 else (amounts[n // 2 - 1] + amounts[n // 2]) / 2)
        variance = sum((x - mean) ** 2 for x in amounts) / n
        std_dev = variance ** 0.5

        # Days between dates
        d1 = datetime.strptime(start_date, "%Y-%m-%d").date()
        d2 = datetime.strptime(end_date, "%Y-%m-%d").date()
        total_days = max((d2 - d1).days + 1, 1)

        return {
            "total": round(total, 2),
            "count": n,
            "average": round(mean, 2),
            "median": round(median, 2),
            "min": round(amounts[0], 2),
            "max": round(amounts[-1], 2),
            "std_dev": round(std_dev, 2),
            "total_days": total_days,
            "avg_per_day": round(total / total_days, 2)
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def daily_breakdown(start_date: str, end_date: str, category: str = ""):
    """Day-by-day spending totals in a range."""
    query = """
        SELECT date, SUM(amount) AS total, COUNT(*) AS txns
        FROM expenses WHERE date BETWEEN ? AND ?
    """
    params: list = [start_date, end_date]
    if category:
        query += " AND category=?"; params.append(category)
    query += " GROUP BY date ORDER BY date"
    try:
        return await _rows(DB_PATH, query, params)
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ===========================================================================
# RESOURCES
# ===========================================================================

@mcp.resource("expense:///categories", mime_type="application/json")
def categories():
    """All available expense categories and their subcategories."""
    try:
        with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return json.dumps({
            "food": ["groceries","dining_out","other"],
            "transport": ["fuel","public_transport","cab_ride_hailing","other"],
            "housing": ["rent","maintenance","other"],
            "utilities": ["electricity","internet","mobile_phone","other"],
            "health": ["medicines","doctor","other"],
            "entertainment": ["movies","streaming","other"],
            "shopping": ["clothing","electronics","other"],
            "misc": ["uncategorized","other"]
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.resource("expense:///summary/today", mime_type="application/json")
async def summary_today():
    """Quick summary of today's expenses."""
    today = date.today().isoformat()
    try:
        rows = await _rows(DB_PATH, """
            SELECT category, SUM(amount) AS total, COUNT(*) AS txns
            FROM expenses WHERE date=?
            GROUP BY category ORDER BY total DESC
        """, (today,))
        cur_total = sum(r["total"] for r in rows)
        return json.dumps({"date": today, "total": round(cur_total, 2), "by_category": rows}, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.resource("expense:///summary/this_month", mime_type="application/json")
async def summary_this_month():
    """Quick summary of expenses in the current calendar month."""
    import calendar
    today = date.today()
    start = f"{today.year:04d}-{today.month:02d}-01"
    last  = calendar.monthrange(today.year, today.month)[1]
    end   = f"{today.year:04d}-{today.month:02d}-{last:02d}"
    m     = f"{today.year:04d}-{today.month:02d}"
    try:
        rows = await _rows(DB_PATH, """
            SELECT category, SUM(amount) AS total, COUNT(*) AS txns
            FROM expenses WHERE date BETWEEN ? AND ?
            GROUP BY category ORDER BY total DESC
        """, (start, end))
        cur_total = sum(r["total"] for r in rows)

        # Budget status
        budgets = await _rows(DB_PATH,
            "SELECT category, amount AS budget FROM budgets WHERE month=?", (m,))
        budget_map = {b["category"]: b["budget"] for b in budgets}
        for r in rows:
            if r["category"] in budget_map:
                b = budget_map[r["category"]]
                r["budget"] = b
                r["remaining"] = round(b - r["total"], 2)
                r["pct_used"] = round(r["total"] / b * 100, 1)

        return json.dumps({
            "month": m,
            "total": round(cur_total, 2),
            "days_into_month": today.day,
            "by_category": rows
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.resource("expense:///recurring/due_soon", mime_type="application/json")
async def recurring_due_soon():
    """Recurring expenses due within the next 7 days."""
    today = date.today()
    from datetime import timedelta
    cutoff = (today + timedelta(days=7)).isoformat()
    try:
        rows = await _rows(DB_PATH, """
            SELECT * FROM recurring
            WHERE active=1 AND next_due <= ?
            ORDER BY next_due
        """, (cutoff,))
        return json.dumps({"as_of": today.isoformat(), "due_within_7_days": rows}, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.resource("expense:///budgets/status", mime_type="application/json")
async def budgets_status():
    """Budget vs actual for the current month."""
    import calendar
    today = date.today()
    m = f"{today.year:04d}-{today.month:02d}"
    last = calendar.monthrange(today.year, today.month)[1]
    start, end = f"{m}-01", f"{m}-{last:02d}"
    try:
        budgets = await _rows(DB_PATH,
            "SELECT category, amount AS budget FROM budgets WHERE month=?", (m,))
        result = []
        async with aiosqlite.connect(DB_PATH) as c:
            for b in budgets:
                cur = await c.execute(
                    "SELECT COALESCE(SUM(amount),0) FROM expenses WHERE date BETWEEN ? AND ? AND category=?",
                    (start, end, b["category"])
                )
                spent = (await cur.fetchone())[0]
                result.append({
                    "category": b["category"],
                    "budget": b["budget"],
                    "spent": round(spent, 2),
                    "remaining": round(b["budget"] - spent, 2),
                    "pct_used": round(spent / b["budget"] * 100, 1) if b["budget"] > 0 else None,
                    "over_budget": spent > b["budget"]
                })
        return json.dumps({"month": m, "budgets": result}, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.resource("expense:///stats/all_time", mime_type="application/json")
async def stats_all_time():
    """High-level all-time statistics."""
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            cur = await c.execute("""
                SELECT
                    COUNT(*) AS total_txns,
                    COALESCE(SUM(amount),0) AS total_spent,
                    COALESCE(AVG(amount),0) AS avg_txn,
                    COALESCE(MIN(amount),0) AS min_txn,
                    COALESCE(MAX(amount),0) AS max_txn,
                    MIN(date) AS first_date,
                    MAX(date) AS last_date
                FROM expenses
            """)
            row = await cur.fetchone()
            cols = [d[0] for d in cur.description]
            stats = dict(zip(cols, row))

            cur = await c.execute("""
                SELECT category, SUM(amount) AS total
                FROM expenses GROUP BY category ORDER BY total DESC LIMIT 5
            """)
            top_cats = [{"category": r[0], "total": r[1]} for r in await cur.fetchall()]
            stats["top_5_categories"] = top_cats

        return json.dumps(stats, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ===========================================================================
# Run
# ===========================================================================

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
