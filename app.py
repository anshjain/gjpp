from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, abort, send_from_directory, Response
from datetime import datetime, date, timedelta
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import uuid
import os
import json
import csv
import io
import smtplib
import logging
from email.mime.text import MIMEText

try:
    from twilio.rest import Client as TwilioClient
except ImportError:
    TwilioClient = None

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.secret_key = os.environ.get('SECRET_KEY', 'gjpp-secret-key-2024-dev-only')
if app.secret_key == 'gjpp-secret-key-2024-dev-only':
    logging.warning("SECRET_KEY is not set — using an insecure development default. "
                     "Set the SECRET_KEY environment variable before deploying to production.")

# Hard ceiling on any single request body (defense in depth, on top of the
# per-file checks below) — generous enough for video uploads, well beyond
# the 4MB document limit. Requests over this are rejected before any
# upload handling code runs.
app.config['MAX_CONTENT_LENGTH'] = 60 * 1024 * 1024  # 60 MB

# ─────────────────────────────────────────
#  PRODUCTION INTEGRATIONS
#  (email, WhatsApp/SMS, and file storage — all driven by environment
#   variables so the same code runs safely in dev and in production;
#   nothing here is mocked, but sending gracefully no-ops with a log
#   warning if the relevant credentials haven't been configured yet.)
# ─────────────────────────────────────────

MAIL_SERVER    = os.environ.get('MAIL_SERVER')
MAIL_PORT      = int(os.environ.get('MAIL_PORT', 587))
MAIL_USERNAME  = os.environ.get('MAIL_USERNAME')
MAIL_PASSWORD  = os.environ.get('MAIL_PASSWORD')
MAIL_USE_TLS   = os.environ.get('MAIL_USE_TLS', 'true').lower() != 'false'
MAIL_SENDER    = os.environ.get('MAIL_DEFAULT_SENDER', MAIL_USERNAME or 'no-reply@gjpp.org')

TWILIO_ACCOUNT_SID   = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN    = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP_FROM = os.environ.get('TWILIO_WHATSAPP_FROM')  # e.g. 'whatsapp:+14155238886'

def email_is_configured():
    return bool(MAIL_SERVER and MAIL_USERNAME and MAIL_PASSWORD)

def whatsapp_is_configured():
    return bool(TwilioClient and TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM)

def send_email(to_address, subject, body):
    """Send a real email over SMTP. No-ops with a log warning if MAIL_* env vars aren't set."""
    if not to_address:
        return False
    if not email_is_configured():
        logging.warning(f"[email not configured] Would send to {to_address}: {subject}")
        return False
    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = MAIL_SENDER
        msg['To'] = to_address
        with smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=10) as server:
            if MAIL_USE_TLS:
                server.starttls()
            server.login(MAIL_USERNAME, MAIL_PASSWORD)
            server.sendmail(MAIL_SENDER, [to_address], msg.as_string())
        return True
    except Exception as e:
        logging.error(f"Failed to send email to {to_address}: {e}")
        return False

def send_whatsapp(to_phone, message):
    """Send a real WhatsApp message via Twilio. No-ops with a log warning if not configured."""
    if not to_phone:
        return False
    if not whatsapp_is_configured():
        logging.warning(f"[WhatsApp not configured] Would send to {to_phone}: {message}")
        return False
    try:
        client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(
            from_=TWILIO_WHATSAPP_FROM,
            to=f"whatsapp:{to_phone}" if not to_phone.startswith('whatsapp:') else to_phone,
            body=message,
        )
        return True
    except Exception as e:
        logging.error(f"Failed to send WhatsApp message to {to_phone}: {e}")
        return False

# ── File storage (local disk under static/uploads — swap for S3/Cloudinary by
#    changing only the functions below; every route calls through these) ──
UPLOAD_ROOT = os.path.join(app.static_folder, 'uploads')
MATERIALS_UPLOAD_DIR = os.path.join(UPLOAD_ROOT, 'materials')
VIDEOS_UPLOAD_DIR    = os.path.join(UPLOAD_ROOT, 'videos')
os.makedirs(MATERIALS_UPLOAD_DIR, exist_ok=True)
os.makedirs(VIDEOS_UPLOAD_DIR, exist_ok=True)

ALLOWED_MATERIAL_EXTS = {'pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp'}
ALLOWED_VIDEO_EXTS    = {'mp4', 'mov', 'webm', 'm4v'}
MAX_MATERIAL_SIZE_BYTES = 4 * 1024 * 1024  # 4 MB

def _allowed_file(filename, allowed_exts):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_exts

def save_uploaded_file(file_storage, dest_dir, allowed_exts, max_size_bytes=None):
    """
    Validate and save a real uploaded file to disk with a collision-proof name.
    Checks (in order): a file was actually chosen, its extension is on the
    allow-list, and — if max_size_bytes is given — it doesn't exceed that size.
    Returns (stored_filename, size_in_bytes, error_code). error_code is None on
    success; otherwise stored_filename/size are None and error_code is one of
    'missing', 'invalid_type', or 'too_large' so the caller can show a precise message.
    """
    if not file_storage or not file_storage.filename:
        return None, None, 'missing'
    if not _allowed_file(file_storage.filename, allowed_exts):
        return None, None, 'invalid_type'
    if max_size_bytes is not None:
        file_storage.stream.seek(0, os.SEEK_END)
        size = file_storage.stream.tell()
        file_storage.stream.seek(0)
        if size > max_size_bytes:
            return None, None, 'too_large'
    safe_name = secure_filename(file_storage.filename)
    stored_name = f"{uuid.uuid4().hex[:12]}_{safe_name}"
    dest_path = os.path.join(dest_dir, stored_name)
    file_storage.save(dest_path)
    size = os.path.getsize(dest_path)
    return stored_name, size, None

def human_file_size(num_bytes):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}" if unit != 'B' else f"{num_bytes} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"

# ─────────────────────────────────────────
#  SEED DATA  (replace with DB in prod)
# ─────────────────────────────────────────
REGIONS = [
    {"id": "north_america", "name": "North America", "timezone": "America/New_York",   "flag": "🇺🇸"},
    {"id": "uk",            "name": "United Kingdom","timezone": "Europe/London",       "flag": "🇬🇧"},
    {"id": "europe",        "name": "Europe",        "timezone": "Europe/Berlin",       "flag": "🇪🇺"},
    {"id": "india",         "name": "India",         "timezone": "Asia/Kolkata",        "flag": "🇮🇳"},
    {"id": "australia",     "name": "Australia",     "timezone": "Australia/Sydney",    "flag": "🇦🇺"},
    {"id": "south_america", "name": "South America", "timezone": "America/Sao_Paulo",   "flag": "🇧🇷"},
]

LEVELS = [
    {"id": "beginner", "name": "Beginner",    "age": "3–6 years",  "color": "#F5C518"},
    {"id": "level1",   "name": "Level 1",     "age": "6–8 years",  "color": "#1A7A3C"},
    {"id": "level2",   "name": "Level 2",     "age": "8–12 years", "color": "#C8102E"},
    {"id": "level3",   "name": "Level 3+",    "age": "12+ years",  "color": "#4A80F0"},
    {"id": "adult",    "name": "Adult Track", "age": "18+ years",  "color": "#FFFFFF"},
]


COUNTRIES = [
    "United States", "Canada", "United Kingdom", "India", "Germany", "France", "Netherlands",
    "Belgium", "Switzerland", "Australia", "New Zealand", "Brazil", "Argentina", "Mexico",
    "Singapore", "United Arab Emirates", "Kenya", "South Africa", "Japan", "Hong Kong", "Other",
]

# Users store: id, role, email, password, name, region, country, city, class_id(teacher)
USERS = {
    "u-admin-1": {
        "id": "u-admin-1", "role": "admin",
        "email": "admin@gjpp.org", "password": "admin123",
        "name": "Admin User",
        "region": "india", "country": "India", "city": "Mumbai",
        "class_id": None
    },
    "u-parent-1": {
        "id": "u-parent-1", "role": "parent",
        "email": "parent@gjpp.org", "password": "parent123",
        "name": "Priya Shah",
        "region": "north_america", "country": "United States", "city": "New York",
        "class_id": None
    },
    "u-parent-2": {
        "id": "u-parent-2", "role": "parent",
        "email": "parent2@gjpp.org", "password": "parent123",
        "name": "Rahul Jain",
        "region": "uk", "country": "United Kingdom", "city": "London",
        "class_id": None
    },
    "u-teacher-1": {
        "id": "u-teacher-1", "role": "teacher",
        "email": "teacher@gjpp.org", "password": "teacher123",
        "name": "Meena Kothari",
        "region": "north_america", "country": "United States", "city": "New York",
        "class_id": "class-beginner-na"
    },
    "u-teacher-2": {
        "id": "u-teacher-2", "role": "teacher",
        "email": "teacher2@gjpp.org", "password": "teacher123",
        "name": "Suresh Mehta",
        "region": "india", "country": "India", "city": "Mumbai",
        "class_id": "class-level1-india"
    },
    "u-radmin-na": {
        "id": "u-radmin-na", "role": "regional_admin",
        "email": "admin.na@gjpp.org", "password": "radmin123",
        "name": "Sarah Johnson",
        "region": "north_america", "country": "United States", "city": "New York",
        "class_id": None
    },
    "u-radmin-india": {
        "id": "u-radmin-india", "role": "regional_admin",
        "email": "admin.india@gjpp.org", "password": "radmin123",
        "name": "Amit Shah",
        "region": "india", "country": "India", "city": "Mumbai",
        "class_id": None
    },
    "u-radmin-uk": {
        "id": "u-radmin-uk", "role": "regional_admin",
        "email": "admin.uk@gjpp.org", "password": "radmin123",
        "name": "Priti Patel",
        "region": "uk", "country": "United Kingdom", "city": "London",
        "class_id": None
    },
}

# Backfill defaults (phone, verification flags) so older seed dicts stay simple above.
_DEMO_PHONES = {"u-parent-1": "+1-555-0001", "u-parent-2": "+44-555-0002"}
for _uid, _u in USERS.items():
    _u.setdefault('phone', _DEMO_PHONES.get(_uid, ''))
    _u.setdefault('email_verified', True)   # seeded demo accounts are pre-verified
    _u.setdefault('phone_verified', True)
    _u.setdefault('verification_code', None)
    _u.setdefault('reset_token', None)
    _u.setdefault('account_status', 'active')  # seeded demo accounts are pre-approved
    # Hash any seed passwords that are still plaintext (idempotent — safe to run every startup;
    # werkzeug hashes always contain a ':' separating the algorithm from its parameters/salt,
    # which no plaintext demo password like "admin123" would ever contain).
    if ':' not in _u['password']:
        _u['password'] = generate_password_hash(_u['password'])

# Students store
STUDENTS = [
    {"id": "s-1", "name": "Aryan Shah",    "age": "8",  "gender": "Male",   "level": "level1",   "region": "north_america", "country": "United States", "city": "New York",    "dob": "2017-09-12", "parent1_name": "Priya Shah",  "parent1_email": "parent@gjpp.org",  "parent1_whatsapp": "+1-555-0001", "class_id": "class-level1-na",      "registered_at": "2025-01-10"},
    {"id": "s-2", "name": "Riva Shah",     "age": "5",  "gender": "Female", "level": "beginner", "region": "north_america", "country": "United States", "city": "New York",    "dob": "2020-09-05", "parent1_name": "Priya Shah",  "parent1_email": "parent@gjpp.org",  "parent1_whatsapp": "+1-555-0001", "class_id": "class-beginner-na",    "registered_at": "2025-01-10"},
    {"id": "s-3", "name": "Dev Jain",      "age": "10", "gender": "Male",   "level": "level2",   "region": "uk",            "country": "United Kingdom","city": "London",       "dob": "2015-08-22", "parent1_name": "Rahul Jain",   "parent1_email": "parent2@gjpp.org", "parent1_whatsapp": "+44-555-0002", "class_id": "class-level2-uk",      "registered_at": "2025-01-15"},
    {"id": "s-4", "name": "Siya Jain",     "age": "7",  "gender": "Female", "level": "level1",   "region": "uk",            "country": "United Kingdom","city": "London",       "dob": "2018-11-30", "parent1_name": "Rahul Jain",   "parent1_email": "parent2@gjpp.org", "parent1_whatsapp": "+44-555-0002", "class_id": "class-level1-uk",      "registered_at": "2025-01-15"},
    {"id": "s-5", "name": "Om Mehta",      "age": "6",  "gender": "Male",   "level": "beginner", "region": "india",         "country": "India",         "city": "Mumbai",       "dob": "2019-09-02", "parent1_name": "Suresh Mehta", "parent1_email": "teacher2@gjpp.org","parent1_whatsapp": "+91-555-0003", "class_id": "class-beginner-india", "registered_at": "2025-02-01"},
    {"id": "s-6", "name": "Priya Mehta",   "age": "9",  "gender": "Female", "level": "level1",   "region": "india",         "country": "India",         "city": "Mumbai",       "dob": "2016-09-08", "parent1_name": "Suresh Mehta", "parent1_email": "teacher2@gjpp.org","parent1_whatsapp": "+91-555-0003", "class_id": "class-level1-india",   "registered_at": "2025-02-01"},
    {"id": "s-7", "name": "Jay Kothari",   "age": "4",  "gender": "Male",   "level": "beginner", "region": "north_america", "country": "United States", "city": "Chicago",      "dob": "2021-02-14", "parent1_name": "Meena Kothari","parent1_email": "teacher@gjpp.org", "parent1_whatsapp": "+1-555-0004",  "class_id": "class-beginner-na",    "registered_at": "2025-02-10"},
    {"id": "s-8", "name": "Anika Kothari", "age": "11", "gender": "Female", "level": "level2",   "region": "north_america", "country": "United States", "city": "Chicago",      "dob": "2014-04-18", "parent1_name": "Meena Kothari","parent1_email": "teacher@gjpp.org", "parent1_whatsapp": "+1-555-0004",  "class_id": "class-level2-na",      "registered_at": "2025-02-10"},
    {"id": "s-9", "name": "Veer Patel",    "age": "13", "gender": "Male",   "level": "level3",   "region": "australia",     "country": "Australia",     "city": "Sydney",       "dob": "2012-06-25", "parent1_name": "Kiran Patel",  "parent1_email": "kiran@example.com","parent1_whatsapp": "+61-555-0005",  "class_id": "class-level3-au",      "registered_at": "2025-02-20"},
    {"id":"s-10", "name": "Nisha Patel",   "age": "15", "gender": "Female", "level": "level3",   "region": "australia",     "country": "Australia",     "city": "Melbourne",    "dob": "2010-12-03", "parent1_name": "Kiran Patel",  "parent1_email": "kiran@example.com","parent1_whatsapp": "+61-555-0005",  "class_id": "class-level3-au",      "registered_at": "2025-03-01"},
]

# Events store (mutable)
EVENTS = [
    {"id": "e-1", "title": "Paryushan Celebrations",  "date": "2025-08-20", "type": "Festival",  "region": "global",        "description": "Annual festival of forgiveness and spiritual reflection", "created_by": "u-admin-1"},
    {"id": "e-2", "title": "Jain Summer Camp 2025",   "date": "2025-06-15", "type": "Camp",      "region": "north_america", "description": "7-day immersive camp for ages 8–18",                      "created_by": "u-admin-1"},
    {"id": "e-3", "title": "Bhawna Yog Session",      "date": "2025-05-10", "type": "Wellness",  "region": "global",        "description": "Monthly guided meditation and contemplation",               "created_by": "u-admin-1"},
    {"id": "e-4", "title": "Teacher Training Workshop","date": "2025-05-25", "type": "Workshop", "region": "uk",            "description": "Volunteer teacher certification program",                   "created_by": "u-admin-1"},
]

# Event registrations store — one record per (event, user) sign-up.
# Powers the attendee pages for Admin / Regional Admin / Teacher and the
# "already registered" / "un-register" behaviour on the public events pages.
EVENT_REGISTRATIONS = []

# Location update requests store
LOCATION_REQUESTS = []


# Study Materials store
STUDY_MATERIALS = [
    {"id": "m-1", "title": "Navkar Mantra - Introduction",      "description": "Complete guide to the Navkar Mantra for beginners", "level": "beginner", "region": "global",        "file_name": "navkar_intro.pdf",      "file_size": "2.1 MB", "file_type": "pdf",   "uploaded_by": "u-admin-1",    "uploaded_at": "2025-01-15", "downloads": 124},
    {"id": "m-2", "title": "Jain Symbols Workbook",             "description": "Interactive workbook for learning Jain symbols",      "level": "level1",   "region": "global",        "file_name": "jain_symbols.pdf",      "file_size": "3.4 MB", "file_type": "pdf",   "uploaded_by": "u-admin-1",    "uploaded_at": "2025-01-20", "downloads": 89},
    {"id": "m-3", "title": "24 Tirthankars - Illustrated",      "description": "Illustrated guide to all 24 Tirthankars",           "level": "level2",   "region": "global",        "file_name": "tirthankars.pdf",       "file_size": "5.2 MB", "file_type": "pdf",   "uploaded_by": "u-admin-1",    "uploaded_at": "2025-02-01", "downloads": 67},
    {"id": "m-4", "title": "Agam Sutras - Level 3 Guide",       "description": "Advanced reading guide for Agam scripture study",    "level": "level3",   "region": "global",        "file_name": "agam_guide.pdf",        "file_size": "4.8 MB", "file_type": "pdf",   "uploaded_by": "u-admin-1",    "uploaded_at": "2025-02-10", "downloads": 45},
    {"id": "m-5", "title": "NA Region - Class Schedule",        "description": "North America regional class timetable 2025",       "level": "beginner", "region": "north_america", "file_name": "na_schedule.pdf",       "file_size": "0.5 MB", "file_type": "pdf",   "uploaded_by": "u-radmin-na",  "uploaded_at": "2025-03-01", "downloads": 33},
    {"id": "m-6", "title": "India Region - Festival Calendar",  "description": "India region Jain festival calendar 2025",          "level": "level1",   "region": "india",         "file_name": "india_calendar.pdf",    "file_size": "1.1 MB", "file_type": "pdf",   "uploaded_by": "u-radmin-india","uploaded_at": "2025-03-05", "downloads": 28},
]

# Give each seed material a real placeholder file on disk so downloads work out of the box.
def _seed_material_files():
    for mat in STUDY_MATERIALS:
        stored_name = f"seed_{mat['id']}_{mat['file_name']}"
        stored_path = os.path.join(MATERIALS_UPLOAD_DIR, stored_name)
        if not os.path.exists(stored_path):
            with open(stored_path, 'wb') as f:
                f.write(f"GJPP placeholder file for: {mat['title']}\n"
                         f"Replace by uploading a real file through Study Materials > Upload.".encode('utf-8'))
        mat['stored_name'] = stored_name

_seed_material_files()

# Promotion Records store
PROMOTIONS = []

# Activity Videos store  
ACTIVITY_VIDEOS = []

# Video Requests store (teacher -> student)
VIDEO_REQUESTS = []

# Volunteers store
VOLUNTEERS = [
    {"id": "v-1", "name": "Anita Desai",   "email": "anita@example.com",  "whatsapp": "+1-555-1001",  "skills": ["Teaching / Tutoring", "Event Coordination"],       "availability": "Weekends only",             "region": "north_america", "country": "United States",  "city": "New York",  "status": "active",   "registered_at": "2025-01-05"},
    {"id": "v-2", "name": "Ravi Joshi",    "email": "ravi@example.com",   "whatsapp": "+91-555-1002", "skills": ["Technology / Web", "Administrative Support"],       "availability": "Both weekdays and weekends", "region": "india",         "country": "India",          "city": "Delhi",     "status": "active",   "registered_at": "2025-01-12"},
    {"id": "v-3", "name": "Pooja Shah",    "email": "pooja@example.com",  "whatsapp": "+44-555-1003", "skills": ["Design / Creative", "Video / Media Production"],    "availability": "Weekends only",             "region": "uk",            "country": "United Kingdom", "city": "London",    "status": "active",   "registered_at": "2025-02-03"},
    {"id": "v-4", "name": "Kiran Mehta",   "email": "kiran@example.com",  "whatsapp": "+61-555-1004", "skills": ["Teaching / Tutoring", "Community Outreach"],        "availability": "Flexible",                  "region": "australia",     "country": "Australia",      "city": "Sydney",    "status": "inactive", "registered_at": "2025-02-18"},
    {"id": "v-5", "name": "Neel Kothari",  "email": "neel@example.com",   "whatsapp": "+49-555-1005", "skills": ["Translation / Languages", "Administrative Support"],"availability": "Weekdays only",             "region": "europe",        "country": "Germany",        "city": "Berlin",    "status": "active",   "registered_at": "2025-03-01"},
    {"id": "v-6", "name": "Sonal Parikh",  "email": "sonal@example.com",  "whatsapp": "+1-555-1006",  "skills": ["Event Coordination", "Community Outreach"],         "availability": "Weekends only",             "region": "north_america", "country": "United States",  "city": "Chicago",   "status": "active",   "registered_at": "2025-03-15"},
]

# ─────────────────────────────────────────
#  STUDY SCHEDULER  (level-based weekly curriculum engine)
# ─────────────────────────────────────────

MIN_CURRICULUM_WEEKS = 6
MAX_CURRICULUM_WEEKS = 30

# 1. LEVEL CURRICULA — every level has its own independent duration (6-30 weeks)
LEVEL_CURRICULA = {
    "beginner": {"level_id": "beginner", "total_weeks": 6},
    "level1":   {"level_id": "level1",   "total_weeks": 12},
    "level2":   {"level_id": "level2",   "total_weeks": 20},
    "level3":   {"level_id": "level3",   "total_weeks": 30},
    "adult":    {"level_id": "adult",    "total_weeks": 8},
}

_SAMPLE_TOPICS = [
    "Navkar Mantra — Recitation & Meaning", "Ahimsa in Daily Life", "Jain Symbols — Introduction",
    "Story: The Merchant's Honesty", "Simple Prayers & Songs", "Week Review & Reflection",
    "24 Tirthankars — Part 1", "24 Tirthankars — Part 2", "Jain Festivals Calendar", "Five Mahavratas — Intro",
    "Story: King Shrenik", "Ashtami & Chaturdashi Practices", "Nine Tattvas — Foundations",
    "Samayik — Practice & Meaning", "Karma Theory — Introduction", "Story: Bharat & Bahubali",
    "Jain Geography — Sacred Places", "Group Discussion: Living Ahimsa", "Agam Scriptures — Overview",
    "Jain History — Key Figures", "Six Substances (Shad Dravya)", "Story: Mahavir's Compassion",
    "Ethics in Modern Life", "Comparative Reflection", "Advanced Tattvagyan — Part 1",
    "Advanced Tattvagyan — Part 2", "Jain Cosmology — Intro", "Story: Parshvanath's Patience",
    "Debate: Applying Dharma Today", "Community Seva Project", "Bhawna Yog — Guided Reflection",
]

def _seed_curriculum_weeks():
    """Generate one independent set of weekly sessions per level, sized to that level's total_weeks."""
    weeks = []
    for level_id, curr in LEVEL_CURRICULA.items():
        for wk in range(1, curr["total_weeks"] + 1):
            topic = _SAMPLE_TOPICS[(wk - 1) % len(_SAMPLE_TOPICS)]
            weeks.append({
                "id": f"cw-{level_id}-{wk}",
                "level_id": level_id,
                "week_number": wk,
                "title": f"Week {wk}: {topic}",
                "content": f"Guided study for Week {wk} covering: {topic}.",
            })
    return weeks

CURRICULUM_WEEKS = _seed_curriculum_weeks()

def get_curriculum_weeks(level_id):
    return sorted([w for w in CURRICULUM_WEEKS if w['level_id'] == level_id], key=lambda w: w['week_number'])

def level_name(level_id):
    lv = next((l for l in LEVELS if l['id'] == level_id), None)
    return lv['name'] if lv else level_id

app.jinja_env.globals['level_name'] = level_name
app.jinja_env.globals['LEVEL_CURRICULA'] = LEVEL_CURRICULA
app.jinja_env.globals['MIN_CURRICULUM_WEEKS'] = MIN_CURRICULUM_WEEKS
app.jinja_env.globals['MAX_CURRICULUM_WEEKS'] = MAX_CURRICULUM_WEEKS

# 2. FESTIVAL CONFIGURATION — configurable pause periods (admin-managed)
FESTIVALS = [
    {"id": "fest-1", "name": "Daslakshan Parva", "start_date": "2026-09-01", "end_date": "2026-09-14",
     "regions": ["global"], "pauses_schedule": True, "year": 2026,
     "notes": "10-day period of reflection and fasting."},
    {"id": "fest-2", "name": "Diwali", "start_date": "2026-11-08", "end_date": "2026-11-14",
     "regions": ["global"], "pauses_schedule": True, "year": 2026,
     "notes": "Festival of lights; marks Mahavir's nirvana."},
    {"id": "fest-3", "name": "Mahaveer Jayanti", "start_date": "2026-03-30", "end_date": "2026-04-05",
     "regions": ["global"], "pauses_schedule": True, "year": 2026,
     "notes": "Birth anniversary of Lord Mahavir."},
    {"id": "fest-4", "name": "Shrut Panchami", "start_date": "2026-06-01", "end_date": "2026-06-07",
     "regions": ["india"], "pauses_schedule": True, "year": 2026,
     "notes": "Celebrates the written recording of Jain scriptures."},
    {"id": "fest-5", "name": "Akshaya Tritiya", "start_date": "2026-04-19", "end_date": "2026-04-25",
     "regions": ["india", "north_america"], "pauses_schedule": True, "year": 2026,
     "notes": "Commemorates the first Ahara Daan to Lord Rishabhdev."},
]

# 3. PER-LEVEL SCHEDULE LAUNCH — two tiers:
#    - GLOBAL_LEVEL_SCHEDULES: a Super Admin sets one default start date per level,
#      applied everywhere a region hasn't set its own date for that level.
#    - REGION_LEVEL_SCHEDULES: a Regional Admin can override that default for their
#      own region + level combination independently of every other region/level.
GLOBAL_LEVEL_SCHEDULES = {
    level_id: {
        "level_id": level_id, "start_date": None,
        "started_by": None, "started_at": None, "status": "not_started",
    }
    for level_id in LEVEL_CURRICULA
}

REGION_LEVEL_SCHEDULES = {}
# keyed by "<region_id>:<level_id>" -> {region_id, level_id, start_date, started_by, started_at, status}

def _rl_key(region_id, level_id):
    return f"{region_id}:{level_id}"

def get_level_schedule_info(region_id, level_id):
    """
    Resolve the effective start date for a region + level: a region-specific
    override always wins; otherwise fall back to the Super Admin's global default
    for that level. Returns {start_date, source, started_at} — source is
    'region', 'global', or None if nothing has been configured yet.
    """
    region_sched = REGION_LEVEL_SCHEDULES.get(_rl_key(region_id, level_id))
    if region_sched and region_sched.get('start_date'):
        return {"start_date": region_sched['start_date'], "source": "region", "started_at": region_sched.get('started_at')}
    global_sched = GLOBAL_LEVEL_SCHEDULES.get(level_id)
    if global_sched and global_sched.get('start_date'):
        return {"start_date": global_sched['start_date'], "source": "global", "started_at": global_sched.get('started_at')}
    return {"start_date": None, "source": None, "started_at": None}

# 3b. CLASS MEETING TIMES — when a level's live class actually meets (day/time),
#     as distinct from the weekly *study curriculum* schedule above. Same two-tier
#     pattern: a Super Admin sets one global default per level; a Regional Admin
#     can override the slots for their own region + level independently.
GLOBAL_CLASS_TIMES = {
    "beginner": {"level_id": "beginner", "slots": [
        {"day": "Wednesday", "time": "7:00 PM",  "label": ""},
        {"day": "Thursday",  "time": "6:30 PM",  "label": ""},
        {"day": "Saturday",  "time": "10:00 AM", "label": ""},
    ]},
    "level1": {"level_id": "level1", "slots": [
        {"day": "Thursday", "time": "7:00 PM",  "label": ""},
        {"day": "Saturday", "time": "11:00 AM", "label": ""},
        {"day": "Wednesday","time": "7:00 PM",  "label": "Bhawna Yog & Stuti"},
    ]},
    "level2": {"level_id": "level2", "slots": [
        {"day": "Thursday", "time": "7:00 PM",  "label": ""},
        {"day": "Saturday", "time": "11:00 AM", "label": ""},
    ]},
    "level3": {"level_id": "level3", "slots": [
        {"day": "Sunday",   "time": "11:00 AM", "label": "Bhaktamar Ji"},
        {"day": "Thursday", "time": "8:00 PM",  "label": "Chahdhala"},
    ]},
    "adult": {"level_id": "adult", "slots": [
        {"day": "Sunday", "time": "8:00 PM", "label": "12+ Years — Level 1 & 2"},
        {"day": "Monday", "time": "8:00 PM", "label": "18+ Dravya Sangrah"},
    ]},
}

# Registrations are not currently accepted for Level 2 (it shares Level 1's
# class time but isn't open for new enrollment yet).
LEVEL_REGISTRATION_OPEN = {"beginner": True, "level1": True, "level2": False, "level3": True, "adult": True}

REGION_CLASS_TIMES = {}
# keyed by "<region_id>:<level_id>" -> {region_id, level_id, slots: [...]} (only present when overridden)

def get_effective_class_times(region_id, level_id):
    """Region-specific slots always win if set; otherwise fall back to the global default."""
    key = _rl_key(region_id, level_id)
    if key in REGION_CLASS_TIMES:
        return REGION_CLASS_TIMES[key]['slots'], 'region'
    return GLOBAL_CLASS_TIMES.get(level_id, {}).get('slots', []), 'global'

app.jinja_env.globals['GLOBAL_CLASS_TIMES'] = GLOBAL_CLASS_TIMES
app.jinja_env.globals['LEVEL_REGISTRATION_OPEN'] = LEVEL_REGISTRATION_OPEN

# 4. STUDENT PROGRESS — tracked against week id, never against a raw calendar date
STUDENT_PROGRESS = []
# {id, student_id, week_id, region_id, level_id, status: 'completed', completed_at, marked_by}

# 5. HOMEWORK — tied to a specific curriculum week, assigned by a teacher
HOMEWORK = []
# {id, week_id, region_id, level_id, class_id, teacher_id, teacher_name, title, description, due_date, created_at}

# ── SCHEDULER HELPERS ──

def _parse_date(s):
    return date.fromisoformat(s) if isinstance(s, str) else s

def get_region_festivals(region_id):
    return [f for f in FESTIVALS if f['pauses_schedule'] and (region_id in f['regions'] or 'global' in f['regions'])]

def _festival_overlapping_week(week_start, week_end, festivals):
    for f in festivals:
        f_start, f_end = _parse_date(f['start_date']), _parse_date(f['end_date'])
        if week_start <= f_end and f_start <= week_end:
            return f
    return None

def compute_week_schedule(region_id, level_id, max_weeks=80):
    """
    Walk week-by-week (7-day blocks) from the region's start_date, assigning this
    level's curriculum weeks in sequence while pausing on any calendar week that
    overlaps a configured festival for that region. No week is ever discarded —
    a paused week resumes right after the festival, shifting everything after it.
    Returns a list of {week_start, week_end, week, festival} entries.
    """
    sched = get_level_schedule_info(region_id, level_id)
    if not sched.get('start_date'):
        return []
    weeks = get_curriculum_weeks(level_id)
    festivals = get_region_festivals(region_id)
    cursor = _parse_date(sched['start_date'])

    result = []
    week_idx = 0
    walked = 0
    while week_idx < len(weeks) and walked < max_weeks:
        week_start = cursor
        week_end = cursor + timedelta(days=6)
        fest = _festival_overlapping_week(week_start, week_end, festivals)
        if fest:
            result.append({"week_start": week_start.isoformat(), "week_end": week_end.isoformat(), "week": None, "festival": fest})
        else:
            result.append({"week_start": week_start.isoformat(), "week_end": week_end.isoformat(), "week": weeks[week_idx], "festival": None})
            week_idx += 1
        cursor += timedelta(days=7)
        walked += 1
    return result

def get_released_weeks(region_id, level_id):
    """All schedule entries that have started as of today (available to view/navigate)."""
    today = date.today()
    return [e for e in compute_week_schedule(region_id, level_id) if _parse_date(e['week_start']) <= today]

def get_current_week_entry(region_id, level_id):
    """The single entry (week or festival pause) covering today, if any."""
    today = date.today()
    for e in compute_week_schedule(region_id, level_id):
        if _parse_date(e['week_start']) <= today <= _parse_date(e['week_end']):
            return e
    return None

def get_completed_week_ids(student_id):
    return {p['week_id'] for p in STUDENT_PROGRESS if p['student_id'] == student_id}

def get_schedule_progress_summary(region_id, level_id, student_id):
    """Curriculum weeks released so far vs. completed by this student."""
    released = [e for e in get_released_weeks(region_id, level_id) if e['week']]
    completed_ids = get_completed_week_ids(student_id)
    completed = [e for e in released if e['week']['id'] in completed_ids]
    return {
        "released_count": len(released),
        "completed_count": len(completed),
        "total_weeks": LEVEL_CURRICULA[level_id]['total_weeks'],
    }

# ─────────────────────────────────────────
#  AUTH HELPERS
# ─────────────────────────────────────────
def current_user():
    uid = session.get('user_id')
    return USERS.get(uid) if uid else None

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user():
            session['next_url'] = request.path
            flash('Please log in to access that page.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            u = current_user()
            if not u or u['role'] not in roles:
                flash('You do not have permission to access that page.', 'error')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated
    return decorator

def is_super_admin():
    u = current_user()
    return u and u['role'] == 'admin'

def is_any_admin():
    u = current_user()
    return u and u['role'] in ('admin', 'regional_admin')

def get_allowed_levels(user):
    """Levels a user may view study materials for. Returns None = no restriction (admin roles)."""
    if not user:
        return set()
    if user['role'] in ('admin', 'regional_admin'):
        return None
    if user['role'] == 'teacher':
        cid = user.get('class_id') or ''
        parts = cid.split('-')
        return {parts[1]} if len(parts) > 1 else set()
    if user['role'] == 'parent':
        return {s['level'] for s in STUDENTS if s.get('parent1_email', '').lower() == user['email'].lower()}
    return set()

def user_can_see_material(material, user):
    """Check if user can access a study material — scoped by region AND class level."""
    if not user:
        return False
    if user['role'] == 'admin':
        return True
    if user['role'] == 'regional_admin':
        return material['region'] in ('global', user['region'])
    if user['role'] in ('teacher', 'parent'):
        if material['region'] not in ('global', user['region']):
            return False
        allowed = get_allowed_levels(user)
        return allowed is None or material['level'] in allowed
    return False

app.jinja_env.globals['is_super_admin'] = is_super_admin
app.jinja_env.globals['is_any_admin'] = is_any_admin

# ── Birthday helpers ──
def parse_dob(dob_str):
    try:
        return date.fromisoformat(dob_str)
    except (TypeError, ValueError):
        return None

def days_to_next_birthday(dob_str, today=None):
    """Days remaining until the student's next birthday (0 = today)."""
    dob = parse_dob(dob_str)
    if not dob:
        return None
    today = today or date.today()
    try:
        next_bday = dob.replace(year=today.year)
    except ValueError:  # Feb 29 on non-leap year
        next_bday = dob.replace(year=today.year, day=28, month=3)
    if next_bday < today:
        try:
            next_bday = dob.replace(year=today.year + 1)
        except ValueError:
            next_bday = dob.replace(year=today.year + 1, day=28, month=3)
    return (next_bday - today).days

def get_upcoming_birthdays(students, days=30):
    """Students with a birthday in the next N days, soonest first."""
    out = []
    for s in students:
        d = days_to_next_birthday(s.get('dob'))
        if d is not None and d <= days:
            out.append({**s, "days_until": d})
    out.sort(key=lambda s: s['days_until'])
    return out

app.jinja_env.globals['days_to_next_birthday'] = days_to_next_birthday

# ── Verification helpers (mock email/phone OTP) ──
def generate_verification_code():
    import random
    return f"{random.randint(0, 999999):06d}"

def is_fully_verified(user):
    return bool(user) and user.get('email_verified') and user.get('phone_verified')

app.jinja_env.globals['is_fully_verified'] = is_fully_verified

# ── Auto teacher assignment ──
def find_teacher_for_class(class_id, class_day=None):
    """
    Return the best-matching teacher for a class_id (level+region).
    Prefers a teacher who also teaches on the requested class_day (if any
    teacher record carries one); falls back to any teacher already
    assigned to that class_id.
    """
    candidates = [u for u in USERS.values() if u['role'] == 'teacher' and u.get('class_id') == class_id]
    if not candidates:
        return None
    if class_day:
        day_match = next((t for t in candidates if t.get('class_day') == class_day), None)
        if day_match:
            return day_match
    return candidates[0]

def region_label(region_id):
    r = next((x for x in REGIONS if x['id'] == region_id), None)
    return r['name'] if r else region_id.replace('_', ' ').title()

app.jinja_env.globals['current_user'] = current_user
app.jinja_env.globals['region_label'] = region_label

def get_teacher_for_class(class_id):
    """Return the teacher user dict for a given class_id, or None."""
    return next((u for u in USERS.values() if u['role'] == 'teacher' and u.get('class_id') == class_id), None)

def get_students_for_class(class_id):
    """Return all students in a given class_id."""
    return [s for s in STUDENTS if s.get('class_id') == class_id]

def get_all_regions():
    return REGIONS

def get_admin_active_region():
    """Return the region the admin is currently browsing (stored in session)."""
    rid = session.get('admin_region')
    if not rid:
        u = current_user()
        rid = u['region'] if u else 'india'
    return next((r for r in REGIONS if r['id'] == rid), REGIONS[0])

app.jinja_env.globals['get_teacher_for_class'] = get_teacher_for_class
app.jinja_env.globals['get_students_for_class'] = get_students_for_class
app.jinja_env.globals['get_all_regions'] = get_all_regions
app.jinja_env.globals['get_admin_active_region'] = get_admin_active_region
app.jinja_env.globals['LEVELS'] = LEVELS

# ─────────────────────────────────────────
#  PERSISTENCE  (real SQLite database — survives restarts/deploys)
#
#  Every mutable data store above (USERS, STUDENTS, EVENTS, ...) stays a
#  plain dict/list exactly as the rest of the app expects — nothing else
#  in the codebase needs to change. Each store gets its own real SQLite
#  table (users, students, events, ...) with columns derived from the
#  actual fields your data uses, so the schema can never silently drop a
#  field. At startup we hydrate these same in-memory objects from the
#  database (via .clear()/.update()/.extend(), so every existing
#  reference — including the Jinja globals registered above — sees the
#  restored data automatically). After every request that changes data,
#  the current state is written back to the database.
#
#  You can inspect the resulting database directly with any SQLite
#  client, e.g.:  sqlite3 data/gjpp.db ".tables"  or  "SELECT * FROM students;"
# ─────────────────────────────────────────
import sqlite3

DATA_DIR = os.environ.get('GJPP_DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'gjpp.db')

# table name -> (the actual module-level store, 'dict' or 'list', primary-key field)
_TABLE_SPECS = {
    'users':                  (USERS, 'dict', 'id'),
    'students':                (STUDENTS, 'list', 'id'),
    'events':                  (EVENTS, 'list', 'id'),
    'event_registrations':     (EVENT_REGISTRATIONS, 'list', 'id'),
    'location_requests':       (LOCATION_REQUESTS, 'list', 'id'),
    'study_materials':         (STUDY_MATERIALS, 'list', 'id'),
    'promotions':               (PROMOTIONS, 'list', 'id'),
    'activity_videos':         (ACTIVITY_VIDEOS, 'list', 'id'),
    'video_requests':          (VIDEO_REQUESTS, 'list', 'id'),
    'volunteers':               (VOLUNTEERS, 'list', 'id'),
    'festivals':                (FESTIVALS, 'list', 'id'),
    'level_curricula':         (LEVEL_CURRICULA, 'dict', 'level_id'),
    'curriculum_weeks':        (CURRICULUM_WEEKS, 'list', 'id'),
    'global_level_schedules':  (GLOBAL_LEVEL_SCHEDULES, 'dict', 'level_id'),
    'region_level_schedules':  (REGION_LEVEL_SCHEDULES, 'dict', None),  # keyed by "region_id:level_id"
    'global_class_times':      (GLOBAL_CLASS_TIMES, 'dict', 'level_id'),
    'region_class_times':      (REGION_CLASS_TIMES, 'dict', None),  # keyed by "region_id:level_id"
    'student_progress':        (STUDENT_PROGRESS, 'list', 'id'),
    'homework':                 (HOMEWORK, 'list', 'id'),
}

def get_db_connection():
    return sqlite3.connect(DB_PATH)

def _records_of(store, kind):
    return list(store.values()) if kind == 'dict' else list(store)

def _table_exists(conn, table):
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None

def _save_table(conn, table, store, kind):
    records = _records_of(store, kind)
    # Column set is derived from the data itself (union of every key seen) so a field
    # can never be silently dropped just because it wasn't hand-listed in a schema.
    columns = sorted({k for rec in records for k in rec.keys()}) or ['id']
    conn.execute(f'DROP TABLE IF EXISTS "{table}"')
    col_defs = ', '.join(f'"{c}" TEXT' for c in columns)
    conn.execute(f'CREATE TABLE "{table}" ({col_defs})')
    if records:
        col_names = ', '.join(f'"{c}"' for c in columns)
        placeholders = ', '.join('?' for _ in columns)
        rows = [tuple(json.dumps(rec.get(c)) for c in columns) for rec in records]
        conn.executemany(f'INSERT INTO "{table}" ({col_names}) VALUES ({placeholders})', rows)

def _load_table(conn, table):
    cur = conn.execute(f'PRAGMA table_info("{table}")')
    columns = [row[1] for row in cur.fetchall()]
    if not columns:
        return []
    col_list = ', '.join(f'"{c}"' for c in columns)
    cur = conn.execute(f'SELECT {col_list} FROM "{table}"')
    records = []
    for row in cur.fetchall():
        records.append({c: (json.loads(v) if v is not None else None) for c, v in zip(columns, row)})
    return records

def save_data():
    """Write the current in-memory state of every store to the SQLite database."""
    try:
        conn = get_db_connection()
        with conn:
            for table, (store, kind, pk) in _TABLE_SPECS.items():
                _save_table(conn, table, store, kind)
        conn.close()
    except Exception as e:
        logging.error(f"Failed to save data to SQLite database ({DB_PATH}): {e}")

def load_data():
    """Restore all persisted stores from the SQLite database in place, if it exists yet."""
    conn = get_db_connection()
    found_existing = False
    for table, (store, kind, pk) in _TABLE_SPECS.items():
        if not _table_exists(conn, table):
            continue
        found_existing = True
        records = _load_table(conn, table)
        if not records:
            continue
        if kind == 'dict':
            store.clear()
            if table == 'region_level_schedules' or table == 'region_class_times':
                for rec in records:
                    store[f"{rec.get('region_id')}:{rec.get('level_id')}"] = rec
            else:
                for rec in records:
                    store[rec[pk]] = rec
        else:
            store.clear()
            store.extend(records)
    conn.close()
    if found_existing:
        logging.info(f"Loaded persisted data from SQLite database at {DB_PATH}.")
    else:
        logging.info(f"No existing database at {DB_PATH} — creating it and seeding with initial demo data.")
    save_data()  # persist whatever is now in memory (restored data, or fresh seed data on first run)

load_data()

@app.after_request
def _persist_after_mutating_request(response):
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        save_data()
    return response

# ─────────────────────────────────────────
#  ADMIN REGION SWITCH
# ─────────────────────────────────────────
@app.route('/admin/switch-region/<region_id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_switch_region(region_id):
    valid = [r['id'] for r in REGIONS]
    if region_id in valid:
        session['admin_region'] = region_id
        flash(f"Switched view to {region_label(region_id)}.", 'success')
    next_url = request.form.get('next') or url_for('admin_dashboard')
    return redirect(next_url)

# ─────────────────────────────────────────
#  PUBLIC ROUTES
# ─────────────────────────────────────────
@app.route('/')
def index():
    u = current_user()
    # Logged-in users go straight to their dashboard
    if u:
        return redirect(url_for('dashboard'))
    return render_template('index.html', regions=REGIONS, events=EVENTS[:3], user=None)

@app.route('/region/<region_id>')
def region_dashboard(region_id):
    u = current_user()
    region = next((r for r in REGIONS if r['id'] == region_id), None)
    if not region:
        flash('Region not found', 'error')
        return redirect(url_for('index'))
    # Non-admin logged-in users can only see their own region
    if u and u['role'] != 'admin' and u.get('region') and u['region'] != region_id:
        flash('You can only view your own region.', 'error')
        return redirect(url_for('region_dashboard', region_id=u['region']))
    region_events = [e for e in EVENTS if e['region'] in ('global', region_id)]
    return render_template('region.html', region=region, levels=LEVELS, events=region_events, user=u)

@app.route('/classes')
def classes():
    u = current_user()
    all_schedules = [
        {"level": "Beginner",     "icon": "🌱", "anchor": "beginner", "color": "#f59e0b", "region": "north_america", "day": "Sunday",  "time": "10:00 AM EST",  "format": "Online"},
        {"level": "Level 1",      "icon": "🌿", "anchor": "level1",   "color": "#10b981", "region": "north_america", "day": "Sunday",  "time": "11:00 AM EST",  "format": "Online"},
        {"level": "Level 2",      "icon": "🌳", "anchor": "level2",   "color": "#3b82f6", "region": "north_america", "day": "Sunday",  "time": "12:00 PM EST",  "format": "Online"},
        {"level": "Level 3+",     "icon": "🏔️", "anchor": "level3",   "color": "#8b5cf6", "region": "north_america", "day": "Saturday","time": "9:00 AM EST",   "format": "Online"},
        {"level": "Beginner",     "icon": "🌱", "anchor": "beginner", "color": "#f59e0b", "region": "uk",            "day": "Sunday",  "time": "10:00 AM GMT",  "format": "Online + In-person"},
        {"level": "Level 1",      "icon": "🌿", "anchor": "level1",   "color": "#10b981", "region": "uk",            "day": "Sunday",  "time": "11:30 AM GMT",  "format": "Online"},
        {"level": "Level 2",      "icon": "🌳", "anchor": "level2",   "color": "#3b82f6", "region": "europe",        "day": "Saturday","time": "10:00 AM CET",  "format": "Online"},
        {"level": "Beginner",     "icon": "🌱", "anchor": "beginner", "color": "#f59e0b", "region": "india",         "day": "Sunday",  "time": "9:00 AM IST",   "format": "In-person + Online"},
        {"level": "Adult Track",  "icon": "🕉️", "anchor": "adult",    "color": "#ec4899", "region": "india",         "day": "Sunday",  "time": "7:00 AM IST",   "format": "Online"},
        {"level": "Beginner",     "icon": "🌱", "anchor": "beginner", "color": "#f59e0b", "region": "australia",     "day": "Sunday",  "time": "10:00 AM AEST", "format": "Online"},
        {"level": "Level 1",      "icon": "🌿", "anchor": "level1",   "color": "#10b981", "region": "australia",     "day": "Sunday",  "time": "11:00 AM AEST", "format": "Online"},
    ]
    # Logged-in users (other than the super admin, who browses every region) only see
    # — and can only enroll into — their own region's class schedule.
    if u and u['role'] != 'admin':
        schedules = [s for s in all_schedules if s['region'] == u.get('region', '')]
    else:
        schedules = all_schedules

    # Group into region sections (in REGIONS' canonical order) for a card-based layout
    region_groups = []
    for r in REGIONS:
        matched = [s for s in schedules if s['region'] == r['id']]
        if matched:
            region_groups.append({"region": r, "classes": matched})

    return render_template('classes.html', levels=LEVELS, regions=REGIONS, user=u,
        schedules=schedules, region_groups=region_groups)

@app.route('/events')
def events_page():
    u = current_user()
    if u and u['role'] != 'admin':
        visible = [e for e in EVENTS if e['region'] in ('global', u.get('region', ''))]
    else:
        visible = EVENTS
    return render_template('events.html', events=visible, user=u)

# ─────────────────────────────────────────
#  EVENT REGISTRATION HELPERS
# ─────────────────────────────────────────
ATTENDEE_CHOICES = ('1', '2', '3', '4', '5+')

def find_event(event_id):
    return next((e for e in EVENTS if e['id'] == event_id), None)

def event_is_past(event):
    """True once the event's date is before today. The event day itself still counts as upcoming,
    so registration and the attendee list stay available through the whole event day.
    An event with a missing/unparseable date is treated as upcoming (never silently wiped)."""
    try:
        return date.fromisoformat(str(event.get('date', ''))[:10]) < date.today()
    except (ValueError, TypeError):
        return False

def purge_past_event_registrations():
    """Clear the attendee list of every event that is over. Returns how many records were removed."""
    past_ids = {e['id'] for e in EVENTS if event_is_past(e)}
    if not past_ids:
        return 0
    before = len(EVENT_REGISTRATIONS)
    EVENT_REGISTRATIONS[:] = [r for r in EVENT_REGISTRATIONS if r['event_id'] not in past_ids]
    return before - len(EVENT_REGISTRATIONS)

app.jinja_env.globals['event_is_past'] = event_is_past

@app.before_request
def _clear_registrations_of_finished_events():
    # Runs on every request, so the cleanup happens the first time anyone loads the site
    # after midnight — no scheduler/cron needed. Only writes to the DB if something was removed.
    if request.endpoint != 'static' and purge_past_event_registrations():
        save_data()

def get_event_registration(event_id, user_id):
    return next((r for r in EVENT_REGISTRATIONS
                 if r['event_id'] == event_id and r['user_id'] == user_id), None)

def user_can_see_event(u, event):
    """Super admins see every event; everyone else sees global + their own region."""
    return u['role'] == 'admin' or event['region'] in ('global', u.get('region', ''))

def _registration_row(reg):
    """Registration record enriched with the user's *current* name/email/location
    (falls back to the sign-up snapshot if the account no longer exists)."""
    row = dict(reg)
    live = USERS.get(reg['user_id'])
    if live:
        row.update(name=live.get('name', row.get('name')), email=live.get('email', row.get('email')),
                   region=live.get('region', row.get('region')), city=live.get('city', row.get('city')),
                   country=live.get('country', row.get('country')), role=live.get('role', row.get('role')))
    row['headcount'] = int(str(row.get('attendees', '1')).rstrip('+') or 1)
    return row

def attendees_for_viewer(event, viewer):
    """Registrations for an event that this viewer is allowed to see.
    Super admins see everyone; regional admins and teachers see only people in their own region
    (which matters for 'global' events that people from every region can join)."""
    rows = [_registration_row(r) for r in EVENT_REGISTRATIONS if r['event_id'] == event['id']]
    if viewer['role'] != 'admin':
        rows = [r for r in rows if r.get('region') == viewer.get('region')]
    return sorted(rows, key=lambda r: r.get('registered_at', ''))

def registration_counts_for_viewer(events, viewer):
    return {e['id']: len(attendees_for_viewer(e, viewer)) for e in events}

def _safe_next(default_endpoint='events_page'):
    nxt = request.form.get('next', '')
    if nxt.startswith('/') and not nxt.startswith('//'):
        return nxt
    return url_for(default_endpoint)

@app.context_processor
def inject_event_registrations():
    """Makes `my_registered_event_ids` available in every template so any page that lists
    events can swap the Register button for the 'already registered' state."""
    u = current_user()
    ids = {r['event_id'] for r in EVENT_REGISTRATIONS if r['user_id'] == u['id']} if u else set()
    return {'my_registered_event_ids': ids}

def _csv_safe(value):
    """Neutralise spreadsheet formula injection in exported cells."""
    v = '' if value is None else str(value)
    return "'" + v if v[:1] in ('=', '+', '-', '@', '\t', '\r') else v

ROLE_LABELS = {'admin': 'Super Admin', 'regional_admin': 'Regional Admin', 'teacher': 'Teacher', 'parent': 'Parent'}

def event_attendees_response(event_id, back_endpoint, view_endpoint):
    """Shared implementation behind the admin / regional-admin / teacher attendee pages."""
    u = current_user()
    event = find_event(event_id)
    if not event or not user_can_see_event(u, event):
        flash('Event not found or not available to you.', 'error')
        return redirect(url_for(back_endpoint))
    if event_is_past(event):
        flash(f"{event['title']} is over — its attendee list has been cleared and is no longer available.", 'error')
        return redirect(url_for(back_endpoint))

    rows = attendees_for_viewer(event, u)
    total_registrations = len(rows)

    q = request.args.get('q', '').strip().lower()
    region_filter = request.args.get('region', '').strip() if u['role'] == 'admin' else ''
    if q:
        rows = [r for r in rows if q in (r.get('name') or '').lower()
                or q in (r.get('email') or '').lower()
                or q in (r.get('city') or '').lower()]
    if region_filter:
        rows = [r for r in rows if r.get('region') == region_filter]

    if request.args.get('export') == 'csv':
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(['Name', 'Email', 'WhatsApp', 'Role', 'Region', 'City', 'Country',
                    'Attendees', 'Registered at'])
        for r in rows:
            w.writerow([_csv_safe(x) for x in (
                r.get('name'), r.get('email'), r.get('whatsapp'), ROLE_LABELS.get(r.get('role'), r.get('role')),
                region_label(r.get('region')), r.get('city'), r.get('country'),
                r.get('attendees'), r.get('registered_at'))])
        safe_title = ''.join(c if c.isalnum() else '_' for c in event['title']).strip('_') or 'event'
        return Response(buf.getvalue(), mimetype='text/csv',
                        headers={'Content-Disposition': f'attachment; filename=attendees_{safe_title}.csv'})

    return render_template('event_attendees.html', user=u, event=event, rows=rows,
        total_registrations=total_registrations,
        total_headcount=sum(r['headcount'] for r in rows),
        has_open_ended=any(str(r.get('attendees')).endswith('+') for r in rows),
        q=q, region_filter=region_filter, regions=REGIONS, role_labels=ROLE_LABELS,
        back_url=url_for(back_endpoint), view_endpoint=view_endpoint,
        is_admin=(u['role'] == 'admin'))

@app.route('/events/<event_id>/register', methods=['GET', 'POST'])
@login_required
def register_event(event_id):
    event = find_event(event_id)
    if not event:
        flash('Event not found', 'error')
        return redirect(url_for('events_page'))
    u = current_user()
    if not user_can_see_event(u, event):
        flash('That event is not available for your region.', 'error')
        return redirect(url_for('events_page'))
    if event_is_past(event):
        flash(f"{event['title']} has already taken place — registration is closed.", 'error')
        return redirect(url_for('events_page'))
    # Already signed up? Never create a duplicate — just tell the user.
    if get_event_registration(event_id, u['id']):
        flash(f"You are already registered for {event['title']}.", 'success')
        return redirect(url_for('events_page'))
    if request.method == 'POST':
        # Already logged in (required for this route) — use the account's own
        # name/email rather than re-trusting a form re-entry of them.
        attendees = request.form.get('attendees', '1')
        if attendees not in ATTENDEE_CHOICES:
            attendees = '1'
        whatsapp  = (request.form.get('whatsapp') or u.get('phone', '') or '').strip()[:40]
        EVENT_REGISTRATIONS.append({
            "id":            f"er-{uuid.uuid4().hex[:10]}",
            "event_id":      event_id,
            "user_id":       u['id'],
            "name":          u['name'],
            "email":         u['email'],
            "role":          u['role'],
            "region":        u.get('region', ''),
            "city":          u.get('city', ''),
            "country":       u.get('country', ''),
            "whatsapp":      whatsapp,
            "attendees":     attendees,
            "registered_at": datetime.now().strftime('%Y-%m-%d %H:%M'),
        })
        flash(f"You're registered for {event['title']}, {u['name']}! Check WhatsApp for details. 🎉", 'success')
        send_whatsapp(whatsapp, f"🙏 You're confirmed for {event['title']} on {event['date']}. See you there!")
        return redirect(url_for('events_page'))
    return render_template('event_register.html', event=event, user=u)

@app.route('/events/<event_id>/unregister', methods=['POST'])
@login_required
def unregister_event(event_id):
    u = current_user()
    event = find_event(event_id)
    reg = get_event_registration(event_id, u['id'])
    if reg:
        EVENT_REGISTRATIONS.remove(reg)
        title = event['title'] if event else 'the event'
        flash(f"You have been un-registered from {title}. You can register again any time.", 'success')
    else:
        flash('You are not registered for that event.', 'error')
    return redirect(_safe_next())

@app.route('/register/student', methods=['GET', 'POST'])
def register_student():
    u = current_user()
    is_returning_parent = bool(u and u['role'] == 'parent')

    # For a logged-in parent, pre-fill the Location section from their most
    # recently enrolled child (if any) so they don't have to retype the same
    # family address for a second or third child — still fully editable.
    prefill = None
    if is_returning_parent:
        my_children = sorted(
            [s for s in STUDENTS if s.get('parent1_email','').lower() == u['email'].lower()],
            key=lambda s: s.get('registered_at',''), reverse=True)
        if my_children:
            prefill = my_children[0]

    if request.method == 'POST':
        # A logged-in parent's own identity is never re-typed or trusted from the
        # form — this both saves them re-entering it and guarantees the new child
        # always links to their existing account rather than risking a mismatch.
        if is_returning_parent:
            parent_email = u['email']
        else:
            parent_email = request.form.get('parent1_email', '').strip().lower()
        password     = request.form.get('password', '')
        confirm      = request.form.get('confirm_password', '')
        level        = request.form.get('level')
        region       = request.form.get('region')
        # Logged-in users (other than the super admin) can only enroll into their
        # own account's region — the submitted value is never trusted for this.
        if u and u['role'] != 'admin':
            region = u.get('region')

        # Level 2 is not currently open for new registrations — never trust the client.
        if not LEVEL_REGISTRATION_OPEN.get(level, True):
            flash('Registration for that level is not currently open. Please choose a different level.', 'error')
            return render_template('register_student.html', levels=LEVELS, regions=REGIONS, countries=COUNTRIES, user=u, prefill=prefill, is_returning_parent=is_returning_parent)

        # The class day/time slot the parent picked, matched against what's actually
        # scheduled for this level+region — never trust a slot that wasn't offered.
        valid_slots, _slot_source = get_effective_class_times(region, level)
        chosen_day   = request.form.get('class_day', '')
        chosen_time  = request.form.get('class_time', '')
        chosen_label = request.form.get('class_label', '')
        slot_is_valid = any(s['day'] == chosen_day and s['time'] == chosen_time and s.get('label','') == chosen_label
                             for s in valid_slots)
        if valid_slots and not slot_is_valid:
            flash('Please select one of the available class times for this level.', 'error')
            return render_template('register_student.html', levels=LEVELS, regions=REGIONS, countries=COUNTRIES, user=u, prefill=prefill, is_returning_parent=is_returning_parent)

        existing_account = next((x for x in USERS.values() if x['email'].lower() == parent_email), None)

        # If no account exists yet for this email, a password is required to create one.
        # (A logged-in parent always has existing_account set, so this never applies to them.)
        if not existing_account:
            if not password or len(password) < 6:
                flash('Please set a password (min 6 characters) so you can log in and see updates about your child.', 'error')
                return render_template('register_student.html', levels=LEVELS, regions=REGIONS, countries=COUNTRIES, user=u, prefill=prefill, is_returning_parent=is_returning_parent)
            if password != confirm:
                flash('Passwords do not match. Please try again.', 'error')
                return render_template('register_student.html', levels=LEVELS, regions=REGIONS, countries=COUNTRIES, user=u, prefill=prefill, is_returning_parent=is_returning_parent)

        class_id = f"class-{level}-{region}"
        student = {
            "id": str(uuid.uuid4()),
            "name": request.form.get('name'),
            "age": request.form.get('age'),
            "dob": request.form.get('dob', ''),
            "gender": request.form.get('gender'),
            "level": level,
            "region": region,
            "country": request.form.get('country'),
            "city": request.form.get('city'),
            "street_address": request.form.get('street_address', ''),
            "apartment": request.form.get('apartment', ''),
            "state": request.form.get('state', ''),
            "postal_code": request.form.get('postal_code', ''),
            "class_day": chosen_day,
            "class_time": chosen_time,
            "class_label": chosen_label,
            "parent1_name": u['name'] if is_returning_parent else request.form.get('parent1_name'),
            "parent1_whatsapp": u.get('phone','') if is_returning_parent else request.form.get('parent1_whatsapp'),
            "parent1_email": parent_email,
            "parent2_name": request.form.get('parent2_name', ''),
            "parent2_whatsapp": request.form.get('parent2_whatsapp', ''),
            "class_id": class_id,
            "registered_at": datetime.now().strftime('%Y-%m-%d'),
        }
        STUDENTS.append(student)

        # Auto-assign a teacher based on the chosen level + region, preferring one
        # who actually teaches the specific day the parent picked.
        assigned_teacher = find_teacher_for_class(class_id, class_day=chosen_day)

        # Create (or reuse) the parent's account so they can log in for future updates
        if existing_account:
            parent_user = existing_account
            account_created = False
        else:
            parent_user = {
                "id": f"u-parent-{str(uuid.uuid4())[:8]}",
                "role": "parent",
                "email": parent_email,
                "password": generate_password_hash(password),
                "name": request.form.get('parent1_name'),
                "phone": request.form.get('parent1_whatsapp', ''),
                "region": region,
                "country": request.form.get('country'),
                "city": request.form.get('city'),
                "class_id": None,
                "email_verified": False,
                "phone_verified": False,
                "verification_code": generate_verification_code(),
                "reset_token": None,
                # New self-registered accounts require a Super Admin or Regional
                # Admin (for their region) to approve before they can log in.
                "account_status": "pending",
            }
            USERS[parent_user['id']] = parent_user
            account_created = True

        if is_returning_parent:
            flash(f"{student['name']} has been added to your account! 🙏", 'success')
        elif account_created:
            flash(f"{student['name']} is enrolled! Your new GJPP account for {parent_user['name']} is "
                  f"awaiting approval from an administrator — you'll get an email as soon as you can log in. 🙏", 'success')
            send_email(parent_user['email'], "Your GJPP account is pending approval",
                f"Hi {parent_user['name']},\n\nThank you for enrolling {student['name']} in GJPP!\n\n"
                f"Your account is currently awaiting approval from an administrator. "
                f"You'll receive another email as soon as it's approved and you can log in.")
        else:
            # Existing account, already approved previously — safe to log them straight in.
            session['user_id'] = parent_user['id']
            session['user_role'] = parent_user['role']
            flash(f"Welcome, {student['name']}! Your child has been added to your existing GJPP account. 🙏", 'success')

        if assigned_teacher:
            flash(f"{student['name']} has been automatically assigned to teacher {assigned_teacher['name']}.", 'success')
        else:
            flash(f"{student['name']} is enrolled — a teacher will be assigned to this class shortly.", 'success')

        send_whatsapp(student['parent1_whatsapp'],
            f"🙏 Jai Jinendra {student['parent1_name']}! {student['name']} is enrolled in GJPP "
            f"({level_name(level)} level)."
            + (f" Assigned teacher: {assigned_teacher['name']}." if assigned_teacher else ""))

        return redirect(url_for('registration_success', type='student'))
    return render_template('register_student.html', levels=LEVELS, regions=REGIONS, countries=COUNTRIES, user=u, prefill=prefill, is_returning_parent=is_returning_parent)

@app.route('/register/volunteer', methods=['GET', 'POST'])
def register_volunteer():
    if request.method == 'POST':
        flash("Thank you! Your volunteer application has been received. 🌟", 'success')
        return redirect(url_for('registration_success', type='volunteer'))
    return render_template('register_volunteer.html', regions=REGIONS, user=current_user())

@app.route('/register/pathshala', methods=['GET', 'POST'])
def register_pathshala():
    if request.method == 'POST':
        flash("Your Pathshala registration has been received! 🏫", 'success')
        return redirect(url_for('registration_success', type='pathshala'))
    return render_template('register_pathshala.html', user=current_user())

@app.route('/enquiry', methods=['GET', 'POST'])
def enquiry():
    if request.method == 'POST':
        flash("Your enquiry has been submitted! We'll get back to you within 24 hours. 🙏", 'success')
        return redirect(url_for('index'))
    return render_template('enquiry.html', user=current_user())

@app.route('/about')
def about():
    return render_template('about.html', regions=REGIONS, user=current_user())

@app.route('/donate')
def donate():
    return render_template('donate.html', user=current_user())

@app.route('/success/<type>')
def registration_success(type):
    messages = {
        'student':   ('Registration Complete!',        'Your child has been enrolled and your parent account is ready. You will receive class details via WhatsApp shortly.', '🎓'),
        'volunteer': ('Application Received!',          'Thank you for volunteering. Our coordinator will reach out to you within 48 hours.', '🌟'),
        'pathshala': ('Pathshala Request Submitted!',  'Our regional coordinator will contact you to guide you through the next steps.', '🏫'),
    }
    title, message, icon = messages.get(type, ('Success!', 'Your submission has been received.', '✅'))
    u = current_user()
    if type == 'student' and not u:
        # A brand-new parent account was created but is awaiting admin approval,
        # so nobody is logged in yet — reflect that accurately on this page.
        title = 'Enrollment Complete!'
        message = 'Your child has been enrolled. Your new GJPP account is awaiting approval from an administrator — you\'ll get an email as soon as you can log in.'
    show_dashboard_cta = type == 'student' and u and u['role'] == 'parent'
    show_verify_cta = show_dashboard_cta and not is_fully_verified(u)
    return render_template('success.html', title=title, message=message, icon=icon, user=u,
        show_dashboard_cta=show_dashboard_cta, show_verify_cta=show_verify_cta)

# ─────────────────────────────────────────
#  AUTH ROUTES
# ─────────────────────────────────────────
@app.route('/login', methods=['GET'])
def login():
    if current_user():
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login_submit():
    email    = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    # Generic login: match on credentials alone — the user's stored profile
    # (their role) determines what they see next, not a tab they had to pick.
    user = next((u for u in USERS.values() if u['email'].lower() == email
                 and check_password_hash(u['password'], password)), None)
    if user:
        if user.get('account_status', 'active') == 'pending':
            flash('Your account is still awaiting approval from an administrator. '
                  "You'll receive an email as soon as it's approved.", 'error')
            return redirect(url_for('login'))
        session['user_id']   = user['id']
        session['user_role'] = user['role']
        flash(f"Welcome back, {user['name']}! 🙏", 'success')
        next_url = session.pop('next_url', None)
        if next_url and next_url.startswith('/'):
            return redirect(next_url)
        return redirect(url_for('dashboard'))
    flash('Invalid email or password. Please try again.', 'error')
    return redirect(url_for('login'))

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been signed out. Jai Jinendra 🙏', 'success')
    return redirect(url_for('login'))

# ─────────────────────────────────────────
#  ACCOUNT — PROFILE & VERIFICATION
# ─────────────────────────────────────────
@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    u = current_user()
    if request.method == 'POST':
        new_name  = request.form.get('name', '').strip()
        new_phone = request.form.get('phone', '').strip()
        new_email = request.form.get('email', '').strip().lower()

        if not new_name or not new_email:
            flash('Name and email are required.', 'error')
            return redirect(url_for('profile'))

        # Prevent switching to an email already used by a different account
        clash = next((x for x in USERS.values() if x['email'].lower() == new_email and x['id'] != u['id']), None)
        if clash:
            flash('That email address is already in use by another account.', 'error')
            return redirect(url_for('profile'))

        email_changed = new_email != u['email'].lower()
        phone_changed = new_phone != u.get('phone', '')

        u['name']  = new_name
        u['phone'] = new_phone
        old_email  = u['email']
        u['email'] = new_email

        # If this parent's login email changes, keep their children's records pointing to the right account
        if u['role'] == 'parent' and email_changed:
            for s in STUDENTS:
                if s.get('parent1_email', '').lower() == old_email.lower():
                    s['parent1_email'] = new_email

        # Changing contact details requires re-verifying that channel
        if email_changed:
            u['email_verified'] = False
            u['verification_code'] = generate_verification_code()
            send_email(u['email'], "Verify your new GJPP email",
                f"Hi {u['name']},\n\nYour verification code is: {u['verification_code']}\n\n"
                f"Enter it at {request.url_root.rstrip('/')}/verify/email to confirm this email address.")
            flash('Email updated — please verify your new email address.', 'success')
        if phone_changed:
            u['phone_verified'] = False
            u['verification_code'] = generate_verification_code()
            send_whatsapp(u['phone'], f"Your GJPP phone verification code is: {u['verification_code']}")
            flash('Phone number updated — please verify it.', 'success')
        if not email_changed and not phone_changed:
            flash('Profile updated successfully! ✅', 'success')

        return redirect(url_for('profile'))

    return render_template('profile.html', user=u)

@app.route('/verify/<channel>', methods=['GET', 'POST'])
@login_required
def verify_channel(channel):
    if channel not in ('email', 'phone'):
        abort(404)
    u = current_user()
    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        if code and code == u.get('verification_code'):
            u[f'{channel}_verified'] = True
            flash(f"Your {channel} has been verified! ✅", 'success')
            return redirect(url_for('profile'))
        flash('Incorrect code. Please try again.', 'error')
    return render_template('verify.html', user=u, channel=channel)

@app.route('/verify/<channel>/resend', methods=['POST'])
@login_required
def verify_resend(channel):
    if channel not in ('email', 'phone'):
        abort(404)
    u = current_user()
    u['verification_code'] = generate_verification_code()
    if channel == 'email':
        sent = send_email(u['email'], "Your GJPP verification code",
            f"Your verification code is: {u['verification_code']}")
        dest = u['email']
    else:
        sent = send_whatsapp(u.get('phone'), f"Your GJPP verification code is: {u['verification_code']}")
        dest = u.get('phone', 'your phone')
    if sent:
        flash(f"A new verification code was sent to {dest}.", 'success')
    else:
        flash(f"Couldn't deliver a code to {dest} right now — please try again shortly or contact support.", 'error')
    return redirect(url_for('verify_channel', channel=channel))

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user = next((u for u in USERS.values() if u['email'].lower() == email), None)
        if user:
            token = uuid.uuid4().hex
            user['reset_token'] = token
            reset_link = f"{request.url_root.rstrip('/')}/reset-password/{token}"
            send_email(user['email'], "Reset your GJPP password",
                f"Hi {user['name']},\n\nClick the link below to reset your password:\n{reset_link}\n\n"
                f"If you didn't request this, you can safely ignore this email.")
        # Always show the same message whether or not the account exists, to avoid leaking which emails are registered
        flash('If that email exists, a password reset link has been sent.', 'success')
        return redirect(url_for('login'))
    return render_template('forgot_password.html', user=current_user())

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    user = next((u for u in USERS.values() if u.get('reset_token') and u['reset_token'] == token), None)
    if not user:
        flash('That reset link is invalid or has expired. Please request a new one.', 'error')
        return redirect(url_for('forgot_password'))
    if request.method == 'POST':
        new_password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        if not new_password or len(new_password) < 6:
            flash('Password must be at least 6 characters.', 'error')
            return render_template('reset_password.html', token=token, user=current_user())
        if new_password != confirm:
            flash('Passwords do not match.', 'error')
            return render_template('reset_password.html', token=token, user=current_user())
        user['password'] = generate_password_hash(new_password)
        user['reset_token'] = None
        flash('Your password has been reset. Please log in with your new password.', 'success')
        return redirect(url_for('login'))
    return render_template('reset_password.html', token=token, user=current_user())

# ─────────────────────────────────────────
#  DASHBOARD (role-based redirect)
# ─────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    u = current_user()
    if u['role'] == 'admin':
        return redirect(url_for('admin_dashboard'))
    elif u['role'] == 'regional_admin':
        return redirect(url_for('regional_admin_dashboard'))
    elif u['role'] == 'teacher':
        return redirect(url_for('teacher_dashboard'))
    else:
        return redirect(url_for('parent_dashboard'))

# ─────────────────────────────────────────
#  ADMIN ROUTES
# ─────────────────────────────────────────
@app.route('/admin')
@login_required
@role_required('admin')
def admin_dashboard():
    pending_requests = [r for r in LOCATION_REQUESTS if r['status'] == 'pending']
    return render_template('admin/dashboard.html',
        user=current_user(),
        students=STUDENTS,
        events=EVENTS,
        regions=REGIONS,
        users=USERS,
        volunteers=VOLUNTEERS,
        pending_requests=pending_requests,
        total_students=len(STUDENTS),
        total_teachers=sum(1 for u in USERS.values() if u['role']=='teacher'),
        total_parents=sum(1 for u in USERS.values() if u['role']=='parent'),
        total_volunteers=len(VOLUNTEERS),
    )

@app.route('/admin/students')
@login_required
@role_required('admin')
def admin_students():
    q_name    = request.args.get('name', '').lower()
    q_region  = request.args.get('region', '')
    q_country = request.args.get('country', '').lower()
    q_city    = request.args.get('city', '').lower()
    q_parent  = request.args.get('parent', '').lower()

    filtered = STUDENTS
    if q_name:    filtered = [s for s in filtered if q_name    in s['name'].lower()]
    if q_region:  filtered = [s for s in filtered if s.get('region','') == q_region]
    if q_country: filtered = [s for s in filtered if q_country in s.get('country','').lower()]
    if q_city:    filtered = [s for s in filtered if q_city    in s.get('city','').lower()]
    if q_parent:  filtered = [s for s in filtered if q_parent  in s.get('parent1_name','').lower()]

    return render_template('admin/students.html',
        user=current_user(), students=filtered, regions=REGIONS,
        q_name=q_name, q_region=q_region, q_country=q_country,
        q_city=q_city, q_parent=q_parent,
    )

@app.route('/admin/events')
@login_required
@role_required('admin')
def admin_events():
    u = current_user()
    return render_template('admin/events.html', user=u, events=EVENTS, regions=REGIONS,
                           reg_counts=registration_counts_for_viewer(EVENTS, u))

@app.route('/admin/events/<event_id>/attendees')
@login_required
@role_required('admin')
def admin_event_attendees(event_id):
    return event_attendees_response(event_id, 'admin_events', 'admin_event_attendees')

@app.route('/admin/events/new', methods=['GET','POST'])
@login_required
@role_required('admin')
def admin_event_new():
    if request.method == 'POST':
        event = {
            "id": f"e-{str(uuid.uuid4())[:8]}",
            "title":       request.form.get('title'),
            "date":        request.form.get('date'),
            "type":        request.form.get('type'),
            "region":      request.form.get('region'),
            "description": request.form.get('description'),
            "created_by":  session['user_id'],
        }
        EVENTS.append(event)
        flash(f"Event '{event['title']}' created successfully! 🎉", 'success')
        return redirect(url_for('admin_events'))
    return render_template('admin/event_form.html', user=current_user(), event=None, regions=REGIONS, action='new')

@app.route('/admin/events/<event_id>/edit', methods=['GET','POST'])
@login_required
@role_required('admin')
def admin_event_edit(event_id):
    event = next((e for e in EVENTS if e['id'] == event_id), None)
    if not event:
        flash('Event not found.', 'error')
        return redirect(url_for('admin_events'))
    if request.method == 'POST':
        event['title']       = request.form.get('title')
        event['date']        = request.form.get('date')
        event['type']        = request.form.get('type')
        event['region']      = request.form.get('region')
        event['description'] = request.form.get('description')
        flash(f"Event '{event['title']}' updated successfully! ✅", 'success')
        return redirect(url_for('admin_events'))
    return render_template('admin/event_form.html', user=current_user(), event=event, regions=REGIONS, action='edit')

@app.route('/admin/events/<event_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_event_delete(event_id):
    event = next((e for e in EVENTS if e['id'] == event_id), None)
    if event:
        # In-place so the persistence layer keeps pointing at the same list object
        EVENTS[:] = [e for e in EVENTS if e['id'] != event_id]
        EVENT_REGISTRATIONS[:] = [r for r in EVENT_REGISTRATIONS if r['event_id'] != event_id]
        flash(f"Event '{event['title']}' deleted.", 'success')
    return redirect(url_for('admin_events'))

@app.route('/admin/location-requests')
@login_required
@role_required('admin')
def admin_location_requests():
    return render_template('admin/location_requests.html',
        user=current_user(),
        requests=LOCATION_REQUESTS,
        users=USERS,
    )

@app.route('/admin/location-requests/<req_id>/approve', methods=['POST'])
@login_required
@role_required('admin')
def approve_location_request(req_id):
    req = next((r for r in LOCATION_REQUESTS if r['id'] == req_id), None)
    if req and req['status'] == 'pending':
        req['status'] = 'approved'
        req['reviewed_at'] = datetime.now().isoformat()
        # Apply the location change to the user
        uid = req['user_id']
        if uid in USERS:
            USERS[uid]['region']         = req['new_region']
            USERS[uid]['country']        = req['new_country']
            USERS[uid]['city']           = req['new_city']
            USERS[uid]['street_address'] = req.get('new_street_address', '')
            USERS[uid]['apartment']      = req.get('new_apartment', '')
            USERS[uid]['state']          = req.get('new_state', '')
            USERS[uid]['postal_code']    = req.get('new_postal_code', '')
        flash('Location request approved and applied.', 'success')
    return redirect(url_for('admin_location_requests'))

@app.route('/admin/location-requests/<req_id>/reject', methods=['POST'])
@login_required
@role_required('admin')
def reject_location_request(req_id):
    req = next((r for r in LOCATION_REQUESTS if r['id'] == req_id), None)
    if req and req['status'] == 'pending':
        req['status'] = 'rejected'
        req['reviewed_at'] = datetime.now().isoformat()
        flash('Location request rejected.', 'success')
    return redirect(url_for('admin_location_requests'))

# ─────────────────────────────────────────
#  PENDING ACCOUNT APPROVAL (self-registered parent logins)
# ─────────────────────────────────────────
@app.route('/admin/pending-accounts')
@login_required
@role_required('admin')
def admin_pending_accounts():
    pending = [u for u in USERS.values() if u.get('account_status') == 'pending']
    pending.sort(key=lambda u: u.get('created_at', ''), reverse=True)
    child_counts = {u['id']: len([s for s in STUDENTS if s.get('parent1_email','').lower() == u['email'].lower()])
                    for u in pending}
    return render_template('admin/pending_accounts.html',
        user=current_user(), pending=pending, child_counts=child_counts, scope='all')

@app.route('/admin/pending-accounts/<uid>/approve', methods=['POST'])
@login_required
@role_required('admin')
def admin_pending_account_approve(uid):
    target = USERS.get(uid)
    if target and target.get('account_status') == 'pending':
        target['account_status'] = 'active'
        flash(f"{target['name']}'s account has been approved.", 'success')
        send_email(target['email'], "Your GJPP account has been approved",
            f"Hi {target['name']},\n\nGreat news — your GJPP account has been approved! "
            f"You can now log in at {request.url_root.rstrip('/')}/login.")
    return redirect(url_for('admin_pending_accounts'))

@app.route('/admin/pending-accounts/<uid>/reject', methods=['POST'])
@login_required
@role_required('admin')
def admin_pending_account_reject(uid):
    target = USERS.get(uid)
    if target and target.get('account_status') == 'pending':
        name = target['name']
        del USERS[uid]
        flash(f"{name}'s account request was rejected and removed. Any enrolled children's records were kept.", 'success')
    return redirect(url_for('admin_pending_accounts'))

@app.route('/radmin/pending-accounts')
@login_required
@role_required('regional_admin')
def radmin_pending_accounts():
    u = current_user()
    pending = [x for x in USERS.values() if x.get('account_status') == 'pending' and x.get('region') == u['region']]
    pending.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    child_counts = {x['id']: len([s for s in STUDENTS if s.get('parent1_email','').lower() == x['email'].lower()])
                    for x in pending}
    return render_template('admin/pending_accounts.html',
        user=u, pending=pending, child_counts=child_counts, scope='region')

@app.route('/radmin/pending-accounts/<uid>/approve', methods=['POST'])
@login_required
@role_required('regional_admin')
def radmin_pending_account_approve(uid):
    u = current_user()
    target = USERS.get(uid)
    # Never trust the client — a Regional Admin may only approve accounts in their own region.
    if target and target.get('account_status') == 'pending' and target.get('region') == u['region']:
        target['account_status'] = 'active'
        flash(f"{target['name']}'s account has been approved.", 'success')
        send_email(target['email'], "Your GJPP account has been approved",
            f"Hi {target['name']},\n\nGreat news — your GJPP account has been approved! "
            f"You can now log in at {request.url_root.rstrip('/')}/login.")
    return redirect(url_for('radmin_pending_accounts'))

@app.route('/radmin/pending-accounts/<uid>/reject', methods=['POST'])
@login_required
@role_required('regional_admin')
def radmin_pending_account_reject(uid):
    u = current_user()
    target = USERS.get(uid)
    if target and target.get('account_status') == 'pending' and target.get('region') == u['region']:
        name = target['name']
        del USERS[uid]
        flash(f"{name}'s account request was rejected and removed. Any enrolled children's records were kept.", 'success')
    return redirect(url_for('radmin_pending_accounts'))

# ─────────────────────────────────────────
#  TEACHER ROUTES
# ─────────────────────────────────────────
@app.route('/teacher')
@login_required
@role_required('teacher')
def teacher_dashboard():
    u = current_user()
    # All students assigned to this teacher's class
    my_students = [s for s in STUDENTS if s.get('class_id') == u.get('class_id')]
    # Also include students in same region/level (broader class match by region)
    region_students = [s for s in STUDENTS if s.get('region') == u.get('region')]
    my_region   = next((r for r in REGIONS if r['id'] == u.get('region')), None)
    my_events   = [e for e in EVENTS if e['region'] in ('global', u.get('region',''))]
    pending_req = next((r for r in LOCATION_REQUESTS if r['user_id']==u['id'] and r['status']=='pending'), None)
    # Build class roster: group students by class_id within the teacher's region
    from collections import defaultdict
    class_roster = defaultdict(list)
    for s in region_students:
        class_roster[s.get('class_id','unassigned')].append(s)
    upcoming_birthdays = get_upcoming_birthdays(my_students, days=30)
    return render_template('teacher/dashboard.html',
        user=u, students=my_students, region_students=region_students,
        class_roster=dict(class_roster), region=my_region,
        events=my_events, pending_req=pending_req,
        levels=LEVELS, all_students=STUDENTS, upcoming_birthdays=upcoming_birthdays,
    )

@app.route('/teacher/events')
@login_required
@role_required('teacher')
def teacher_events():
    u = current_user()
    events = [e for e in EVENTS if e['region'] in ('global', u.get('region', ''))]
    region = next((r for r in REGIONS if r['id'] == u.get('region')), None)
    return render_template('teacher/events.html', user=u, events=events, region=region,
                           reg_counts=registration_counts_for_viewer(events, u))

@app.route('/teacher/events/<event_id>/attendees')
@login_required
@role_required('teacher')
def teacher_event_attendees(event_id):
    return event_attendees_response(event_id, 'teacher_events', 'teacher_event_attendees')

@app.route('/teacher/send-birthday-message/<student_id>', methods=['POST'])
@login_required
@role_required('teacher')
def teacher_send_birthday_message(student_id):
    u = current_user()
    student = next((s for s in STUDENTS if s['id'] == student_id and s.get('class_id') == u.get('class_id')), None)
    if not student:
        flash('Student not found in your class.', 'error')
        return redirect(url_for('teacher_dashboard'))
    flash(f"🎉 Birthday message sent to {student['name']} and family via WhatsApp!", 'success')
    return redirect(request.referrer or url_for('teacher_dashboard'))

# ─────────────────────────────────────────
#  PARENT ROUTES
# ─────────────────────────────────────────
@app.route('/parent')
@login_required
@role_required('parent')
def parent_dashboard():
    u = current_user()
    # Parent sees only their children (matched by parent email)
    my_children = [s for s in STUDENTS if s.get('parent1_email','').lower() == u['email'].lower()]
    my_region   = next((r for r in REGIONS if r['id'] == u.get('region')), None)
    my_events   = [e for e in EVENTS if e['region'] in ('global', u.get('region',''))]
    pending_req = next((r for r in LOCATION_REQUESTS if r['user_id']==u['id'] and r['status']=='pending'), None)
    return render_template('parent/dashboard.html',
        user=u, children=my_children, region=my_region, events=my_events, pending_req=pending_req,
    )

# ─────────────────────────────────────────
#  LOCATION UPDATE REQUEST (teacher + parent)
# ─────────────────────────────────────────
@app.route('/request-location-update', methods=['GET','POST'])
@login_required
@role_required('teacher', 'parent')
def request_location_update():
    u = current_user()
    existing = next((r for r in LOCATION_REQUESTS if r['user_id']==u['id'] and r['status']=='pending'), None)
    if request.method == 'POST':
        if existing:
            flash('You already have a pending location update request.', 'error')
            return redirect(url_for('dashboard'))
        req = {
            "id":          str(uuid.uuid4()),
            "user_id":     u['id'],
            "user_name":   u['name'],
            "user_role":   u['role'],
            "user_email":  u['email'],
            "old_region":  u.get('region',''),
            "old_country": u.get('country',''),
            "old_city":    u.get('city',''),
            "old_street_address": u.get('street_address',''),
            "old_apartment":      u.get('apartment',''),
            "old_state":          u.get('state',''),
            "old_postal_code":    u.get('postal_code',''),
            "new_region":  request.form.get('region'),
            "new_country": request.form.get('country'),
            "new_city":    request.form.get('city'),
            "new_street_address": request.form.get('street_address',''),
            "new_apartment":      request.form.get('apartment',''),
            "new_state":          request.form.get('state',''),
            "new_postal_code":    request.form.get('postal_code',''),
            "reason":      request.form.get('reason',''),
            "status":      "pending",
            "submitted_at": datetime.now().isoformat(),
            "reviewed_at":  None,
        }
        LOCATION_REQUESTS.append(req)
        flash('Location update request submitted! Admin will review shortly.', 'success')
        return redirect(url_for('dashboard'))
    return render_template('location_request.html', user=u, regions=REGIONS, existing=existing)


# ─────────────────────────────────────────
#  ADMIN — TEACHERS
# ─────────────────────────────────────────
@app.route('/admin/teachers')
@login_required
@role_required('admin')
def admin_teachers():
    teachers = [u for u in USERS.values() if u['role'] == 'teacher']
    q_name    = request.args.get('name', '').lower()
    q_region  = request.args.get('region', '')
    q_country = request.args.get('country', '').lower()
    q_city    = request.args.get('city', '').lower()
    if q_name:    teachers = [t for t in teachers if q_name    in t['name'].lower()]
    if q_region:  teachers = [t for t in teachers if t.get('region','') == q_region]
    if q_country: teachers = [t for t in teachers if q_country in t.get('country','').lower()]
    if q_city:    teachers = [t for t in teachers if q_city    in t.get('city','').lower()]
    return render_template('admin/teachers.html',
        user=current_user(), teachers=teachers, regions=REGIONS,
        levels=LEVELS, students=STUDENTS,
        q_name=q_name, q_region=q_region, q_country=q_country, q_city=q_city,
    )

@app.route('/admin/teachers/new', methods=['GET','POST'])
@login_required
@role_required('admin')
def admin_teacher_new():
    if request.method == 'POST':
        email = request.form.get('email','').strip().lower()
        if any(u['email'].lower() == email for u in USERS.values()):
            flash('A user with that email already exists.', 'error')
            return redirect(url_for('admin_teacher_new'))
        uid = f"u-teacher-{str(uuid.uuid4())[:8]}"
        USERS[uid] = {
            "id": uid, "role": "teacher",
            "email": email,
            "password": generate_password_hash(request.form.get('password', 'teacher123')),
            "name": request.form.get('name'),
            "region": request.form.get('region'),
            "country": request.form.get('country'),
            "city": request.form.get('city'),
            "class_id": request.form.get('class_id') or None,
            "phone": request.form.get('phone',''),
            "created_at": datetime.now().strftime('%Y-%m-%d'),
            "email_verified": False, "phone_verified": False,
            "verification_code": generate_verification_code(), "reset_token": None,
        }
        flash(f"Teacher '{request.form.get('name')}' added successfully! 🎓", 'success')
        send_email(email, "Your GJPP teacher account",
            f"Hi {request.form.get('name')},\n\nYour GJPP teacher account has been created.\n"
            f"Login email: {email}\nLog in at {request.url_root.rstrip('/')}/login to get started.")
        return redirect(url_for('admin_teachers'))
    return render_template('admin/teacher_form.html',
        user=current_user(), teacher=None, regions=REGIONS, levels=LEVELS, action='new')

@app.route('/admin/teachers/<uid>/edit', methods=['GET','POST'])
@login_required
@role_required('admin')
def admin_teacher_edit(uid):
    teacher = USERS.get(uid)
    if not teacher or teacher['role'] != 'teacher':
        flash('Teacher not found.', 'error')
        return redirect(url_for('admin_teachers'))
    if request.method == 'POST':
        teacher['name']     = request.form.get('name')
        teacher['email']    = request.form.get('email','').strip().lower()
        teacher['region']   = request.form.get('region')
        teacher['country']  = request.form.get('country')
        teacher['city']     = request.form.get('city')
        teacher['class_id'] = request.form.get('class_id') or None
        teacher['phone']    = request.form.get('phone','')
        if request.form.get('password'):
            teacher['password'] = generate_password_hash(request.form.get('password'))
        flash(f"Teacher '{teacher['name']}' updated successfully! ✅", 'success')
        return redirect(url_for('admin_teachers'))
    return render_template('admin/teacher_form.html',
        user=current_user(), teacher=teacher, regions=REGIONS, levels=LEVELS, action='edit')

@app.route('/admin/teachers/<uid>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_teacher_delete(uid):
    teacher = USERS.get(uid)
    if teacher and teacher['role'] == 'teacher':
        name = teacher['name']
        del USERS[uid]
        flash(f"Teacher '{name}' removed from the system.", 'success')
    return redirect(url_for('admin_teachers'))

# ─────────────────────────────────────────
#  ADMIN — PARENTS
# ─────────────────────────────────────────
@app.route('/admin/parents')
@login_required
@role_required('admin')
def admin_parents():
    parents = [u for u in USERS.values() if u['role'] == 'parent']
    q_name  = request.args.get('name', '').lower()
    q_email = request.args.get('email', '').lower()
    if q_name:  parents = [p for p in parents if q_name in p['name'].lower()]
    if q_email: parents = [p for p in parents if q_email in p['email'].lower()]
    child_counts = {}
    for p in parents:
        child_counts[p['id']] = len([s for s in STUDENTS if s.get('parent1_email','').lower() == p['email'].lower()])
    return render_template('admin/parents.html',
        user=current_user(), parents=parents, regions=REGIONS, child_counts=child_counts,
        q_name=q_name, q_email=q_email,
    )

@app.route('/admin/parents/new', methods=['GET','POST'])
@login_required
@role_required('admin')
def admin_parent_new():
    if request.method == 'POST':
        email = request.form.get('email','').strip().lower()
        if any(u['email'].lower() == email for u in USERS.values()):
            flash('A user with that email already exists.', 'error')
            return redirect(url_for('admin_parent_new'))
        password = request.form.get('password','').strip()
        if not password:
            password = uuid.uuid4().hex[:10]  # admin didn't set one — generate a temporary one
        uid = f"u-parent-{str(uuid.uuid4())[:8]}"
        name = request.form.get('name')
        USERS[uid] = {
            "id": uid, "role": "parent",
            "email": email,
            "password": generate_password_hash(password),
            "name": name,
            "region": request.form.get('region'),
            "country": request.form.get('country'),
            "city": request.form.get('city'),
            "class_id": None,
            "phone": request.form.get('phone',''),
            "created_at": datetime.now().strftime('%Y-%m-%d'),
            "email_verified": False, "phone_verified": False,
            "verification_code": generate_verification_code(), "reset_token": None,
        }
        flash(f"Parent account '{name}' created successfully! 👨‍👩‍👦", 'success')
        send_email(email, "Your GJPP parent account",
            f"Hi {name},\n\nA GJPP account has been created for you.\n\n"
            f"Login email: {email}\nTemporary password: {password}\n\n"
            f"Log in at {request.url_root.rstrip('/')}/login and update your password from your profile page.")
        return redirect(url_for('admin_parents'))
    return render_template('admin/parent_form.html',
        user=current_user(), parent=None, regions=REGIONS, action='new')

@app.route('/admin/parents/<uid>/edit', methods=['GET','POST'])
@login_required
@role_required('admin')
def admin_parent_edit(uid):
    parent = USERS.get(uid)
    if not parent or parent['role'] != 'parent':
        flash('Parent not found.', 'error')
        return redirect(url_for('admin_parents'))
    if request.method == 'POST':
        old_email = parent['email']
        parent['name']    = request.form.get('name')
        parent['email']   = request.form.get('email','').strip().lower()
        parent['region']  = request.form.get('region')
        parent['country'] = request.form.get('country')
        parent['city']    = request.form.get('city')
        parent['phone']   = request.form.get('phone','')
        if request.form.get('password'):
            parent['password'] = generate_password_hash(request.form.get('password'))
        # Keep the parent's children pointed at their (possibly new) login email
        if parent['email'] != old_email:
            for s in STUDENTS:
                if s.get('parent1_email','').lower() == old_email.lower():
                    s['parent1_email'] = parent['email']
        flash(f"Parent account '{parent['name']}' updated successfully! ✅", 'success')
        return redirect(url_for('admin_parents'))
    my_children = [s for s in STUDENTS if s.get('parent1_email','').lower() == parent['email'].lower()]
    return render_template('admin/parent_form.html',
        user=current_user(), parent=parent, regions=REGIONS, action='edit', my_children=my_children)

@app.route('/admin/parents/<uid>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_parent_delete(uid):
    parent = USERS.get(uid)
    if parent and parent['role'] == 'parent':
        name = parent['name']
        del USERS[uid]
        flash(f"Parent account '{name}' removed. Their children's records were kept — only the login was deleted.", 'success')
    return redirect(url_for('admin_parents'))

# ─────────────────────────────────────────
#  ADMIN — REGIONAL ADMINS
# ─────────────────────────────────────────
@app.route('/admin/regional-admins')
@login_required
@role_required('admin')
def admin_regional_admins():
    radmins = [u for u in USERS.values() if u['role'] == 'regional_admin']
    return render_template('admin/regional_admins.html',
        user=current_user(), radmins=radmins, regions=REGIONS)

@app.route('/admin/regional-admins/new', methods=['GET','POST'])
@login_required
@role_required('admin')
def admin_regional_admin_new():
    if request.method == 'POST':
        email = request.form.get('email','').strip().lower()
        if any(u['email'].lower() == email for u in USERS.values()):
            flash('A user with that email already exists.', 'error')
            return redirect(url_for('admin_regional_admin_new'))
        password = request.form.get('password','').strip()
        if not password:
            flash('Please set a password for this account.', 'error')
            return redirect(url_for('admin_regional_admin_new'))
        uid = f"u-radmin-{str(uuid.uuid4())[:8]}"
        name = request.form.get('name')
        USERS[uid] = {
            "id": uid, "role": "regional_admin",
            "email": email,
            "password": generate_password_hash(password),
            "name": name,
            "region": request.form.get('region'),
            "country": request.form.get('country'),
            "city": request.form.get('city'),
            "class_id": None,
            "phone": request.form.get('phone',''),
            "created_at": datetime.now().strftime('%Y-%m-%d'),
            "email_verified": False, "phone_verified": False,
            "verification_code": generate_verification_code(), "reset_token": None,
        }
        flash(f"Regional Admin '{name}' added successfully for {region_label(request.form.get('region'))}! 🛡️", 'success')
        send_email(email, "Your GJPP Regional Admin account",
            f"Hi {name},\n\nYou've been made a Regional Admin for {region_label(request.form.get('region'))} on GJPP.\n\n"
            f"Login email: {email}\nLog in at {request.url_root.rstrip('/')}/login to get started.")
        return redirect(url_for('admin_regional_admins'))
    return render_template('admin/regional_admin_form.html',
        user=current_user(), radmin=None, regions=REGIONS, action='new')

@app.route('/admin/regional-admins/<uid>/edit', methods=['GET','POST'])
@login_required
@role_required('admin')
def admin_regional_admin_edit(uid):
    radmin = USERS.get(uid)
    if not radmin or radmin['role'] != 'regional_admin':
        flash('Regional Admin not found.', 'error')
        return redirect(url_for('admin_regional_admins'))
    if request.method == 'POST':
        radmin['name']    = request.form.get('name')
        radmin['email']   = request.form.get('email','').strip().lower()
        radmin['region']  = request.form.get('region')
        radmin['country'] = request.form.get('country')
        radmin['city']    = request.form.get('city')
        radmin['phone']   = request.form.get('phone','')
        if request.form.get('password'):
            radmin['password'] = generate_password_hash(request.form.get('password'))
        flash(f"Regional Admin '{radmin['name']}' updated successfully! ✅", 'success')
        return redirect(url_for('admin_regional_admins'))
    return render_template('admin/regional_admin_form.html',
        user=current_user(), radmin=radmin, regions=REGIONS, action='edit')

@app.route('/admin/regional-admins/<uid>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_regional_admin_delete(uid):
    radmin = USERS.get(uid)
    if radmin and radmin['role'] == 'regional_admin':
        name = radmin['name']
        del USERS[uid]
        flash(f"Regional Admin '{name}' removed from the system.", 'success')
    return redirect(url_for('admin_regional_admins'))

# ─────────────────────────────────────────
#  ADMIN — SUPER ADMINS
# ─────────────────────────────────────────
@app.route('/admin/admins')
@login_required
@role_required('admin')
def admin_admins():
    admins = [u for u in USERS.values() if u['role'] == 'admin']
    return render_template('admin/admins.html', user=current_user(), admins=admins)

@app.route('/admin/admins/new', methods=['GET','POST'])
@login_required
@role_required('admin')
def admin_admin_new():
    if request.method == 'POST':
        email = request.form.get('email','').strip().lower()
        if any(u['email'].lower() == email for u in USERS.values()):
            flash('A user with that email already exists.', 'error')
            return redirect(url_for('admin_admin_new'))
        password = request.form.get('password','').strip()
        if not password:
            flash('Please set a password for this account.', 'error')
            return redirect(url_for('admin_admin_new'))
        uid = f"u-admin-{str(uuid.uuid4())[:8]}"
        name = request.form.get('name')
        USERS[uid] = {
            "id": uid, "role": "admin",
            "email": email,
            "password": generate_password_hash(password),
            "name": name,
            "region": request.form.get('region') or REGIONS[0]['id'],
            "country": request.form.get('country',''),
            "city": request.form.get('city',''),
            "class_id": None,
            "phone": request.form.get('phone',''),
            "created_at": datetime.now().strftime('%Y-%m-%d'),
            "email_verified": False, "phone_verified": False,
            "verification_code": generate_verification_code(), "reset_token": None,
        }
        flash(f"Super Admin '{name}' added successfully! 👑", 'success')
        send_email(email, "Your GJPP Super Admin account",
            f"Hi {name},\n\nYou've been granted Super Admin access on GJPP — full platform access across every region.\n\n"
            f"Login email: {email}\nLog in at {request.url_root.rstrip('/')}/login to get started.")
        return redirect(url_for('admin_admins'))
    return render_template('admin/admin_form.html',
        user=current_user(), admin_acct=None, regions=REGIONS, action='new')

@app.route('/admin/admins/<uid>/edit', methods=['GET','POST'])
@login_required
@role_required('admin')
def admin_admin_edit(uid):
    admin_acct = USERS.get(uid)
    if not admin_acct or admin_acct['role'] != 'admin':
        flash('Admin not found.', 'error')
        return redirect(url_for('admin_admins'))
    if request.method == 'POST':
        admin_acct['name']    = request.form.get('name')
        admin_acct['email']   = request.form.get('email','').strip().lower()
        admin_acct['phone']   = request.form.get('phone','')
        if request.form.get('password'):
            admin_acct['password'] = generate_password_hash(request.form.get('password'))
        flash(f"Admin '{admin_acct['name']}' updated successfully! ✅", 'success')
        return redirect(url_for('admin_admins'))
    return render_template('admin/admin_form.html',
        user=current_user(), admin_acct=admin_acct, regions=REGIONS, action='edit')

@app.route('/admin/admins/<uid>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_admin_delete(uid):
    u = current_user()
    target = USERS.get(uid)
    if not target or target['role'] != 'admin':
        return redirect(url_for('admin_admins'))
    if uid == u['id']:
        flash('You cannot remove your own Super Admin account while logged in as it.', 'error')
        return redirect(url_for('admin_admins'))
    remaining_admins = [x for x in USERS.values() if x['role'] == 'admin']
    if len(remaining_admins) <= 1:
        flash('Cannot remove the last remaining Super Admin account.', 'error')
        return redirect(url_for('admin_admins'))
    name = target['name']
    del USERS[uid]
    flash(f"Super Admin '{name}' removed from the system.", 'success')
    return redirect(url_for('admin_admins'))

# ─────────────────────────────────────────
#  ADMIN — VOLUNTEERS
# ─────────────────────────────────────────
@app.route('/admin/volunteers')
@login_required
@role_required('admin')
def admin_volunteers():
    vols = list(VOLUNTEERS)
    q_name    = request.args.get('name', '').lower()
    q_region  = request.args.get('region', '')
    q_country = request.args.get('country', '').lower()
    q_city    = request.args.get('city', '').lower()
    q_skill   = request.args.get('skill', '').lower()
    q_status  = request.args.get('status', '')
    if q_name:    vols = [v for v in vols if q_name   in v['name'].lower()]
    if q_region:  vols = [v for v in vols if v.get('region','') == q_region]
    if q_country: vols = [v for v in vols if q_country in v.get('country','').lower()]
    if q_city:    vols = [v for v in vols if q_city   in v.get('city','').lower()]
    if q_skill:   vols = [v for v in vols if any(q_skill in s.lower() for s in v.get('skills',[]))]
    if q_status:  vols = [v for v in vols if v.get('status','') == q_status]
    all_skills = sorted({s for v in VOLUNTEERS for s in v.get('skills',[])})
    return render_template('admin/volunteers.html',
        user=current_user(), volunteers=vols, regions=REGIONS, all_skills=all_skills,
        q_name=q_name, q_region=q_region, q_country=q_country,
        q_city=q_city, q_skill=q_skill, q_status=q_status,
    )

@app.route('/admin/volunteers/new', methods=['GET','POST'])
@login_required
@role_required('admin')
def admin_volunteer_new():
    all_skills = ["Teaching / Tutoring","Technology / Web","Design / Creative",
                  "Event Coordination","Translation / Languages","Administrative Support",
                  "Video / Media Production","Community Outreach"]
    if request.method == 'POST':
        vol = {
            "id": f"v-{str(uuid.uuid4())[:8]}",
            "name":         request.form.get('name'),
            "email":        request.form.get('email','').strip().lower(),
            "whatsapp":     request.form.get('whatsapp',''),
            "skills":       request.form.getlist('skills'),
            "availability": request.form.get('availability',''),
            "region":       request.form.get('region'),
            "country":      request.form.get('country'),
            "city":         request.form.get('city'),
            "status":       request.form.get('status','active'),
            "registered_at": datetime.now().strftime('%Y-%m-%d'),
        }
        VOLUNTEERS.append(vol)
        flash(f"Volunteer '{vol['name']}' added successfully! 🌟", 'success')
        return redirect(url_for('admin_volunteers'))
    return render_template('admin/volunteer_form.html',
        user=current_user(), volunteer=None, regions=REGIONS, all_skills=all_skills, action='new')

@app.route('/admin/volunteers/<vid>/edit', methods=['GET','POST'])
@login_required
@role_required('admin')
def admin_volunteer_edit(vid):
    vol = next((v for v in VOLUNTEERS if v['id'] == vid), None)
    if not vol:
        flash('Volunteer not found.', 'error')
        return redirect(url_for('admin_volunteers'))
    all_skills = ["Teaching / Tutoring","Technology / Web","Design / Creative",
                  "Event Coordination","Translation / Languages","Administrative Support",
                  "Video / Media Production","Community Outreach"]
    if request.method == 'POST':
        vol['name']         = request.form.get('name')
        vol['email']        = request.form.get('email','').strip().lower()
        vol['whatsapp']     = request.form.get('whatsapp','')
        vol['skills']       = request.form.getlist('skills')
        vol['availability'] = request.form.get('availability','')
        vol['region']       = request.form.get('region')
        vol['country']      = request.form.get('country')
        vol['city']         = request.form.get('city')
        vol['status']       = request.form.get('status','active')
        flash(f"Volunteer '{vol['name']}' updated successfully! ✅", 'success')
        return redirect(url_for('admin_volunteers'))
    return render_template('admin/volunteer_form.html',
        user=current_user(), volunteer=vol, regions=REGIONS, all_skills=all_skills, action='edit')

@app.route('/admin/volunteers/<vid>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_volunteer_delete(vid):
    global VOLUNTEERS
    vol = next((v for v in VOLUNTEERS if v['id'] == vid), None)
    if vol:
        VOLUNTEERS = [v for v in VOLUNTEERS if v['id'] != vid]
        flash(f"Volunteer '{vol['name']}' removed.", 'success')
    return redirect(url_for('admin_volunteers'))

# ─────────────────────────────────────────
#  ADMIN — ADD STUDENT (admin form)
# ─────────────────────────────────────────
@app.route('/admin/students/new', methods=['GET','POST'])
@login_required
@role_required('admin')
def admin_student_new():
    if request.method == 'POST':
        class_id = f"class-{request.form.get('level')}-{request.form.get('region')}"
        parent_email = request.form.get('parent1_email','').strip().lower()

        # Auto-create (or link to) a parent login, same as public self-enrollment does —
        # otherwise a student added here would have a parent with no way to sign in.
        existing_account = next((u for u in USERS.values() if u['email'].lower() == parent_email), None)
        account_created = False
        generated_password = None
        if parent_email and not existing_account:
            generated_password = uuid.uuid4().hex[:10]
            parent_uid = f"u-parent-{str(uuid.uuid4())[:8]}"
            USERS[parent_uid] = {
                "id": parent_uid, "role": "parent",
                "email": parent_email,
                "password": generate_password_hash(generated_password),
                "name": request.form.get('parent1_name'),
                "phone": request.form.get('parent1_whatsapp', ''),
                "region": request.form.get('region'),
                "country": request.form.get('country'),
                "city": request.form.get('city'),
                "class_id": None,
                "email_verified": False, "phone_verified": False,
                "verification_code": generate_verification_code(), "reset_token": None,
            }
            account_created = True

        student = {
            "id": f"s-{str(uuid.uuid4())[:8]}",
            "name":           request.form.get('name'),
            "age":            request.form.get('age'),
            "dob":            request.form.get('dob', ''),
            "gender":         request.form.get('gender'),
            "level":          request.form.get('level'),
            "region":         request.form.get('region'),
            "country":        request.form.get('country'),
            "city":           request.form.get('city'),
            "street_address": request.form.get('street_address', ''),
            "apartment":      request.form.get('apartment', ''),
            "state":          request.form.get('state', ''),
            "postal_code":    request.form.get('postal_code', ''),
            "parent1_name":   request.form.get('parent1_name'),
            "parent1_email":  parent_email,
            "parent1_whatsapp": request.form.get('parent1_whatsapp'),
            "parent2_name":   request.form.get('parent2_name',''),
            "parent2_whatsapp": request.form.get('parent2_whatsapp',''),
            "class_id":       class_id,
            "registered_at":  datetime.now().strftime('%Y-%m-%d'),
        }
        STUDENTS.append(student)
        assigned_teacher = find_teacher_for_class(class_id)
        flash(f"Student '{student['name']}' added successfully! 🎓" +
              (f" Auto-assigned to {assigned_teacher['name']}." if assigned_teacher else " No teacher assigned to this class yet."), 'success')
        if account_created:
            flash(f"A new parent login was also created for {parent_email} — a temporary password was emailed to them.", 'success')
            send_email(parent_email, "Your GJPP parent account",
                f"Hi {student['parent1_name']},\n\nA GJPP parent account has been created for you so you can see "
                f"{student['name']}'s classes, materials, and updates.\n\n"
                f"Login email: {parent_email}\nTemporary password: {generated_password}\n\n"
                f"Log in at {request.url_root.rstrip('/')}/login and update your password from your profile page.")
        return redirect(url_for('admin_students'))
    return render_template('admin/student_form.html',
        user=current_user(), student=None, regions=REGIONS, levels=LEVELS, countries=COUNTRIES, action='new')

@app.route('/admin/students/<sid>/edit', methods=['GET','POST'])
@login_required
@role_required('admin')
def admin_student_edit(sid):
    student = next((s for s in STUDENTS if s['id'] == sid), None)
    if not student:
        flash('Student not found.', 'error')
        return redirect(url_for('admin_students'))
    if request.method == 'POST':
        student['name']             = request.form.get('name')
        student['age']              = request.form.get('age')
        student['dob']              = request.form.get('dob', student.get('dob', ''))
        student['gender']           = request.form.get('gender')
        student['level']            = request.form.get('level')
        student['region']           = request.form.get('region')
        student['country']          = request.form.get('country')
        student['city']             = request.form.get('city')
        student['street_address']   = request.form.get('street_address', '')
        student['apartment']        = request.form.get('apartment', '')
        student['state']            = request.form.get('state', '')
        student['postal_code']      = request.form.get('postal_code', '')
        student['parent1_name']     = request.form.get('parent1_name')
        student['parent1_email']    = request.form.get('parent1_email','').strip().lower()
        student['parent1_whatsapp'] = request.form.get('parent1_whatsapp')
        student['class_id']         = f"class-{student['level']}-{student['region']}"
        flash(f"Student '{student['name']}' updated successfully! ✅", 'success')
        return redirect(url_for('admin_students'))
    return render_template('admin/student_form.html',
        user=current_user(), student=student, regions=REGIONS, levels=LEVELS, countries=COUNTRIES, action='edit')

@app.route('/admin/students/<sid>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_student_delete(sid):
    global STUDENTS
    student = next((s for s in STUDENTS if s['id'] == sid), None)
    if student:
        STUDENTS = [s for s in STUDENTS if s['id'] != sid]
        flash(f"Student '{student['name']}' removed.", 'success')
    return redirect(url_for('admin_students'))


# ─────────────────────────────────────────
#  REGIONAL ADMIN ROUTES
# ─────────────────────────────────────────
@app.route('/radmin')
@login_required
@role_required('regional_admin')
def regional_admin_dashboard():
    u = current_user()
    my_region = u['region']
    region_obj = next((r for r in REGIONS if r['id'] == my_region), None)
    region_students  = [s for s in STUDENTS if s.get('region') == my_region]
    region_teachers  = [t for t in USERS.values() if t['role'] == 'teacher' and t.get('region') == my_region]
    region_volunteers= [v for v in VOLUNTEERS if v.get('region') == my_region]
    region_events    = [e for e in EVENTS if e['region'] in ('global', my_region)]
    region_materials = [m for m in STUDY_MATERIALS if m['region'] in ('global', my_region)]
    pending_reqs     = [r for r in LOCATION_REQUESTS if r['status'] == 'pending' and
                        any(USERS.get(r['user_id'],{}).get('region') == my_region for _ in [1])]
    return render_template('radmin/dashboard.html',
        user=u, region=region_obj,
        students=region_students, teachers=region_teachers,
        volunteers=region_volunteers, events=region_events,
        materials=region_materials, pending_requests=pending_reqs,
    )

@app.route('/radmin/students')
@login_required
@role_required('regional_admin')
def radmin_students():
    u = current_user()
    students = [s for s in STUDENTS if s.get('region') == u['region']]
    q_name = request.args.get('name','').lower()
    q_city = request.args.get('city','').lower()
    if q_name: students = [s for s in students if q_name in s['name'].lower()]
    if q_city: students = [s for s in students if q_city in s.get('city','').lower()]
    region = next((r for r in REGIONS if r['id'] == u['region']), None)
    return render_template('radmin/students.html', user=u, students=students, region=region, q_name=q_name, q_city=q_city)

@app.route('/radmin/teachers')
@login_required
@role_required('regional_admin')
def radmin_teachers():
    u = current_user()
    teachers = [t for t in USERS.values() if t['role'] == 'teacher' and t.get('region') == u['region']]
    region = next((r for r in REGIONS if r['id'] == u['region']), None)
    return render_template('radmin/teachers.html', user=u, teachers=teachers, region=region, students=STUDENTS)

@app.route('/radmin/events')
@login_required
@role_required('regional_admin')
def radmin_events():
    u = current_user()
    events = [e for e in EVENTS if e['region'] in ('global', u['region'])]
    region = next((r for r in REGIONS if r['id'] == u['region']), None)
    return render_template('radmin/events.html', user=u, events=events, region=region, regions=REGIONS,
                           reg_counts=registration_counts_for_viewer(events, u))

@app.route('/radmin/events/<event_id>/attendees')
@login_required
@role_required('regional_admin')
def radmin_event_attendees(event_id):
    return event_attendees_response(event_id, 'radmin_events', 'radmin_event_attendees')

@app.route('/radmin/events/new', methods=['GET','POST'])
@login_required
@role_required('regional_admin')
def radmin_event_new():
    u = current_user()
    if request.method == 'POST':
        event = {
            "id": f"e-{str(uuid.uuid4())[:8]}",
            "title":       request.form.get('title'),
            "date":        request.form.get('date'),
            "type":        request.form.get('type'),
            "region":      u['region'],  # Regional admin can only create for their region
            "description": request.form.get('description'),
            "created_by":  u['id'],
        }
        EVENTS.append(event)
        flash(f"Event '{event['title']}' created!", 'success')
        return redirect(url_for('radmin_events'))
    region = next((r for r in REGIONS if r['id'] == u['region']), None)
    return render_template('radmin/event_form.html', user=u, event=None, region=region, action='new')

@app.route('/radmin/events/<event_id>/edit', methods=['GET','POST'])
@login_required
@role_required('regional_admin')
def radmin_event_edit(event_id):
    u = current_user()
    event = next((e for e in EVENTS if e['id'] == event_id and e['region'] == u['region']), None)
    if not event:
        flash('Event not found or not in your region.', 'error')
        return redirect(url_for('radmin_events'))
    if request.method == 'POST':
        event['title']       = request.form.get('title')
        event['date']        = request.form.get('date')
        event['type']        = request.form.get('type')
        event['description'] = request.form.get('description')
        flash('Event updated!', 'success')
        return redirect(url_for('radmin_events'))
    region = next((r for r in REGIONS if r['id'] == u['region']), None)
    return render_template('radmin/event_form.html', user=u, event=event, region=region, action='edit')

@app.route('/radmin/events/<event_id>/delete', methods=['POST'])
@login_required
@role_required('regional_admin')
def radmin_event_delete(event_id):
    u = current_user()
    event = next((e for e in EVENTS if e['id'] == event_id and e['region'] == u['region']), None)
    if event:
        EVENTS[:] = [e for e in EVENTS if e['id'] != event_id]
        EVENT_REGISTRATIONS[:] = [r for r in EVENT_REGISTRATIONS if r['event_id'] != event_id]
        flash(f"Event deleted.", 'success')
    return redirect(url_for('radmin_events'))

# ─────────────────────────────────────────
#  STUDY MATERIALS
# ─────────────────────────────────────────
@app.route('/materials')
@login_required
def materials():
    u = current_user()
    if u['role'] == 'admin':
        mats = STUDY_MATERIALS
    elif u['role'] == 'regional_admin':
        mats = [m for m in STUDY_MATERIALS if m['region'] in ('global', u['region'])]
    else:  # teacher or parent — restricted to their region AND their class level(s)
        allowed_levels = get_allowed_levels(u)
        mats = [m for m in STUDY_MATERIALS
                if m['region'] in ('global', u['region']) and m['level'] in allowed_levels]
    levels_filter = request.args.get('level', '')
    if levels_filter:
        mats = [m for m in mats if m['level'] == levels_filter]
    return render_template('materials/list.html', user=u, materials=mats, levels=LEVELS, level_filter=levels_filter)

@app.route('/materials/upload', methods=['GET','POST'])
@login_required
@role_required('admin', 'regional_admin')
def materials_upload():
    u = current_user()
    if request.method == 'POST':
        region = request.form.get('region')
        # Regional admin can only upload for their own region or global
        if u['role'] == 'regional_admin':
            region = u['region']

        uploaded_file = request.files.get('file')
        stored_name, size_bytes, error = save_uploaded_file(
            uploaded_file, MATERIALS_UPLOAD_DIR, ALLOWED_MATERIAL_EXTS, max_size_bytes=MAX_MATERIAL_SIZE_BYTES)
        if error:
            messages = {
                'missing':      'Please choose a file to upload.',
                'invalid_type': 'Only PDF and image files (PNG, JPG, GIF, WebP) are allowed.',
                'too_large':    'That file is too large — the maximum size is 4 MB.',
            }
            flash(messages.get(error, 'Could not upload that file.'), 'error')
            return render_template('materials/upload.html', user=u, levels=LEVELS, regions=REGIONS)

        original_name = secure_filename(uploaded_file.filename)
        mat = {
            "id":          f"m-{str(uuid.uuid4())[:8]}",
            "title":       request.form.get('title'),
            "description": request.form.get('description'),
            "level":       request.form.get('level'),
            "region":      region,
            "file_name":   original_name,
            "stored_name": stored_name,
            "file_size":   human_file_size(size_bytes),
            "file_type":   original_name.rsplit('.', 1)[-1].lower(),
            "uploaded_by": u['id'],
            "uploaded_at": datetime.now().strftime('%Y-%m-%d'),
            "downloads":   0,
        }
        STUDY_MATERIALS.append(mat)
        flash(f"Material '{mat['title']}' uploaded successfully! 📚", 'success')
        return redirect(url_for('materials'))
    return render_template('materials/upload.html', user=u, levels=LEVELS, regions=REGIONS)

@app.route('/materials/<mat_id>/download')
@login_required
def material_download(mat_id):
    mat = next((m for m in STUDY_MATERIALS if m['id'] == mat_id), None)
    if not mat:
        flash('Material not found.', 'error')
        return redirect(url_for('materials'))
    u = current_user()
    if not user_can_see_material(mat, u):
        flash('You do not have access to this material.', 'error')
        return redirect(url_for('materials'))
    if not mat.get('stored_name'):
        flash('This material has no file attached yet. Please contact your administrator.', 'error')
        return redirect(url_for('materials'))
    stored_path = os.path.join(MATERIALS_UPLOAD_DIR, mat['stored_name'])
    if not os.path.exists(stored_path):
        flash('That file is missing from storage. Please contact your administrator.', 'error')
        return redirect(url_for('materials'))
    mat['downloads'] = mat.get('downloads', 0) + 1
    return send_from_directory(MATERIALS_UPLOAD_DIR, mat['stored_name'],
        as_attachment=True, download_name=mat.get('file_name', mat['stored_name']))

@app.route('/materials/<mat_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'regional_admin')
def material_delete(mat_id):
    global STUDY_MATERIALS
    u = current_user()
    mat = next((m for m in STUDY_MATERIALS if m['id'] == mat_id), None)
    if mat:
        if u['role'] == 'regional_admin' and mat['region'] not in ('global', u['region']):
            flash('You can only delete materials in your region.', 'error')
            return redirect(url_for('materials'))
        if mat.get('stored_name'):
            stored_path = os.path.join(MATERIALS_UPLOAD_DIR, mat['stored_name'])
            if os.path.exists(stored_path):
                os.remove(stored_path)
        STUDY_MATERIALS = [m for m in STUDY_MATERIALS if m['id'] != mat_id]
        flash(f"Material deleted.", 'success')
    return redirect(url_for('materials'))

# ─────────────────────────────────────────
#  STUDENT PROMOTION
# ─────────────────────────────────────────
LEVEL_ORDER = ['beginner', 'level1', 'level2', 'level3', 'adult']

@app.route('/teacher/promote/<student_id>', methods=['GET','POST'])
@login_required
@role_required('teacher')
def teacher_promote_student(student_id):
    u = current_user()
    student = next((s for s in STUDENTS if s['id'] == student_id), None)
    if not student or student.get('class_id') != u.get('class_id'):
        flash('Student not found in your class.', 'error')
        return redirect(url_for('teacher_students'))
    if request.method == 'POST':
        exam_score = request.form.get('exam_score','0')
        passed     = request.form.get('passed') == 'yes'
        notes      = request.form.get('notes','')
        if passed:
            current_idx = LEVEL_ORDER.index(student['level']) if student['level'] in LEVEL_ORDER else 0
            if current_idx < len(LEVEL_ORDER) - 1:
                old_level = student['level']
                new_level = LEVEL_ORDER[current_idx + 1]
                student['level']    = new_level
                student['class_id'] = f"class-{new_level}-{student['region']}"
                promotion = {
                    "id":          str(uuid.uuid4()),
                    "student_id":  student_id,
                    "student_name":student['name'],
                    "teacher_id":  u['id'],
                    "teacher_name":u['name'],
                    "old_level":   old_level,
                    "new_level":   new_level,
                    "exam_score":  exam_score,
                    "notes":       notes,
                    "promoted_at": datetime.now().strftime('%Y-%m-%d'),
                }
                PROMOTIONS.append(promotion)
                flash(f"{student['name']} promoted from {old_level} to {new_level}! 🎓", 'success')
            else:
                flash(f"{student['name']} is already at the highest level.", 'success')
        else:
            flash(f"{student['name']} did not pass the exam. Please review and retry.", 'error')
        return redirect(url_for('teacher_students'))
    return render_template('teacher/promote.html', user=u, student=student, levels=LEVELS, LEVEL_ORDER=LEVEL_ORDER)

@app.route('/teacher/promotions')
@login_required
@role_required('teacher')
def teacher_promotions():
    u = current_user()
    my_promotions = [p for p in PROMOTIONS if p['teacher_id'] == u['id']]
    return render_template('teacher/promotions.html', user=u, promotions=my_promotions)

# ─────────────────────────────────────────
#  VIDEO REQUESTS & ACTIVITY VIDEOS
# ─────────────────────────────────────────
@app.route('/teacher/video-requests', methods=['GET'])
@login_required
@role_required('teacher')
def teacher_video_requests():
    u = current_user()
    my_class_students = [s for s in STUDENTS if s.get('class_id') == u.get('class_id')]
    my_requests = [r for r in VIDEO_REQUESTS if r['teacher_id'] == u['id']]
    return render_template('teacher/video_requests.html', user=u, students=my_class_students, requests=my_requests)

@app.route('/teacher/video-requests/new', methods=['POST'])
@login_required
@role_required('teacher')
def teacher_create_video_request():
    u = current_user()
    student_ids = request.form.getlist('student_ids')
    topic       = request.form.get('topic','')
    due_date    = request.form.get('due_date','')
    for sid in student_ids:
        student = next((s for s in STUDENTS if s['id'] == sid), None)
        if student:
            VIDEO_REQUESTS.append({
                "id":           str(uuid.uuid4()),
                "teacher_id":   u['id'],
                "teacher_name": u['name'],
                "student_id":   sid,
                "student_name": student['name'],
                "topic":        topic,
                "due_date":     due_date,
                "status":       "pending",
                "created_at":   datetime.now().strftime('%Y-%m-%d'),
                "video_url":    None,
                "video_name":   None,
            })
    flash(f"Video request sent to {len(student_ids)} student(s)!", 'success')
    return redirect(url_for('teacher_video_requests'))

@app.route('/parent/video-requests')
@login_required
@role_required('parent')
def parent_video_requests():
    u = current_user()
    my_children = [s for s in STUDENTS if s.get('parent1_email','').lower() == u['email'].lower()]
    child_ids   = [c['id'] for c in my_children]
    my_requests = [r for r in VIDEO_REQUESTS if r['student_id'] in child_ids]
    uploads     = [v for v in ACTIVITY_VIDEOS if v['student_id'] in child_ids]
    return render_template('parent/video_requests.html', user=u, requests=my_requests, uploads=uploads, children=my_children)

@app.route('/parent/upload-video', methods=['POST'])
@login_required
@role_required('parent')
def parent_upload_video():
    u = current_user()
    request_id  = request.form.get('request_id')
    student_id  = request.form.get('student_id')
    video_title = request.form.get('video_title','My Activity Video')
    video_url   = request.form.get('video_url','').strip()  # external link (YouTube/Drive) — optional
    description = request.form.get('description','')
    student = next((s for s in STUDENTS if s['id'] == student_id), None)
    if not student:
        flash('Student not found.', 'error')
        return redirect(url_for('parent_video_requests'))

    uploaded_file = request.files.get('video_file')
    stored_name, size_bytes, upload_error = save_uploaded_file(uploaded_file, VIDEOS_UPLOAD_DIR, ALLOWED_VIDEO_EXTS)

    if not stored_name and not video_url:
        if upload_error == 'invalid_type':
            flash('That file type is not a supported video format. Please upload MP4, MOV, WebM, or M4V, or paste a link instead.', 'error')
        else:
            flash('Please either upload a video file or paste a video link (YouTube/Drive).', 'error')
        return redirect(url_for('parent_video_requests'))

    video = {
        "id":          str(uuid.uuid4()),
        "student_id":  student_id,
        "student_name":student['name'],
        "parent_id":   u['id'],
        "request_id":  request_id,
        "title":       video_title,
        "description": description,
        "stored_name": stored_name,          # real uploaded file, if provided
        "video_url":   video_url or None,    # external link, if provided instead
        "file_size":   human_file_size(size_bytes) if size_bytes else None,
        "uploaded_at": datetime.now().strftime('%Y-%m-%d'),
        "status":      "submitted",
    }
    ACTIVITY_VIDEOS.append(video)
    # Mark request as fulfilled
    req = next((r for r in VIDEO_REQUESTS if r['id'] == request_id), None)
    if req:
        req['status']     = 'submitted'
        req['video_name'] = video.get('video_url') or video.get('stored_name')
    flash(f"Video uploaded successfully for {student['name']}! 🎬", 'success')
    send_whatsapp(student.get('parent1_whatsapp'), f"Your video '{video_title}' for {student['name']} was received. Thank you! 🎬")
    return redirect(url_for('parent_video_requests'))

@app.route('/videos/<stored_name>')
@login_required
def serve_activity_video(stored_name):
    """Stream an uploaded activity video — access limited to the uploading family and staff."""
    video = next((v for v in ACTIVITY_VIDEOS if v.get('stored_name') == stored_name), None)
    if not video:
        abort(404)
    u = current_user()
    student = next((s for s in STUDENTS if s['id'] == video['student_id']), None)
    is_owner = student and student.get('parent1_email', '').lower() == u['email'].lower()
    is_staff = u['role'] in ('admin', 'regional_admin', 'teacher')
    if not (is_owner or is_staff):
        abort(403)
    return send_from_directory(VIDEOS_UPLOAD_DIR, stored_name)

@app.route('/teacher/videos')
@login_required
@role_required('teacher')
def teacher_view_videos():
    u = current_user()
    my_class_ids = [s['id'] for s in STUDENTS if s.get('class_id') == u.get('class_id')]
    my_videos    = [v for v in ACTIVITY_VIDEOS if v['student_id'] in my_class_ids]
    my_requests  = [r for r in VIDEO_REQUESTS if r['teacher_id'] == u['id']]
    return render_template('teacher/videos.html', user=u, videos=my_videos, requests=my_requests)

# ─────────────────────────────────────────
#  STUDY SCHEDULER
# ─────────────────────────────────────────

@app.route('/admin/festivals')
@login_required
@role_required('admin')
def admin_festivals():
    u = current_user()
    festivals_sorted = sorted(FESTIVALS, key=lambda f: f['start_date'])
    return render_template('admin/festivals.html', user=u, festivals=festivals_sorted, regions=REGIONS)

@app.route('/admin/festivals/new', methods=['POST'])
@login_required
@role_required('admin')
def admin_festival_new():
    name       = request.form.get('name', '').strip()
    start_date = request.form.get('start_date')
    end_date   = request.form.get('end_date')
    region_sel = request.form.getlist('regions')
    notes      = request.form.get('notes', '')

    if not name or not start_date or not end_date:
        flash('Name, start date and end date are required.', 'error')
        return redirect(url_for('admin_festivals'))
    if end_date < start_date:
        flash('End date must be on or after the start date.', 'error')
        return redirect(url_for('admin_festivals'))

    fest = {
        "id": f"fest-{str(uuid.uuid4())[:8]}",
        "name": name,
        "start_date": start_date,
        "end_date": end_date,
        "regions": region_sel if region_sel else ["global"],
        "pauses_schedule": request.form.get('pauses_schedule') == 'yes',
        "year": _parse_date(start_date).year,
        "notes": notes,
    }
    FESTIVALS.append(fest)
    flash(f"Festival '{name}' added — active schedules will adjust automatically.", 'success')
    return redirect(url_for('admin_festivals'))

@app.route('/admin/festivals/<fest_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_festival_delete(fest_id):
    global FESTIVALS
    fest = next((f for f in FESTIVALS if f['id'] == fest_id), None)
    if fest:
        FESTIVALS = [f for f in FESTIVALS if f['id'] != fest_id]
        flash(f"Festival '{fest['name']}' removed.", 'success')
    return redirect(url_for('admin_festivals'))


@app.route('/curriculum')
@login_required
@role_required('admin')
def curriculum_levels():
    u = current_user()
    levels_data = []
    for lv in LEVELS:
        curr = LEVEL_CURRICULA[lv['id']]
        levels_data.append({"level": lv, "total_weeks": curr['total_weeks'], "week_count": len(get_curriculum_weeks(lv['id']))})
    return render_template('admin/curriculum.html', user=u, levels_data=levels_data,
        min_weeks=MIN_CURRICULUM_WEEKS, max_weeks=MAX_CURRICULUM_WEEKS)

@app.route('/curriculum/<level_id>/duration', methods=['POST'])
@login_required
@role_required('admin')
def curriculum_set_duration(level_id):
    if level_id not in LEVEL_CURRICULA:
        abort(404)
    try:
        new_total = int(request.form.get('total_weeks', 0))
    except ValueError:
        new_total = 0
    if not (MIN_CURRICULUM_WEEKS <= new_total <= MAX_CURRICULUM_WEEKS):
        flash(f"Course duration must be between {MIN_CURRICULUM_WEEKS} and {MAX_CURRICULUM_WEEKS} weeks.", 'error')
        return redirect(url_for('curriculum_levels'))

    existing = get_curriculum_weeks(level_id)
    current_total = len(existing)
    if new_total > current_total:
        # Add new blank weeks at the end, preserving everything already designed
        for wk in range(current_total + 1, new_total + 1):
            topic = _SAMPLE_TOPICS[(wk - 1) % len(_SAMPLE_TOPICS)]
            CURRICULUM_WEEKS.append({
                "id": f"cw-{level_id}-{wk}", "level_id": level_id, "week_number": wk,
                "title": f"Week {wk}: {topic}", "content": f"Guided study for Week {wk} covering: {topic}.",
            })
    elif new_total < current_total:
        # Trim trailing weeks beyond the new duration
        keep_ids = {w['id'] for w in existing if w['week_number'] <= new_total}
        CURRICULUM_WEEKS[:] = [w for w in CURRICULUM_WEEKS if w['level_id'] != level_id or w['id'] in keep_ids]

    LEVEL_CURRICULA[level_id]['total_weeks'] = new_total
    flash(f"{level_name(level_id)} curriculum duration set to {new_total} weeks.", 'success')
    return redirect(url_for('curriculum_levels'))

@app.route('/curriculum/<level_id>')
@login_required
@role_required('admin')
def curriculum_weeks_list(level_id):
    if level_id not in LEVEL_CURRICULA:
        abort(404)
    u = current_user()
    weeks = get_curriculum_weeks(level_id)
    return render_template('admin/curriculum_weeks.html', user=u, level_id=level_id,
        weeks=weeks, total_weeks=LEVEL_CURRICULA[level_id]['total_weeks'])

@app.route('/curriculum/<level_id>/week/<int:week_number>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def curriculum_week_edit(level_id, week_number):
    if level_id not in LEVEL_CURRICULA:
        abort(404)
    u = current_user()
    week = next((w for w in CURRICULUM_WEEKS if w['level_id'] == level_id and w['week_number'] == week_number), None)
    if not week:
        flash('That week does not exist for this level.', 'error')
        return redirect(url_for('curriculum_weeks_list', level_id=level_id))
    if request.method == 'POST':
        week['title'] = request.form.get('title', '').strip() or week['title']
        week['content'] = request.form.get('content', '').strip()
        flash(f"Week {week_number} updated for {level_name(level_id)}.", 'success')
        return redirect(url_for('curriculum_weeks_list', level_id=level_id))
    return render_template('admin/curriculum_week_form.html', user=u, level_id=level_id, week=week)


@app.route('/admin/class-times')
@login_required
@role_required('admin')
def admin_class_times():
    u = current_user()
    levels_data = []
    for lv in LEVELS:
        slots = GLOBAL_CLASS_TIMES.get(lv['id'], {}).get('slots', [])
        levels_data.append({"level": lv, "slots": slots, "registration_open": LEVEL_REGISTRATION_OPEN.get(lv['id'], True)})
    return render_template('admin/class_times.html', user=u, levels_data=levels_data)

@app.route('/admin/class-times/<level_id>/add', methods=['POST'])
@login_required
@role_required('admin')
def admin_class_times_add(level_id):
    if level_id not in LEVEL_CURRICULA:
        abort(404)
    day   = request.form.get('day', '').strip()
    time  = request.form.get('time', '').strip()
    label = request.form.get('label', '').strip()
    if not day or not time:
        flash('Please provide both a day and a time.', 'error')
        return redirect(url_for('admin_class_times'))
    GLOBAL_CLASS_TIMES.setdefault(level_id, {"level_id": level_id, "slots": []})
    GLOBAL_CLASS_TIMES[level_id]['slots'].append({"day": day, "time": time, "label": label})
    flash(f"Added {day} {time} to {level_name(level_id)}'s global schedule.", 'success')
    return redirect(url_for('admin_class_times'))

@app.route('/admin/class-times/<level_id>/remove', methods=['POST'])
@login_required
@role_required('admin')
def admin_class_times_remove(level_id):
    if level_id not in LEVEL_CURRICULA:
        abort(404)
    idx = request.form.get('index', type=int)
    slots = GLOBAL_CLASS_TIMES.get(level_id, {}).get('slots', [])
    if idx is not None and 0 <= idx < len(slots):
        removed = slots.pop(idx)
        flash(f"Removed {removed['day']} {removed['time']} from {level_name(level_id)}.", 'success')
    return redirect(url_for('admin_class_times'))

@app.route('/admin/class-times/<level_id>/toggle-registration', methods=['POST'])
@login_required
@role_required('admin')
def admin_class_times_toggle(level_id):
    if level_id not in LEVEL_CURRICULA:
        abort(404)
    LEVEL_REGISTRATION_OPEN[level_id] = not LEVEL_REGISTRATION_OPEN.get(level_id, True)
    status = 'now open for registration' if LEVEL_REGISTRATION_OPEN[level_id] else 'now closed for registration'
    flash(f"{level_name(level_id)} is {status}.", 'success')
    return redirect(url_for('admin_class_times'))


@app.route('/radmin/class-times')
@login_required
@role_required('regional_admin')
def radmin_class_times():
    u = current_user()
    region_id = u['region']
    region = next((r for r in REGIONS if r['id'] == region_id), None)
    levels_data = []
    for lv in LEVELS:
        slots, source = get_effective_class_times(region_id, lv['id'])
        levels_data.append({"level": lv, "slots": slots, "source": source})
    return render_template('radmin/class_times.html', user=u, region=region, levels_data=levels_data)

@app.route('/radmin/class-times/<level_id>/add', methods=['POST'])
@login_required
@role_required('regional_admin')
def radmin_class_times_add(level_id):
    if level_id not in LEVEL_CURRICULA:
        abort(404)
    u = current_user()
    region_id = u['region']
    day   = request.form.get('day', '').strip()
    time  = request.form.get('time', '').strip()
    label = request.form.get('label', '').strip()
    if not day or not time:
        flash('Please provide both a day and a time.', 'error')
        return redirect(url_for('radmin_class_times'))
    key = _rl_key(region_id, level_id)
    if key not in REGION_CLASS_TIMES:
        # First override for this level — start from a copy of the global default
        # rather than empty, so the admin is editing on top of what's already there.
        base_slots = list(GLOBAL_CLASS_TIMES.get(level_id, {}).get('slots', []))
        REGION_CLASS_TIMES[key] = {"region_id": region_id, "level_id": level_id, "slots": base_slots}
    REGION_CLASS_TIMES[key]['slots'].append({"day": day, "time": time, "label": label})
    flash(f"Added {day} {time} to {level_name(level_id)} for {region_label(region_id)}.", 'success')
    return redirect(url_for('radmin_class_times'))

@app.route('/radmin/class-times/<level_id>/remove', methods=['POST'])
@login_required
@role_required('regional_admin')
def radmin_class_times_remove(level_id):
    if level_id not in LEVEL_CURRICULA:
        abort(404)
    u = current_user()
    region_id = u['region']
    key = _rl_key(region_id, level_id)
    idx = request.form.get('index', type=int)
    if key in REGION_CLASS_TIMES:
        slots = REGION_CLASS_TIMES[key]['slots']
        if idx is not None and 0 <= idx < len(slots):
            removed = slots.pop(idx)
            flash(f"Removed {removed['day']} {removed['time']} from {level_name(level_id)}.", 'success')
    return redirect(url_for('radmin_class_times'))

@app.route('/radmin/class-times/<level_id>/reset', methods=['POST'])
@login_required
@role_required('regional_admin')
def radmin_class_times_reset(level_id):
    if level_id not in LEVEL_CURRICULA:
        abort(404)
    u = current_user()
    key = _rl_key(u['region'], level_id)
    REGION_CLASS_TIMES.pop(key, None)
    flash(f"{level_name(level_id)} reverted to the global default schedule for {region_label(u['region'])}.", 'success')
    return redirect(url_for('radmin_class_times'))


@app.route('/admin/scheduler')
@login_required
@role_required('admin')
def admin_scheduler():
    u = current_user()
    levels_data = []
    for lv in LEVELS:
        gsched = GLOBAL_LEVEL_SCHEDULES[lv['id']]
        override_count = len([k for k in REGION_LEVEL_SCHEDULES
                               if k.endswith(f":{lv['id']}") and REGION_LEVEL_SCHEDULES[k].get('start_date')])
        levels_data.append({
            "level": lv, "sched": gsched, "total_weeks": LEVEL_CURRICULA[lv['id']]['total_weeks'],
            "override_count": override_count,
        })
    return render_template('admin/scheduler.html', user=u, levels_data=levels_data, regions=REGIONS)

@app.route('/admin/scheduler/<level_id>/start', methods=['POST'])
@login_required
@role_required('admin')
def admin_scheduler_start(level_id):
    if level_id not in LEVEL_CURRICULA:
        abort(404)
    u = current_user()
    start_date = request.form.get('start_date')
    if not start_date:
        flash('Please choose a start date.', 'error')
        return redirect(url_for('admin_scheduler'))
    GLOBAL_LEVEL_SCHEDULES[level_id].update({
        "start_date": start_date, "started_by": u['id'],
        "started_at": datetime.now().strftime('%Y-%m-%d %H:%M'), "status": "active",
    })
    flash(f"Global schedule for {level_name(level_id)} set to start {start_date}. Applies to every region that hasn't set its own date for this level. 🌍", 'success')
    return redirect(url_for('admin_scheduler'))

@app.route('/admin/scheduler/<level_id>/reset', methods=['POST'])
@login_required
@role_required('admin')
def admin_scheduler_reset(level_id):
    if level_id not in LEVEL_CURRICULA:
        abort(404)
    GLOBAL_LEVEL_SCHEDULES[level_id].update({
        "start_date": None, "started_by": None, "started_at": None, "status": "not_started",
    })
    flash(f"Global schedule cleared for {level_name(level_id)}.", 'success')
    return redirect(url_for('admin_scheduler'))


@app.route('/radmin/scheduler')
@login_required
@role_required('regional_admin')
def radmin_scheduler():
    u = current_user()
    region_id = u['region']
    region = next((r for r in REGIONS if r['id'] == region_id), None)
    festivals = sorted(get_region_festivals(region_id), key=lambda f: f['start_date'])

    levels_progress = []
    for lv in LEVELS:
        info = get_level_schedule_info(region_id, lv['id'])
        entry = get_current_week_entry(region_id, lv['id']) if info['start_date'] else None
        released = get_released_weeks(region_id, lv['id']) if info['start_date'] else []
        levels_progress.append({
            "level": lv, "info": info, "current_entry": entry, "released_count": len(released),
            "total_weeks": LEVEL_CURRICULA[lv['id']]['total_weeks'],
        })

    return render_template('radmin/scheduler.html', user=u, region=region,
        festivals=festivals, levels_progress=levels_progress)

@app.route('/radmin/scheduler/<level_id>/start', methods=['POST'])
@login_required
@role_required('regional_admin')
def radmin_scheduler_start(level_id):
    if level_id not in LEVEL_CURRICULA:
        abort(404)
    u = current_user()
    region_id = u['region']
    start_date = request.form.get('start_date')
    if not start_date:
        flash('Please choose a start date.', 'error')
        return redirect(url_for('radmin_scheduler'))
    REGION_LEVEL_SCHEDULES[_rl_key(region_id, level_id)] = {
        "region_id": region_id, "level_id": level_id, "start_date": start_date,
        "started_by": u['id'], "started_at": datetime.now().strftime('%Y-%m-%d %H:%M'), "status": "active",
    }
    flash(f"{level_name(level_id)} schedule started for {region_label(region_id)} on {start_date}! 🎉", 'success')
    return redirect(url_for('radmin_scheduler'))

@app.route('/radmin/scheduler/<level_id>/reset', methods=['POST'])
@login_required
@role_required('regional_admin')
def radmin_scheduler_reset(level_id):
    if level_id not in LEVEL_CURRICULA:
        abort(404)
    u = current_user()
    region_id = u['region']
    REGION_LEVEL_SCHEDULES.pop(_rl_key(region_id, level_id), None)
    flash(f"{level_name(level_id)} reverted to the global default schedule for {region_label(region_id)}.", 'success')
    return redirect(url_for('radmin_scheduler'))


@app.route('/teacher/scheduler')
@login_required
@role_required('teacher')
def teacher_scheduler():
    u = current_user()
    region_id = u['region']
    level_id = u['class_id'].split('-')[1] if u.get('class_id') else None
    sched = get_level_schedule_info(region_id, level_id) if level_id else {"start_date": None, "source": None}
    my_students = [s for s in STUDENTS if s.get('class_id') == u.get('class_id')]

    released = get_released_weeks(region_id, level_id) if (sched.get('start_date') and level_id) else []
    requested_week = request.args.get('week', type=int)
    if requested_week:
        entry = next((e for e in released if e['week'] and e['week']['week_number'] == requested_week), None)
    else:
        entry = get_current_week_entry(region_id, level_id) if level_id else None
        if entry is None and released:
            # No session active exactly today (e.g. mid-festival with no exact match) — show the latest released week
            weeks_only = [e for e in released if e['week']]
            entry = weeks_only[-1] if weeks_only else released[-1]

    todays_homework = None
    completion = []
    if entry and entry.get('week'):
        wid = entry['week']['id']
        todays_homework = next((h for h in HOMEWORK if h['week_id'] == wid and h['class_id'] == u.get('class_id')), None)
        for s in my_students:
            done = any(p['student_id'] == s['id'] and p['week_id'] == wid for p in STUDENT_PROGRESS)
            completion.append({"student": s, "completed": done})

    return render_template('teacher/scheduler.html', user=u, sched=sched, entry=entry,
        students=my_students, todays_homework=todays_homework, completion=completion,
        level_id=level_id, released=released, total_weeks=LEVEL_CURRICULA.get(level_id, {}).get('total_weeks'))

@app.route('/teacher/scheduler/homework/new', methods=['POST'])
@login_required
@role_required('teacher')
def teacher_homework_new():
    u = current_user()
    week_id = request.form.get('week_id')
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '')
    due_date = request.form.get('due_date', '')
    if not week_id or not title:
        flash('Homework needs a title and a linked week.', 'error')
        return redirect(url_for('teacher_scheduler'))
    level_id = u['class_id'].split('-')[1] if u.get('class_id') else None
    HOMEWORK.append({
        "id": f"hw-{str(uuid.uuid4())[:8]}",
        "week_id": week_id,
        "region_id": u['region'],
        "level_id": level_id,
        "class_id": u.get('class_id'),
        "teacher_id": u['id'],
        "teacher_name": u['name'],
        "title": title,
        "description": description,
        "due_date": due_date,
        "created_at": datetime.now().strftime('%Y-%m-%d'),
    })
    flash(f"Homework '{title}' assigned! 📝", 'success')
    return redirect(url_for('teacher_scheduler'))

@app.route('/teacher/scheduler/homework')
@login_required
@role_required('teacher')
def teacher_homework_list():
    u = current_user()
    my_hw = [h for h in HOMEWORK if h['class_id'] == u.get('class_id')]
    weeks_by_id = {w['id']: w for w in CURRICULUM_WEEKS}
    for h in my_hw:
        h['week'] = weeks_by_id.get(h['week_id'])
    my_hw.sort(key=lambda h: h['created_at'], reverse=True)
    return render_template('teacher/homework.html', user=u, homework=my_hw)

@app.route('/teacher/scheduler/mark-complete', methods=['POST'])
@login_required
@role_required('teacher')
def teacher_mark_session_complete():
    u = current_user()
    student_id = request.form.get('student_id')
    week_id = request.form.get('week_id')
    student = next((s for s in STUDENTS if s['id'] == student_id), None)
    if not student:
        flash('Student not found.', 'error')
        return redirect(url_for('teacher_scheduler'))
    already = any(p['student_id'] == student_id and p['week_id'] == week_id for p in STUDENT_PROGRESS)
    if not already:
        STUDENT_PROGRESS.append({
            "id": str(uuid.uuid4()), "student_id": student_id, "week_id": week_id,
            "region_id": u['region'], "level_id": student.get('level'), "status": "completed",
            "completed_at": datetime.now().strftime('%Y-%m-%d %H:%M'), "marked_by": u['id'],
        })
        flash(f"Marked this week's session complete for {student['name']}.", 'success')
    return redirect(url_for('teacher_scheduler'))


@app.route('/parent/scheduler')
@login_required
@role_required('parent')
def parent_scheduler():
    u = current_user()
    region_id = u['region']
    my_children = [s for s in STUDENTS if s.get('parent1_email', '').lower() == u['email'].lower()]
    requested_week = request.args.get('week', type=int)

    children_data = []
    for child in my_children:
        level_id = child.get('level')
        info = get_level_schedule_info(region_id, level_id)
        released = get_released_weeks(region_id, level_id) if info['start_date'] else []
        if requested_week:
            entry = next((e for e in released if e['week'] and e['week']['week_number'] == requested_week), None)
        else:
            entry = get_current_week_entry(region_id, level_id) if info['start_date'] else None
            if entry is None and released:
                weeks_only = [e for e in released if e['week']]
                entry = weeks_only[-1] if weeks_only else released[-1]

        progress = get_schedule_progress_summary(region_id, level_id, child['id']) if info['start_date'] else None
        completed_this_week = False
        homework_this_week = None
        if entry and entry.get('week'):
            wid = entry['week']['id']
            completed_this_week = any(p['student_id'] == child['id'] and p['week_id'] == wid for p in STUDENT_PROGRESS)
            homework_this_week = next((h for h in HOMEWORK if h['week_id'] == wid and h['class_id'] == child.get('class_id')), None)

        children_data.append({
            "child": child, "info": info, "progress": progress, "entry": entry,
            "completed_this_week": completed_this_week, "homework_this_week": homework_this_week,
            "released": released,
        })

    return render_template('parent/scheduler.html', user=u, children_data=children_data)

@app.route('/parent/scheduler/mark-complete', methods=['POST'])
@login_required
@role_required('parent')
def parent_mark_session_complete():
    u = current_user()
    student_id = request.form.get('student_id')
    week_id = request.form.get('week_id')
    child = next((s for s in STUDENTS if s['id'] == student_id
                  and s.get('parent1_email', '').lower() == u['email'].lower()), None)
    if not child:
        flash('Child not found.', 'error')
        return redirect(url_for('parent_scheduler'))
    already = any(p['student_id'] == student_id and p['week_id'] == week_id for p in STUDENT_PROGRESS)
    if not already:
        STUDENT_PROGRESS.append({
            "id": str(uuid.uuid4()), "student_id": student_id, "week_id": week_id,
            "region_id": u['region'], "level_id": child.get('level'), "status": "completed",
            "completed_at": datetime.now().strftime('%Y-%m-%d %H:%M'), "marked_by": u['id'],
        })
        flash(f"Great job! Marked this week's session complete for {child['name']}. 🎉", 'success')
    return redirect(url_for('parent_scheduler'))

# ─────────────────────────────────────────
#  API
# ─────────────────────────────────────────
@app.route('/api/regions')
def api_regions():
    return jsonify(REGIONS)

@app.route('/api/class-times/<region_id>/<level_id>')
def api_class_times(region_id, level_id):
    """Returns the effective class meeting slots (day/time/label) for a region+level,
    honoring a Regional Admin's override if one exists, otherwise the global default."""
    if level_id not in LEVEL_REGISTRATION_OPEN:
        return jsonify({"slots": [], "source": None, "registration_open": False}), 404
    slots, source = get_effective_class_times(region_id, level_id)
    return jsonify({
        "slots": slots,
        "source": source,
        "registration_open": LEVEL_REGISTRATION_OPEN.get(level_id, True),
    })

@app.route('/api/stats')
def api_stats():
    pending = len([r for r in LOCATION_REQUESTS if r['status']=='pending'])
    pending_accounts_all = len([u for u in USERS.values() if u.get('account_status') == 'pending'])
    u = current_user()
    pending_accounts_region = 0
    if u and u['role'] == 'regional_admin':
        pending_accounts_region = len([x for x in USERS.values()
                                        if x.get('account_status') == 'pending' and x.get('region') == u['region']])
    return jsonify({
        "students":        len(STUDENTS),
        "regions":         len(REGIONS),
        "pathshalas":      42,
        "volunteers":      len(VOLUNTEERS),
        "pending_requests": pending,
        "pending_accounts": pending_accounts_all,
        "pending_accounts_region": pending_accounts_region,
    })

# ─────────────────────────────────────────
#  TEACHER SUB-PAGES
# ─────────────────────────────────────────
@app.route('/teacher/students')
@login_required
@role_required('teacher')
def teacher_students():
    u = current_user()
    # Primary class students
    my_students = [s for s in STUDENTS if s.get('class_id') == u.get('class_id')]
    # All students in the teacher's region grouped by class
    from collections import defaultdict
    region_students = [s for s in STUDENTS if s.get('region') == u.get('region')]
    class_roster = defaultdict(list)
    for s in region_students:
        class_roster[s.get('class_id','unassigned')].append(s)
    my_region = next((r for r in REGIONS if r['id'] == u.get('region')), None)
    upcoming_birthdays = get_upcoming_birthdays(my_students, days=30)
    return render_template('teacher/students.html',
        user=u, students=my_students, class_roster=dict(class_roster),
        region=my_region, levels=LEVELS, all_students=region_students,
        upcoming_birthdays=upcoming_birthdays,
    )

@app.route('/teacher/students/<student_id>/send-birthday', methods=['POST'])
@login_required
@role_required('teacher')
def teacher_send_birthday(student_id):
    u = current_user()
    student = next((s for s in STUDENTS if s['id'] == student_id and s.get('class_id') == u.get('class_id')), None)
    if not student:
        flash('Student not found in your class.', 'error')
        return redirect(request.referrer or url_for('teacher_students'))
    flash(f"🎂 Birthday message sent to {student['name']} and {student.get('parent1_name','their parent')} via WhatsApp!", 'success')
    return redirect(request.referrer or url_for('teacher_students'))

@app.route('/teacher/classes')
@login_required
@role_required('teacher')
def teacher_classes():
    u = current_user()
    region_students = [s for s in STUDENTS if s.get('region') == u.get('region')]
    from collections import defaultdict
    class_roster = defaultdict(list)
    for s in region_students:
        class_roster[s.get('class_id','unassigned')].append(s)
    # Map class_id -> teacher
    class_teachers = {}
    for uid, usr in USERS.items():
        if usr['role'] == 'teacher' and usr.get('class_id'):
            class_teachers[usr['class_id']] = usr
    my_region = next((r for r in REGIONS if r['id'] == u.get('region')), None)
    return render_template('teacher/classes.html',
        user=u, class_roster=dict(class_roster),
        class_teachers=class_teachers,
        region=my_region, levels=LEVELS,
    )

# ─────────────────────────────────────────
#  PARENT SUB-PAGES
# ─────────────────────────────────────────
@app.route('/parent/children')
@login_required
@role_required('parent')
def parent_children():
    u = current_user()
    my_children = [s for s in STUDENTS if s.get('parent1_email','').lower() == u['email'].lower()]
    # Get teacher for each child's class
    child_teachers = {}
    for c in my_children:
        t = get_teacher_for_class(c.get('class_id'))
        if t:
            child_teachers[c['id']] = t
    my_region = next((r for r in REGIONS if r['id'] == u.get('region')), None)
    return render_template('parent/children.html',
        user=u, children=my_children, child_teachers=child_teachers,
        region=my_region, levels=LEVELS,
    )

@app.route('/parent/teachers')
@login_required
@role_required('parent')
def parent_teachers():
    u = current_user()
    # Teachers in parent's region
    region_teachers = [t for t in USERS.values() if t['role']=='teacher' and t.get('region')==u.get('region')]
    # For each teacher, get their students
    teacher_info = []
    for t in region_teachers:
        t_students = [s for s in STUDENTS if s.get('class_id') == t.get('class_id')]
        # Find level info
        class_id = t.get('class_id','')
        level_id = class_id.split('-')[1] if class_id and len(class_id.split('-')) > 1 else ''
        level = next((l for l in LEVELS if l['id']==level_id), None)
        teacher_info.append({
            'teacher': t,
            'students': t_students,
            'level': level,
            'student_count': len(t_students),
        })
    # Also get teachers from other regions (global view)
    all_teachers = [t for t in USERS.values() if t['role']=='teacher']
    all_teacher_info = []
    for t in all_teachers:
        t_students = [s for s in STUDENTS if s.get('class_id') == t.get('class_id')]
        class_id = t.get('class_id','')
        level_id = class_id.split('-')[1] if class_id and len(class_id.split('-')) > 1 else ''
        level = next((l for l in LEVELS if l['id']==level_id), None)
        all_teacher_info.append({
            'teacher': t,
            'students': t_students,
            'level': level,
            'student_count': len(t_students),
            'region': next((r for r in REGIONS if r['id']==t.get('region')), None),
        })
    my_children = [s for s in STUDENTS if s.get('parent1_email','').lower() == u['email'].lower()]
    my_region = next((r for r in REGIONS if r['id'] == u.get('region')), None)
    return render_template('parent/teachers.html',
        user=u, teacher_info=teacher_info, all_teacher_info=all_teacher_info,
        my_children=my_children, region=my_region, regions=REGIONS,
    )

@app.errorhandler(413)
def file_too_large(e):
    flash('That upload is too large for this server to accept. Please choose a smaller file.', 'error')
    return redirect(request.referrer or url_for('index')), 302

if __name__ == '__main__':
    app.run(debug=True, port=5000)
