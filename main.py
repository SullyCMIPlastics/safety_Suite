"""
CMI CMMS — backend API
Serves the static HTML app and provides a key-value store backed by SQLite.
Every browser STORE.set() fires a PUT here; on page load the browser fetches
all keys so any device sees the same data.
"""
import asyncio, datetime, hashlib, json, os, re, smtplib, sqlite3, uuid
from collections import defaultdict
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DB_PATH  = os.environ.get("DB_PATH",  "/data/cmms.db")
APP_DIR  = os.environ.get("APP_DIR",  "/app/static")

# ── SMTP config (set via docker-compose environment) ─────────────────────────
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.office365.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")          # e.g. cmms@cmiplastics.com
SMTP_PASS = os.environ.get("SMTP_PASS", "")          # mailbox password / app password
SMTP_FROM = os.environ.get("SMTP_FROM", "CMI CMMS <cmms@cmiplastics.com>")
CMMS_URL  = os.environ.get("CMMS_URL",  "https://cmms.cmi")

app = FastAPI(docs_url=None, redoc_url=None)


# ── database helpers ──────────────────────────────────────────────────────────

def db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS store (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    # Separate table for large embedded files (training PPTs, videos, tests).
    # Kept out of the `store` table so the bulk /api/store load (used by the
    # CMMS and the training app on startup) never has to transfer file bytes.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS blobs (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn

def db_get(key: str):
    conn = db()
    row = conn.execute("SELECT value FROM store WHERE key=?", (key,)).fetchone()
    conn.close()
    return json.loads(row[0]) if row else None

def db_set(key: str, value):
    conn = db()
    conn.execute(
        "INSERT OR REPLACE INTO store (key, value) VALUES (?, ?)",
        (key, json.dumps(value)),
    )
    conn.commit()
    conn.close()


# ── email helpers ─────────────────────────────────────────────────────────────

def smtp_configured() -> bool:
    return bool(SMTP_USER and SMTP_PASS)

def send_email(to: str, subject: str, html: str):
    """Send an HTML email via Office 365 SMTP (STARTTLS)."""
    msg = MIMEMultipart("alternative")
    msg["From"]    = SMTP_FROM
    msg["To"]      = to
    msg["Subject"] = subject
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.sendmail(SMTP_USER, to, msg.as_string())


def _priority_color(p: str) -> str:
    return {"Critical": "#dc2626", "High": "#ea580c", "Medium": "#d97706", "Low": "#2563eb"}.get(p, "#64748b")

def _days_overdue(due_str: str) -> int:
    try:
        due  = datetime.date.fromisoformat(due_str)
        diff = (datetime.date.today() - due).days
        return max(diff, 0)
    except Exception:
        return 0

def _build_overdue_email(tech_name: str, wos: list) -> str:
    count = len(wos)
    rows  = ""
    for w in wos:
        days = _days_overdue(w.get("due", ""))
        prio = w.get("priority", "")
        rows += f"""
        <tr>
          <td style="padding:12px 14px;border-bottom:1px solid #f1f5f9;font-weight:700;color:#1a3a5c;white-space:nowrap;">{w.get('num','—')}</td>
          <td style="padding:12px 14px;border-bottom:1px solid #f1f5f9;color:#374151;">{w.get('title','—')}</td>
          <td style="padding:12px 14px;border-bottom:1px solid #f1f5f9;color:#64748b;">{w.get('assetName','—')}</td>
          <td style="padding:12px 14px;border-bottom:1px solid #f1f5f9;color:{_priority_color(prio)};font-weight:600;">{prio}</td>
          <td style="padding:12px 14px;border-bottom:1px solid #f1f5f9;color:#dc2626;font-weight:700;white-space:nowrap;">{w.get('due','—')} <span style="font-size:11px;background:#fee2e2;color:#991b1b;padding:2px 6px;border-radius:10px;margin-left:4px;">{days}d overdue</span></td>
        </tr>"""

    first_name = tech_name.split()[0] if tech_name else "there"
    plural     = "work orders are" if count > 1 else "work order is"
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <div style="max-width:680px;margin:32px auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.08);">

    <!-- Header -->
    <div style="background:#1a3a5c;padding:28px 32px;">
      <div style="font-size:11px;font-weight:700;color:#93c5fd;letter-spacing:.08em;text-transform:uppercase;margin-bottom:6px;">CMI Plastics — CMMS Alert</div>
      <div style="font-size:22px;font-weight:700;color:#fff;">⚠️ Overdue Work Order{'' if count == 1 else 's'} Assigned to You</div>
    </div>

    <!-- Body -->
    <div style="padding:28px 32px;">
      <p style="font-size:15px;color:#374151;margin:0 0 20px;">Hi <strong>{first_name}</strong>,</p>
      <p style="font-size:15px;color:#374151;margin:0 0 24px;">
        You have <strong style="color:#dc2626;">{count} {plural}</strong> currently overdue and assigned to you.
        Please review and update the status as soon as possible.
      </p>

      <!-- WO Table -->
      <div style="border-radius:10px;overflow:hidden;border:1px solid #e2e8f0;margin-bottom:28px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
          <thead>
            <tr style="background:#f8fafc;">
              <th style="padding:10px 14px;text-align:left;font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em;">WO #</th>
              <th style="padding:10px 14px;text-align:left;font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em;">Title</th>
              <th style="padding:10px 14px;text-align:left;font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em;">Asset</th>
              <th style="padding:10px 14px;text-align:left;font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em;">Priority</th>
              <th style="padding:10px 14px;text-align:left;font-size:11px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em;">Due Date</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
      </div>

      <!-- CTA -->
      <div style="text-align:center;margin-bottom:28px;">
        <a href="{CMMS_URL}" style="display:inline-block;background:#1d4ed8;color:#fff;text-decoration:none;padding:13px 32px;border-radius:8px;font-size:15px;font-weight:700;letter-spacing:.01em;">Open CMMS →</a>
      </div>

      <p style="font-size:13px;color:#94a3b8;border-top:1px solid #f1f5f9;padding-top:16px;margin:0;">
        This is an automated notification from the CMI Maintenance Management System.
        Notifications are sent once daily at 7 AM for any overdue work orders.
        Reply to this email or contact your supervisor if you have questions.
      </p>
    </div>
  </div>
</body>
</html>"""


async def run_overdue_notifications(force: bool = False) -> dict:
    """
    Find all overdue, unarchived WOs with an assigned technician and
    send each technician one email listing all their overdue WOs.
    Tracks sent WO IDs per day to avoid duplicate emails.
    """
    if not smtp_configured():
        return {"ok": False, "error": "SMTP not configured (SMTP_USER / SMTP_PASS missing)"}

    today      = datetime.date.today()
    today_str  = today.isoformat()
    wos_raw    = db_get("workorders")  or []
    techs_raw  = db_get("technicians") or []

    # Read notification rules — default: notify after 24 hours overdue
    rules           = db_get("notif_rules") or {}
    overdue_enabled = rules.get("overdue_enabled", True)
    min_hours       = int(rules.get("overdue_min_hours", 24))
    min_days        = max(1, round(min_hours / 24))  # convert to whole days for date comparison

    if not overdue_enabled:
        print("[CMMS] Overdue notifications disabled in settings — skipping")
        return {"ok": True, "sent": 0, "message": "Overdue notifications disabled"}

    # All overdue open WOs past the minimum threshold
    threshold_date = (today - datetime.timedelta(days=min_days)).isoformat()
    overdue = [
        w for w in wos_raw
        if not w.get("archived")
        and w.get("due")
        and w["due"] <= threshold_date
        and w.get("assigned", "").strip()
    ]
    if not overdue:
        return {"ok": True, "sent": 0, "message": "No overdue work orders found"}

    # Daily dedup tracking
    notif_record = db_get("overdue_notif_log") or {}
    if notif_record.get("date") != today_str or force:
        notif_record = {"date": today_str, "sent_wo_ids": [], "errors": []}

    sent_ids  = set(notif_record.get("sent_wo_ids", []))
    new_ids   = []
    sent_count = 0
    errors     = []

    # Group overdue WOs by assigned tech name
    by_tech = defaultdict(list)
    for w in overdue:
        by_tech[w["assigned"].strip()].append(w)

    for tech_name, tech_wos in by_tech.items():
        # Find technician record (need their email)
        tech = next(
            (t for t in techs_raw
             if f"{t.get('first','')} {t.get('last','')}".strip() == tech_name
             and not t.get("archived")),
            None,
        )
        if not tech or not tech.get("email", "").strip():
            print(f"[CMMS] Skipping '{tech_name}' — no email on file")
            continue

        # Only include WOs not already emailed today (unless force)
        unsent = [w for w in tech_wos if w["id"] not in sent_ids] if not force else tech_wos
        if not unsent:
            continue

        email_addr = tech["email"].strip()
        subject    = f"[CMI CMMS] {len(unsent)} Overdue Work Order{'s' if len(unsent) > 1 else ''} — Action Required"
        html       = _build_overdue_email(tech_name, unsent)

        try:
            send_email(email_addr, subject, html)
            new_ids.extend([w["id"] for w in unsent])
            sent_count += len(unsent)
            print(f"[CMMS] Overdue notification sent to {email_addr} ({len(unsent)} WOs)")
        except Exception as e:
            msg = f"Failed to email {email_addr}: {e}"
            errors.append(msg)
            print(f"[CMMS] {msg}")

    # Persist dedup record
    notif_record["sent_wo_ids"] = list(sent_ids | set(new_ids))
    notif_record["errors"]      = errors
    notif_record["last_run"]    = datetime.datetime.now().isoformat(timespec="seconds")
    db_set("overdue_notif_log", notif_record)

    return {"ok": True, "sent": sent_count, "errors": errors}


# ── PM auto-generation helpers ────────────────────────────────────────────────

def _add_months(d: datetime.date, n: int) -> datetime.date:
    """Add n months to a date, clamping day to last valid day of target month."""
    month = d.month - 1 + n
    year  = d.year + month // 12
    month = month % 12 + 1
    leap  = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    days_in = [0,31,29 if leap else 28,31,30,31,30,31,31,30,31,30,31]
    return datetime.date(year, month, min(d.day, days_in[month]))

def _lead_days(freq: str) -> int:
    """
    How many days in advance of the due date to create a WO.
    Proportional to frequency so short-cycle PMs don't clutter the board.
    """
    f = (freq or "").strip().lower()
    if f == "daily":                                         return 0   # generate on due day only
    if f == "weekly":                                        return 2
    if f in ("biweekly", "bi-weekly"):                       return 5
    if f == "monthly":                                       return 7
    if f in ("every 2 months",):                             return 10
    if f in ("quarterly", "every 3 months"):                 return 14
    if f in ("every 4 months",):                             return 14
    if f in ("semi-annual", "every 6 months"):               return 21
    if f in ("annual", "annually", "yearly"):                return 30
    m = re.match(r"every\s+(\d+)\s+days?$",   f)
    if m: return max(0, min(int(m.group(1)) // 2, 3))
    m = re.match(r"every\s+(\d+)\s+weeks?$",  f)
    if m: n = int(m.group(1)); return max(2, min(n * 2, 14))
    m = re.match(r"every\s+(\d+)\s+months?$", f)
    if m: n = int(m.group(1)); return 7 if n == 1 else 14 if n <= 3 else 21
    return 7  # unknown frequency — default 7 days

def _advance_date(base: datetime.date, freq: str) -> datetime.date:
    """Advance base by one frequency period (calendar-based, no floating)."""
    f = (freq or "").strip().lower()
    if f == "daily":                                         return base + datetime.timedelta(days=1)
    if f == "weekly":                                        return base + datetime.timedelta(weeks=1)
    if f in ("biweekly", "bi-weekly"):                       return base + datetime.timedelta(weeks=2)
    if f == "monthly":                                       return _add_months(base, 1)
    if f in ("quarterly", "every 3 months"):                 return _add_months(base, 3)
    if f in ("semi-annual", "every 6 months"):               return _add_months(base, 6)
    if f in ("annual", "annually", "yearly"):                return _add_months(base, 12)
    m = re.match(r"every\s+(\d+)\s+days?$",   f);
    if m: return base + datetime.timedelta(days=int(m.group(1)))
    m = re.match(r"every\s+(\d+)\s+weeks?$",  f);
    if m: return base + datetime.timedelta(weeks=int(m.group(1)))
    m = re.match(r"every\s+(\d+)\s+months?$", f);
    if m: return _add_months(base, int(m.group(1)))
    m = re.match(r"every\s+(\d+)\s+years?$",  f);
    if m: return _add_months(base, int(m.group(1)) * 12)
    return base + datetime.timedelta(days=30)   # unknown frequency — default 30 days

async def auto_generate_pm_wos() -> dict:
    """
    Calendar-based PM WO auto-generation (no floating).
    For every active, non-paused PM whose next due date is today or past:
      1. Create an Open WO (unless one already exists for this PM + due date).
      2. Advance pm.next to the next future calendar occurrence.
    """
    today     = datetime.date.today()
    today_str = today.isoformat()

    pms = db_get("pms") or []
    wos = db_get("workorders") or []

    # Dedup set: (pmId, dueDate) for any existing open WO generated by the system
    existing_pm_due = {
        (w.get("pmId"), w.get("due"))
        for w in wos if not w.get("archived") and w.get("pmId") and w.get("due")
    }
    # Legacy dedup (older WOs without pmId): title + assetName + due
    existing_legacy = {
        (w.get("title",""), w.get("assetName",""), w.get("due",""))
        for w in wos if not w.get("archived") and not w.get("pmId")
    }

    # Next available WO number
    nums = []
    for w in wos:
        try: nums.append(int(w.get("num","0").replace("WO-","")))
        except: pass
    next_wo_num = max(nums, default=0) + 1

    created      = 0
    pms_changed  = False

    for pm in pms:
        freq = (pm.get("freq") or "").strip()
        pm_status = (pm.get("status") or "Active").strip()
        if pm.get("paused") or pm_status in ("Inactive", "On Hold") or not freq or freq.lower() == "as needed":
            continue

        pm_next = pm.get("next")

        # Initialise pm.next if it was never set.
        # Use last+freq if the PM has been done before; otherwise start one
        # full period in the future so we don't flood the board on day-1.
        if not pm_next:
            if pm.get("last"):
                try:
                    pm_next = _advance_date(
                        datetime.date.fromisoformat(pm["last"]), freq
                    ).isoformat()
                except Exception:
                    pm_next = _advance_date(today, freq).isoformat()
            else:
                pm_next = _advance_date(today, freq).isoformat()
            pm["next"] = pm_next
            pms_changed = True

        try:
            next_date = datetime.date.fromisoformat(pm_next)
        except Exception:
            continue

        # Only generate if within the lead-time window for this frequency
        lead    = _lead_days(freq)
        window  = today + datetime.timedelta(days=lead)
        if next_date > window:
            continue   # too early — not within generation window yet

        # Dedup check
        pm_id  = pm.get("id", "")
        if (pm_id, pm_next) in existing_pm_due:
            # WO already exists — still advance pm.next so we don't get stuck
            new_next = _advance_date(next_date, freq)
            while new_next <= today:
                new_next = _advance_date(new_next, freq)
            pm["next"] = new_next.isoformat()
            pms_changed = True
            continue

        if (pm.get("task",""), pm.get("assetName",""), pm_next) in existing_legacy:
            continue   # legacy dedup hit

        # Create WO
        wo_num = f"WO-{str(next_wo_num).zfill(3)}"
        next_wo_num += 1
        wos.append({
            "id":            str(uuid.uuid4()),
            "num":           wo_num,
            "pmId":          pm_id,
            "title":         pm.get("task", ""),
            "asset":         pm.get("asset", ""),
            "assetName":     pm.get("assetName", ""),
            "assetLinks":    pm.get("assetLinks", []),
            "type":          pm.get("type", "Preventive"),
            "priority":      pm.get("priority", "Medium"),
            "status":        "Open",
            "assigned":      pm.get("assigned", ""),
            "due":           pm_next,
            "estHrs":        pm.get("estHrs", 0),
            "actHrs":        None,
            "laborCost":     None,
            "desc":          f"Auto-generated from PM {pm.get('num','')} (scheduled {pm_next}).",
            "notes":         "",
            "cancelReason":  "",
            "created":       today_str,
            "taskListId":    pm.get("taskListId") or "",
            "taskProgress":  {},
            "mechanicNotes": [],
            "partsUsed":     [],
            "partsDeducted": False,
            "archived":      False,
            "archivedAt":    None,
            "archivedReason":"",
        })
        existing_pm_due.add((pm_id, pm_next))
        created += 1

        # Advance pm.next to next future calendar date (no floating)
        new_next = _advance_date(next_date, freq)
        while new_next <= today:
            new_next = _advance_date(new_next, freq)
        pm["next"] = new_next.isoformat()
        pms_changed = True

    if created > 0:
        db_set("workorders", wos)
    if pms_changed:
        db_set("pms", pms)

    print(f"[CMMS] PM auto-gen: {created} WO(s) created, schedules updated")
    return {"ok": True, "created": created}


async def _daily_scheduler():
    """Wake up every day at 07:00 local server time and fire overdue notifications Mon–Fri only."""
    print("[CMMS] Daily overdue-notification scheduler started (fires at 07:00 Mon–Fri)")
    while True:
        now      = datetime.datetime.now()
        next_run = now.replace(hour=7, minute=0, second=0, microsecond=0)
        if now >= next_run:
            next_run += datetime.timedelta(days=1)
        wait = (next_run - now).total_seconds()
        print(f"[CMMS] Next overdue check at {next_run.strftime('%Y-%m-%d %H:%M')} ({wait/3600:.1f} h from now)")
        await asyncio.sleep(wait)
        weekday   = datetime.datetime.now().weekday()   # 0=Mon … 6=Sun
        day_name  = datetime.datetime.now().strftime("%A")
        if weekday < 5:   # Monday–Friday only
            # 1. Auto-generate PM work orders
            try:
                gen = await auto_generate_pm_wos()
                print(f"[CMMS] PM auto-gen ({day_name}): {gen}")
            except Exception as exc:
                print(f"[CMMS] PM auto-gen error: {exc}")
            # 2. Send overdue WO email notifications
            try:
                notif = await run_overdue_notifications()
                print(f"[CMMS] Overdue emails ({day_name}): {notif}")
            except Exception as exc:
                print(f"[CMMS] Overdue email error: {exc}")
        else:
            print(f"[CMMS] Skipping — today is {day_name} (weekdays only)")


# ── API routes (must be registered BEFORE the static-file catch-all) ─────────

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(_daily_scheduler())


@app.get("/api/health")
def health():
    return {"status": "ok", "smtp_configured": smtp_configured()}


@app.post("/api/pm/generate")
async def trigger_pm_generate():
    """Manually trigger PM WO auto-generation (same logic as the daily scheduler)."""
    result = await auto_generate_pm_wos()
    return result


@app.post("/api/notify/overdue")
async def trigger_overdue(force: bool = False):
    """
    Manually trigger overdue email notifications.
    Add ?force=true to re-send even for WOs already emailed today.
    """
    result = await run_overdue_notifications(force=force)
    return result


@app.get("/api/notify/status")
def notify_status():
    """Return the last notification run log and current rules."""
    log   = db_get("overdue_notif_log") or {}
    rules = db_get("notif_rules")       or {}
    return {
        "smtp_configured":    smtp_configured(),
        "smtp_from":          SMTP_FROM,
        "smtp_host":          SMTP_HOST,
        "log":                log,
        "rules":              rules,
    }


class AuthRequest(BaseModel):
    email: str
    password: str

@app.post("/api/auth")
def auth_login(req: AuthRequest):
    """Verify credentials. Returns {ok, error} — never exposes the stored hash."""
    email = (req.email or "").lower().strip()
    pw_hash = hashlib.sha256((email + req.password).encode("utf-8")).hexdigest()
    auth = db_get("auth") or {}
    stored = auth.get(email)
    if not stored:
        return {"ok": False, "error": "no_password"}
    if stored != pw_hash:
        return {"ok": False, "error": "wrong_password"}
    return {"ok": True}


class SetPasswordRequest(BaseModel):
    email: str
    password: str

@app.post("/api/auth/set")
def auth_set(req: SetPasswordRequest):
    """Set (or reset) a password. Computes hash server-side."""
    email = (req.email or "").lower().strip()
    if not email or not req.password:
        return {"ok": False, "error": "missing_fields"}
    pw_hash = hashlib.sha256((email + req.password).encode("utf-8")).hexdigest()
    conn = db()
    row = conn.execute("SELECT value FROM store WHERE key='auth'").fetchone()
    auth = json.loads(row[0]) if row else {}
    auth[email] = pw_hash
    conn.execute("INSERT OR REPLACE INTO store (key, value) VALUES ('auth', ?)", (json.dumps(auth),))
    conn.commit()
    conn.close()
    return {"ok": True}


class ClearPasswordRequest(BaseModel):
    email: str

@app.post("/api/auth/clear")
def auth_clear(req: ClearPasswordRequest):
    """Clear a technician's password so they must re-set it."""
    email = (req.email or "").lower().strip()
    conn = db()
    row = conn.execute("SELECT value FROM store WHERE key='auth'").fetchone()
    auth = json.loads(row[0]) if row else {}
    auth.pop(email, None)
    conn.execute("INSERT OR REPLACE INTO store (key, value) VALUES ('auth', ?)", (json.dumps(auth),))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/store")
def get_all():
    """Return every key as a JSON object — used by the browser on page load."""
    conn = db()
    rows = conn.execute("SELECT key, value FROM store").fetchall()
    conn.close()
    return JSONResponse({row[0]: json.loads(row[1]) for row in rows})


@app.put("/api/store/{key}")
async def put_key(key: str, request: Request):
    """Accept raw JSON body and upsert into the store."""
    raw = await request.body()
    conn = db()
    conn.execute(
        "INSERT OR REPLACE INTO store (key, value) VALUES (?, ?)",
        (key, raw.decode("utf-8")),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


# ── blob storage (embedded training files: PPT / video / test / PDF) ─────────
# Stored individually and fetched on demand so they never bloat the bulk
# /api/store payload that both apps load at startup.

@app.get("/api/blob/{key}")
def get_blob(key: str):
    """Return a single stored file blob ({filename, mime, data}) or 404."""
    conn = db()
    row = conn.execute("SELECT value FROM blobs WHERE key=?", (key,)).fetchone()
    conn.close()
    if not row:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(json.loads(row[0]))


@app.put("/api/blob/{key}")
async def put_blob(key: str, request: Request):
    """Accept a raw JSON body (file metadata + base64 data URL) and upsert it."""
    raw = await request.body()
    conn = db()
    conn.execute(
        "INSERT OR REPLACE INTO blobs (key, value) VALUES (?, ?)",
        (key, raw.decode("utf-8")),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/blob/{key}")
def delete_blob(key: str):
    """Delete a stored file blob (called when a training material is removed)."""
    conn = db()
    conn.execute("DELETE FROM blobs WHERE key=?", (key,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ── static file serving (index.html + any future assets) ─────────────────────

os.makedirs(APP_DIR, exist_ok=True)
app.mount("/", StaticFiles(directory=APP_DIR, html=True), name="static")
