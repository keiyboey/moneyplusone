import os
import re
import shutil
import sqlite3
import secrets
import threading
import time
import traceback
import requests
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, g, make_response
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "instance", "money_plus_one.db")
BACKUP_DIR = os.path.join(BASE_DIR, "instance", "backups")
POLLER_PID_FILE = os.path.join(BASE_DIR, "instance", "telegram_poller.pid")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("MPO_SECRET_KEY") or "CHANGE-THIS-SECRET-KEY-BEFORE-DEPLOYMENT"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0") == "1"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "templates"), exist_ok=True)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "9003883686:AAEboeJF9YsOPhiI3DYW8Ek7mDf8F1kc8Pw")
TELEGRAM_ADMIN_CHAT_ID = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "")

DEPOSIT_AMOUNTS = [
    3000, 5000, 10000, 15000, 25000, 40000, 60000,
    100000, 150000, 250000, 350000, 500000, 750000, 1000000, 1500000
]

MACHINES = [
    {"id": 1, "name": "MP+ Pro-1", "price": 3500, "daily_profit": 875, "hourly_profit": 36.46, "days": 90, "total_profit": 78750},
    {"id": 2, "name": "MP+ Pro-2", "price": 5500, "daily_profit": 1375, "hourly_profit": 57.29, "days": 90, "total_profit": 123750},
    {"id": 3, "name": "MP+ Pro-3", "price": 10500, "daily_profit": 2625, "hourly_profit": 109.38, "days": 90, "total_profit": 236250},
    {"id": 4, "name": "MP+ Pro-4", "price": 15500, "daily_profit": 3875, "hourly_profit": 161.46, "days": 90, "total_profit": 348750},
    {"id": 5, "name": "MP+ Pro-5", "price": 25500, "daily_profit": 6375, "hourly_profit": 265.63, "days": 90, "total_profit": 573750},
    {"id": 6, "name": "MP+ Pro-6", "price": 40500, "daily_profit": 10125, "hourly_profit": 421.88, "days": 90, "total_profit": 911250},
    {"id": 7, "name": "MP+ Pro-7", "price": 60500, "daily_profit": 15125, "hourly_profit": 630.21, "days": 90, "total_profit": 1361250},
    {"id": 8, "name": "MP+ Pro-8", "price": 100500, "daily_profit": 25125, "hourly_profit": 1046.88, "days": 90, "total_profit": 2261250},
    {"id": 9, "name": "MP+ Pro-9", "price": 150500, "daily_profit": 37625, "hourly_profit": 1567.71, "days": 90, "total_profit": 3386250},
    {"id": 10, "name": "MP+ Pro-10", "price": 250500, "daily_profit": 62625, "hourly_profit": 2609.38, "days": 90, "total_profit": 5636250},
    {"id": 11, "name": "MP+ Pro-11", "price": 350500, "daily_profit": 87625, "hourly_profit": 3651.04, "days": 90, "total_profit": 7886250},
    {"id": 12, "name": "MP+ Pro-12", "price": 500500, "daily_profit": 125125, "hourly_profit": 5213.54, "days": 90, "total_profit": 11261250},
    {"id": 13, "name": "MP+ Pro-13", "price": 750500, "daily_profit": 187625, "hourly_profit": 7817.71, "days": 90, "total_profit": 16886250},
    {"id": 14, "name": "MP+ Pro-14", "price": 1000500, "daily_profit": 250125, "hourly_profit": 10421.88, "days": 90, "total_profit": 22511250},
    {"id": 15, "name": "MP+ Pro-15", "price": 1500500, "daily_profit": 375125, "hourly_profit": 15630.21, "days": 90, "total_profit": 33761250},
]

def normalize_identifier(raw_value):
    val = (raw_value or "").strip()
    if "@" in val:
        return "email", val.lower()
    digits = re.sub(r"[^\d+]", "", val)
    if digits.startswith("+"):
        digits = "+" + re.sub(r"\D", "", digits[1:])
    else:
        digits = re.sub(r"\D", "", digits)
    return "phone", digits

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            phone_number TEXT DEFAULT '',
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            balance REAL NOT NULL DEFAULT 0.0,
            total_deposit REAL NOT NULL DEFAULT 0.0,
            total_income REAL NOT NULL DEFAULT 0.0,
            referral_code TEXT,
            referred_by INTEGER,
            referral_bonus_paid INTEGER NOT NULL DEFAULT 0,
            has_invested INTEGER NOT NULL DEFAULT 0,
            last_daily_claim TEXT,
            last_seen TEXT,
            account_status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cols = [r[1] for r in db.execute("PRAGMA table_info(users)").fetchall()]
    for col, ddl in [
        ("phone_number", "TEXT DEFAULT ''"),
        ("balance", "REAL NOT NULL DEFAULT 0.0"),
        ("total_deposit", "REAL NOT NULL DEFAULT 0.0"),
        ("total_income", "REAL NOT NULL DEFAULT 0.0"),
        ("referral_code", "TEXT"),
        ("referred_by", "INTEGER"),
        ("referral_bonus_paid", "INTEGER NOT NULL DEFAULT 0"),
        ("has_invested", "INTEGER NOT NULL DEFAULT 0"),
        ("last_daily_claim", "TEXT"),
        ("last_seen", "TEXT"),
        ("account_status", "TEXT NOT NULL DEFAULT 'active'"),
    ]:
        if col not in cols:
            db.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")

    db.execute("""
        CREATE TABLE IF NOT EXISTS user_machines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            machine_id INTEGER NOT NULL,
            machine_name TEXT NOT NULL,
            price REAL NOT NULL,
            daily_profit REAL NOT NULL,
            hourly_profit REAL NOT NULL,
            total_profit REAL NOT NULL,
            days INTEGER NOT NULL DEFAULT 90,
            hours_credited INTEGER NOT NULL DEFAULT 0,
            max_hours INTEGER NOT NULL DEFAULT 2160,
            purchased_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            sender_name TEXT NOT NULL DEFAULT 'Direct Transfer',
            transaction_ref TEXT DEFAULT '',
            screenshot_filename TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            bank_name TEXT NOT NULL,
            account_number TEXT NOT NULL,
            account_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'success',
            is_seen INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS support_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    defaults = {
        "bank_name": "OPay / Moniepoint",
        "account_number": "0000000000",
        "account_name": "MONEY PLUS ONE ENTERPRISES",
        "service_link": "https://t.me/moneyplusone_support",
        "group_link": "https://t.me/moneyplusone_group",
        "min_deposit": "3000",
        "min_withdrawal": "1500",
        "max_withdrawal": "1000000",
        "daily_check_in": "100",
        "maintenance_mode": "0",
        "telegram_admin_chat_id": TELEGRAM_ADMIN_CHAT_ID
    }
    for k, v in defaults.items():
        db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    db.execute("UPDATE settings SET value = '1500' WHERE key = 'min_withdrawal' AND value = '3000'")
    db.commit()
    db.close()

init_db()

def get_settings():
    rows = get_db().execute("SELECT key, value FROM settings").fetchall()
    data = {r["key"]: r["value"] for r in rows}
    if TELEGRAM_ADMIN_CHAT_ID and not data.get("telegram_admin_chat_id"):
        data["telegram_admin_chat_id"] = TELEGRAM_ADMIN_CHAT_ID
    return data

def get_admin_chat_id_standalone():
    c = sqlite3.connect(DB_PATH)
    row = c.execute("SELECT value FROM settings WHERE key = 'telegram_admin_chat_id'").fetchone()
    c.close()
    if row and row[0]:
        return row[0]
    return TELEGRAM_ADMIN_CHAT_ID

def add_user_notification(conn, user_id, message, category="success"):
    conn.execute(
        "INSERT INTO notifications (user_id, message, category, is_seen) VALUES (?, ?, ?, 0)",
        (user_id, message, category)
    )

def send_telegram_request(text, kind, record_id, chat_id=None):
    target_chat = chat_id or get_admin_chat_id_standalone()
    if not TELEGRAM_BOT_TOKEN or not target_chat:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": target_chat,
        "text": text,
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "✅ Approved", "callback_data": f"app:{kind}:{record_id}"},
                {"text": "❌ Reject", "callback_data": f"rej:{kind}:{record_id}"}
            ]]
        }
    }
    try:
        requests.post(url, json=payload, timeout=8)
    except Exception:
        pass

def send_telegram_simple(text, chat_id=None, reply_markup=None):
    target_chat = chat_id or get_admin_chat_id_standalone()
    if not TELEGRAM_BOT_TOKEN or not target_chat:
        return
    payload = {"chat_id": target_chat, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=8
        )
    except Exception:
        pass

def build_pending_counter_text():
    c = sqlite3.connect(DB_PATH)
    pending_dep = c.execute("SELECT COUNT(*) FROM deposits WHERE status = 'pending'").fetchone()[0]
    pending_wd = c.execute("SELECT COUNT(*) FROM withdrawals WHERE status = 'pending'").fetchone()[0]
    open_tickets = c.execute("SELECT COUNT(*) FROM support_tickets WHERE status = 'open'").fetchone()[0]
    c.close()
    return (
        "⏳ PENDING REQUESTS COUNTER\n"
        f"• Pending Deposits: {pending_dep}\n"
        f"• Pending Withdrawals: {pending_wd}\n"
        f"• Open Support Tickets: {open_tickets}"
    )

def build_admin_stats_text():
    c = sqlite3.connect(DB_PATH)
    total_members = c.execute("SELECT COUNT(*) FROM users WHERE role = 'user'").fetchone()[0]
    total_depositors = c.execute("""
        SELECT COUNT(DISTINCT d.user_id)
        FROM deposits d
        JOIN users u ON u.id = d.user_id
        WHERE u.role = 'user' AND d.status = 'approved'
    """).fetchone()[0]
    pending_dep = c.execute("SELECT COUNT(*) FROM deposits WHERE status = 'pending'").fetchone()[0]
    pending_wd = c.execute("SELECT COUNT(*) FROM withdrawals WHERE status = 'pending'").fetchone()[0]
    maint = c.execute("SELECT value FROM settings WHERE key = 'maintenance_mode'").fetchone()
    maint_state = "ON (Active)" if (maint and maint[0] == "1") else "OFF (Normal)"
    c.close()
    return (
        "📊 MONEY PLUS ONE — ADMIN PANEL STATS\n"
        f"• Total Members: {total_members}\n"
        f"• Total Depositors: {total_depositors}\n"
        f"• Pending Deposits: {pending_dep}\n"
        f"• Pending Withdrawals: {pending_wd}\n"
        f"• Maintenance Mode: {maint_state}"
    )

def build_daily_summary_text():
    c = sqlite3.connect(DB_PATH)
    today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new_users = c.execute(
        "SELECT COUNT(*) FROM users WHERE role = 'user' AND date(created_at) = ?",
        (today_prefix,)
    ).fetchone()[0]
    dep_approved_sum = c.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM deposits WHERE status = 'approved' AND date(created_at) = ?",
        (today_prefix,)
    ).fetchone()[0]
    wd_approved_sum = c.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM withdrawals WHERE status = 'approved' AND date(created_at) = ?",
        (today_prefix,)
    ).fetchone()[0]
    pending_dep = c.execute("SELECT COUNT(*) FROM deposits WHERE status = 'pending'").fetchone()[0]
    pending_wd = c.execute("SELECT COUNT(*) FROM withdrawals WHERE status = 'pending'").fetchone()[0]
    c.close()
    return (
        f"📅 DAILY SUMMARY ({today_prefix})\n"
        f"• New Users Today: {new_users}\n"
        f"• Approved Deposits Today: ₦{float(dep_approved_sum):,.2f}\n"
        f"• Approved Withdrawals Today: ₦{float(wd_approved_sum):,.2f}\n"
        f"• Still Pending: {pending_dep} Deposits | {pending_wd} Withdrawals"
    )

def format_user_line(r):
    status = r["status"] if "status" in r.keys() else (r["account_status"] or "active")
    phone = r["phone_number"] or "-"
    bal = float(r["balance"] or 0.0)
    dep = float(r["total_deposit"] or 0.0) if "total_deposit" in r.keys() else 0.0
    inc = float(r["total_income"] or 0.0) if "total_income" in r.keys() else 0.0
    ref = r["referral_code"] if "referral_code" in r.keys() and r["referral_code"] else "-"
    seen = r["last_seen"] if "last_seen" in r.keys() and r["last_seen"] else "-"
    return (
        f"Account ID: #{r['id']} | {r['full_name']} | {status.upper()}\n"
        f"Email: {r['email']} | Phone: {phone}\n"
        f"Balance: ₦{bal:,.2f} | Deposit: ₦{dep:,.2f} | Income: ₦{inc:,.2f}\n"
        f"Ref: {ref} | Seen: {seen}"
    )

def build_user_action_markup(rows_list, limit_rows=6):
    inline_rows = []
    for r in rows_list[:limit_rows]:
        st = (r["status"] or "active").lower()
        msg_btn = {"text": f"✉️ Msg #{r['id']}", "callback_data": f"uact:msg:{r['id']}"}
        bal_btn = {"text": f"💰 Bal #{r['id']}", "callback_data": f"uact:bal:{r['id']}"}
        toggle_btn = (
            {"text": f"✅ Unsuspend #{r['id']}", "callback_data": f"ust:unsuspend:{r['id']}"}
            if st == "suspended"
            else {"text": f"🚫 Suspend #{r['id']}", "callback_data": f"ust:suspend:{r['id']}"}
        )
        del_btn = {"text": f"🗑️ Delete #{r['id']}", "callback_data": f"udel:ask:{r['id']}"}
        inline_rows.append([msg_btn, bal_btn, toggle_btn, del_btn])
    return {"inline_keyboard": inline_rows} if inline_rows else None

def build_all_users_messages():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    rows = c.execute("""
        SELECT id, full_name, email, phone_number, balance, total_deposit, total_income,
               referral_code, last_seen, COALESCE(account_status, 'active') AS status
        FROM users WHERE role = 'user' ORDER BY id ASC
    """).fetchall()
    c.close()
    if not rows:
        return [("👥 No registered users found.", None)]

    chunks = []
    header = f"👥 ALL USERS ({len(rows)}):\n\n" + "\n\n".join(format_user_line(r) for r in rows[:12])
    markup = build_user_action_markup(rows, limit_rows=6)
    chunks.append((header, markup))

    for idx in range(12, len(rows), 12):
        part = "\n\n".join(format_user_line(r) for r in rows[idx:idx + 12])
        chunks.append((part, None))
    return chunks

def search_users_response(query_str):
    q = (query_str or "").strip()
    if not q:
        return "🔍 Type user ID, name, email, or phone number to search.", None
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    if q.isdigit():
        rows = c.execute("""
            SELECT id, full_name, email, phone_number, balance, total_deposit, total_income,
                   referral_code, last_seen, COALESCE(account_status, 'active') AS status
            FROM users WHERE role = 'user' AND (id = ? OR phone_number LIKE ?) LIMIT 10
        """, (int(q), f"%{q}%")).fetchall()
    else:
        like_q = f"%{q}%"
        rows = c.execute("""
            SELECT id, full_name, email, phone_number, balance, total_deposit, total_income,
                   referral_code, last_seen, COALESCE(account_status, 'active') AS status
            FROM users WHERE role = 'user' AND (full_name LIKE ? OR email LIKE ? OR phone_number LIKE ?) LIMIT 10
        """, (like_q, like_q, like_q)).fetchall()
    c.close()
    if not rows:
        return f"🔍 No user found matching: {q}", None

    lines = [f"🔍 SEARCH RESULTS ({q}):\n"] + [format_user_line(r) for r in rows]
    return "\n\n".join(lines), build_user_action_markup(rows, limit_rows=6)

def set_user_account_status(user_id, target_status):
    new_status = "suspended" if target_status == "suspend" else "active"
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    u = c.execute("SELECT id, full_name, COALESCE(account_status, 'active') AS status FROM users WHERE id = ? AND role = 'user'", (user_id,)).fetchone()
    if not u:
        c.close()
        return f"❌ User #{user_id} not found."
    c.execute("UPDATE users SET account_status = ? WHERE id = ?", (new_status, user_id))
    c.commit()
    c.close()
    if new_status == "suspended":
        return f"🚫 User #{user_id} ({u['full_name']}) is now SUSPENDED."
    return f"✅ User #{user_id} ({u['full_name']}) is now UNSUSPENDED (ACTIVE)."

def delete_user_completely(user_id):
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    u = c.execute("SELECT id, full_name, email FROM users WHERE id = ? AND role = 'user'", (user_id,)).fetchone()
    if not u:
        c.close()
        return f"❌ User #{user_id} not found."
    c.execute("UPDATE users SET referred_by = NULL WHERE referred_by = ?", (user_id,))
    c.execute("DELETE FROM user_machines WHERE user_id = ?", (user_id,))
    c.execute("DELETE FROM deposits WHERE user_id = ?", (user_id,))
    c.execute("DELETE FROM withdrawals WHERE user_id = ?", (user_id,))
    c.execute("DELETE FROM notifications WHERE user_id = ?", (user_id,))
    c.execute("DELETE FROM support_tickets WHERE user_id = ?", (user_id,))
    c.execute("DELETE FROM users WHERE id = ? AND role = 'user'", (user_id,))
    c.commit()
    c.close()
    return f"🗑️ User #{user_id} ({u['full_name']} | {u['email']}) and all related records have been permanently deleted."

def send_message_to_user(user_id, text_msg):
    clean_msg = (text_msg or "").strip()
    if not clean_msg:
        return "❌ Message text cannot be empty."
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    u = c.execute("SELECT id, full_name, email FROM users WHERE id = ? AND role = 'user'", (user_id,)).fetchone()
    if not u:
        c.close()
        return f"❌ User #{user_id} not found."
    add_user_notification(c, user_id, f"Admin Message: {clean_msg}", "success")
    c.commit()
    c.close()
    return f"✉️ Message sent to User #{user_id} ({u['full_name']}): {clean_msg}"

def broadcast_message_to_all_users(text_msg):
    clean_msg = (text_msg or "").strip()
    if not clean_msg:
        return "❌ Broadcast message cannot be empty."
    c = sqlite3.connect(DB_PATH)
    users = c.execute("SELECT id FROM users WHERE role = 'user'").fetchall()
    for r in users:
        add_user_notification(c, r[0], f"Announcement: {clean_msg}", "success")
    c.commit()
    c.close()
    return f"📢 Broadcast delivered to {len(users)} users."

def adjust_user_balance(user_id, delta_amount):
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    u = c.execute("SELECT id, full_name, balance, total_income FROM users WHERE id = ? AND role = 'user'", (user_id,)).fetchone()
    if not u:
        c.close()
        return f"❌ User #{user_id} not found."
    new_bal = round(float(u["balance"] or 0.0) + float(delta_amount), 2)
    new_inc = round(float(u["total_income"] or 0.0) + (float(delta_amount) if delta_amount > 0 else 0.0), 2)
    c.execute("UPDATE users SET balance = ?, total_income = ? WHERE id = ?", (new_bal, new_inc, user_id))
    action_label = f"credited ₦{delta_amount:,.2f}" if delta_amount >= 0 else f"debited ₦{abs(delta_amount):,.2f}"
    add_user_notification(c, user_id, f"Wallet Update: Admin {action_label}. New balance: ₦{new_bal:,.2f}.", "success")
    c.commit()
    c.close()
    return f"💰 User #{user_id} ({u['full_name']}) {action_label}. New Balance: ₦{new_bal:,.2f}"

def build_open_tickets_text():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    rows = c.execute("""
        SELECT t.id, t.subject, t.message, t.status, t.created_at, u.full_name, u.email
        FROM support_tickets t JOIN users u ON t.user_id = u.id
        WHERE t.status = 'open' ORDER BY t.id DESC LIMIT 10
    """).fetchall()
    c.close()
    if not rows:
        return "✅ No open support tickets right now."
    blocks = ["🎫 OPEN SUPPORT TICKETS:"]
    for t in rows:
        blocks.append(
            f"#{t['id']} | {t['full_name']} ({t['email']})\n"
            f"Subject: {t['subject']}\n"
            f"Message: {t['message']}\n"
            f"Close: /close_ticket {t['id']}"
        )
    return "\n\n".join(blocks)

def close_support_ticket(ticket_id):
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    t = c.execute("SELECT id, user_id, subject, status FROM support_tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not t:
        c.close()
        return f"❌ Ticket #{ticket_id} not found."
    c.execute("UPDATE support_tickets SET status = 'closed' WHERE id = ?", (ticket_id,))
    add_user_notification(c, t["user_id"], f"Support Ticket #{ticket_id} ({t['subject']}) has been resolved and closed.", "success")
    c.commit()
    c.close()
    return f"✅ Ticket #{ticket_id} closed and user notified."

def build_bot_settings_text():
    c = sqlite3.connect(DB_PATH)
    rows = c.execute("SELECT key, value FROM settings").fetchall()
    c.close()
    s = {k: v for k, v in rows}
    return (
        "⚙️ CURRENT PLATFORM SETTINGS\n"
        f"• Bank: {s.get('bank_name', '-')}\n"
        f"• Account No: {s.get('account_number', '-')}\n"
        f"• Account Name: {s.get('account_name', '-')}\n"
        f"• Min Deposit: ₦{s.get('min_deposit', '3000')}\n"
        f"• Min Withdrawal: ₦{s.get('min_withdrawal', '1500')}\n"
        f"• Max Withdrawal: ₦{s.get('max_withdrawal', '1000000')}\n"
        f"• Daily Check-In: ₦{s.get('daily_check_in', '100')}\n"
        f"• Maintenance Mode: {'ON' if s.get('maintenance_mode') == '1' else 'OFF'}\n\n"
        "Quick update command:\n"
        "/setsetting <key> <value>\n"
        "Keys: bank_name, account_number, account_name, min_deposit, min_withdrawal, max_withdrawal, daily_check_in"
    )

def update_single_setting(key_name, new_val):
    allowed = {
        "bank_name", "account_number", "account_name",
        "min_deposit", "min_withdrawal", "max_withdrawal", "daily_check_in"
    }
    k = (key_name or "").strip()
    v = (new_val or "").strip()
    if k not in allowed or not v:
        return f"❌ Invalid setting key '{k}' or empty value."
    c = sqlite3.connect(DB_PATH)
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v))
    c.commit()
    c.close()
    return f"✅ Updated setting '{k}' = '{v}'."

def toggle_maintenance_mode():
    c = sqlite3.connect(DB_PATH)
    row = c.execute("SELECT value FROM settings WHERE key = 'maintenance_mode'").fetchone()
    current = row[0] if row else "0"
    new_val = "0" if current == "1" else "1"
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('maintenance_mode', ?)", (new_val,))
    c.commit()
    c.close()
    if new_val == "1":
        return "🛠️ Maintenance Mode is now ON. Website access is temporarily paused for users."
    return "✅ Maintenance Mode is now OFF. Website is back to normal."

def run_db_backup_and_alert(chat_id=None):
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(BACKUP_DIR, f"money_plus_one_{stamp}.db")
        if os.path.exists(DB_PATH):
            shutil.copy2(DB_PATH, dest)
        msg = f"💾 BACKUP ALERT\nDatabase backup completed successfully.\nFile: {os.path.basename(dest)}"
    except Exception as exc:
        msg = f"⚠️ BACKUP ALERT\nBackup failed: {exc}"
    send_telegram_simple(msg, chat_id)
    return msg

def send_admin_panel(chat_id):
    keyboard = {
        "keyboard": [
            [{"text": "📊 Admin Stats"}, {"text": "⏳ Pending Count"}],
            [{"text": "📅 Daily Summary"}, {"text": "🔍 User Search"}],
            [{"text": "✉️ Message User"}, {"text": "📢 Broadcast All"}],
            [{"text": "💰 Adjust Balance"}, {"text": "🗑️ Delete User"}],
            [{"text": "🎫 Support Tickets"}, {"text": "⚙️ Bot Settings"}],
            [{"text": "🛠️ Maintenance"}, {"text": "💾 Backup DB"}],
            [{"text": "💳 Pending Deposits"}, {"text": "💸 Pending Withdrawals"}]
        ],
        "resize_keyboard": True
    }
    text = (
        f"🛠️ MONEY PLUS ONE — FULL ADMIN PANEL\n"
        f"Admin Chat ID: {chat_id}\n\n"
        f"{build_admin_stats_text()}\n\n"
        "Commands:\n"
        "• /msg <user_id> <text>\n"
        "• /broadcast <text>\n"
        "• /addbal <user_id> <amount> | /subbal <user_id> <amount>\n"
        "• /suspend <user_id> | /unsuspend <user_id> | /deluser <user_id>\n"
        "• /close_ticket <id> | /setsetting <key> <value>"
    )
    send_telegram_simple(text, chat_id, reply_markup=keyboard)

def send_pending_list(chat_id, kind):
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    if kind == "deposit":
        rows = c.execute("""
            SELECT d.id, d.amount, u.full_name, u.email
            FROM deposits d JOIN users u ON d.user_id = u.id
            WHERE d.status = 'pending' ORDER BY d.id DESC LIMIT 10
        """).fetchall()
        c.close()
        if not rows:
            send_telegram_simple("✅ No pending deposits right now.", chat_id)
            return
        for d in rows:
            msg = (
                f"NEW DEPOSIT REQUEST (#{d['id']})\n"
                f"User: {d['full_name']}\n"
                f"Email: {d['email']}\n"
                f"Amount: ₦{float(d['amount']):,.2f}"
            )
            send_telegram_request(msg, "deposit", d["id"], chat_id)
    else:
        rows = c.execute("""
            SELECT w.id, w.amount, w.bank_name, w.account_number, w.account_name, u.full_name
            FROM withdrawals w JOIN users u ON w.user_id = u.id
            WHERE w.status = 'pending' ORDER BY w.id DESC LIMIT 10
        """).fetchall()
        c.close()
        if not rows:
            send_telegram_simple("✅ No pending withdrawals right now.", chat_id)
            return
        for w in rows:
            msg = (
                f"NEW WITHDRAWAL REQUEST (#{w['id']})\n"
                f"User: {w['full_name']}\n"
                f"Amount: ₦{float(w['amount']):,.2f}\n"
                f"Bank: {w['bank_name']}\n"
                f"Account: {w['account_number']} ({w['account_name']})"
            )
            send_telegram_request(msg, "withdraw", w["id"], chat_id)

def award_referral_commission_if_eligible(conn, invited_user_id):
    u = conn.execute("SELECT * FROM users WHERE id = ?", (invited_user_id,)).fetchone()
    if not u or not u["referred_by"] or u["referral_bonus_paid"]:
        return
    if u["total_deposit"] > 0 and u["has_invested"] == 1:
        inviter_id = u["referred_by"]
        conn.execute(
            "UPDATE users SET balance = round(balance + 1000.0, 2), total_income = round(total_income + 1000.0, 2), referral_bonus_paid = 1 WHERE id = ?",
            (inviter_id,)
        )
        add_user_notification(
            conn,
            inviter_id,
            f"Referral Commission: You received ₦1,000.00 because {u['full_name']} deposited and invested in a machine.",
            "success"
        )

def sync_user_earnings(user_id):
    db = get_db()
    active_machines = db.execute(
        "SELECT * FROM user_machines WHERE user_id = ? AND status = 'active'",
        (user_id,)
    ).fetchall()
    now = datetime.now(timezone.utc)
    total_new_credit = 0.0

    for m in active_machines:
        purchased_at = datetime.fromisoformat(m["purchased_at"])
        if purchased_at.tzinfo is None:
            purchased_at = purchased_at.replace(tzinfo=timezone.utc)
        elapsed_hours = int((now - purchased_at).total_seconds() // 3600)
        target_hours = min(elapsed_hours, m["max_hours"])
        new_hours = target_hours - m["hours_credited"]

        if new_hours > 0:
            hourly_rate = round(m["daily_profit"] / 24.0, 2)
            if target_hours >= m["max_hours"]:
                already_paid = round(m["hours_credited"] * hourly_rate, 2)
                credit = round(m["total_profit"] - already_paid, 2)
                new_status = "completed"
            else:
                credit = round(new_hours * hourly_rate, 2)
                new_status = "active"

            if credit > 0:
                total_new_credit = round(total_new_credit + credit, 2)

            db.execute(
                "UPDATE user_machines SET hours_credited = ?, status = ? WHERE id = ?",
                (target_hours, new_status, m["id"])
            )

    if total_new_credit > 0:
        db.execute(
            "UPDATE users SET balance = round(balance + ?, 2), total_income = round(total_income + ?, 2) WHERE id = ?",
            (total_new_credit, total_new_credit, user_id)
        )
        db.commit()

def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token

app.jinja_env.globals["csrf_token"] = csrf_token

@app.before_request
def basic_csrf_protection():
    if request.method == "POST":
        sent = request.form.get("_csrf_token", "")
        saved = session.get("_csrf_token", "")
        if not sent or not saved or not secrets.compare_digest(sent, saved):
            return "Security check failed. Refresh the page and try again.", 400

@app.before_request
def load_current_user():
    g.user = None
    user_id = session.get("user_id")
    if user_id:
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None:
            session.clear()
        else:
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            db.execute("UPDATE users SET last_seen = ? WHERE id = ?", (now_str, user["id"]))
            if not user["referral_code"]:
                code = f"MP{user['id']}{secrets.token_hex(2).upper()}"
                db.execute("UPDATE users SET referral_code = ? WHERE id = ?", (code, user["id"]))
            db.commit()
            if (user["account_status"] or "active") == "suspended" and user["role"] != "admin":
                session.clear()
                flash("Your account has been suspended. Contact support.", "error")
                return
            if user["role"] == "user":
                sync_user_earnings(user["id"])
            g.user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    settings = get_settings()
    if settings.get("maintenance_mode") == "1":
        is_admin = bool(g.user and g.user["role"] == "admin")
        allowed_paths = ("/login", "/logout", "/static/")
        if not is_admin and not any(request.path.startswith(p) for p in allowed_paths):
            return (
                "<h3>🛠️ Maintenance Mode</h3><p>Website is temporarily under maintenance. Please try again shortly.</p>",
                503
            )

@app.errorhandler(500)
def handle_500_error(err):
    tb_short = traceback.format_exc()[-600:]
    send_telegram_simple(
        f"🚨 WEBSITE ERROR (500)\nPath: {request.path}\nDetail:\n{tb_short}"
    )
    return "An unexpected error occurred. Admin has been notified.", 500

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            session.pop("_flashes", None)
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            session.pop("_flashes", None)
            flash("Please sign in first.", "warning")
            return redirect(url_for("login"))
        if g.user["role"] != "admin":
            flash("Admin access only.", "error")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)
    return wrapped

@app.route("/")
def home():
    if g.user:
        if g.user["role"] == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("dashboard"))
    return render_template("index.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("dashboard"))
    ref_code = request.args.get("ref", "").strip()
    if request.method == "POST":
        name = request.form.get("full_name", "").strip()
        raw_contact = request.form.get("email", "").strip()
        kind, contact_val = normalize_identifier(raw_contact)
        phone_extra = request.form.get("phone_number", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        ref_input = request.form.get("ref_code", ref_code).strip().upper()

        valid_email = (kind == "email" and "@" in contact_val and "." in contact_val.split("@")[-1] and len(contact_val) <= 150)
        valid_phone = (kind == "phone" and 7 <= len(contact_val.lstrip("+")) <= 15)

        if len(name) < 2 or len(name) > 100:
            flash("Enter your full name (2-100 characters).", "error")
        elif not (valid_email or valid_phone):
            flash("Enter a valid Gmail/Email or Phone Number.", "error")
        elif len(password) < 8:
            flash("Password must contain at least 8 characters.", "error")
        elif password != confirm:
            flash("Passwords do not match.", "error")
        else:
            db = get_db()
            if kind == "email":
                existing = db.execute("SELECT id FROM users WHERE email = ?", (contact_val,)).fetchone()
                email_val = contact_val
                phone_val = phone_extra
            else:
                existing = db.execute("SELECT id FROM users WHERE phone_number = ?", (contact_val,)).fetchone()
                email_val = f"phone_{contact_val.lstrip('+')}@noemail.local"
                phone_val = contact_val

            if existing:
                flash("An account with this email or phone number already exists.", "error")
            else:
                referred_by = None
                if ref_input:
                    inviter = db.execute("SELECT id FROM users WHERE referral_code = ?", (ref_input,)).fetchone()
                    if inviter:
                        referred_by = inviter["id"]
                try:
                    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    cur = db.execute(
                        """INSERT INTO users
                           (full_name, email, phone_number, password_hash, role, balance, total_income, referred_by, last_seen, account_status)
                           VALUES (?, ?, ?, ?, 'user', 500.0, 500.0, ?, ?, 'active')""",
                        (name, email_val, phone_val, generate_password_hash(password), referred_by, now_str)
                    )
                    new_id = cur.lastrowid
                    code = f"MP{new_id}{secrets.token_hex(2).upper()}"
                    db.execute("UPDATE users SET referral_code = ? WHERE id = ?", (code, new_id))
                    add_user_notification(
                        db,
                        new_id,
                        "Welcome Bonus: ₦500.00 has been credited to your account.",
                        "success"
                    )
                    db.commit()
                    session.clear()
                    session["user_id"] = new_id
                    session.permanent = True
                    session["show_notice"] = True
                    return redirect(url_for("dashboard"))
                except sqlite3.IntegrityError:
                    flash("An account with this email or phone number already exists.", "error")
    return render_template("register.html", ref_code=ref_code)

@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        session.pop("_flashes", None)
        return redirect(url_for("admin_dashboard" if g.user["role"] == "admin" else "dashboard"))

    entered_email = ""
    if request.method == "POST":
        raw_contact = request.form.get("email", "").strip()
        kind, contact_val = normalize_identifier(raw_contact)
        password = request.form.get("password", "")
        entered_email = raw_contact
        session.pop("_flashes", None)

        db = get_db()
        if kind == "email":
            user = db.execute("SELECT * FROM users WHERE email = ?", (contact_val,)).fetchone()
        else:
            user = db.execute("SELECT * FROM users WHERE phone_number = ?", (contact_val,)).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            if (user["account_status"] or "active") == "suspended" and user["role"] != "admin":
                flash("Your account is suspended.", "error")
            else:
                old_csrf = session.get("_csrf_token")
                session.clear()
                if old_csrf:
                    session["_csrf_token"] = old_csrf
                session["user_id"] = user["id"]
                session.permanent = True
                session["show_notice"] = True
                return redirect(url_for("admin_dashboard" if user["role"] == "admin" else "dashboard"))
        else:
            send_telegram_simple(
                f"🚫 FAILED LOGIN ALERT\nInput: {raw_contact or '(empty)'}\nIP: {request.remote_addr or 'unknown'}"
            )
            flash("Email/Phone or password is incorrect.", "error")

    resp = make_response(render_template("login.html", entered_email=entered_email))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("You have signed out.", "success")
    return redirect(url_for("home"))

@app.route("/support-ticket", methods=["POST"])
@login_required
def submit_support_ticket():
    subject = request.form.get("subject", "Support Request").strip()
    message = request.form.get("message", "").strip()
    db = get_db()
    if not message:
        add_user_notification(db, g.user["id"], "Please enter your support message.", "error")
        db.commit()
        return redirect(url_for("dashboard", tab="mine"))

    cur = db.execute(
        "INSERT INTO support_tickets (user_id, subject, message, status) VALUES (?, ?, ?, 'open')",
        (g.user["id"], subject[:100], message[:1000])
    )
    t_id = cur.lastrowid
    db.commit()

    send_telegram_simple(
        f"🎫 NEW SUPPORT TICKET (#{t_id})\n"
        f"User: {g.user['full_name']} (ID: {g.user['id']})\n"
        f"Email: {g.user['email']}\n"
        f"Subject: {subject[:100]}\n"
        f"Message: {message[:1000]}"
    )
    add_user_notification(db, g.user["id"], f"Support Ticket #{t_id} submitted. Admin has been notified.", "success")
    db.commit()
    return redirect(url_for("dashboard", tab="mine"))

@app.route("/dashboard")
@login_required
def dashboard():
    if g.user["role"] == "admin":
        return redirect(url_for("admin_dashboard"))
    db = get_db()
    settings = get_settings()
    show_notice = session.pop("show_notice", False)

    top_notifications = db.execute(
        "SELECT * FROM notifications WHERE user_id = ? AND is_seen = 0 ORDER BY id DESC",
        (g.user["id"],)
    ).fetchall()
    if top_notifications:
        db.execute("UPDATE notifications SET is_seen = 1 WHERE user_id = ? AND is_seen = 0", (g.user["id"],))
        db.commit()

    my_machines = db.execute(
        "SELECT * FROM user_machines WHERE user_id = ? ORDER BY id DESC",
        (g.user["id"],)
    ).fetchall()

    my_deposits = db.execute(
        "SELECT * FROM deposits WHERE user_id = ? ORDER BY id DESC LIMIT 20",
        (g.user["id"],)
    ).fetchall()

    my_withdrawals = db.execute(
        "SELECT * FROM withdrawals WHERE user_id = ? ORDER BY id DESC LIMIT 20",
        (g.user["id"],)
    ).fetchall()

    team_members = db.execute(
        "SELECT full_name, created_at, total_deposit, has_invested FROM users WHERE referred_by = ? ORDER BY id DESC",
        (g.user["id"],)
    ).fetchall()

    return render_template(
        "dashboard.html",
        machines=MACHINES,
        deposit_amounts=DEPOSIT_AMOUNTS,
        my_machines=my_machines,
        my_deposits=my_deposits,
        my_withdrawals=my_withdrawals,
        team_members=team_members,
        top_notifications=top_notifications,
        settings=settings,
        show_notice=show_notice
    )

@app.route("/daily-checkin", methods=["POST"])
@login_required
def daily_checkin():
    if g.user["role"] == "admin":
        return redirect(url_for("admin_dashboard"))
    db = get_db()
    settings = get_settings()
    reward = float(settings.get("daily_check_in", 100))
    now = datetime.now(timezone.utc)

    if g.user["last_daily_claim"]:
        last = datetime.fromisoformat(g.user["last_daily_claim"])
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if (now - last).total_seconds() < 86400:
            add_user_notification(
                db,
                g.user["id"],
                "Daily Check-In: Already claimed for today. Please try again in 24 hours.",
                "error"
            )
            db.commit()
            return redirect(url_for("dashboard"))

    db.execute(
        "UPDATE users SET balance = round(balance + ?, 2), total_income = round(total_income + ?, 2), last_daily_claim = ? WHERE id = ?",
        (reward, reward, now.isoformat(), g.user["id"])
    )
    add_user_notification(
        db,
        g.user["id"],
        f"Daily Check-In Bonus: ₦{reward:,.2f} has been added to your balance.",
        "success"
    )
    db.commit()
    return redirect(url_for("dashboard"))

@app.route("/buy-machine/<int:machine_id>", methods=["POST"])
@login_required
def buy_machine(machine_id):
    if g.user["role"] == "admin":
        return redirect(url_for("admin_dashboard"))
    machine = next((m for m in MACHINES if m["id"] == machine_id), None)
    if not machine:
        add_user_notification(get_db(), g.user["id"], "Selected machine plan does not exist.", "error")
        get_db().commit()
        return redirect(url_for("dashboard"))

    db = get_db()
    if g.user["balance"] < machine["price"]:
        add_user_notification(
            db,
            g.user["id"],
            f"Insufficient balance (₦{g.user['balance']:,.2f}). Deposit at least ₦{machine['price']:,} first.",
            "error"
        )
        db.commit()
        return redirect(url_for("dashboard", tab="wallet"))

    now_iso = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE users SET balance = round(balance - ?, 2), has_invested = 1 WHERE id = ?",
        (machine["price"], g.user["id"])
    )
    db.execute("""
        INSERT INTO user_machines
        (user_id, machine_id, machine_name, price, daily_profit, hourly_profit, total_profit, days, hours_credited, max_hours, purchased_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 'active')
    """, (
        g.user["id"], machine["id"], machine["name"], machine["price"],
        machine["daily_profit"], machine["hourly_profit"], machine["total_profit"],
        machine["days"], machine["days"] * 24, now_iso
    ))
    add_user_notification(
        db,
        g.user["id"],
        f"Machine Activated: {machine['name']} is now active and earning hourly.",
        "success"
    )
    award_referral_commission_if_eligible(db, g.user["id"])
    db.commit()
    return redirect(url_for("dashboard", tab="income"))

@app.route("/deposit/confirm", methods=["POST"])
@login_required
def submit_deposit():
    if g.user["role"] == "admin":
        return redirect(url_for("admin_dashboard"))
    settings = get_settings()
    min_dep = float(settings.get("min_deposit", 3000))

    try:
        amount = float(request.form.get("amount", 0))
    except ValueError:
        amount = 0

    db = get_db()
    if amount < min_dep:
        add_user_notification(db, g.user["id"], f"Minimum deposit amount is ₦{min_dep:,.0f}.", "error")
        db.commit()
        return redirect(url_for("dashboard", tab="wallet"))

    cur = db.execute(
        "INSERT INTO deposits (user_id, amount, sender_name, transaction_ref, screenshot_filename, status) VALUES (?, ?, ?, '', '', 'pending')",
        (g.user["id"], amount, g.user["full_name"])
    )
    dep_id = cur.lastrowid

    msg = (
        f"NEW DEPOSIT REQUEST (#{dep_id})\n"
        f"User: {g.user['full_name']} (ID: {g.user['id']})\n"
        f"Email: {g.user['email']}\n"
        f"Amount: ₦{amount:,.2f}\n"
        f"Bank: {settings.get('bank_name')} | {settings.get('account_number')}"
    )
    send_telegram_request(msg, "deposit", dep_id, settings.get("telegram_admin_chat_id"))

    add_user_notification(
        db,
        g.user["id"],
        f"Deposit Submitted: ₦{amount:,.2f} payment confirmation received. Please wait up to 5 minutes for verification.",
        "success"
    )
    db.commit()
    return redirect(url_for("dashboard", tab="wallet"))

@app.route("/withdraw", methods=["POST"])
@login_required
def submit_withdraw():
    if g.user["role"] == "admin":
        return redirect(url_for("admin_dashboard"))
    settings = get_settings()
    raw_min_wd = float(settings.get("min_withdrawal", 1500))
    min_wd = 1500.0 if raw_min_wd == 3000.0 else raw_min_wd
    max_wd = float(settings.get("max_withdrawal", 1000000))

    raw_amt = request.form.get("amount", "0").replace(",", "").strip()
    try:
        amount = float(raw_amt)
    except ValueError:
        amount = 0.0

    bank_name = request.form.get("bank_name", "").strip()
    account_number = request.form.get("account_number", "").strip()
    account_name = request.form.get("account_name", "").strip()

    db = get_db()

    today_start = datetime.now(timezone.utc).strftime("%Y-%m-%d 00:00:00")
    today_count = db.execute(
        "SELECT COUNT(*) FROM withdrawals WHERE user_id = ? AND created_at >= ?",
        (g.user["id"], today_start)
    ).fetchone()[0]

    def fail_wd(err_msg):
        add_user_notification(db, g.user["id"], err_msg, "error")
        db.commit()
        return redirect(url_for("dashboard", tab="wallet"))

    if today_count >= 2:
        return fail_wd("Maximum 2 withdrawal requests per day allowed.")
    if amount < min_wd:
        return fail_wd(f"Minimum withdrawal amount is ₦{min_wd:,.0f}.")
    if amount > max_wd:
        return fail_wd(f"Maximum withdrawal amount is ₦{max_wd:,.0f}.")
    if amount > float(g.user["balance"]):
        return fail_wd(f"Insufficient balance (₦{float(g.user['balance']):,.2f}).")
    if not bank_name or not account_number or not account_name:
        return fail_wd("Complete all bank account details.")

    has_invited_investor = db.execute(
        "SELECT COUNT(*) FROM users WHERE referred_by = ? AND total_deposit > 0 AND has_invested = 1",
        (g.user["id"],)
    ).fetchone()[0] > 0

    if float(g.user["total_deposit"]) > 0 and int(g.user["has_invested"]) == 0:
        return fail_wd("Invest in a machine first before withdrawing deposited funds.")
    if int(g.user["has_invested"]) == 0 and not has_invited_investor:
        return fail_wd("Invest in a machine or have an invited partner deposit & invest before withdrawing.")

    db.execute("UPDATE users SET balance = round(balance - ?, 2) WHERE id = ?", (amount, g.user["id"]))
    cur = db.execute("""
        INSERT INTO withdrawals (user_id, amount, bank_name, account_number, account_name, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
    """, (g.user["id"], amount, bank_name, account_number, account_name))
    wd_id = cur.lastrowid

    add_user_notification(
        db,
        g.user["id"],
        f"Withdrawal Submitted: ₦{amount:,.2f} has been deducted from your balance and sent to {bank_name} ({account_number}). Please wait up to 5 minutes.",
        "success"
    )
    db.commit()

    msg = (
        f"NEW WITHDRAWAL REQUEST (#{wd_id})\n"
        f"User: {g.user['full_name']} (ID: {g.user['id']})\n"
        f"Email: {g.user['email']}\n"
        f"Amount: ₦{amount:,.2f}\n"
        f"Bank: {bank_name}\n"
        f"Account: {account_number} ({account_name})"
    )
    send_telegram_request(msg, "withdraw", wd_id, settings.get("telegram_admin_chat_id"))
    return redirect(url_for("dashboard", tab="wallet"))

def apply_admin_decision(record_type, record_id, action, reason=None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    result_text = "Already processed."
    reject_reason = (reason or "Verification criteria not met").strip()

    if record_type == "deposit":
        dep = conn.execute("SELECT * FROM deposits WHERE id = ?", (record_id,)).fetchone()
        if dep and dep["status"] == "pending":
            if action == "approve":
                conn.execute("UPDATE deposits SET status = 'approved', reviewed_at = ? WHERE id = ?", (now_str, record_id))
                conn.execute(
                    "UPDATE users SET balance = round(balance + ?, 2), total_deposit = round(total_deposit + ?, 2) WHERE id = ?",
                    (dep["amount"], dep["amount"], dep["user_id"])
                )
                add_user_notification(
                    conn,
                    dep["user_id"],
                    f"Deposit Approved: ₦{dep['amount']:,.2f} has been credited to your balance.",
                    "success"
                )
                award_referral_commission_if_eligible(conn, dep["user_id"])
                result_text = f"Deposit #{record_id} APPROVED (₦{dep['amount']:,.2f})."
            elif action == "reject":
                conn.execute("UPDATE deposits SET status = 'rejected', reviewed_at = ? WHERE id = ?", (now_str, record_id))
                add_user_notification(
                    conn,
                    dep["user_id"],
                    f"Deposit Rejected: Your ₦{dep['amount']:,.2f} deposit request was rejected. Reason: {reject_reason}.",
                    "error"
                )
                result_text = f"Deposit #{record_id} REJECTED ({reject_reason})."
            conn.commit()
    elif record_type == "withdraw":
        wd = conn.execute("SELECT * FROM withdrawals WHERE id = ?", (record_id,)).fetchone()
        if wd and wd["status"] == "pending":
            if action == "approve":
                conn.execute("UPDATE withdrawals SET status = 'approved', reviewed_at = ? WHERE id = ?", (now_str, record_id))
                add_user_notification(
                    conn,
                    wd["user_id"],
                    f"Withdrawal Approved: ₦{wd['amount']:,.2f} has been approved.",
                    "success"
                )
                result_text = f"Withdrawal #{record_id} APPROVED (₦{wd['amount']:,.2f})."
            elif action == "reject":
                conn.execute("UPDATE withdrawals SET status = 'rejected', reviewed_at = ? WHERE id = ?", (now_str, record_id))
                conn.execute("UPDATE users SET balance = round(balance + ?, 2) WHERE id = ?", (wd["amount"], wd["user_id"]))
                add_user_notification(
                    conn,
                    wd["user_id"],
                    f"Withdrawal Rejected: ₦{wd['amount']:,.2f} was rejected and refunded to your balance. Reason: {reject_reason}.",
                    "error"
                )
                result_text = f"Withdrawal #{record_id} REJECTED & refunded ({reject_reason})."
            conn.commit()

    conn.close()
    return result_text

def poll_telegram_bot():
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        with open(POLLER_PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook",
            json={"drop_pending_updates": False},
            timeout=8
        )
    except Exception:
        pass

    offset = None
    pending_prompts = {}
    while True:
        try:
            params = {"timeout": 25}
            if offset is not None:
                params["offset"] = offset
            resp = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates", params=params, timeout=30).json()
            if resp.get("ok"):
                for upd in resp.get("result", []):
                    offset = upd["update_id"] + 1
                    msg = upd.get("message")
                    if msg and msg.get("text"):
                        chat_id = msg["chat"]["id"]
                        txt = msg["text"].strip()

                        if txt in ("/start", "/admin"):
                            pending_prompts.pop(chat_id, None)
                            c = sqlite3.connect(DB_PATH)
                            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('telegram_admin_chat_id', ?)", (str(chat_id),))
                            c.commit()
                            c.close()
                            send_admin_panel(chat_id)
                        elif txt == "📊 Admin Stats":
                            pending_prompts.pop(chat_id, None)
                            send_telegram_simple(build_admin_stats_text(), chat_id)
                        elif txt == "⏳ Pending Count":
                            pending_prompts.pop(chat_id, None)
                            send_telegram_simple(build_pending_counter_text(), chat_id)
                        elif txt == "📅 Daily Summary":
                            pending_prompts.pop(chat_id, None)
                            send_telegram_simple(build_daily_summary_text(), chat_id)
                        elif txt == "🔍 User Search":
                            pending_prompts[chat_id] = "search"
                            for body_txt, mk in build_all_users_messages():
                                send_telegram_simple(body_txt, chat_id, reply_markup=mk)
                            send_telegram_simple("🔍 Type a name, Account ID, email, or phone number to search a specific user:", chat_id)
                        elif txt == "✉️ Message User":
                            pending_prompts[chat_id] = "msg_user"
                            send_telegram_simple("✉️ Send in format:\n<user_id> <your message>\nExample: 2 Hello, your deposit is confirmed.", chat_id)
                        elif txt == "📢 Broadcast All":
                            pending_prompts[chat_id] = "broadcast"
                            send_telegram_simple("📢 Type the announcement message you want to send to ALL users:", chat_id)
                        elif txt == "💰 Adjust Balance":
                            pending_prompts[chat_id] = "adjust_bal"
                            send_telegram_simple("💰 Send in format:\n<user_id> <+amount or -amount>\nExample: 2 +2500  or  2 -1000", chat_id)
                        elif txt == "🗑️ Delete User":
                            pending_prompts[chat_id] = "delete_user"
                            send_telegram_simple("🗑️ Enter the User ID you want to permanently delete:", chat_id)
                        elif txt == "🎫 Support Tickets":
                            pending_prompts.pop(chat_id, None)
                            send_telegram_simple(build_open_tickets_text(), chat_id)
                        elif txt == "⚙️ Bot Settings":
                            pending_prompts.pop(chat_id, None)
                            send_telegram_simple(build_bot_settings_text(), chat_id)
                        elif txt == "🛠️ Maintenance":
                            pending_prompts.pop(chat_id, None)
                            send_telegram_simple(toggle_maintenance_mode(), chat_id)
                        elif txt == "💾 Backup DB":
                            pending_prompts.pop(chat_id, None)
                            run_db_backup_and_alert(chat_id)
                        elif txt == "💳 Pending Deposits":
                            pending_prompts.pop(chat_id, None)
                            send_pending_list(chat_id, "deposit")
                        elif txt == "💸 Pending Withdrawals":
                            pending_prompts.pop(chat_id, None)
                            send_pending_list(chat_id, "withdraw")
                        elif txt.startswith("/search"):
                            q_part = txt.replace("/search", "", 1).strip()
                            res_txt, mk = search_users_response(q_part)
                            send_telegram_simple(res_txt, chat_id, reply_markup=mk)
                        elif txt.startswith("/msg "):
                            rest = txt[5:].strip()
                            parts = rest.split(" ", 1)
                            if len(parts) == 2 and parts[0].isdigit():
                                send_telegram_simple(send_message_to_user(int(parts[0]), parts[1]), chat_id)
                            else:
                                send_telegram_simple("Usage: /msg <user_id> <message>", chat_id)
                        elif txt.startswith("/broadcast "):
                            send_telegram_simple(broadcast_message_to_all_users(txt[11:]), chat_id)
                        elif txt.startswith("/addbal "):
                            rest = txt[8:].strip().split()
                            if len(rest) == 2 and rest[0].isdigit():
                                try:
                                    send_telegram_simple(adjust_user_balance(int(rest[0]), abs(float(rest[1]))), chat_id)
                                except ValueError:
                                    send_telegram_simple("Usage: /addbal <user_id> <amount>", chat_id)
                            else:
                                send_telegram_simple("Usage: /addbal <user_id> <amount>", chat_id)
                        elif txt.startswith("/subbal "):
                            rest = txt[8:].strip().split()
                            if len(rest) == 2 and rest[0].isdigit():
                                try:
                                    send_telegram_simple(adjust_user_balance(int(rest[0]), -abs(float(rest[1]))), chat_id)
                                except ValueError:
                                    send_telegram_simple("Usage: /subbal <user_id> <amount>", chat_id)
                            else:
                                send_telegram_simple("Usage: /subbal <user_id> <amount>", chat_id)
                        elif txt.startswith("/deluser "):
                            arg = txt[9:].strip()
                            if arg.isdigit():
                                uid = int(arg)
                                send_telegram_simple(
                                    f"⚠️ Confirm permanent deletion of User #{uid}?",
                                    chat_id,
                                    reply_markup={"inline_keyboard": [[
                                        {"text": f"✅ Yes, Delete #{uid}", "callback_data": f"udel:confirm:{uid}"},
                                        {"text": "❌ Cancel", "callback_data": f"udel:cancel:{uid}"}
                                    ]]}
                                )
                            else:
                                send_telegram_simple("Usage: /deluser <user_id>", chat_id)
                        elif txt.startswith("/suspend "):
                            arg = txt.replace("/suspend ", "", 1).strip()
                            if arg.isdigit():
                                send_telegram_simple(set_user_account_status(int(arg), "suspend"), chat_id)
                            else:
                                send_telegram_simple("Usage: /suspend <user_id>", chat_id)
                        elif txt.startswith("/unsuspend "):
                            arg = txt.replace("/unsuspend ", "", 1).strip()
                            if arg.isdigit():
                                send_telegram_simple(set_user_account_status(int(arg), "unsuspend"), chat_id)
                            else:
                                send_telegram_simple("Usage: /unsuspend <user_id>", chat_id)
                        elif txt.startswith("/close_ticket "):
                            arg = txt[14:].strip()
                            if arg.isdigit():
                                send_telegram_simple(close_support_ticket(int(arg)), chat_id)
                            else:
                                send_telegram_simple("Usage: /close_ticket <ticket_id>", chat_id)
                        elif txt.startswith("/setsetting "):
                            rest = txt[12:].strip().split(" ", 1)
                            if len(rest) == 2:
                                send_telegram_simple(update_single_setting(rest[0], rest[1]), chat_id)
                            else:
                                send_telegram_simple("Usage: /setsetting <key> <value>", chat_id)
                        elif chat_id in pending_prompts:
                            prompt_state = pending_prompts.pop(chat_id)
                            if isinstance(prompt_state, tuple) and prompt_state[0] == "direct_msg":
                                target_uid = prompt_state[1]
                                send_telegram_simple(send_message_to_user(target_uid, txt), chat_id)
                            elif isinstance(prompt_state, tuple) and prompt_state[0] == "direct_bal":
                                target_uid = prompt_state[1]
                                try:
                                    send_telegram_simple(adjust_user_balance(target_uid, float(txt)), chat_id)
                                except ValueError:
                                    send_telegram_simple(f"❌ Invalid amount for User #{target_uid}. Example: +2500 or -1000", chat_id)
                            elif prompt_state == "search":
                                res_txt, mk = search_users_response(txt)
                                send_telegram_simple(res_txt, chat_id, reply_markup=mk)
                            elif prompt_state == "msg_user":
                                parts = txt.split(" ", 1)
                                if len(parts) == 2 and parts[0].isdigit():
                                    send_telegram_simple(send_message_to_user(int(parts[0]), parts[1]), chat_id)
                                else:
                                    send_telegram_simple("❌ Invalid format. Use: <user_id> <message>", chat_id)
                            elif prompt_state == "broadcast":
                                send_telegram_simple(broadcast_message_to_all_users(txt), chat_id)
                            elif prompt_state == "adjust_bal":
                                parts = txt.split()
                                if len(parts) == 2 and parts[0].isdigit():
                                    try:
                                        send_telegram_simple(adjust_user_balance(int(parts[0]), float(parts[1])), chat_id)
                                    except ValueError:
                                        send_telegram_simple("❌ Invalid amount. Use: <user_id> <+amount or -amount>", chat_id)
                                else:
                                    send_telegram_simple("❌ Invalid format. Use: <user_id> <+amount or -amount>", chat_id)
                            elif prompt_state == "delete_user":
                                if txt.isdigit():
                                    uid = int(txt)
                                    send_telegram_simple(
                                        f"⚠️ Confirm permanent deletion of User #{uid}?",
                                        chat_id,
                                        reply_markup={"inline_keyboard": [[
                                            {"text": f"✅ Yes, Delete #{uid}", "callback_data": f"udel:confirm:{uid}"},
                                            {"text": "❌ Cancel", "callback_data": f"udel:cancel:{uid}"}
                                        ]]}
                                    )
                                else:
                                    send_telegram_simple("❌ Enter a valid numeric User ID.", chat_id)

                    cb = upd.get("callback_query")
                    if cb:
                        data = cb.get("data", "")
                        chat_id = cb["message"]["chat"]["id"]
                        message_id = cb["message"]["message_id"]
                        parts = data.split(":")
                        if len(parts) == 3 and parts[0] in ("app", "rej"):
                            action = "approve" if parts[0] == "app" else "reject"
                            kind = parts[1]
                            rec_id = int(parts[2])
                            summary = apply_admin_decision(kind, rec_id, action)
                            requests.post(
                                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                                json={"callback_query_id": cb["id"], "text": summary},
                                timeout=8
                            )
                            requests.post(
                                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText",
                                json={
                                    "chat_id": chat_id,
                                    "message_id": message_id,
                                    "text": f"{cb['message']['text']}\n\nStatus: {summary}"
                                },
                                timeout=8
                            )
                        elif len(parts) == 3 and parts[0] == "uact":
                            sub_cmd = parts[1]
                            uid = int(parts[2])
                            if sub_cmd == "msg":
                                pending_prompts[chat_id] = ("direct_msg", uid)
                                requests.post(
                                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                                    json={"callback_query_id": cb["id"], "text": f"Type message for Account #{uid}"},
                                    timeout=8
                                )
                                send_telegram_simple(f"✉️ Type the message you want to send to Account #{uid}:", chat_id)
                            elif sub_cmd == "bal":
                                pending_prompts[chat_id] = ("direct_bal", uid)
                                requests.post(
                                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                                    json={"callback_query_id": cb["id"], "text": f"Type balance change for Account #{uid}"},
                                    timeout=8
                                )
                                send_telegram_simple(f"💰 Type amount to credit or debit Account #{uid} (e.g. +2500 or -1000):", chat_id)
                        elif len(parts) == 3 and parts[0] == "ust":
                            st_action = parts[1]
                            uid = int(parts[2])
                            summary = set_user_account_status(uid, st_action)
                            requests.post(
                                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                                json={"callback_query_id": cb["id"], "text": summary},
                                timeout=8
                            )
                            send_telegram_simple(summary, chat_id)
                        elif len(parts) == 3 and parts[0] == "udel":
                            sub_op = parts[1]
                            uid = int(parts[2])
                            if sub_op == "ask":
                                requests.post(
                                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                                    json={"callback_query_id": cb["id"], "text": f"Confirm deleting User #{uid}"},
                                    timeout=8
                                )
                                send_telegram_simple(
                                    f"⚠️ Are you sure you want to permanently delete User #{uid}?",
                                    chat_id,
                                    reply_markup={"inline_keyboard": [[
                                        {"text": f"✅ Confirm Delete #{uid}", "callback_data": f"udel:confirm:{uid}"},
                                        {"text": "❌ Cancel", "callback_data": f"udel:cancel:{uid}"}
                                    ]]}
                                )
                            elif sub_op == "confirm":
                                summary = delete_user_completely(uid)
                                requests.post(
                                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                                    json={"callback_query_id": cb["id"], "text": summary},
                                    timeout=8
                                )
                                send_telegram_simple(summary, chat_id)
                            else:
                                requests.post(
                                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                                    json={"callback_query_id": cb["id"], "text": "Deletion cancelled"},
                                    timeout=8
                                )
                                send_telegram_simple(f"❌ Cancelled deletion of User #{uid}.", chat_id)
        except Exception:
            time.sleep(4)

@app.route("/admin")
@admin_required
def admin_dashboard():
    db = get_db()
    total_members = db.execute("SELECT COUNT(*) FROM users WHERE role = 'user'").fetchone()[0]
    total_depositors = db.execute("""
        SELECT COUNT(DISTINCT d.user_id)
        FROM deposits d
        JOIN users u ON u.id = d.user_id
        WHERE u.role = 'user' AND d.status = 'approved'
    """).fetchone()[0]

    cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    active_users = db.execute("""
        SELECT COUNT(*)
        FROM users
        WHERE role = 'user'
          AND COALESCE(account_status, 'active') = 'active'
          AND last_seen IS NOT NULL
          AND last_seen >= ?
    """, (cutoff_24h,)).fetchone()[0]

    pending_deposits_count = db.execute("SELECT COUNT(*) FROM deposits WHERE status = 'pending'").fetchone()[0]
    pending_withdrawals_count = db.execute("SELECT COUNT(*) FROM withdrawals WHERE status = 'pending'").fetchone()[0]

    users_list = db.execute("""
        SELECT id, full_name, email, phone_number, created_at, last_seen, COALESCE(account_status, 'active') AS account_status
        FROM users
        WHERE role = 'user'
        ORDER BY id DESC
    """).fetchall()

    deposits = db.execute("""
        SELECT d.*, u.full_name, u.email
        FROM deposits d JOIN users u ON d.user_id = u.id
        ORDER BY CASE WHEN d.status = 'pending' THEN 0 ELSE 1 END, d.id DESC LIMIT 30
    """).fetchall()

    withdrawals = db.execute("""
        SELECT w.*, u.full_name, u.email
        FROM withdrawals w JOIN users u ON w.user_id = u.id
        ORDER BY CASE WHEN w.status = 'pending' THEN 0 ELSE 1 END, w.id DESC LIMIT 30
    """).fetchall()

    settings = get_settings()
    return render_template(
        "admin_dashboard.html",
        total_members=total_members,
        total_depositors=total_depositors,
        active_users=active_users,
        pending_deposits_count=pending_deposits_count,
        pending_withdrawals_count=pending_withdrawals_count,
        users_list=users_list,
        deposits=deposits,
        withdrawals=withdrawals,
        settings=settings
    )

@app.route("/admin/user/<int:user_id>/toggle-status", methods=["POST"])
@admin_required
def admin_toggle_user_status(user_id):
    db = get_db()
    u = db.execute("SELECT id, account_status FROM users WHERE id = ? AND role = 'user'", (user_id,)).fetchone()
    if u:
        new_status = "suspended" if (u["account_status"] or "active") == "active" else "active"
        db.execute("UPDATE users SET account_status = ? WHERE id = ?", (new_status, user_id))
        db.commit()
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/deposit/<int:deposit_id>/<action>", methods=["POST"])
@admin_required
def admin_deposit_action(deposit_id, action):
    reason = request.form.get("reason", "")
    apply_admin_decision("deposit", deposit_id, action, reason=reason)
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/withdraw/<int:withdraw_id>/<action>", methods=["POST"])
@admin_required
def admin_withdraw_action(withdraw_id, action):
    reason = request.form.get("reason", "")
    apply_admin_decision("withdraw", withdraw_id, action, reason=reason)
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/settings", methods=["POST"])
@admin_required
def admin_update_settings():
    db = get_db()
    for k in ["bank_name", "account_number", "account_name", "service_link", "group_link", "min_deposit", "min_withdrawal", "max_withdrawal", "daily_check_in", "maintenance_mode", "telegram_admin_chat_id"]:
        val = request.form.get(k, "").strip()
        if val != "":
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, val))
    db.commit()
    return redirect(url_for("admin_dashboard"))

def _is_pid_running(pid):
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False

def start_single_telegram_poller():
    if not TELEGRAM_BOT_TOKEN:
        return
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    if os.path.exists(POLLER_PID_FILE):
        try:
            with open(POLLER_PID_FILE, "r", encoding="utf-8") as f:
                raw_pid = f.read().strip()
            if raw_pid.isdigit() and int(raw_pid) != os.getpid() and _is_pid_running(int(raw_pid)):
                return
        except Exception:
            pass
    threading.Thread(target=poll_telegram_bot, daemon=True).start()

if __name__ == "__main__":
    start_single_telegram_poller()
    app.run(host="0.0.0.0", port=5000, debug=True)
