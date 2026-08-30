from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, abort
from datetime import datetime, date, timedelta
from functools import wraps
import uuid

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.secret_key = 'gjpp-secret-key-2024'

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

CLASS_DAYS = ["Sunday", "Saturday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

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
        "class_id": "class-beginner-na", "class_day": "Sunday"
    },
    "u-teacher-2": {
        "id": "u-teacher-2", "role": "teacher",
        "email": "teacher2@gjpp.org", "password": "teacher123",
        "name": "Suresh Mehta",
        "region": "india", "country": "India", "city": "Mumbai",
        "class_id": "class-level1-india", "class_day": "Sunday"
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
#  STUDY SCHEDULER  (six-week curriculum engine)
# ─────────────────────────────────────────

# 1. MASTER CURRICULUM — one reusable six-week program (sequence-based, not date-based)
MASTER_CURRICULUM = {
    "id": "curriculum-6wk-v1",
    "name": "Six-Week Foundational Study Program",
    "total_weeks": 6,
    "days_per_week": 7,
}

def _seed_curriculum_sessions():
    """Generate 42 sequence-based sessions (6 weeks x 7 days). Sequence, not calendar dates."""
    topics = [
        "Navkar Mantra — Recitation & Meaning", "Ahimsa in Daily Life", "Jain Symbols — Introduction",
        "Story: The Merchant's Honesty", "Simple Prayers & Songs", "Review & Reflection", "Week Wrap-up Quiz",
        "24 Tirthankars — Part 1", "24 Tirthankars — Part 2", "Jain Festivals Calendar", "Five Mahavratas — Intro",
        "Story: King Shrenik", "Ashtami & Chaturdashi Practices", "Week Wrap-up Quiz",
        "Nine Tattvas — Foundations", "Samayik — Practice & Meaning", "Karma Theory — Introduction",
        "Story: Bharat & Bahubali", "Jain Geography — Sacred Places", "Group Discussion: Living Ahimsa", "Week Wrap-up Quiz",
        "Agam Scriptures — Overview", "Jain History — Key Figures", "Six Substances (Shad Dravya)",
        "Story: Mahavir's Compassion", "Ethics in Modern Life", "Comparative Reflection", "Week Wrap-up Quiz",
        "Advanced Tattvagyan — Part 1", "Advanced Tattvagyan — Part 2", "Jain Cosmology — Intro",
        "Story: Parshvanath's Patience", "Debate: Applying Dharma Today", "Community Seva Project", "Week Wrap-up Quiz",
        "Bhawna Yog — Guided Reflection", "Jain Philosophy — Synthesis", "Final Review Part 1",
        "Final Review Part 2", "Student Presentations", "Certificate Preparation", "Graduation & Celebration",
    ]
    sessions = []
    day_number = 1
    for week in range(1, MASTER_CURRICULUM["total_weeks"] + 1):
        for day_in_week in range(1, MASTER_CURRICULUM["days_per_week"] + 1):
            idx = day_number - 1
            sessions.append({
                "id": f"sess-{day_number}",
                "curriculum_id": MASTER_CURRICULUM["id"],
                "day_number": day_number,
                "week_number": week,
                "day_in_week": day_in_week,
                "title": topics[idx] if idx < len(topics) else f"Week {week} Day {day_in_week} Session",
                "content": f"Guided study session covering: {topics[idx] if idx < len(topics) else 'core Jain values'}.",
            })
            day_number += 1
    return sessions

CURRICULUM_SESSIONS = _seed_curriculum_sessions()

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

# 3. REGION-SPECIFIC SCHEDULE INSTANCES — each region starts the master curriculum independently
REGION_SCHEDULES = {
    r["id"]: {
        "region_id": r["id"],
        "curriculum_id": MASTER_CURRICULUM["id"],
        "start_date": None,
        "started_by": None,
        "started_at": None,
        "status": "not_started",   # not_started | active | completed
    }
    for r in REGIONS
}

# 4. STUDENT PROGRESS — tracked against session id, not calendar date
STUDENT_PROGRESS = []
# {id, student_id, session_id, region_id, status: 'completed', completed_at, marked_by}

# 5. HOMEWORK — tied to a session, assigned by a teacher
HOMEWORK = []
# {id, session_id, region_id, class_id, teacher_id, teacher_name, title, description, due_date, created_at}

HOMEWORK_SUBMISSIONS = []
# {id, homework_id, student_id, status, submitted_at, notes}

# ── SCHEDULER HELPERS ──

def _parse_date(s):
    return date.fromisoformat(s) if isinstance(s, str) else s

def get_region_festivals(region_id):
    return [f for f in FESTIVALS if f['pauses_schedule'] and (region_id in f['regions'] or 'global' in f['regions'])]

def _festival_on_date(d, festivals):
    for f in festivals:
        if _parse_date(f['start_date']) <= d <= _parse_date(f['end_date']):
            return f
    return None

def compute_schedule_map(region_id, max_days=250):
    """
    Walk day-by-day from the region's start_date, assigning curriculum sessions
    in sequence while skipping (pausing on) configured festival days for that region.
    Festival weeks are never discarded — remaining sessions simply shift forward.
    Returns a list of {date, session_id, session, festival} entries.
    """
    sched = REGION_SCHEDULES.get(region_id)
    if not sched or not sched.get('start_date'):
        return []
    sessions = sorted(CURRICULUM_SESSIONS, key=lambda s: s['day_number'])
    festivals = get_region_festivals(region_id)
    start = _parse_date(sched['start_date'])

    result = []
    session_idx = 0
    d = start
    walked = 0
    while session_idx < len(sessions) and walked < max_days:
        fest = _festival_on_date(d, festivals)
        if fest:
            result.append({"date": d.isoformat(), "session": None, "festival": fest})
        else:
            result.append({"date": d.isoformat(), "session": sessions[session_idx], "festival": None})
            session_idx += 1
        d += timedelta(days=1)
        walked += 1
    return result

def get_entry_for_date(region_id, target_date):
    """Return the schedule-map entry for a specific calendar date, or None if out of range."""
    for entry in compute_schedule_map(region_id):
        if entry['date'] == target_date.isoformat():
            return entry
    return None

def get_today_entry(region_id):
    return get_entry_for_date(region_id, date.today())

def get_completed_session_ids(student_id):
    return {p['session_id'] for p in STUDENT_PROGRESS if p['student_id'] == student_id}

def get_schedule_progress_summary(region_id, student_id):
    """Total sessions released so far (today or earlier) vs. completed by this student."""
    schedule_map = compute_schedule_map(region_id)
    today = date.today()
    released = [e for e in schedule_map if e['session'] and _parse_date(e['date']) <= today]
    completed_ids = get_completed_session_ids(student_id)
    completed = [e for e in released if e['session']['id'] in completed_ids]
    return {
        "released_count": len(released),
        "completed_count": len(completed),
        "total_sessions": len(CURRICULUM_SESSIONS),
    }

app.jinja_env.globals['MASTER_CURRICULUM'] = MASTER_CURRICULUM

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
    Prefers a teacher who also teaches on the requested class_day;
    falls back to any teacher already assigned to that class_id.
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
    return render_template('classes.html', levels=LEVELS, regions=REGIONS, user=current_user())

@app.route('/events')
def events_page():
    u = current_user()
    if u and u['role'] != 'admin':
        visible = [e for e in EVENTS if e['region'] in ('global', u.get('region', ''))]
    else:
        visible = EVENTS
    return render_template('events.html', events=visible, user=u)

@app.route('/events/<event_id>/register', methods=['GET', 'POST'])
@login_required
def register_event(event_id):
    event = next((e for e in EVENTS if e['id'] == event_id), None)
    if not event:
        flash('Event not found', 'error')
        return redirect(url_for('events_page'))
    u = current_user()
    if request.method == 'POST':
        flash(f"You've registered for {event['title']}! Check WhatsApp for details. 🎉", 'success')
        return redirect(url_for('events_page'))
    return render_template('event_register.html', event=event, user=u)

@app.route('/register/student', methods=['GET', 'POST'])
def register_student():
    if request.method == 'POST':
        parent_email = request.form.get('parent1_email', '').strip().lower()
        password     = request.form.get('password', '')
        confirm      = request.form.get('confirm_password', '')
        level        = request.form.get('level')
        region       = request.form.get('region')
        class_day    = request.form.get('class_day', '')

        existing_account = next((u for u in USERS.values() if u['email'].lower() == parent_email), None)

        # If no account exists yet for this email, a password is required to create one.
        if not existing_account:
            if not password or len(password) < 6:
                flash('Please set a password (min 6 characters) so you can log in and see updates about your child.', 'error')
                return render_template('register_student.html', levels=LEVELS, regions=REGIONS, countries=COUNTRIES, class_days=CLASS_DAYS, user=current_user())
            if password != confirm:
                flash('Passwords do not match. Please try again.', 'error')
                return render_template('register_student.html', levels=LEVELS, regions=REGIONS, countries=COUNTRIES, class_days=CLASS_DAYS, user=current_user())

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
            "class_day": class_day,
            "parent1_name": request.form.get('parent1_name'),
            "parent1_whatsapp": request.form.get('parent1_whatsapp'),
            "parent1_email": parent_email,
            "parent2_name": request.form.get('parent2_name', ''),
            "parent2_whatsapp": request.form.get('parent2_whatsapp', ''),
            "class_id": class_id,
            "registered_at": datetime.now().strftime('%Y-%m-%d'),
        }
        STUDENTS.append(student)

        # Auto-assign a teacher based on the chosen level + region, preferring a class-day match
        assigned_teacher = find_teacher_for_class(class_id, class_day)

        # Create (or reuse) the parent's account so they can log in for future updates
        if existing_account:
            parent_user = existing_account
            account_created = False
        else:
            parent_user = {
                "id": f"u-parent-{str(uuid.uuid4())[:8]}",
                "role": "parent",
                "email": parent_email,
                "password": password,
                "name": request.form.get('parent1_name'),
                "phone": request.form.get('parent1_whatsapp', ''),
                "region": region,
                "country": request.form.get('country'),
                "city": request.form.get('city'),
                "class_id": None,
                "email_verified": False,
                "phone_verified": False,
                "verification_code": generate_verification_code(),
            }
            USERS[parent_user['id']] = parent_user
            account_created = True

        # Log the parent in immediately
        session['user_id'] = parent_user['id']
        session['user_role'] = parent_user['role']

        if account_created:
            flash(f"Welcome, {student['name']}! Your GJPP parent account was created — check your email to verify it. 🙏", 'success')
        else:
            flash(f"Welcome, {student['name']}! Your child has been added to your existing GJPP account. 🙏", 'success')

        if assigned_teacher:
            day_note = f" on {assigned_teacher.get('class_day')}" if assigned_teacher.get('class_day') else ""
            flash(f"{student['name']} has been automatically assigned to teacher {assigned_teacher['name']}{day_note}.", 'success')
        else:
            flash(f"{student['name']} is enrolled — a teacher will be assigned to this class shortly.", 'success')

        return redirect(url_for('registration_success', type='student'))
    return render_template('register_student.html', levels=LEVELS, regions=REGIONS, countries=COUNTRIES, class_days=CLASS_DAYS, user=current_user())

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
    role = request.args.get('role', 'admin')
    return render_template('login.html', role=role)

@app.route('/login/<role>', methods=['POST'])
def login_submit(role):
    email    = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    # Accept regional_admin via 'admin' role tab too
    search_roles = [role]
    if role == 'admin':
        search_roles = ['admin', 'regional_admin']
    user = next((u for u in USERS.values() if u['email'].lower() == email and u['role'] in search_roles and u['password'] == password), None)
    if user:
        session['user_id']   = user['id']
        session['user_role'] = user['role']
        flash(f"Welcome back, {user['name']}! 🙏", 'success')
        next_url = session.pop('next_url', None)
        if next_url and next_url.startswith('/'):
            return redirect(next_url)
        return redirect(url_for('dashboard'))
    flash('Invalid email or password. Please try again.', 'error')
    return redirect(url_for('login') + f'?role={role}')

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
            flash('Email updated — please verify your new email address.', 'success')
        if phone_changed:
            u['phone_verified'] = False
            u['verification_code'] = generate_verification_code()
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
    dest = u['email'] if channel == 'email' else u.get('phone', 'your phone')
    flash(f"A new verification code was sent to {dest}. (Demo code: {u['verification_code']})", 'success')
    return redirect(url_for('verify_channel', channel=channel))

@app.route('/forgot-password', methods=['GET','POST'])
def forgot_password():
    if request.method == 'POST':
        flash('If that email exists, a reset link has been sent.', 'success')
        return redirect(url_for('login'))
    return render_template('forgot_password.html', user=current_user())

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
    return render_template('admin/events.html', user=current_user(), events=EVENTS, regions=REGIONS)

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
    global EVENTS
    event = next((e for e in EVENTS if e['id'] == event_id), None)
    if event:
        EVENTS = [e for e in EVENTS if e['id'] != event_id]
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
            USERS[uid]['region']  = req['new_region']
            USERS[uid]['country'] = req['new_country']
            USERS[uid]['city']    = req['new_city']
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
            "new_region":  request.form.get('region'),
            "new_country": request.form.get('country'),
            "new_city":    request.form.get('city'),
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
            "password": request.form.get('password', 'teacher123'),
            "name": request.form.get('name'),
            "region": request.form.get('region'),
            "country": request.form.get('country'),
            "city": request.form.get('city'),
            "class_id": request.form.get('class_id') or None,
            "phone": request.form.get('phone',''),
            "created_at": datetime.now().strftime('%Y-%m-%d'),
        }
        flash(f"Teacher '{request.form.get('name')}' added successfully! 🎓", 'success')
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
            teacher['password'] = request.form.get('password')
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
            "parent1_name":   request.form.get('parent1_name'),
            "parent1_email":  request.form.get('parent1_email','').strip().lower(),
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
    return render_template('radmin/events.html', user=u, events=events, region=region, regions=REGIONS)

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
    global EVENTS
    u = current_user()
    event = next((e for e in EVENTS if e['id'] == event_id and e['region'] == u['region']), None)
    if event:
        EVENTS = [e for e in EVENTS if e['id'] != event_id]
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
        mat = {
            "id":          f"m-{str(uuid.uuid4())[:8]}",
            "title":       request.form.get('title'),
            "description": request.form.get('description'),
            "level":       request.form.get('level'),
            "region":      region,
            "file_name":   request.form.get('file_name','document.pdf'),
            "file_size":   "N/A",
            "file_type":   request.form.get('file_type','pdf'),
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
    # Increment download count
    mat['downloads'] = mat.get('downloads', 0) + 1
    flash(f"Downloading '{mat['title']}'... (In production this would serve the actual file)", 'success')
    return redirect(url_for('materials'))

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
    video_url   = request.form.get('video_url','')  # In prod: handle actual file upload
    description = request.form.get('description','')
    student = next((s for s in STUDENTS if s['id'] == student_id), None)
    if not student:
        flash('Student not found.', 'error')
        return redirect(url_for('parent_video_requests'))
    video = {
        "id":          str(uuid.uuid4()),
        "student_id":  student_id,
        "student_name":student['name'],
        "parent_id":   u['id'],
        "request_id":  request_id,
        "title":       video_title,
        "description": description,
        "video_url":   video_url or f"activity_{student_id}_{datetime.now().strftime('%Y%m%d')}.mp4",
        "uploaded_at": datetime.now().strftime('%Y-%m-%d'),
        "status":      "submitted",
    }
    ACTIVITY_VIDEOS.append(video)
    # Mark request as fulfilled
    req = next((r for r in VIDEO_REQUESTS if r['id'] == request_id), None)
    if req:
        req['status']     = 'submitted'
        req['video_name'] = video['video_url']
    flash(f"Video uploaded successfully for {student['name']}! 🎬", 'success')
    return redirect(url_for('parent_video_requests'))

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


@app.route('/radmin/scheduler')
@login_required
@role_required('regional_admin')
def radmin_scheduler():
    u = current_user()
    region_id = u['region']
    sched = REGION_SCHEDULES[region_id]
    region = next((r for r in REGIONS if r['id'] == region_id), None)
    festivals = sorted(get_region_festivals(region_id), key=lambda f: f['start_date'])

    schedule_map = compute_schedule_map(region_id) if sched['start_date'] else []
    today_entry = get_today_entry(region_id) if sched['start_date'] else None

    # Week-level summary for display
    weeks_summary = []
    if schedule_map:
        current_week = None
        for entry in schedule_map:
            if entry['session']:
                wk = entry['session']['week_number']
                if current_week is None or current_week['week_number'] != wk:
                    current_week = {"week_number": wk, "start_date": entry['date'], "end_date": entry['date'], "session_count": 0}
                    weeks_summary.append(current_week)
                current_week['end_date'] = entry['date']
                current_week['session_count'] += 1

    return render_template('radmin/scheduler.html', user=u, region=region, sched=sched,
        festivals=festivals, today_entry=today_entry, weeks_summary=weeks_summary,
        total_sessions=len(CURRICULUM_SESSIONS), curriculum=MASTER_CURRICULUM)

@app.route('/radmin/scheduler/start', methods=['POST'])
@login_required
@role_required('regional_admin')
def radmin_scheduler_start():
    u = current_user()
    region_id = u['region']
    start_date = request.form.get('start_date')
    if not start_date:
        flash('Please choose a start date.', 'error')
        return redirect(url_for('radmin_scheduler'))
    REGION_SCHEDULES[region_id].update({
        "start_date": start_date,
        "started_by": u['id'],
        "started_at": datetime.now().strftime('%Y-%m-%d %H:%M'),
        "status": "active",
    })
    flash(f"Six-week schedule started for {region_label(region_id)} on {start_date}! 🎉", 'success')
    return redirect(url_for('radmin_scheduler'))

@app.route('/radmin/scheduler/reset', methods=['POST'])
@login_required
@role_required('regional_admin')
def radmin_scheduler_reset():
    u = current_user()
    region_id = u['region']
    REGION_SCHEDULES[region_id].update({
        "start_date": None, "started_by": None, "started_at": None, "status": "not_started",
    })
    flash('Schedule reset. You can start it again from any date.', 'success')
    return redirect(url_for('radmin_scheduler'))


@app.route('/teacher/scheduler')
@login_required
@role_required('teacher')
def teacher_scheduler():
    u = current_user()
    region_id = u['region']
    sched = REGION_SCHEDULES.get(region_id, {})
    today_entry = get_today_entry(region_id) if sched.get('start_date') else None
    my_students = [s for s in STUDENTS if s.get('class_id') == u.get('class_id')]

    # Homework already assigned for today's session
    todays_homework = None
    if today_entry and today_entry['session']:
        todays_homework = next((h for h in HOMEWORK if h['session_id'] == today_entry['session']['id']
                                 and h['class_id'] == u.get('class_id')), None)

    # Student completion for today's session
    completion = []
    if today_entry and today_entry['session']:
        sid = today_entry['session']['id']
        for s in my_students:
            done = any(p['student_id'] == s['id'] and p['session_id'] == sid for p in STUDENT_PROGRESS)
            completion.append({"student": s, "completed": done})

    return render_template('teacher/scheduler.html', user=u, sched=sched, today_entry=today_entry,
        students=my_students, todays_homework=todays_homework, completion=completion,
        curriculum=MASTER_CURRICULUM)

@app.route('/teacher/scheduler/homework/new', methods=['POST'])
@login_required
@role_required('teacher')
def teacher_homework_new():
    u = current_user()
    session_id = request.form.get('session_id')
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '')
    due_date = request.form.get('due_date', '')
    if not session_id or not title:
        flash('Homework needs a title and a linked session.', 'error')
        return redirect(url_for('teacher_scheduler'))
    HOMEWORK.append({
        "id": f"hw-{str(uuid.uuid4())[:8]}",
        "session_id": session_id,
        "region_id": u['region'],
        "class_id": u.get('class_id'),
        "teacher_id": u['id'],
        "teacher_name": u['name'],
        "title": title,
        "description": description,
        "due_date": due_date,
        "created_at": datetime.now().strftime('%Y-%m-%d'),
    })
    flash(f"Homework '{title}' assigned for today's session! 📝", 'success')
    return redirect(url_for('teacher_scheduler'))

@app.route('/teacher/scheduler/homework')
@login_required
@role_required('teacher')
def teacher_homework_list():
    u = current_user()
    my_hw = [h for h in HOMEWORK if h['class_id'] == u.get('class_id')]
    sessions_by_id = {s['id']: s for s in CURRICULUM_SESSIONS}
    for h in my_hw:
        h['session'] = sessions_by_id.get(h['session_id'])
    my_hw.sort(key=lambda h: h['created_at'], reverse=True)
    return render_template('teacher/homework.html', user=u, homework=my_hw)

@app.route('/teacher/scheduler/mark-complete', methods=['POST'])
@login_required
@role_required('teacher')
def teacher_mark_session_complete():
    u = current_user()
    student_id = request.form.get('student_id')
    session_id = request.form.get('session_id')
    student = next((s for s in STUDENTS if s['id'] == student_id), None)
    if not student:
        flash('Student not found.', 'error')
        return redirect(url_for('teacher_scheduler'))
    already = any(p['student_id'] == student_id and p['session_id'] == session_id for p in STUDENT_PROGRESS)
    if not already:
        STUDENT_PROGRESS.append({
            "id": str(uuid.uuid4()), "student_id": student_id, "session_id": session_id,
            "region_id": u['region'], "status": "completed",
            "completed_at": datetime.now().strftime('%Y-%m-%d %H:%M'), "marked_by": u['id'],
        })
        flash(f"Marked today's session complete for {student['name']}.", 'success')
    return redirect(url_for('teacher_scheduler'))


@app.route('/parent/scheduler')
@login_required
@role_required('parent')
def parent_scheduler():
    u = current_user()
    region_id = u['region']
    sched = REGION_SCHEDULES.get(region_id, {})
    today_entry = get_today_entry(region_id) if sched.get('start_date') else None
    my_children = [s for s in STUDENTS if s.get('parent1_email', '').lower() == u['email'].lower()]

    children_data = []
    for child in my_children:
        progress = get_schedule_progress_summary(region_id, child['id']) if sched.get('start_date') else None
        completed_today = False
        if today_entry and today_entry['session']:
            completed_today = any(p['student_id'] == child['id'] and p['session_id'] == today_entry['session']['id']
                                   for p in STUDENT_PROGRESS)
        # Homework relevant to this child's class
        homework_today = None
        if today_entry and today_entry['session']:
            homework_today = next((h for h in HOMEWORK if h['session_id'] == today_entry['session']['id']
                                    and h['class_id'] == child.get('class_id')), None)
        children_data.append({
            "child": child, "progress": progress, "completed_today": completed_today, "homework_today": homework_today,
        })

    return render_template('parent/scheduler.html', user=u, sched=sched, today_entry=today_entry,
        children_data=children_data, curriculum=MASTER_CURRICULUM)

@app.route('/parent/scheduler/mark-complete', methods=['POST'])
@login_required
@role_required('parent')
def parent_mark_session_complete():
    u = current_user()
    student_id = request.form.get('student_id')
    session_id = request.form.get('session_id')
    child = next((s for s in STUDENTS if s['id'] == student_id
                  and s.get('parent1_email', '').lower() == u['email'].lower()), None)
    if not child:
        flash('Child not found.', 'error')
        return redirect(url_for('parent_scheduler'))
    already = any(p['student_id'] == student_id and p['session_id'] == session_id for p in STUDENT_PROGRESS)
    if not already:
        STUDENT_PROGRESS.append({
            "id": str(uuid.uuid4()), "student_id": student_id, "session_id": session_id,
            "region_id": u['region'], "status": "completed",
            "completed_at": datetime.now().strftime('%Y-%m-%d %H:%M'), "marked_by": u['id'],
        })
        flash(f"Great job! Marked today's session complete for {child['name']}. 🎉", 'success')
    return redirect(url_for('parent_scheduler'))

# ─────────────────────────────────────────
#  API
# ─────────────────────────────────────────
@app.route('/api/regions')
def api_regions():
    return jsonify(REGIONS)

@app.route('/api/stats')
def api_stats():
    pending = len([r for r in LOCATION_REQUESTS if r['status']=='pending'])
    return jsonify({
        "students":        len(STUDENTS),
        "regions":         len(REGIONS),
        "pathshalas":      42,
        "volunteers":      len(VOLUNTEERS),
        "pending_requests": pending,
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

if __name__ == '__main__':
    app.run(debug=True, port=5000)
