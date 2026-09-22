"""SQLite schema, connection handling and per-business-type seed data."""
import json
import os
import sqlite3
import uuid
from datetime import date, datetime, timedelta

DB_PATH = os.environ.get("APPT_DB", os.path.join(os.path.dirname(__file__), "appointments.db"))

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS locations(
  id TEXT PRIMARY KEY, slug TEXT UNIQUE NOT NULL, type TEXT NOT NULL,
  name TEXT NOT NULL, city TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', onboarded INTEGER NOT NULL DEFAULT 0,
  slot_min INTEGER NOT NULL DEFAULT 30, cancel_hours INTEGER NOT NULL DEFAULT 24,
  open_time TEXT NOT NULL DEFAULT '09:00', close_time TEXT NOT NULL DEFAULT '19:00',
  deposit REAL NOT NULL DEFAULT 0, features TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS staff(
  id TEXT PRIMARY KEY, location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  name TEXT NOT NULL, role TEXT NOT NULL DEFAULT '', color TEXT NOT NULL,
  pin TEXT NOT NULL DEFAULT '', rate REAL NOT NULL DEFAULT 0,
  clock_in TEXT, clock_in_date TEXT, sort INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS staff_hours(
  staff_id TEXT NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
  dow INTEGER NOT NULL, open_time TEXT NOT NULL, close_time TEXT NOT NULL,
  PRIMARY KEY(staff_id, dow));
CREATE TABLE IF NOT EXISTS shifts(
  id TEXT PRIMARY KEY, staff_id TEXT NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
  date TEXT NOT NULL, clock_in TEXT NOT NULL, clock_out TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS services(
  id TEXT PRIMARY KEY, location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  cat TEXT NOT NULL DEFAULT 'Services', name TEXT NOT NULL, dur INTEGER NOT NULL,
  price REAL NOT NULL DEFAULT 0, emoji TEXT NOT NULL DEFAULT '✨',
  online INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS service_staff(
  service_id TEXT NOT NULL REFERENCES services(id) ON DELETE CASCADE,
  staff_id TEXT NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
  PRIMARY KEY(service_id, staff_id));
CREATE TABLE IF NOT EXISTS classes(
  id TEXT PRIMARY KEY, location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  name TEXT NOT NULL, time TEXT NOT NULL, dur INTEGER NOT NULL, cap INTEGER NOT NULL,
  price REAL NOT NULL DEFAULT 0, staff_id TEXT NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
  days TEXT NOT NULL DEFAULT '0,1,2,3,4,5', online INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS passes(
  id TEXT PRIMARY KEY, location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  name TEXT NOT NULL, type TEXT NOT NULL CHECK(type IN ('pack','unlimited','intro')),
  credits INTEGER, price REAL NOT NULL, days INTEGER NOT NULL,
  svc_name TEXT, descr TEXT);
CREATE TABLE IF NOT EXISTS clients(
  id TEXT PRIMARY KEY, location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  name TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '', visits INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS client_passes(
  id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  pass_id TEXT NOT NULL REFERENCES passes(id) ON DELETE CASCADE,
  remaining INTEGER, expires TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS appointments(
  id TEXT PRIMARY KEY, location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  date TEXT NOT NULL, time TEXT NOT NULL,
  service_id TEXT NOT NULL REFERENCES services(id) ON DELETE CASCADE,
  staff_id TEXT NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
  client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK(status IN ('booked','arrived','done','noshow','cancelled')),
  source TEXT NOT NULL CHECK(source IN ('online','walkin','google','phone')),
  total REAL NOT NULL DEFAULT 0, tip REAL NOT NULL DEFAULT 0, paid INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_appt_day ON appointments(location_id, date);
CREATE TABLE IF NOT EXISTS sales(
  id TEXT PRIMARY KEY, location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  date TEXT NOT NULL, client_id TEXT REFERENCES clients(id) ON DELETE SET NULL,
  staff_id TEXT REFERENCES staff(id) ON DELETE SET NULL,
  label TEXT NOT NULL, total REAL NOT NULL, tip REAL NOT NULL DEFAULT 0,
  method TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_sales_day ON sales(location_id, date);
CREATE TABLE IF NOT EXISTS attendance(
  class_id TEXT NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
  date TEXT NOT NULL, client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL, PRIMARY KEY(class_id, date, client_id));
CREATE TABLE IF NOT EXISTS hl_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, location_id TEXT, action TEXT NOT NULL,
  ok INTEGER NOT NULL, detail TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS waitlist(
  id TEXT PRIMARY KEY, location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  class_id TEXT REFERENCES classes(id) ON DELETE CASCADE, service_id TEXT REFERENCES services(id) ON DELETE CASCADE,
  staff_id TEXT REFERENCES staff(id) ON DELETE SET NULL, date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'waiting', offered_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS blocks(
  id TEXT PRIMARY KEY, location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  staff_id TEXT REFERENCES staff(id) ON DELETE CASCADE, date TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'block', reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS giftcards(
  id TEXT PRIMARY KEY, location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  code TEXT UNIQUE NOT NULL, balance REAL NOT NULL, initial REAL NOT NULL, client_id TEXT REFERENCES clients(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, location_id TEXT, actor TEXT, action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS pages(
  location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  id TEXT NOT NULL, name TEXT NOT NULL, on_ INTEGER NOT NULL DEFAULT 1,
  note TEXT NOT NULL DEFAULT '', sort INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(location_id, id));
"""

COLUMN_TYPES = dict(gap_min='INTEGER', deposit='REAL', fee='REAL', spot='INTEGER', spots='INTEGER', auto_renew='INTEGER', price='REAL', expiry_notified='TEXT')
COLORS = ['#2563eb', '#7c3aed', '#db2777', '#059669', '#d97706', '#0891b2']
CLIENTS_A = ['Priya S.', 'Marco L.', 'Jordan P.', 'Maya R.', 'Sam K.', 'Lena H.', 'Chris D.', 'Ines M.']
CLIENTS_B = ['Jordan Lee', 'Sam Ortiz', 'Avery Patel', 'Riley Nguyen', 'Casey Brooks', 'Morgan Diaz', 'Taylor Okafor', 'Jamie Fischer']

TYPES = {
    'salon': dict(label='Salon', emoji='💇', section='SALON SERVICES', biz='Blush & Blade', city='Austin, Texas',
                  staff='Stylists', staffOne='stylist', svc='Services', hasClasses=False,
                  passWord='Packages & memberships', rush='Saturday rush',
                  team=[('Ava Lopez', 'Senior stylist'), ('Noah Kim', 'Stylist'), ('Zoe Patel', 'Colorist')],
                  services=[('Cuts', [("Women's cut & style", 60, 65, '✂️'), ("Men's cut", 30, 35, '💈'), ('Kids cut', 30, 25, '🧒')]),
                            ('Color', [('Root touch-up', 90, 95, '🎨'), ('Balayage', 150, 220, '🌟')]),
                            ('Finish', [('Blowout', 45, 45, '💨'), ('Beard trim', 20, 20, '🪒')])],
                  classes=[],
                  passes=[('5 blowouts', dict(type='pack', credits=5, price=200, days=180, svc='Blowout')),
                          ('Glow membership', dict(type='unlimited', price=89, days=30, desc='1 blowout a week + 15% off color')),
                          ('New guest cut + blowout', dict(type='intro', credits=1, price=49, days=30))],
                  clients=CLIENTS_A),
    'gym': dict(label='Gym', emoji='🏋️', section='GYM SERVICES', biz='Ironworks Gym', city='Denver, Colorado',
                staff='Trainers', staffOne='trainer', svc='Sessions & classes', hasClasses=True,
                passWord='Memberships & passes', rush='Monday 6am rush',
                team=[('Dana Cole', 'Head coach'), ('Rob Ellis', 'Trainer'), ('Mei Tan', 'Trainer')],
                services=[('Personal training', [('PT session · 60', 60, 80, '🏋️'), ('PT session · 30', 30, 45, '⏱️'), ('Movement assessment', 45, 0, '📋')])],
                classes=[('Strength 60', '06:00', 60, 12, 25, 0), ('Sweat circuit', '12:00', 45, 16, 22, 1), ('Kettlebell flow', '17:45', 45, 12, 22, 2)],
                passes=[('Unlimited monthly', dict(type='unlimited', price=129, days=30, desc='All classes + open gym')),
                        ('10-class pack', dict(type='pack', credits=10, price=190, days=90)),
                        ('2-week trial', dict(type='intro', credits=None, price=39, days=14))],
                clients=CLIENTS_A),
    'studio': dict(label='Studio', emoji='🧘', section='STUDIO SERVICES', biz='Ember Yoga & Pilates', city='San Diego, California',
                   staff='Instructors', staffOne='instructor', svc='Classes & privates', hasClasses=True,
                   passWord='Memberships & passes', rush='Sunrise rush',
                   team=[('Maya Chen', 'Owner · yoga'), ('Tomas Berg', 'Reformer'), ('Priya Nair', 'Yoga')],
                   services=[('Privates', [('Private yoga · 60', 60, 85, '🧘'), ('Private reformer · 55', 55, 95, '🛏️')])],
                   classes=[('Sunrise Vinyasa', '06:00', 60, 20, 24, 2), ('Reformer Pilates', '07:15', 50, 8, 38, 1),
                            ('Power Flow', '09:00', 60, 24, 24, 0), ('Candlelight Restore', '19:00', 60, 20, 22, 2)],
                   passes=[('Unlimited monthly', dict(type='unlimited', price=149, days=30, desc='Every class, both rooms')),
                           ('10-class pack', dict(type='pack', credits=10, price=190, days=180)),
                           ('2 weeks unlimited', dict(type='intro', credits=None, price=49, days=14))],
                   clients=CLIENTS_B),
}

PAGES = [('home', 'Home', 1, 'Hero, hours, book button'), ('services', 'Services & prices', 1, 'Auto from your list'),
         ('book', 'Book', 1, 'The booking page'), ('team', 'Team', 1, 'Auto from Team'), ('about', 'About & reviews', 0, 'Draft')]


def uid():
    return uuid.uuid4().hex[:8]


def now_iso():
    return datetime.now().isoformat(timespec='seconds')


def today():
    return date.today().isoformat()


def mins(t):
    return int(t[:2]) * 60 + int(t[3:5])


def tstr(m):
    m = max(0, m)
    return '%02d:%02d' % ((m // 60) % 24, m % 60)


def now_mins():
    n = datetime.now()
    return n.hour * 60 + n.minute


def slug_of(name):
    return ''.join(ch for ch in name.lower() if ch.isalpha())


def connect():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con


def init(con):
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute('PRAGMA table_info(locations)')}
    if 'features' not in cols:  # migrate databases created before tool choices existed
        con.execute("ALTER TABLE locations ADD COLUMN features TEXT NOT NULL DEFAULT '{}'")
    for table, col in (('clients', 'hl_contact_id'), ('appointments', 'hl_event_id'), ('appointments', 'hl_invoice_id'),
                       ('staff', 'hl_user_id'), ('staff', 'hl_calendar_id'), ('services', 'hl_calendar_id'), ('locations', 'hl_funnel_id'), ('locations', 'hl_funnel_name'), ('locations', 'hl_funnel_url'),
                       ('services', 'hl_product_id'), ('services', 'hl_price_id'), ('passes', 'hl_product_id'), ('passes', 'hl_price_id'),
                       ('clients', 'email'), ('clients', 'hl_opportunity_id'), ('client_passes', 'hl_record_id'), ('client_passes', 'expiry_notified'),
                       ('attendance', 'hl_record_id'), ('shifts', 'hl_record_id'),
                       # depth: services, clients, appointments, passes, classes, staff
                       ('services', 'gap_min'), ('services', 'deposit'), ('services', 'addons'), ('services', 'descr'),
                       ('clients', 'notes'), ('clients', 'tags'), ('clients', 'birthday'), ('clients', 'preferred_staff'), ('clients', 'card_brand'), ('clients', 'card_last4'), ('clients', 'source'),
                       ('appointments', 'deposit'), ('appointments', 'fee'), ('appointments', 'manage_token'), ('appointments', 'series_id'), ('appointments', 'addons'), ('appointments', 'notes'), ('appointments', 'spot'),
                       ('client_passes', 'status'), ('client_passes', 'next_billing'), ('client_passes', 'auto_renew'), ('client_passes', 'frozen_until'), ('client_passes', 'price'),
                       ('classes', 'spots'), ('classes', 'descr'), ('attendance', 'spot'),
                       ('staff', 'role_level'), ('staff', 'email'), ('staff', 'phone'), ('staff', 'bio'),
                       ('sales', 'refund_of'), ('sales', 'appointment_id'), ('sales', 'note'),
                       ('locations', 'policy'), ('locations', 'brand')):
        if col not in {r[1] for r in con.execute('PRAGMA table_info(%s)' % table)}:
            con.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, col, COLUMN_TYPES.get(col, 'TEXT')))
    have = {r['type'] for r in con.execute('SELECT type FROM locations')}
    for t in TYPES:
        if t not in have:
            seed_location(con, t)
    con.commit()


def wipe_location(con, loc_id):
    con.execute('DELETE FROM locations WHERE id=?', (loc_id,))


def seed_location(con, t, loc_id=None, onboarded=0):
    """Create (or recreate) a location of business type `t` with demo data anchored to now."""
    T = TYPES[t]
    loc_id = loc_id or uid()
    con.execute('INSERT INTO locations(id,slug,type,name,city,status,onboarded,created_at,policy) VALUES(?,?,?,?,?,?,?,?,?)',
                (loc_id, slug_of(T['biz']), t, T['biz'], T['city'], 'open', onboarded, now_iso(),
                 json.dumps(dict(noshow_fee=50, late_cancel_fee=25, deposit_default=0, reminder_hours=24, require_card_online=False, waitlist=True, self_service=True))))
    open_t, close_t = '09:00', '19:00'
    staff = []
    for i, (n, r) in enumerate(T['team']):
        sid = 's' + uid()
        clock = tstr(max(mins(open_t) - 10 + i * 17, 0)) if i < 2 else None
        con.execute('INSERT INTO staff(id,location_id,name,role,color,pin,rate,clock_in,clock_in_date,sort,role_level) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (sid, loc_id, n, r, COLORS[i], str(1111 * (i + 1)), 0 if i == 0 else 35, clock, today() if clock else None, i, 'owner' if i == 0 else 'staff'))
        for k in range(7):
            if k == 6 or (i == 1 and k == 0):
                continue
            con.execute('INSERT INTO staff_hours VALUES(?,?,?,?)', (sid, k, open_t, '16:00' if k >= 5 else close_t))
        staff.append(sid)
    services = []
    order = 0
    for cat, items in T['services']:
        for n, dur, price, em in items:
            vid = 'v' + uid()
            gap = 30 if cat == 'Color' else 0
            dep = 25 if price >= 90 else 0
            addons = json.dumps([dict(name='Deep conditioning', price=15, min=10), dict(name='Scalp massage', price=10, min=10)]) if t == 'salon' and cat in ('Cuts', 'Finish') else (json.dumps([dict(name='Body composition scan', price=20, min=10)]) if t == 'gym' else json.dumps([]))
            con.execute('INSERT INTO services(id,location_id,cat,name,dur,price,emoji,online,sort,gap_min,deposit,addons) VALUES(?,?,?,?,?,?,?,1,?,?,?,?)',
                        (vid, loc_id, cat, n, dur, price, em, order, gap, dep, addons))
            order += 1
            for sid in staff:
                con.execute('INSERT INTO service_staff VALUES(?,?)', (vid, sid))
            services.append((vid, dur, price, n))
    classes = []
    for n, time_, dur, cap, price, si in T['classes']:
        cid = 'c' + uid()
        con.execute('INSERT INTO classes(id,location_id,name,time,dur,cap,price,staff_id,days,online) VALUES(?,?,?,?,?,?,?,?,?,1)',
                    (cid, loc_id, n, time_, dur, cap, price, staff[si], '0,1,2,3,4,5'))
        classes.append((cid, staff[si], price, n))
    passes = []
    for n, o in T['passes']:
        pid = 'p' + uid()
        con.execute('INSERT INTO passes(id,location_id,name,type,credits,price,days,svc_name,descr) VALUES(?,?,?,?,?,?,?,?,?)',
                    (pid, loc_id, n, o['type'], o.get('credits'), o['price'], o['days'], o.get('svc'), o.get('desc')))
        passes.append((pid, o))
    clients = []
    visits = [38, 12, 3, 21, 7, 0, 55, 2]
    area = [214, 469, 512, 720, 619]
    for i, n in enumerate(T['clients']):
        kid = 'k' + uid()
        con.execute('INSERT INTO clients(id,location_id,name,phone,visits,created_at) VALUES(?,?,?,?,?,?)',
                    (kid, loc_id, n, '(%d) •••-0%d' % (area[i % 5], 200 + i * 7), visits[i], now_iso()))
        clients.append(kid)

    def grant(ci, pi, rem):
        pid, o = passes[pi]
        exp = (date.today() + timedelta(days=o['days'] - 5)).isoformat()
        if o['type'] == 'unlimited' or not o.get('credits'):
            remaining = None
        else:
            remaining = rem if rem is not None else o['credits']
        con.execute('INSERT INTO client_passes(id,client_id,pass_id,remaining,expires,created_at,status,next_billing,auto_renew,price) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (uid(), clients[ci], pid, remaining, exp, now_iso(), 'active', exp if o['type'] == 'unlimited' else None, 1 if o['type'] == 'unlimited' else 0, o['price']))
    grant(0, 0, 3); grant(3, 1, None); grant(1, 2, 1)
    if classes:
        grant(4, 1, None); grant(6, 0, 7)

    base = max(mins(open_t), (now_mins() - 90) // 30 * 30)
    plan = [(0, 0, 0, 0, 'done', 'online'), (30, 1, 1, 1, 'done', 'walkin'), (60, 2, 2, 2, 'arrived', 'online'),
            (90, 0, 0, 3, 'booked', 'online'), (100, 3, 1, 4, 'booked', 'google'), (150, 1, 2, 5, 'booked', 'phone'),
            (210, 4, 0, 6, 'booked', 'online'), (240, 0, 1, 7, 'booked', 'online')]
    tips = [8, 5]
    nsales = 0
    for off, svi, sti, ci, status, source in plan:
        vid, dur, price, vname = services[svi % len(services)]
        t = tstr(min(base + off, mins(close_t) - dur))
        con.execute('INSERT INTO appointments(id,location_id,date,time,service_id,staff_id,client_id,status,source,total,tip,paid,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    ('a' + uid(), loc_id, today(), t, vid, staff[sti], clients[ci], status, source, price, 0, 1 if status == 'done' else 0, now_iso()))
        if status == 'done':
            con.execute('INSERT INTO sales(id,location_id,date,client_id,staff_id,label,total,tip,method,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (uid(), loc_id, today(), clients[ci], staff[sti], vname, price, tips[nsales % 2], 'Tap to pay', now_iso()))
            nsales += 1
    vid, dur, price, vname = services[0]
    con.execute('INSERT INTO appointments(id,location_id,date,time,service_id,staff_id,client_id,status,source,total,tip,paid,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                ('a' + uid(), loc_id, (date.today() + timedelta(days=1)).isoformat(), '10:00', vid, staff[0], clients[1], 'booked', 'online', price, 0, 0, now_iso()))
    for i, (cid, sid, price, n) in enumerate(classes):
        for kid in clients[:3 + i * 2]:
            con.execute('INSERT OR IGNORE INTO attendance(class_id,date,client_id,created_at) VALUES(?,?,?,?)', (cid, today(), kid, now_iso()))
    for i, (pid, n, on, note) in enumerate(PAGES):
        con.execute('INSERT INTO pages(location_id,id,name,on_,note,sort) VALUES(?,?,?,?,?,?)', (loc_id, pid, n, on, note, i))
    return loc_id
