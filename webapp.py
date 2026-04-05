from __future__ import annotations

import json
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client


SESSION_COOKIE = "expense_tracker_session"
SESSION_TTL_DAYS = 30
OTP_TTL_MINUTES = 10
PHONE_RE = re.compile(r"^\+?[1-9]\d{9,14}$")


def register_web_routes(mcp: Any, db_path: str, categories_path: str) -> None:
    base_dir = Path(__file__).parent / "web"
    index_path = base_dir / "index.html"
    css_path = base_dir / "styles.css"
    js_path = base_dir / "app.js"

    async def fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        async with aiosqlite.connect(db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def fetch_one(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = await fetch_all(query, params)
        return rows[0] if rows else None

    async def execute(query: str, params: tuple[Any, ...] = ()) -> int:
        async with aiosqlite.connect(db_path) as conn:
            cursor = await conn.execute(query, params)
            await conn.commit()
            return cursor.rowcount

    async def insert(query: str, params: tuple[Any, ...] = ()) -> int:
        async with aiosqlite.connect(db_path) as conn:
            cursor = await conn.execute(query, params)
            await conn.commit()
            return int(cursor.lastrowid)

    def json_error(message: str, status_code: int = 400) -> JSONResponse:
        return JSONResponse({"ok": False, "error": message}, status_code=status_code)

    def utc_now() -> datetime:
        return datetime.now(timezone.utc)

    def iso_now() -> str:
        return utc_now().isoformat(timespec="seconds")

    def normalize_phone(phone: str) -> str:
        phone = "".join(ch for ch in phone.strip() if ch in "+0123456789")
        if not PHONE_RE.match(phone):
            raise ValueError("Enter a valid phone number with country code when possible.")
        return phone

    def google_sign_in_enabled() -> bool:
        return bool(os.getenv("GOOGLE_CLIENT_ID", "").strip())

    def get_google_client_id() -> str:
        return os.getenv("GOOGLE_CLIENT_ID", "").strip()

    def verify_google_credential(credential: str) -> dict[str, Any]:
        idinfo = google_id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            get_google_client_id(),
        )
        if idinfo.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
            raise ValueError("Google token issuer is not valid.")
        return idinfo

    def twilio_verify_enabled() -> bool:
        service_sid = os.getenv("TWILIO_VERIFY_SERVICE_SID", "").strip()
        account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
        auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
        api_key_sid = os.getenv("TWILIO_API_KEY_SID", "").strip()
        api_key_secret = os.getenv("TWILIO_API_KEY_SECRET", "").strip()
        has_auth_token_credentials = bool(account_sid and auth_token)
        has_api_key_credentials = bool(account_sid and api_key_sid and api_key_secret)
        return bool(service_sid and (has_auth_token_credentials or has_api_key_credentials))

    def get_twilio_client() -> Client:
        account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
        auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
        api_key_sid = os.getenv("TWILIO_API_KEY_SID", "").strip()
        api_key_secret = os.getenv("TWILIO_API_KEY_SECRET", "").strip()
        if api_key_sid and api_key_secret and account_sid:
            return Client(api_key_sid, api_key_secret, account_sid)
        return Client(account_sid, auth_token)

    def get_twilio_service_sid() -> str:
        return os.getenv("TWILIO_VERIFY_SERVICE_SID", "").strip()

    def month_bounds(month: str | None = None) -> tuple[str, str, str]:
        if month:
            year, month_num = map(int, month.split("-"))
            current = date(year, month_num, 1)
        else:
            today = date.today()
            current = date(today.year, today.month, 1)
        next_month = date(current.year + (1 if current.month == 12 else 0), 1 if current.month == 12 else current.month + 1, 1)
        last_day = next_month - timedelta(days=1)
        month_key = f"{current.year:04d}-{current.month:02d}"
        return month_key, current.isoformat(), last_day.isoformat()

    async def get_user_from_request(request: Request) -> dict[str, Any] | None:
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return None
        return await fetch_one(
            """
            SELECT u.*
            FROM app_sessions s
            JOIN app_users u ON u.id = s.user_id
            WHERE s.token = ? AND s.expires_at > ?
            """,
            (token, iso_now()),
        )

    async def require_user(request: Request) -> dict[str, Any] | JSONResponse:
        user = await get_user_from_request(request)
        if not user:
            return json_error("Please sign in to continue.", 401)
        return user

    def with_session(response: JSONResponse, token: str, expires_at: str) -> JSONResponse:
        expires = datetime.fromisoformat(expires_at)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            samesite="lax",
            secure=False,
            expires=expires,
            path="/",
        )
        return response

    async def read_json(request: Request) -> dict[str, Any]:
        try:
            return await request.json()
        except json.JSONDecodeError:
            raise ValueError("Invalid JSON payload.")

    def compute_next_due(current_due: str, frequency: str) -> str:
        due = datetime.strptime(current_due, "%Y-%m-%d").date()
        if frequency == "weekly":
            return (due + timedelta(days=7)).isoformat()
        if frequency == "yearly":
            return due.replace(year=due.year + 1).isoformat()

        month = due.month + 1
        year = due.year + (1 if month > 12 else 0)
        month = 1 if month > 12 else month
        while True:
            try:
                return due.replace(year=year, month=month).isoformat()
            except ValueError:
                due -= timedelta(days=1)

    async def get_dashboard_payload(user_id: int, month: str | None = None) -> dict[str, Any]:
        month_key, start_date, end_date = month_bounds(month)

        totals = await fetch_one(
            """
            SELECT COALESCE(SUM(amount), 0) AS spent, COUNT(*) AS transactions
            FROM app_expenses
            WHERE user_id = ? AND date BETWEEN ? AND ?
            """,
            (user_id, start_date, end_date),
        ) or {"spent": 0, "transactions": 0}

        budgets = await fetch_all(
            """
            SELECT b.id, b.category, b.amount AS budget,
                   COALESCE(SUM(e.amount), 0) AS spent
            FROM app_budgets b
            LEFT JOIN app_expenses e
                ON e.user_id = b.user_id
               AND e.category = b.category
               AND e.date BETWEEN ? AND ?
            WHERE b.user_id = ? AND b.month = ?
            GROUP BY b.id, b.category, b.amount
            ORDER BY spent DESC, b.category ASC
            """,
            (start_date, end_date, user_id, month_key),
        )

        categories = await fetch_all(
            """
            SELECT category, SUM(amount) AS total
            FROM app_expenses
            WHERE user_id = ? AND date BETWEEN ? AND ?
            GROUP BY category
            ORDER BY total DESC
            LIMIT 6
            """,
            (user_id, start_date, end_date),
        )

        trend = await fetch_all(
            """
            SELECT date, SUM(amount) AS total
            FROM app_expenses
            WHERE user_id = ? AND date BETWEEN ? AND ?
            GROUP BY date
            ORDER BY date ASC
            """,
            (user_id, start_date, end_date),
        )

        recent = await fetch_all(
            """
            SELECT id, date, amount, category, subcategory, note, payment_mode, currency
            FROM app_expenses
            WHERE user_id = ?
            ORDER BY date DESC, id DESC
            LIMIT 8
            """,
            (user_id,),
        )

        recurring_due = await fetch_all(
            """
            SELECT id, description, amount, category, next_due, frequency
            FROM app_recurring
            WHERE user_id = ? AND active = 1 AND next_due <= ?
            ORDER BY next_due ASC
            LIMIT 6
            """,
            (user_id, (date.today() + timedelta(days=14)).isoformat()),
        )

        total_budget = sum(float(item["budget"]) for item in budgets)
        spent = round(float(totals["spent"] or 0), 2)
        monthly_income = float((await fetch_one("SELECT monthly_income FROM app_users WHERE id = ?", (user_id,)))["monthly_income"])

        budget_left = round(total_budget - spent, 2)
        savings_left = round(max(monthly_income - spent, 0), 2)

        highlights: list[str] = []
        if categories:
            highlights.append(f"Top spend this month is {categories[0]['category']} at {round(float(categories[0]['total']), 2)}.")
        over_budget = [item for item in budgets if float(item["spent"]) > float(item["budget"])]
        if over_budget:
            highlights.append(f"{over_budget[0]['category']} is over budget by {round(float(over_budget[0]['spent']) - float(over_budget[0]['budget']), 2)}.")
        elif total_budget:
            highlights.append(f"You still have {budget_left:.2f} left across active budgets.")
        if monthly_income:
            highlights.append(f"Estimated room before income limit: {savings_left:.2f}.")

        for budget in budgets:
            budget["spent"] = round(float(budget["spent"]), 2)
            budget["budget"] = round(float(budget["budget"]), 2)
            budget["remaining"] = round(float(budget["budget"]) - float(budget["spent"]), 2)
            budget["pct_used"] = round((float(budget["spent"]) / float(budget["budget"]) * 100), 1) if float(budget["budget"]) else 0

        return {
            "month": month_key,
            "stats": {
                "spent": spent,
                "transactions": int(totals["transactions"] or 0),
                "budgeted": round(total_budget, 2),
                "budget_left": budget_left,
                "monthly_income": monthly_income,
                "savings_left": savings_left,
            },
            "categories": [{"category": item["category"], "total": round(float(item["total"]), 2)} for item in categories],
            "trend": [{"date": item["date"], "total": round(float(item["total"]), 2)} for item in trend],
            "budgets": budgets,
            "recent_expenses": recent,
            "recurring_due": recurring_due,
            "highlights": highlights,
        }

    @mcp.custom_route("/", methods=["GET"])
    async def web_home(request: Request) -> HTMLResponse:
        return HTMLResponse(index_path.read_text(encoding="utf-8"))

    @mcp.custom_route("/styles.css", methods=["GET"])
    async def web_styles(request: Request) -> FileResponse:
        return FileResponse(css_path)

    @mcp.custom_route("/app.js", methods=["GET"])
    async def web_script(request: Request) -> FileResponse:
        return FileResponse(js_path, media_type="application/javascript")

    @mcp.custom_route("/api/health", methods=["GET"])
    async def api_health(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "service": "ExpenseTracker", "mcp_path": "/mcp"})

    @mcp.custom_route("/api/categories", methods=["GET"])
    async def api_categories(request: Request) -> JSONResponse:
        with open(categories_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return JSONResponse({"ok": True, "categories": payload})

    @mcp.custom_route("/api/public-config", methods=["GET"])
    async def api_public_config(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "auth": {
                    "mode": "google" if google_sign_in_enabled() else "disabled",
                    "google_client_id": get_google_client_id(),
                },
                "mcp_url": "https://academic-gold-weasel.fastmcp.app/mcp",
            }
        )

    @mcp.custom_route("/api/auth/google", methods=["POST"])
    async def auth_google(request: Request) -> JSONResponse:
        if not google_sign_in_enabled():
            return json_error("Google sign-in is not configured yet.", 503)

        try:
            payload = await read_json(request)
            credential = str(payload.get("credential", "")).strip()
            if not credential:
                return json_error("Missing Google credential.")
            google_user = verify_google_credential(credential)
        except ValueError as exc:
            return json_error(str(exc), 401)
        except Exception as exc:
            return json_error(f"Google sign-in failed: {exc}", 401)

        google_sub = str(google_user.get("sub", "")).strip()
        email = str(google_user.get("email", "")).strip()
        full_name = str(google_user.get("name", "")).strip() or "Google User"
        avatar_url = str(google_user.get("picture", "")).strip()
        if not google_sub:
            return json_error("Google account identifier is missing.", 401)

        user = await fetch_one("SELECT * FROM app_users WHERE google_sub = ?", (google_sub,))
        if user is None and email:
            user = await fetch_one("SELECT * FROM app_users WHERE email = ?", (email,))

        if user is None:
            synthetic_phone = f"google:{google_sub}"
            user_id = await insert(
                """
                INSERT INTO app_users(
                    phone, full_name, city, currency, monthly_income, savings_goal,
                    created_at, last_login_at, email, avatar_url, google_sub, auth_provider
                )
                VALUES (?, ?, '', 'INR', 0, 0, ?, ?, ?, ?, ?, 'google')
                """,
                (synthetic_phone, full_name, iso_now(), iso_now(), email, avatar_url, google_sub),
            )
            user = await fetch_one("SELECT * FROM app_users WHERE id = ?", (user_id,))
        else:
            await execute(
                """
                UPDATE app_users
                SET full_name = ?, email = ?, avatar_url = ?, google_sub = ?, auth_provider = 'google', last_login_at = ?
                WHERE id = ?
                """,
                (full_name or user.get("full_name", ""), email, avatar_url, google_sub, iso_now(), int(user["id"])),
            )
            user = await fetch_one("SELECT * FROM app_users WHERE id = ?", (int(user["id"]),))

        token = secrets.token_urlsafe(32)
        expires_at = (utc_now() + timedelta(days=SESSION_TTL_DAYS)).isoformat(timespec="seconds")
        await insert(
            "INSERT INTO app_sessions(token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, int(user["id"]), expires_at),
        )

        response = JSONResponse(
            {
                "ok": True,
                "user": user,
                "onboarding_required": not bool(user.get("full_name")) or float(user.get("monthly_income") or 0) == 0,
            }
        )
        return with_session(response, token, expires_at)

    @mcp.custom_route("/api/auth/send-otp", methods=["POST"])
    async def auth_send_otp(request: Request) -> JSONResponse:
        try:
            payload = await read_json(request)
            phone = normalize_phone(payload.get("phone", ""))
        except ValueError as exc:
            return json_error(str(exc))

        existing = await fetch_one("SELECT id FROM app_users WHERE phone = ?", (phone,))

        if twilio_verify_enabled():
            try:
                client = get_twilio_client()
                verification = client.verify.v2.services(get_twilio_service_sid()).verifications.create(
                    to=phone,
                    channel="sms",
                )
                return JSONResponse(
                    {
                        "ok": True,
                        "message": "OTP sent via SMS.",
                        "delivery": "twilio",
                        "verification_status": getattr(verification, "status", "pending"),
                        "is_new_user": existing is None,
                    }
                )
            except TwilioRestException as exc:
                return json_error(
                    f"Twilio could not send the OTP. On a trial account, verify the destination phone number first. Details: {exc.msg}",
                    502,
                )
            except Exception as exc:
                return json_error(f"OTP provider error: {exc}", 502)

        code = f"{secrets.randbelow(900000) + 100000:06d}"
        expires_at = (utc_now() + timedelta(minutes=OTP_TTL_MINUTES)).isoformat(timespec="seconds")
        await insert(
            "INSERT INTO app_otp_codes(phone, code, expires_at) VALUES (?, ?, ?)",
            (phone, code, expires_at),
        )

        return JSONResponse(
            {
                "ok": True,
                "message": "OTP generated successfully in demo mode.",
                "delivery": "demo",
                "demo_code": code,
                "expires_in_seconds": OTP_TTL_MINUTES * 60,
                "is_new_user": existing is None,
            }
        )

    @mcp.custom_route("/api/auth/verify-otp", methods=["POST"])
    async def auth_verify_otp(request: Request) -> JSONResponse:
        try:
            payload = await read_json(request)
            phone = normalize_phone(payload.get("phone", ""))
            code = str(payload.get("code", "")).strip()
            full_name = str(payload.get("full_name", "")).strip()
        except ValueError as exc:
            return json_error(str(exc))

        if not re.fullmatch(r"\d{6}", code):
            return json_error("OTP must be a 6 digit code.")

        otp = None
        if twilio_verify_enabled():
            try:
                client = get_twilio_client()
                verification_check = client.verify.v2.services(get_twilio_service_sid()).verification_checks.create(
                    to=phone,
                    code=code,
                )
                if getattr(verification_check, "status", "") != "approved":
                    return json_error("Invalid or expired OTP.", 401)
            except TwilioRestException as exc:
                return json_error(f"Twilio could not verify the OTP. Details: {exc.msg}", 502)
            except Exception as exc:
                return json_error(f"OTP verification error: {exc}", 502)
        else:
            otp = await fetch_one(
                """
                SELECT id, phone, code
                FROM app_otp_codes
                WHERE phone = ? AND code = ? AND consumed_at IS NULL AND expires_at > ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (phone, code, iso_now()),
            )
            if not otp:
                return json_error("Invalid or expired OTP.", 401)

        user = await fetch_one("SELECT * FROM app_users WHERE phone = ?", (phone,))
        if user is None:
            user_id = await insert(
                """
                INSERT INTO app_users(phone, full_name, currency, monthly_income, savings_goal, city, last_login_at)
                VALUES (?, ?, 'INR', 0, 0, '', ?)
                """,
                (phone, full_name or "New Member", iso_now()),
            )
            user = await fetch_one("SELECT * FROM app_users WHERE id = ?", (user_id,))
        else:
            await execute("UPDATE app_users SET last_login_at = ? WHERE id = ?", (iso_now(), int(user["id"])))
            if full_name and not user.get("full_name"):
                await execute("UPDATE app_users SET full_name = ? WHERE id = ?", (full_name, int(user["id"])))
            user = await fetch_one("SELECT * FROM app_users WHERE id = ?", (int(user["id"]),))

        if otp is not None:
            await execute("UPDATE app_otp_codes SET consumed_at = ? WHERE id = ?", (iso_now(), int(otp["id"])))
        token = secrets.token_urlsafe(32)
        expires_at = (utc_now() + timedelta(days=SESSION_TTL_DAYS)).isoformat(timespec="seconds")
        await insert(
            "INSERT INTO app_sessions(token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, int(user["id"]), expires_at),
        )

        response = JSONResponse(
            {
                "ok": True,
                "user": user,
                "onboarding_required": not bool(user.get("full_name")) or float(user.get("monthly_income") or 0) == 0,
            }
        )
        return with_session(response, token, expires_at)

    @mcp.custom_route("/api/auth/logout", methods=["POST"])
    async def auth_logout(request: Request) -> JSONResponse:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            await execute("DELETE FROM app_sessions WHERE token = ?", (token,))
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @mcp.custom_route("/api/me", methods=["GET"])
    async def api_me(request: Request) -> JSONResponse:
        user = await require_user(request)
        if isinstance(user, JSONResponse):
            return user
        return JSONResponse({"ok": True, "user": user})

    @mcp.custom_route("/api/profile", methods=["POST"])
    async def api_profile(request: Request) -> JSONResponse:
        user = await require_user(request)
        if isinstance(user, JSONResponse):
            return user
        try:
            payload = await read_json(request)
            full_name = str(payload.get("full_name", user["full_name"])).strip()[:80]
            city = str(payload.get("city", user["city"])).strip()[:80]
            currency = str(payload.get("currency", user["currency"] or "INR")).strip().upper()[:8] or "INR"
            monthly_income = float(payload.get("monthly_income", user["monthly_income"] or 0) or 0)
            savings_goal = float(payload.get("savings_goal", user["savings_goal"] or 0) or 0)
        except (ValueError, TypeError):
            return json_error("Profile values are not valid.")

        await execute(
            """
            UPDATE app_users
            SET full_name = ?, city = ?, currency = ?, monthly_income = ?, savings_goal = ?
            WHERE id = ?
            """,
            (full_name, city, currency, monthly_income, savings_goal, int(user["id"])),
        )
        updated = await fetch_one("SELECT * FROM app_users WHERE id = ?", (int(user["id"]),))
        return JSONResponse({"ok": True, "user": updated})

    @mcp.custom_route("/api/dashboard", methods=["GET"])
    async def api_dashboard(request: Request) -> JSONResponse:
        user = await require_user(request)
        if isinstance(user, JSONResponse):
            return user
        month = request.query_params.get("month")
        payload = await get_dashboard_payload(int(user["id"]), month)
        payload["ok"] = True
        payload["user"] = user
        return JSONResponse(payload)

    @mcp.custom_route("/api/expenses", methods=["GET"])
    async def api_expenses_list(request: Request) -> JSONResponse:
        user = await require_user(request)
        if isinstance(user, JSONResponse):
            return user

        month = request.query_params.get("month")
        category = request.query_params.get("category", "").strip()
        search = request.query_params.get("search", "").strip()
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        if not start_date or not end_date:
            _, start_date, end_date = month_bounds(month)

        query = """
            SELECT id, date, amount, category, subcategory, note, tags, payment_mode, currency
            FROM app_expenses
            WHERE user_id = ? AND date BETWEEN ? AND ?
        """
        params: list[Any] = [int(user["id"]), start_date, end_date]
        if category:
            query += " AND category = ?"
            params.append(category)
        if search:
            query += " AND (note LIKE ? OR subcategory LIKE ? OR category LIKE ? OR tags LIKE ?)"
            wildcard = f"%{search}%"
            params.extend([wildcard, wildcard, wildcard, wildcard])
        query += " ORDER BY date DESC, id DESC LIMIT 200"
        rows = await fetch_all(query, tuple(params))
        return JSONResponse({"ok": True, "expenses": rows})

    @mcp.custom_route("/api/expenses", methods=["POST"])
    async def api_expenses_create(request: Request) -> JSONResponse:
        user = await require_user(request)
        if isinstance(user, JSONResponse):
            return user
        try:
            payload = await read_json(request)
            expense_date = str(payload.get("date", date.today().isoformat())).strip()
            amount = float(payload.get("amount", 0))
            category = str(payload.get("category", "")).strip()
            subcategory = str(payload.get("subcategory", "")).strip()
            note = str(payload.get("note", "")).strip()
            tags = str(payload.get("tags", "")).strip()
            payment_mode = str(payload.get("payment_mode", "")).strip()
            currency = str(payload.get("currency", user["currency"] or "INR")).strip().upper()[:8] or "INR"
        except (TypeError, ValueError):
            return json_error("Expense payload is not valid.")

        if amount <= 0:
            return json_error("Amount must be greater than zero.")
        if not category:
            return json_error("Category is required.")

        expense_id = await insert(
            """
            INSERT INTO app_expenses(user_id, date, amount, category, subcategory, note, tags, payment_mode, currency)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (int(user["id"]), expense_date, amount, category, subcategory, note, tags, payment_mode, currency),
        )
        expense = await fetch_one("SELECT * FROM app_expenses WHERE id = ?", (expense_id,))
        return JSONResponse({"ok": True, "expense": expense})

    @mcp.custom_route("/api/expenses/{expense_id}", methods=["PATCH"])
    async def api_expenses_update(request: Request) -> JSONResponse:
        user = await require_user(request)
        if isinstance(user, JSONResponse):
            return user
        expense_id = int(request.path_params["expense_id"])
        existing = await fetch_one("SELECT * FROM app_expenses WHERE id = ? AND user_id = ?", (expense_id, int(user["id"])))
        if not existing:
            return json_error("Expense not found.", 404)
        try:
            payload = await read_json(request)
            expense_date = str(payload.get("date", existing["date"])).strip()
            amount = float(payload.get("amount", existing["amount"]))
            category = str(payload.get("category", existing["category"])).strip()
            subcategory = str(payload.get("subcategory", existing["subcategory"])).strip()
            note = str(payload.get("note", existing["note"])).strip()
            tags = str(payload.get("tags", existing["tags"])).strip()
            payment_mode = str(payload.get("payment_mode", existing["payment_mode"])).strip()
            currency = str(payload.get("currency", existing["currency"])).strip().upper()[:8]
        except (TypeError, ValueError):
            return json_error("Expense payload is not valid.")

        await execute(
            """
            UPDATE app_expenses
            SET date = ?, amount = ?, category = ?, subcategory = ?, note = ?, tags = ?, payment_mode = ?, currency = ?
            WHERE id = ? AND user_id = ?
            """,
            (expense_date, amount, category, subcategory, note, tags, payment_mode, currency, expense_id, int(user["id"])),
        )
        expense = await fetch_one("SELECT * FROM app_expenses WHERE id = ?", (expense_id,))
        return JSONResponse({"ok": True, "expense": expense})

    @mcp.custom_route("/api/expenses/{expense_id}", methods=["DELETE"])
    async def api_expenses_delete(request: Request) -> JSONResponse:
        user = await require_user(request)
        if isinstance(user, JSONResponse):
            return user
        expense_id = int(request.path_params["expense_id"])
        count = await execute("DELETE FROM app_expenses WHERE id = ? AND user_id = ?", (expense_id, int(user["id"])))
        if not count:
            return json_error("Expense not found.", 404)
        return JSONResponse({"ok": True})

    @mcp.custom_route("/api/budgets", methods=["GET"])
    async def api_budgets_list(request: Request) -> JSONResponse:
        user = await require_user(request)
        if isinstance(user, JSONResponse):
            return user
        month = request.query_params.get("month")
        month_key, start_date, end_date = month_bounds(month)
        budgets = await fetch_all(
            """
            SELECT b.id, b.category, b.amount AS budget, COALESCE(SUM(e.amount), 0) AS spent
            FROM app_budgets b
            LEFT JOIN app_expenses e
                ON e.user_id = b.user_id
               AND e.category = b.category
               AND e.date BETWEEN ? AND ?
            WHERE b.user_id = ? AND b.month = ?
            GROUP BY b.id, b.category, b.amount
            ORDER BY b.category ASC
            """,
            (start_date, end_date, int(user["id"]), month_key),
        )
        for item in budgets:
            item["budget"] = round(float(item["budget"]), 2)
            item["spent"] = round(float(item["spent"]), 2)
            item["remaining"] = round(float(item["budget"]) - float(item["spent"]), 2)
        return JSONResponse({"ok": True, "month": month_key, "budgets": budgets})

    @mcp.custom_route("/api/budgets", methods=["POST"])
    async def api_budgets_upsert(request: Request) -> JSONResponse:
        user = await require_user(request)
        if isinstance(user, JSONResponse):
            return user
        try:
            payload = await read_json(request)
            month = str(payload.get("month", month_bounds()[0])).strip()
            category = str(payload.get("category", "")).strip()
            amount = float(payload.get("amount", 0))
        except (TypeError, ValueError):
            return json_error("Budget payload is not valid.")
        if not category or amount <= 0:
            return json_error("Category and positive amount are required.")
        await execute(
            """
            INSERT INTO app_budgets(user_id, month, category, amount)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, month, category) DO UPDATE SET amount = excluded.amount
            """,
            (int(user["id"]), month, category, amount),
        )
        row = await fetch_one("SELECT id, user_id, month, category, amount FROM app_budgets WHERE user_id = ? AND month = ? AND category = ?", (int(user["id"]), month, category))
        return JSONResponse({"ok": True, "budget": row})

    @mcp.custom_route("/api/budgets/{budget_id}", methods=["DELETE"])
    async def api_budgets_delete(request: Request) -> JSONResponse:
        user = await require_user(request)
        if isinstance(user, JSONResponse):
            return user
        budget_id = int(request.path_params["budget_id"])
        count = await execute("DELETE FROM app_budgets WHERE id = ? AND user_id = ?", (budget_id, int(user["id"])))
        if not count:
            return json_error("Budget not found.", 404)
        return JSONResponse({"ok": True})

    @mcp.custom_route("/api/recurring", methods=["GET"])
    async def api_recurring_list(request: Request) -> JSONResponse:
        user = await require_user(request)
        if isinstance(user, JSONResponse):
            return user
        rows = await fetch_all(
            """
            SELECT id, description, amount, category, subcategory, payment_mode, frequency, next_due, active
            FROM app_recurring
            WHERE user_id = ?
            ORDER BY active DESC, next_due ASC
            """,
            (int(user["id"]),),
        )
        return JSONResponse({"ok": True, "items": rows})

    @mcp.custom_route("/api/recurring", methods=["POST"])
    async def api_recurring_create(request: Request) -> JSONResponse:
        user = await require_user(request)
        if isinstance(user, JSONResponse):
            return user
        try:
            payload = await read_json(request)
            description = str(payload.get("description", "")).strip()
            amount = float(payload.get("amount", 0))
            category = str(payload.get("category", "")).strip()
            subcategory = str(payload.get("subcategory", "")).strip()
            payment_mode = str(payload.get("payment_mode", "")).strip()
            frequency = str(payload.get("frequency", "monthly")).strip().lower()
            next_due = str(payload.get("next_due", date.today().isoformat())).strip()
        except (TypeError, ValueError):
            return json_error("Recurring payload is not valid.")
        if frequency not in {"weekly", "monthly", "yearly"}:
            return json_error("Frequency must be weekly, monthly, or yearly.")
        if not description or amount <= 0 or not category:
            return json_error("Description, category, and positive amount are required.")
        item_id = await insert(
            """
            INSERT INTO app_recurring(user_id, description, amount, category, subcategory, payment_mode, frequency, next_due, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (int(user["id"]), description, amount, category, subcategory, payment_mode, frequency, next_due),
        )
        item = await fetch_one("SELECT * FROM app_recurring WHERE id = ?", (item_id,))
        return JSONResponse({"ok": True, "item": item})

    @mcp.custom_route("/api/recurring/{item_id}/log", methods=["POST"])
    async def api_recurring_log(request: Request) -> JSONResponse:
        user = await require_user(request)
        if isinstance(user, JSONResponse):
            return user
        item_id = int(request.path_params["item_id"])
        item = await fetch_one("SELECT * FROM app_recurring WHERE id = ? AND user_id = ?", (item_id, int(user["id"])))
        if not item:
            return json_error("Recurring item not found.", 404)
        payload = await read_json(request)
        log_date = str(payload.get("date", date.today().isoformat())).strip()
        expense_id = await insert(
            """
            INSERT INTO app_expenses(user_id, date, amount, category, subcategory, note, tags, payment_mode, currency)
            VALUES (?, ?, ?, ?, ?, ?, '', ?, ?)
            """,
            (
                int(user["id"]),
                log_date,
                float(item["amount"]),
                item["category"],
                item["subcategory"],
                item["description"],
                item["payment_mode"],
                user.get("currency") or "INR",
            ),
        )
        next_due = compute_next_due(item["next_due"], item["frequency"])
        await execute("UPDATE app_recurring SET next_due = ? WHERE id = ?", (next_due, item_id))
        expense = await fetch_one("SELECT * FROM app_expenses WHERE id = ?", (expense_id,))
        return JSONResponse({"ok": True, "expense": expense, "next_due": next_due})

    @mcp.custom_route("/api/recurring/{item_id}", methods=["DELETE"])
    async def api_recurring_delete(request: Request) -> JSONResponse:
        user = await require_user(request)
        if isinstance(user, JSONResponse):
            return user
        item_id = int(request.path_params["item_id"])
        count = await execute("UPDATE app_recurring SET active = 0 WHERE id = ? AND user_id = ?", (item_id, int(user["id"])))
        if not count:
            return json_error("Recurring item not found.", 404)
        return JSONResponse({"ok": True})
