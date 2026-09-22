"""Appointments — HTTP server, REST API and business rules. Standard library only.

Run:  python3 server.py [port]      (default 8787)
Admin app:   http://localhost:8787/
Public page: http://localhost:8787/book/<slug>
"""
import json
import os
import re
import sys
import threading
import uuid
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import db
import hl
from db import uid, now_iso, today, mins, tstr, now_mins

STATIC = os.path.join(os.path.dirname(__file__), 'static')
# A built React app (npm run build → ../dist) is served in preference to the plain static pages.
DIST = os.environ.get('APPT_DIST', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'dist'))
if not os.path.isfile(os.path.join(DIST, 'index.html')):
    DIST = None
LOCK = threading.Lock()
CON = db.connect()
db.init(CON)


class ApiError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def dow(d):
    return date.fromisoformat(d).weekday()  # Monday = 0


def rows(sql, args=()):
    return [dict(r) for r in CON.execute(sql, args)]


def one(sql, args=()):
    r = CON.execute(sql, args).fetchone()
    return dict(r) if r else None


def need(row, what):
    if not row:
        raise ApiError(what + ' not found', 404)
    return row


# ---------------------------------------------------------------- state snapshot
def vocab(t):
    T = db.TYPES[t]
    return {k: T[k] for k in ('label', 'emoji', 'section', 'biz', 'city', 'staff', 'staffOne', 'svc', 'hasClasses', 'passWord', 'rush')}


FEATURE_DEFAULTS = dict(booking=True, passes=True, classes=True, tips=True, reminders=True, google=False)


def features_of(L):
    f = dict(FEATURE_DEFAULTS)
    f['classes'] = db.TYPES[L['type']]['hasClasses']
    try:
        f.update({k: bool(v) for k, v in json.loads(L.get('features') or '{}').items() if k in FEATURE_DEFAULTS})
    except ValueError:
        pass
    return f


def location_list():
    return [dict(id=l['id'], slug=l['slug'], type=l['type'], name=l['name'], city=l['city'],
                 label=db.TYPES[l['type']]['label'], emoji=db.TYPES[l['type']]['emoji']) for l in sorted(rows('SELECT * FROM locations'), key=lambda l: list(db.TYPES).index(l['type']))]


def staff_hours(sid):
    h = {str(k): None for k in range(7)}
    for r in rows('SELECT dow,open_time,close_time FROM staff_hours WHERE staff_id=?', (sid,)):
        h[str(r['dow'])] = [r['open_time'], r['close_time']]
    return h


def state(loc_id):
    L = need(one('SELECT * FROM locations WHERE id=?', (loc_id,)), 'location')
    td = today()
    staff = []
    for s in rows('SELECT * FROM staff WHERE location_id=? ORDER BY sort, rowid', (loc_id,)):
        clock = s['clock_in'] if s['clock_in_date'] == td else None
        staff.append(dict(id=s['id'], name=s['name'], role=s['role'], color=s['color'], pin=s['pin'], rate=s['rate'],
                          clockIn=clock, hours=staff_hours(s['id']), hlUserId=s.get('hl_user_id'), hlCalendarId=s.get('hl_calendar_id'), level=s.get('role_level') or 'staff', email=s.get('email') or '', phone=s.get('phone') or '',
                          shifts=[[x['clock_in'], x['clock_out']] for x in rows('SELECT clock_in,clock_out FROM shifts WHERE staff_id=? AND date=?', (s['id'], td))]))
    services = []
    for v in rows('SELECT * FROM services WHERE location_id=? ORDER BY sort, rowid', (loc_id,)):
        services.append(dict(id=v['id'], cat=v['cat'], name=v['name'], dur=v['dur'], price=v['price'], em=v['emoji'], online=bool(v['online']), hlCalendarId=v.get('hl_calendar_id'), hlProductId=v.get('hl_product_id'), gap=int(v.get('gap_min') or 0), deposit=float(v.get('deposit') or 0), addons=json.loads(v.get('addons') or '[]'), descr=v.get('descr') or '',
                             staff=[r['staff_id'] for r in rows('SELECT staff_id FROM service_staff WHERE service_id=?', (v['id'],))]))
    classes = [dict(id=c['id'], name=c['name'], time=c['time'], dur=c['dur'], cap=c['cap'], price=c['price'], staffId=c['staff_id'],
                    days=[int(x) for x in c['days'].split(',') if x != ''], online=bool(c['online']), spots=bool(c.get('spots')), descr=c.get('descr') or '')
               for c in rows('SELECT * FROM classes WHERE location_id=? ORDER BY time', (loc_id,))]
    passes = [dict(id=p['id'], name=p['name'], type=p['type'], credits=p['credits'], price=p['price'], days=p['days'], svc=p['svc_name'], desc=p['descr'], hlProductId=p.get('hl_product_id'))
              for p in rows('SELECT * FROM passes WHERE location_id=? ORDER BY rowid', (loc_id,))]
    clients = []
    for c in rows('SELECT * FROM clients WHERE location_id=? ORDER BY rowid', (loc_id,)):
        clients.append(dict(id=c['id'], name=c['name'], phone=c['phone'], email=c.get('email') or '', visits=c['visits'], hlContactId=c.get('hl_contact_id'), hlOpportunityId=c.get('hl_opportunity_id'),
                            notes=c.get('notes') or '', tags=[t for t in (c.get('tags') or '').split(',') if t], birthday=c.get('birthday') or '', preferredStaff=c.get('preferred_staff') or '', card=(dict(brand=c['card_brand'] or 'Card', last4=c['card_last4']) if c.get('card_last4') else None), created=c.get('created_at', '')[:10],
                            passes=[dict(id=x['id'], passId=x['pass_id'], remaining=x['remaining'], expires=x['expires'], status=x.get('status') or 'active', nextBilling=x.get('next_billing'), autoRenew=bool(x.get('auto_renew')), frozenUntil=x.get('frozen_until'), price=float(x['price']) if x.get('price') not in (None, '') else None)
                                    for x in rows('SELECT * FROM client_passes WHERE client_id=? ORDER BY rowid', (c['id'],))]))
    since = (date.today() - timedelta(days=30)).isoformat()
    appts = [dict(id=a['id'], date=a['date'], time=a['time'], serviceId=a['service_id'], staffId=a['staff_id'], clientId=a['client_id'],
                  status=a['status'], source=a['source'], total=a['total'], tip=a['tip'], paid=bool(a['paid']), hlEventId=a.get('hl_event_id'),
                  deposit=float(a.get('deposit') or 0), fee=float(a.get('fee') or 0), token=a.get('manage_token'), seriesId=a.get('series_id'), addons=json.loads(a.get('addons') or '[]'), notes=a.get('notes') or '', spot=int(a['spot']) if a.get('spot') not in (None, '') else None)
             for a in rows('SELECT * FROM appointments WHERE location_id=? AND date>=? ORDER BY date,time', (loc_id, since))]
    sales = [dict(id=s['id'], date=s['date'], clientId=s['client_id'], staffId=s['staff_id'], label=s['label'], total=s['total'], tip=s['tip'], method=s['method'], refundOf=s.get('refund_of'), appointmentId=s.get('appointment_id'), note=s.get('note') or '', at=(s.get('created_at') or '')[11:16])
             for s in rows('SELECT * FROM sales WHERE location_id=? AND date>=? ORDER BY rowid', (loc_id, since))]
    attend = {}
    for r in rows('SELECT a.class_id,a.date,a.client_id FROM attendance a JOIN classes c ON c.id=a.class_id WHERE c.location_id=? AND a.date>=? ORDER BY a.rowid', (loc_id, since)):
        attend.setdefault(r['class_id'] + '_' + r['date'], []).append(r['client_id'])
    spots = {}
    for r in rows('SELECT a.class_id,a.date,a.client_id,a.spot FROM attendance a JOIN classes c ON c.id=a.class_id WHERE c.location_id=? AND a.spot IS NOT NULL', (loc_id,)):
        spots.setdefault(r['class_id'] + '_' + r['date'], {})[r['client_id']] = r['spot']
    pages = [dict(id=p['id'], name=p['name'], on=bool(p['on_']), note=p['note']) for p in rows('SELECT * FROM pages WHERE location_id=? ORDER BY sort', (loc_id,))]
    waitlist = [dict(id=w['id'], clientId=w['client_id'], classId=w['class_id'], serviceId=w['service_id'], staffId=w['staff_id'], date=w['date'], status=w['status'], created=w['created_at'][:16]) for w in rows("SELECT * FROM waitlist WHERE location_id=? AND date>=? AND status IN ('waiting','offered') ORDER BY created_at", (loc_id, since))]
    blocks = [dict(id=b['id'], staffId=b['staff_id'], date=b['date'], start=b['start'], end=b['end'], kind=b['kind'], reason=b['reason']) for b in rows('SELECT * FROM blocks WHERE location_id=? AND date>=? ORDER BY date, start', (loc_id, since))]
    giftcards = [dict(id=g['id'], code=g['code'], balance=g['balance'], initial=g['initial'], clientId=g['client_id'], created=g['created_at'][:10]) for g in rows('SELECT * FROM giftcards WHERE location_id=? ORDER BY created_at DESC LIMIT 50', (loc_id,))]
    audit_rows = [dict(ts=r['ts'], actor=r['actor'], action=r['action'], detail=r['detail']) for r in rows('SELECT * FROM audit WHERE location_id=? ORDER BY id DESC LIMIT 30', (loc_id,))]
    return dict(id=L['id'], slug=L['slug'], type=L['type'], vocab=vocab(L['type']), status=L['status'], onboarded=bool(L['onboarded']),
                rules=dict(cancelHours=L['cancel_hours'], slot=L['slot_min'], open=L['open_time'], close=L['close_time'], deposit=L['deposit']),
                features=features_of(L), hl=hl_summary(L['id']),
                hlFunnel=dict(id=L.get('hl_funnel_id'), name=L.get('hl_funnel_name'), url=L.get('hl_funnel_url')) if L.get('hl_funnel_id') else None,
                staff=staff, services=services, classes=classes, passes=passes, clients=clients, appts=appts, sales=sales, attend=attend, pages=pages,
                spots=spots, waitlist=waitlist, blocks=blocks, giftcards=giftcards, audit=audit_rows, policy=policy_of(L), name=L['name'], city=L['city'], brand=json.loads(L.get('brand') or '{}'),
                today=td, now=tstr(now_mins()), locations=location_list())


# ---------------------------------------------------------------- HighLevel sync
HL_TZ = {}


def hl_active(loc_id, key):
    cfg = hl.load_cfg()
    return hl.configured(cfg) and cfg.get('app_location_id') == loc_id and cfg['sync'].get(key, False)


def hl_log(loc_id, action, ok, detail=''):
    CON.execute('INSERT INTO hl_log(ts,location_id,action,ok,detail) VALUES(?,?,?,?,?)', (now_iso(), loc_id, action, 1 if ok else 0, str(detail)[:400]))
    CON.execute('DELETE FROM hl_log WHERE id NOT IN (SELECT id FROM hl_log ORDER BY id DESC LIMIT 200)')


def hl_try(loc_id, action, fn):
    """Run one HighLevel call; never let it break the local operation."""
    try:
        out = fn()
        hl_log(loc_id, action, True, out if isinstance(out, str) else '')
        return out
    except hl.HLError as e:
        hl_log(loc_id, action, False, e.message)
    except Exception as e:  # noqa
        hl_log(loc_id, action, False, repr(e))
    return None


def hl_tz():
    cfg = hl.load_cfg()
    key = cfg.get('location_id')
    if key and key not in HL_TZ:
        try:
            HL_TZ[key] = hl.location_info(cfg).get('timezone', '')
        except Exception:
            HL_TZ[key] = ''
    return HL_TZ.get(key, '')


def hl_summary(loc_id):
    cfg = hl.load_cfg()
    linked = hl.configured(cfg) and cfg.get('app_location_id') == loc_id
    return dict(configured=hl.configured(cfg), linked=linked, sync=cfg['sync'],
                contacts=one('SELECT COUNT(*) n FROM clients WHERE location_id=? AND hl_contact_id IS NOT NULL', (loc_id,))['n'],
                appts=one('SELECT COUNT(*) n FROM appointments WHERE location_id=? AND hl_event_id IS NOT NULL', (loc_id,))['n'],
                staff=one('SELECT COUNT(*) n FROM staff WHERE location_id=? AND hl_user_id IS NOT NULL', (loc_id,))['n'],
                services=one('SELECT COUNT(*) n FROM services WHERE location_id=? AND hl_calendar_id IS NOT NULL', (loc_id,))['n'],
                products=one('SELECT (SELECT COUNT(*) FROM services WHERE location_id=? AND hl_product_id IS NOT NULL)+(SELECT COUNT(*) FROM passes WHERE location_id=? AND hl_product_id IS NOT NULL) n', (loc_id, loc_id))['n'],
                opportunities=one('SELECT COUNT(*) n FROM clients WHERE location_id=? AND hl_opportunity_id IS NOT NULL', (loc_id,))['n'],
                records=one('SELECT (SELECT COUNT(*) FROM client_passes cp JOIN clients c ON c.id=cp.client_id WHERE c.location_id=? AND cp.hl_record_id IS NOT NULL)+(SELECT COUNT(*) FROM attendance a JOIN classes k ON k.id=a.class_id WHERE k.location_id=? AND a.hl_record_id IS NOT NULL)+(SELECT COUNT(*) FROM shifts sh JOIN staff st ON st.id=sh.staff_id WHERE st.location_id=? AND sh.hl_record_id IS NOT NULL) n', (loc_id, loc_id, loc_id))['n'])


def hl_contact_for(loc_id, kid):
    """Return the HighLevel contact id for a client, creating the contact if sync is on."""
    k = one('SELECT * FROM clients WHERE id=?', (kid,))
    if not k:
        return None
    if k.get('hl_contact_id'):
        return k['hl_contact_id']
    if not hl_active(loc_id, 'contacts'):
        return None
    cid = hl_try(loc_id, 'contact.upsert ' + k['name'], lambda: hl.upsert_contact_full(k['name'], k['phone'], k.get('email') or ''))
    if cid:
        CON.execute('UPDATE clients SET hl_contact_id=? WHERE id=?', (cid, kid))
    return cid


def hl_enrol(loc_id, cid, event, who=''):
    cfg = hl.load_cfg()
    wid = (cfg.get('workflows') or {}).get(event, '')
    if cid and wid and hl_active(loc_id, 'workflows'):
        hl_try(loc_id, 'workflow.%s %s' % (event, who), lambda: hl.enrol_workflow(cid, wid))


def hl_opportunity(loc_id, kid, event, value=0):
    """Keep one opportunity per client in the mapped pipeline: created at first booking, moved on no-show, won on first payment."""
    cfg = hl.load_cfg()
    p = cfg.get('pipeline') or {}
    if not (hl_active(loc_id, 'opportunities') and p.get('id')):
        return
    k = one('SELECT * FROM clients WHERE id=?', (kid,))
    if not k:
        return
    stage = p.get(event, '')
    if not k.get('hl_opportunity_id'):
        if event != 'booked' or not stage:
            return
        cid = hl_contact_for(loc_id, kid)
        if not cid:
            return
        oid = hl_try(loc_id, 'opportunity.create ' + k['name'], lambda: hl.create_opportunity(p['id'], stage, cid, k['name'] + ' · first booking', value)) or hl_try(loc_id, 'opportunity.find ' + k['name'], lambda: hl.find_opportunity(cid))
        if oid:
            CON.execute('UPDATE clients SET hl_opportunity_id=? WHERE id=?', (oid, kid))
        return
    if stage:
        status = 'won' if event == 'paid' else None
        hl_try(loc_id, 'opportunity.%s %s' % (event, k['name']), lambda: hl.update_opportunity(k['hl_opportunity_id'], stage, status, value if event == 'paid' else None))


def hl_record(loc_id, kind, props, contact_id=None):
    """Write a custom-object record (pass, attendance, shift) and relate it to the contact when there is one."""
    cfg = hl.load_cfg()
    o = (cfg.get('objects') or {}).get(kind)
    if not (hl_active(loc_id, 'objects') and o):
        return None
    rid = hl_try(loc_id, 'record.%s %s' % (kind, props.get('label', '')), lambda: hl.create_record(o['schema'], props))
    if rid and contact_id and o.get('assoc'):
        hl_try(loc_id, 'relate.%s' % kind, lambda: hl.relate(o['assoc'], rid, contact_id, o.get('contact_first', True)))
    return rid


def hl_after_book(loc_id, aid, quiet=False):
    """quiet=True writes the calendar event, workflow and opportunity but sends no confirmation (used for back-dated history)."""
    if not hl_active(loc_id, 'appointments'):
        return
    a = one('SELECT a.*, v.name svc_name, v.dur, COALESCE(v.hl_calendar_id, s.hl_calendar_id) hl_calendar_id, s.hl_user_id, s.name staff_name FROM appointments a JOIN services v ON v.id=a.service_id JOIN staff s ON s.id=a.staff_id WHERE a.id=?', (aid,))
    if not a:
        return
    cid = hl_contact_for(loc_id, a['client_id'])
    if not a['hl_calendar_id']:
        hl_log(loc_id, 'appointment.create', False, a['svc_name'] + ' has no HighLevel calendar yet (Services & booking → Push to HighLevel)')
        k = one('SELECT name FROM clients WHERE id=?', (a['client_id'],))
        hl_enrol(loc_id, cid, 'booked', k['name'])
        hl_opportunity(loc_id, a['client_id'], 'booked', one('SELECT price FROM services WHERE id=?', (a['service_id'],))['price'])
        return
    if not cid:
        hl_log(loc_id, 'appointment.create', False, 'no HighLevel contact for this client (contact sync off?)')
        return
    tz = hl_tz()
    start = hl.iso_at(a['date'], a['time'], tz)
    end = hl.iso_at(a['date'], tstr(mins(a['time']) + a['dur']), tz)
    k = one('SELECT name, phone FROM clients WHERE id=?', (a['client_id'],))
    title = '%s · %s' % (a['svc_name'], k['name'])
    eid = hl_try(loc_id, 'appointment.create ' + title, lambda: hl.create_appointment(a['hl_calendar_id'], cid, start, end, title, a['hl_user_id']))
    if eid:
        CON.execute('UPDATE appointments SET hl_event_id=? WHERE id=?', (eid, aid))
    hl_enrol(loc_id, cid, 'booked', k['name'])
    hl_opportunity(loc_id, a['client_id'], 'booked', one('SELECT price FROM services WHERE id=?', (a['service_id'],))['price'])
    if not quiet and hl_active(loc_id, 'sms') and hl.clean_phone(k['phone']):
        L = one('SELECT name FROM locations WHERE id=?', (loc_id,))
        msg = 'You\'re booked at %s: %s on %s at %s with %s. Reply to this text to change it.' % (L['name'], a['svc_name'], a['date'], a['time'], a['staff_name'].split(' ')[0])
        hl_try(loc_id, 'sms.confirmation ' + k['name'], lambda: hl.send_sms(cid, msg))


def hl_after_status(loc_id, aid, status):
    a = one('SELECT hl_event_id, client_id FROM appointments WHERE id=?', (aid,))
    if not a:
        return
    if a['hl_event_id'] and hl_active(loc_id, 'appointments'):
        hl_try(loc_id, 'appointment.%s' % status, lambda: hl.update_appointment_status(a['hl_event_id'], status))
    k = one('SELECT name, hl_contact_id FROM clients WHERE id=?', (a['client_id'],))
    if status == 'noshow':
        hl_enrol(loc_id, k['hl_contact_id'], 'noshow', k['name'])
        hl_opportunity(loc_id, a['client_id'], 'noshow')
    if status == 'done':
        hl_enrol(loc_id, k['hl_contact_id'], 'showed', k['name'])


def hl_after_pay(loc_id, aid, method, tip, quiet=False):
    a = one('SELECT a.*, v.name svc_name, v.price, v.hl_product_id, v.hl_price_id FROM appointments a JOIN services v ON v.id=a.service_id WHERE a.id=?', (aid,))
    if not a:
        return
    amount0 = 0 if method == 'Pass' else a['price']
    hl_opportunity(loc_id, a['client_id'], 'paid', amount0 + (tip or 0))
    if not quiet and hl_active(loc_id, 'email'):
        k0 = one('SELECT * FROM clients WHERE id=?', (a['client_id'],))
        if k0 and k0.get('email') and '@' in k0['email']:
            cid0 = hl_contact_for(loc_id, a['client_id'])
            L0 = one('SELECT name FROM locations WHERE id=?', (loc_id,))
            html = '<p>Hi %s,</p><p>Thanks for visiting %s today.</p><table><tr><td>%s</td><td style="text-align:right">$%.2f</td></tr>%s<tr><td><b>Total</b></td><td style="text-align:right"><b>$%.2f</b></td></tr></table><p>Paid by %s. See you next time.</p>' % (
                k0['name'].split(' ')[0], L0['name'], a['svc_name'], amount0, ('<tr><td>Tip</td><td style="text-align:right">$%.2f</td></tr>' % tip) if tip else '', amount0 + (tip or 0), method.lower())
            if cid0:
                hl_try(loc_id, 'email.receipt ' + k0['name'], lambda: hl.send_email(cid0, 'Your receipt from ' + L0['name'], html))
    if not hl_active(loc_id, 'invoices') or a.get('hl_invoice_id'):
        return
    cid = hl_contact_for(loc_id, a['client_id'])
    if not cid:
        hl_log(loc_id, 'invoice.create', False, 'no HighLevel contact for this client')
        return
    k = one('SELECT name, phone FROM clients WHERE id=?', (a['client_id'],))
    amount = 0 if method == 'Pass' else a['price']
    inv = hl_try(loc_id, 'invoice.create ' + a['svc_name'], lambda: hl.invoice_and_record_payment(cid, k['name'], k['phone'], a['svc_name'], amount, tip, method, product_id=a.get('hl_product_id'), price_id=a.get('hl_price_id')))
    if inv:
        CON.execute('UPDATE appointments SET hl_invoice_id=? WHERE id=?', (inv, aid))


# ---------------------------------------------------------------- rules
def service_of(vid):
    return need(one('SELECT * FROM services WHERE id=?', (vid,)), 'service')


def staff_of(sid):
    return need(one('SELECT * FROM staff WHERE id=?', (sid,)), 'staff member')


def client_of(kid):
    return need(one('SELECT * FROM clients WHERE id=?', (kid,)), 'client')


def staff_busy(sid, d, t, dur, exclude=None):
    start, end = mins(t), mins(t) + dur
    for a in rows("SELECT a.time, v.dur, COALESCE(v.gap_min,0) gap, a.addons FROM appointments a JOIN services v ON v.id=a.service_id WHERE a.staff_id=? AND a.date=? AND a.status NOT IN ('cancelled','noshow') AND a.id IS NOT ?", (sid, d, exclude)):
        extra = sum(int(x.get('min') or 0) for x in json.loads(a['addons'] or '[]'))
        if start < mins(a['time']) + int(a['dur']) + extra + int(a['gap'] or 0) and mins(a['time']) < end:
            return True
    w = str(dow(d))
    for c in rows('SELECT time,dur,days FROM classes WHERE staff_id=?', (sid,)):
        if w in c['days'].split(',') and start < mins(c['time']) + c['dur'] and mins(c['time']) < end:
            return True
    for b in blocks_for(sid, d):
        if start < mins(b['end']) and mins(b['start']) < end:
            return True
    return False


def slots(vid, sid, d, slot_min):
    v = service_of(vid)
    h = one('SELECT open_time,close_time FROM staff_hours WHERE staff_id=? AND dow=?', (sid, dow(d)))
    if not h:
        return []
    out = []
    m = mins(h['open_time'])
    need_min = v['dur'] + int(v.get('gap_min') or 0)
    while m + v['dur'] <= mins(h['close_time']):
        t = tstr(m)
        if not (d == today() and m < now_mins() + 30) and not staff_busy(sid, d, t, need_min):
            out.append(t)
        m += slot_min
    return out


def qualified_staff(vid):
    return [r['staff_id'] for r in rows('SELECT ss.staff_id FROM service_staff ss JOIN staff s ON s.id=ss.staff_id WHERE ss.service_id=? ORDER BY s.sort', (vid,))]


def active_pass(kid, svc_name=None):
    for x in rows('SELECT cp.*, p.svc_name, p.type FROM client_passes cp JOIN passes p ON p.id=cp.pass_id WHERE cp.client_id=? AND cp.expires>=? ORDER BY cp.rowid', (kid, today())):
        if (x['remaining'] is None or x['remaining'] > 0) and (svc_name is None or x['svc_name'] is None or x['svc_name'] == svc_name):
            return x
    return None


def add_sale(loc_id, kid, sid, label, total, tip, method, d=None):
    CON.execute('INSERT INTO sales(id,location_id,date,client_id,staff_id,label,total,tip,method,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (uid(), loc_id, d or today(), kid, sid, label, total, tip, method, now_iso()))


def new_client(loc_id, name, phone=None, email=None):
    kid = 'k' + uid()
    import random
    CON.execute('INSERT INTO clients(id,location_id,name,phone,email,visits,created_at) VALUES(?,?,?,?,?,0,?)',
                (kid, loc_id, name.strip(), phone or '(new) •••-%d' % random.randint(1000, 9999), (email or '').strip() or None, now_iso()))
    if hl_active(loc_id, 'contacts'):
        hl_contact_for(loc_id, kid)
    return kid


def book(loc_id, b, enforce=True):
    """Create an appointment. b: service_id, staff_id (optional), client_id, date, time, source, status."""
    v = service_of(b['service_id'])
    L = one('SELECT * FROM locations WHERE id=?', (loc_id,))
    d, t = b.get('date') or today(), b['time']
    sid = b.get('staff_id')
    if not sid:
        for cand in qualified_staff(v['id']):
            if t in slots(v['id'], cand, d, L['slot_min']):
                sid = cand
                break
        if not sid:
            raise ApiError('No one is free at that time', 409)
    elif enforce:
        if sid not in qualified_staff(v['id']):
            raise ApiError('That staff member does not offer this service', 409)
        if staff_busy(sid, d, t, v['dur']):
            raise ApiError('That slot was just taken', 409)
        if not one('SELECT 1 FROM staff_hours WHERE staff_id=? AND dow=?', (sid, dow(d))):
            raise ApiError('Not working that day', 409)
    aid = 'a' + uid()
    addons = [x for x in (json.loads(v.get('addons') or '[]')) if x.get('name') in set(b.get('addons') or [])]
    total = v['price'] + sum(float(x.get('price') or 0) for x in addons)
    CON.execute('INSERT INTO appointments(id,location_id,date,time,service_id,staff_id,client_id,status,source,total,tip,paid,created_at,manage_token,addons,notes,spot) VALUES(?,?,?,?,?,?,?,?,?,?,0,0,?,?,?,?,?)',
                (aid, loc_id, d, t, v['id'], sid, b['client_id'], b.get('status', 'booked'), b.get('source', 'phone'), total, now_iso(), manage_token(), json.dumps(addons), (b.get('notes') or '')[:300], b.get('spot')))
    if b.get('status', 'booked') == 'booked' and not b.get('no_sync'):
        take_deposit(loc_id, aid)
    if not b.get('no_sync'):
        hl_after_book(loc_id, aid)
    return dict(id=aid, staff_id=sid)


# ---------------------------------------------------------------- handlers
def h_locations(ctx):
    return location_list()


def h_state(ctx):
    return state(ctx['loc'])


def h_patch_location(ctx):
    b = ctx['body']
    sets, args = [], []
    for key, col in (('status', 'status'), ('onboarded', 'onboarded'), ('slot', 'slot_min'), ('cancelHours', 'cancel_hours'), ('deposit', 'deposit')):
        if key in b:
            val = b[key]
            if key == 'status' and val not in ('open', 'busy', 'closed'):
                raise ApiError('bad status')
            if key == 'onboarded':
                val = 1 if val else 0
            sets.append(col + '=?')
            args.append(val)
    if isinstance(b.get('features'), dict):
        L = need(one('SELECT * FROM locations WHERE id=?', (ctx['loc'],)), 'location')
        f = features_of(L)
        f.update({k: bool(v) for k, v in b['features'].items() if k in FEATURE_DEFAULTS})
        sets.append('features=?')
        args.append(json.dumps(f))
        if 'booking' in b['features']:  # the booking-page tool and the public Book page are one switch
            CON.execute('UPDATE pages SET on_=? WHERE location_id=? AND id=?', (1 if b['features']['booking'] else 0, ctx['loc'], 'book'))
    if sets:
        CON.execute('UPDATE locations SET ' + ','.join(sets) + ' WHERE id=?', args + [ctx['loc']])
    return state(ctx['loc'])


def h_reset(ctx):
    L = need(one('SELECT * FROM locations WHERE id=?', (ctx['loc'],)), 'location')
    db.wipe_location(CON, L['id'])
    db.seed_location(CON, ctx['body'].get('type', L['type']), loc_id=L['id'], onboarded=1 if ctx['body'].get('onboarded', True) else 0)
    return state(L['id'])


def h_rush(ctx):
    loc = ctx['loc']
    L = one('SELECT * FROM locations WHERE id=?', (loc,))
    services = rows('SELECT * FROM services WHERE location_id=? ORDER BY sort', (loc,))
    staff = rows('SELECT * FROM staff WHERE location_id=? ORDER BY sort', (loc,))
    names = ['Aisha B.', 'Diego R.', 'Mei L.', 'Tobias W.', 'Farah Q.', 'Liam O.']
    n = 0
    for i, nm in enumerate(names):
        v = services[i % len(services)]
        s = staff[i % len(staff)]
        sl = slots(v['id'], s['id'], today(), L['slot_min'])
        if not sl:
            continue
        kid = new_client(loc, nm)
        book(loc, dict(service_id=v['id'], staff_id=s['id'], client_id=kid, date=today(), time=sl[0], source='online' if i % 2 else 'google', no_sync=True), enforce=False)
        n += 1
    w = str(dow(today()))
    for c in rows('SELECT * FROM classes WHERE location_id=? ORDER BY time', (loc,)):
        if w not in c['days'].split(','):
            continue
        have = [r['client_id'] for r in rows('SELECT client_id FROM attendance WHERE class_id=? AND date=?', (c['id'], today()))]
        room = c['cap'] - len(have)
        for k in rows('SELECT id FROM clients WHERE location_id=? ORDER BY rowid LIMIT 4 OFFSET 3', (loc,)):
            if room <= 0:
                break
            if k['id'] not in have:
                CON.execute('INSERT OR IGNORE INTO attendance(class_id,date,client_id,created_at) VALUES(?,?,?,?)', (c['id'], today(), k['id'], now_iso()))
                room -= 1
        break
    return dict(booked=n, state=state(loc))


# staff
def h_staff_create(ctx):
    b, loc = ctx['body'], ctx['loc']
    n = len(rows('SELECT id FROM staff WHERE location_id=?', (loc,)))
    sid = 's' + uid()
    L = one('SELECT * FROM locations WHERE id=?', (loc,))
    CON.execute('INSERT INTO staff(id,location_id,name,role,color,pin,rate,sort,role_level,email,phone) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (sid, loc, b['name'].strip(), b.get('role', ''), db.COLORS[n % len(db.COLORS)], str(b.get('pin', '')), float(b.get('rate', 0) or 0), n, b.get('level', 'staff') if b.get('level') in ('owner', 'desk', 'staff') else 'staff', b.get('email'), b.get('phone')))
    for k in range(6):
        CON.execute('INSERT INTO staff_hours VALUES(?,?,?,?)', (sid, k, L['open_time'], L['close_time']))
    for v in rows('SELECT id FROM services WHERE location_id=?', (loc,)):
        CON.execute('INSERT OR IGNORE INTO service_staff VALUES(?,?)', (v['id'], sid))
    return state(loc)


def h_staff_update(ctx):
    b = ctx['body']
    s = staff_of(ctx['id'])
    lvl = b.get('level', s.get('role_level') or 'staff')
    if lvl not in ('owner', 'desk', 'staff'):
        raise ApiError('level must be owner, desk or staff')
    CON.execute('UPDATE staff SET name=?,role=?,rate=?,pin=?,role_level=?,email=?,phone=? WHERE id=?',
                (b.get('name', s['name']).strip(), b.get('role', s['role']), float(b.get('rate', s['rate']) or 0), str(b.get('pin', s['pin'])), lvl, b.get('email', s.get('email')), b.get('phone', s.get('phone')), s['id']))
    return state(ctx['loc'])


def h_staff_delete(ctx):
    staff_of(ctx['id'])
    CON.execute('DELETE FROM staff WHERE id=?', (ctx['id'],))
    return state(ctx['loc'])


def h_staff_hours(ctx):
    """PUT hours: {dow: [open, close] | null}  — toggles a single day."""
    s = staff_of(ctx['id'])
    L = one('SELECT * FROM locations WHERE id=?', (ctx['loc'],))
    for k, v in ctx['body'].items():
        k = int(k)
        CON.execute('DELETE FROM staff_hours WHERE staff_id=? AND dow=?', (s['id'], k))
        if v:
            o, c = (v if isinstance(v, list) else [L['open_time'], L['close_time']])
            CON.execute('INSERT INTO staff_hours VALUES(?,?,?,?)', (s['id'], k, o, c))
    return state(ctx['loc'])


def h_staff_clock(ctx):
    s = staff_of(ctx['id'])
    td = today()
    if s['clock_in'] and s['clock_in_date'] == td:
        shid = uid()
        out_t = tstr(now_mins())
        CON.execute('INSERT INTO shifts(id,staff_id,date,clock_in,clock_out) VALUES(?,?,?,?,?)', (shid, s['id'], td, s['clock_in'], out_t))
        CON.execute('UPDATE staff SET clock_in=NULL, clock_in_date=NULL WHERE id=?', (s['id'],))
        hrs = round(max(0, mins(out_t) - mins(s['clock_in'])) / 60, 2)
        rid = hl_record(ctx['loc'], 'shift', dict(label='%s · %s' % (s['name'], td), staff=s['name'], shift_date=td, clock_in=s['clock_in'], clock_out=out_t, hours=hrs))
        if rid:
            CON.execute('UPDATE shifts SET hl_record_id=? WHERE id=?', (rid, shid))
        action = 'out'
    else:
        CON.execute('UPDATE staff SET clock_in=?, clock_in_date=? WHERE id=?', (tstr(now_mins()), td, s['id']))
        action = 'in'
    return dict(action=action, state=state(ctx['loc']))


# services
def h_service_create(ctx):
    b, loc = ctx['body'], ctx['loc']
    vid = 'v' + uid()
    n = len(rows('SELECT id FROM services WHERE location_id=?', (loc,)))
    CON.execute('INSERT INTO services(id,location_id,cat,name,dur,price,emoji,online,sort,gap_min,deposit,addons,descr) VALUES(?,?,?,?,?,?,?,1,?,?,?,?,?)',
                (vid, loc, b.get('cat') or 'Services', b['name'].strip(), int(b.get('dur', 30)), float(b.get('price', 0) or 0), b.get('em', '✨'), n, int(b.get('gap', 0) or 0), float(b.get('deposit', 0) or 0), json.dumps(b.get('addons') or []), (b.get('descr') or '')[:300]))
    for sid in b.get('staff', [s['id'] for s in rows('SELECT id FROM staff WHERE location_id=?', (loc,))]):
        CON.execute('INSERT OR IGNORE INTO service_staff VALUES(?,?)', (vid, sid))
    return state(loc)


def h_service_update(ctx):
    b = ctx['body']
    v = service_of(ctx['id'])
    CON.execute('UPDATE services SET cat=?,name=?,dur=?,price=?,online=?,gap_min=?,deposit=?,addons=?,descr=? WHERE id=?',
                (b.get('cat', v['cat']), b.get('name', v['name']).strip(), int(b.get('dur', v['dur'])), float(b.get('price', v['price']) or 0),
                 1 if b.get('online', bool(v['online'])) else 0, int(b.get('gap', v.get('gap_min') or 0) or 0), float(b.get('deposit', v.get('deposit') or 0) or 0),
                 json.dumps([dict(name=str(x.get('name', ''))[:40], price=float(x.get('price') or 0), min=int(x.get('min') or 0)) for x in b['addons'] if x.get('name')]) if isinstance(b.get('addons'), list) else (v.get('addons') or '[]'),
                 (b.get('descr', v.get('descr')) or '')[:300], v['id']))
    if 'staff' in b:
        CON.execute('DELETE FROM service_staff WHERE service_id=?', (v['id'],))
        for sid in b['staff']:
            CON.execute('INSERT OR IGNORE INTO service_staff VALUES(?,?)', (v['id'], sid))
    return state(ctx['loc'])


def h_service_delete(ctx):
    service_of(ctx['id'])
    CON.execute('DELETE FROM services WHERE id=?', (ctx['id'],))
    return state(ctx['loc'])


# classes
def h_class_create(ctx):
    b, loc = ctx['body'], ctx['loc']
    CON.execute('INSERT INTO classes(id,location_id,name,time,dur,cap,price,staff_id,days,online) VALUES(?,?,?,?,?,?,?,?,?,1)',
                ('c' + uid(), loc, b['name'].strip(), b.get('time', '18:00'), int(b.get('dur', 60)), int(b.get('cap', 12)), float(b.get('price', 20) or 0),
                 b['staffId'], ','.join(str(int(x)) for x in b.get('days', [0, 1, 2, 3, 4]))))
    return state(loc)


def h_class_update(ctx):
    b = ctx['body']
    c = need(one('SELECT * FROM classes WHERE id=?', (ctx['id'],)), 'class')
    CON.execute('UPDATE classes SET name=?,time=?,dur=?,cap=?,price=?,staff_id=?,days=?,online=?,spots=?,descr=? WHERE id=?',
                (b.get('name', c['name']).strip(), b.get('time', c['time']), int(b.get('dur', c['dur'])), int(b.get('cap', c['cap'])),
                 float(b.get('price', c['price']) or 0), b.get('staffId', c['staff_id']),
                 ','.join(str(int(x)) for x in b['days']) if 'days' in b else c['days'], 1 if b.get('online', bool(c['online'])) else 0,
                 1 if b.get('spots', bool(c.get('spots'))) else 0, (b.get('descr', c.get('descr')) or '')[:300], c['id']))
    return state(ctx['loc'])


def h_class_delete(ctx):
    CON.execute('DELETE FROM classes WHERE id=?', (ctx['id'],))
    return state(ctx['loc'])


def h_attend_add(ctx):
    b = ctx['body']
    c = need(one('SELECT * FROM classes WHERE id=?', (ctx['id'],)), 'class')
    d = b.get('date') or today()
    k = client_of(b['client_id'])
    have = rows('SELECT client_id FROM attendance WHERE class_id=? AND date=?', (c['id'], d))
    if len(have) >= c['cap']:
        raise ApiError('Class is full', 409)
    if any(h['client_id'] == k['id'] for h in have):
        raise ApiError('Already checked in', 409)
    spot = b.get('spot')
    if spot and any(h.get('spot') == spot for h in rows('SELECT spot FROM attendance WHERE class_id=? AND date=?', (c['id'], d))):
        raise ApiError('Spot %s is taken' % spot, 409)
    CON.execute('INSERT INTO attendance(class_id,date,client_id,created_at,spot) VALUES(?,?,?,?,?)', (c['id'], d, k['id'], now_iso(), spot))
    p = active_pass(k['id'])
    if p and p['remaining'] is not None:
        CON.execute('UPDATE client_passes SET remaining=remaining-1 WHERE id=?', (p['id'],))
    CON.execute('UPDATE clients SET visits=visits+1 WHERE id=?', (k['id'],))
    add_sale(ctx['loc'], k['id'], c['staff_id'], c['name'], c['price'], 0, 'Pass' if p else 'Tap to pay', d)
    if hl_active(ctx['loc'], 'objects'):
        cid = hl_contact_for(ctx['loc'], k['id'])
        rid = hl_record(ctx['loc'], 'attendance', dict(label='%s · %s · %s' % (c['name'], d, k['name']), class_name=c['name'], visit_date=d, client=k['name'], paid_with='pass' if p else 'drop-in'), cid)
        if rid:
            CON.execute('UPDATE attendance SET hl_record_id=? WHERE class_id=? AND date=? AND client_id=?', (rid, c['id'], d, k['id']))
    return dict(pass_used=bool(p), state=state(ctx['loc']))


def h_attend_remove(ctx):
    d = ctx['query'].get('date', today())
    CON.execute('DELETE FROM attendance WHERE class_id=? AND date=? AND client_id=?', (ctx['id'], d, ctx['sub']))
    offer_waitlist(ctx['loc'], dict(class_id=ctx['id'], date=d))
    return state(ctx['loc'])


# passes
def h_pass_create(ctx):
    b, loc = ctx['body'], ctx['loc']
    t = b.get('type', 'pack')
    if t not in ('pack', 'unlimited', 'intro'):
        raise ApiError('bad pass type')
    CON.execute('INSERT INTO passes(id,location_id,name,type,credits,price,days,svc_name,descr) VALUES(?,?,?,?,?,?,?,?,?)',
                ('p' + uid(), loc, b['name'].strip(), t, None if t == 'unlimited' else (int(b['credits']) if b.get('credits') not in (None, '') else None),
                 float(b.get('price', 0) or 0), int(b.get('days', 90)), b.get('svc'), b.get('desc')))
    return state(loc)


def h_pass_delete(ctx):
    CON.execute('DELETE FROM passes WHERE id=?', (ctx['id'],))
    return state(ctx['loc'])


def h_pass_sell(ctx):
    b = ctx['body']
    p = need(one('SELECT * FROM passes WHERE id=?', (ctx['id'],)), 'pass')
    k = client_of(b['client_id'])
    remaining = p['credits'] if p['type'] in ('pack', 'intro') and p['credits'] else None
    exp = (date.today() + timedelta(days=p['days'])).isoformat()
    CON.execute('INSERT INTO client_passes(id,client_id,pass_id,remaining,expires,created_at,status,next_billing,auto_renew,price) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (uid(), k['id'], p['id'], remaining, exp, now_iso(), 'active', exp if p['type'] == 'unlimited' else None, 1 if p['type'] == 'unlimited' else 0, p['price']))
    if b.get('method') == 'Gift card':
        redeem_giftcard(ctx['loc'], b.get('code'), p['price'])
    audit(ctx['loc'], 'pass.sell', '%s %s' % (k['name'], p['name']))
    first = one('SELECT id FROM staff WHERE location_id=? ORDER BY sort LIMIT 1', (ctx['loc'],))
    add_sale(ctx['loc'], k['id'], first['id'] if first else None, p['name'], p['price'], 0, b.get('method', 'Tap to pay'))
    cp = one('SELECT * FROM client_passes WHERE client_id=? ORDER BY rowid DESC LIMIT 1', (k['id'],))
    cid = hl_contact_for(ctx['loc'], k['id']) if hl_active(ctx['loc'], 'contacts') or hl_active(ctx['loc'], 'objects') else k.get('hl_contact_id')
    hl_enrol(ctx['loc'], cid, 'pass_sold', k['name'])
    rid = hl_record(ctx['loc'], 'pass', dict(label='%s · %s' % (p['name'], k['name']), pass_type=p['type'], remaining=remaining if remaining is not None else 0, expires=cp['expires'], client=k['name'], price_usd=float(p['price'])), cid)
    if rid:
        CON.execute('UPDATE client_passes SET hl_record_id=? WHERE id=?', (rid, cp['id']))
    return state(ctx['loc'])


# clients
def h_client_create(ctx):
    b = ctx['body']
    if not b.get('name', '').strip():
        raise ApiError('name required')
    kid = new_client(ctx['loc'], b['name'], b.get('phone'), b.get('email'))
    return dict(id=kid, state=state(ctx['loc']))


# appointments
def h_appt_create(ctx):
    b, loc = ctx['body'], ctx['loc']
    if b.get('walkin'):
        v = service_of(b['service_id'])
        t = tstr(now_mins() // 5 * 5)
        sid = b.get('staff_id')
        free = [s for s in qualified_staff(v['id']) if one('SELECT 1 FROM staff_hours WHERE staff_id=? AND dow=?', (s, dow(today()))) and not staff_busy(s, today(), t, v['dur'])]
        if free:
            sid = free[0] if (not sid or sid not in free) else sid
        elif not sid:
            sid = qualified_staff(v['id'])[0]
        r = book(loc, dict(service_id=v['id'], staff_id=sid, client_id=b['client_id'], date=today(), time=t, source='walkin', status='arrived'), enforce=False)
    else:
        r = book(loc, dict(service_id=b['service_id'], staff_id=b.get('staff_id'), client_id=b['client_id'], date=b.get('date'), time=b['time'], source=b.get('source', 'phone'), addons=b.get('addons'), notes=b.get('notes'), spot=b.get('spot')))
        rep_ = b.get('repeat') or {}
        if rep_.get('count'):
            base = dict(service_id=b['service_id'], staff_id=r['staff_id'], client_id=b['client_id'], date=b.get('date') or today(), time=b['time'], source=b.get('source', 'phone'), addons=b.get('addons'))
            series, made, skipped = book_series(loc, base, int(rep_.get('every_weeks', 1)), min(int(rep_['count']), 26))
            CON.execute('UPDATE appointments SET series_id=? WHERE id=?', (series, r['id']))
            return dict(id=r['id'], staff_id=r['staff_id'], series=dict(id=series, made=made, skipped=skipped), state=state(loc))
    return dict(id=r['id'], staff_id=r['staff_id'], state=state(loc))


def appt_of(aid):
    return need(one('SELECT * FROM appointments WHERE id=?', (aid,)), 'appointment')


def set_status(ctx, frm, to):
    a = appt_of(ctx['id'])
    if a['status'] not in frm:
        raise ApiError('Appointment is ' + a['status'], 409)
    CON.execute('UPDATE appointments SET status=? WHERE id=?', (to, a['id']))
    if to in ('noshow', 'cancelled'):
        hl_after_status(ctx['loc'], a['id'], to)
    return state(ctx['loc'])


def h_checkin(ctx):
    return set_status(ctx, ('booked',), 'arrived')


def h_unarrive(ctx):
    return set_status(ctx, ('arrived',), 'booked')


def h_noshow(ctx):
    a = appt_of(ctx['id'])
    st = set_status(ctx, ('booked', 'arrived'), 'noshow')
    fee = apply_fee(ctx['loc'], a['id'], 'noshow') if ctx['body'].get('fee', True) else 0
    offer_waitlist(ctx['loc'], a)
    st = state(ctx['loc']); st['fee'] = fee
    return st


def h_cancel(ctx):
    a = appt_of(ctx['id'])
    L = one('SELECT * FROM locations WHERE id=?', (ctx['loc'],))
    late = is_late(a, L)
    if ctx['body'].get('series') and a.get('series_id'):
        for x in rows("SELECT id FROM appointments WHERE series_id=? AND status='booked' AND (date>? OR (date=? AND time>=?))", (a['series_id'], a['date'], a['date'], a['time'])):
            CON.execute("UPDATE appointments SET status='cancelled' WHERE id=?", (x['id'],))
            hl_after_status(ctx['loc'], x['id'], 'cancelled')
        return state(ctx['loc'])
    set_status(ctx, ('booked', 'arrived'), 'cancelled')
    fee = apply_fee(ctx['loc'], a['id'], 'late') if (late and ctx['body'].get('fee', True)) else 0
    offer_waitlist(ctx['loc'], a)
    st = state(ctx['loc']); st['fee'] = fee; st['late'] = late
    return st


def h_pay(ctx):
    b = ctx['body']
    a = appt_of(ctx['id'])
    if a['status'] not in ('booked', 'arrived'):
        raise ApiError('Appointment is ' + a['status'], 409)
    v = service_of(a['service_id'])
    method = b.get('method', 'Tap to pay')
    if method not in ('Tap to pay', 'Card on file', 'Cash', 'Pass', 'Gift card'):
        raise ApiError('bad method')
    tip = round(float(b.get('tip', 0) or 0), 2)
    price = float(a['total'] or v['price'])  # includes add-ons
    if method == 'Pass':
        p = active_pass(a['client_id'], v['name'])
        if not p:
            raise ApiError('No valid pass for this service', 409)
        if p['remaining'] is not None:
            CON.execute('UPDATE client_passes SET remaining=remaining-1 WHERE id=?', (p['id'],))
        total = 0
    else:
        total = max(0, price - float(a.get('deposit') or 0))  # deposit already taken
    if method == 'Gift card':
        redeem_giftcard(ctx['loc'], b.get('code'), total + tip)
    if method == 'Card on file':
        k = client_of(a['client_id'])
        if not k.get('card_last4'):
            raise ApiError('No card on file for this client', 409)
    CON.execute('UPDATE appointments SET status=?,paid=1,tip=?,total=? WHERE id=?', ('done', tip, 0 if method == 'Pass' else price, a['id']))
    CON.execute('UPDATE clients SET visits=visits+1 WHERE id=?', (a['client_id'],))
    label = v['name'] + (' + ' + ', '.join(x['name'] for x in json.loads(a.get('addons') or '[]')) if a.get('addons') and a['addons'] != '[]' else '')
    CON.execute('INSERT INTO sales(id,location_id,date,client_id,staff_id,label,total,tip,method,created_at,appointment_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (uid(), ctx['loc'], a['date'], a['client_id'], a['staff_id'], label, price if method != 'Pass' else price, tip, method, now_iso(), a['id']))
    audit(ctx['loc'], 'pay', '%s $%.2f %s' % (label, total + tip, method))
    hl_after_status(ctx['loc'], a['id'], 'done')
    hl_after_pay(ctx['loc'], a['id'], method, tip)
    return dict(charged=total + tip, deposit=a.get('deposit') or 0, state=state(ctx['loc']))


def h_availability(ctx):
    q = ctx['query']
    L = one('SELECT * FROM locations WHERE id=?', (ctx['loc'],))
    d = q.get('date', today())
    vid = q['service_id']
    if q.get('staff_id'):
        return dict(slots=slots(vid, q['staff_id'], d, L['slot_min']))
    out = set()
    for sid in qualified_staff(vid):
        out.update(slots(vid, sid, d, L['slot_min']))
    return dict(slots=sorted(out))


# pages
def h_page_update(ctx):
    b = ctx['body']
    CON.execute('UPDATE pages SET on_=? WHERE location_id=? AND id=?', (1 if b.get('on') else 0, ctx['loc'], ctx['id']))
    return state(ctx['loc'])


# public
def public_loc(slug):
    return need(one('SELECT * FROM locations WHERE slug=?', (slug,)), 'business')


def h_public_info(ctx):
    L = public_loc(ctx['slug'])
    st = state(L['id'])
    return dict(name=L['name'], city=L['city'], slug=L['slug'], status=L['status'], type=L['type'], vocab=st['vocab'],
                services=[dict(id=v['id'], cat=v['cat'], name=v['name'], dur=v['dur'], price=v['price'], em=v['em'], staff=v['staff'], deposit=v['deposit'], addons=v['addons'], descr=v['descr']) for v in st['services'] if v['online']],
                classes=[dict(id=c['id'], name=c['name'], time=c['time'], dur=c['dur'], cap=c['cap'], price=c['price'], days=c['days'], staffId=c['staffId']) for c in st['classes'] if c['online'] and st['features']['classes']],
                policy=dict(cancel_hours=st['rules']['cancelHours'], late_fee=st['policy'].get('late_cancel_fee'), noshow_fee=st['policy'].get('noshow_fee'), require_card=bool(st['policy'].get('require_card_online'))),
                staff=[dict(id=s['id'], name=s['name'], role=s['role'], color=s['color']) for s in st['staff']],
                pages={p['id']: p['on'] for p in st['pages']}, features=st['features'], cancelHours=st['rules']['cancelHours'], today=st['today'], website=(st.get('hlFunnel') or {}).get('url'))


def h_public_availability(ctx):
    L = public_loc(ctx['slug'])
    q = ctx['query']
    d = q.get('date', today())
    if L['status'] != 'open' and d == today():
        return dict(slots=[], reason='No slots today')
    ctx['loc'] = L['id']
    return h_availability(ctx)


def h_public_book(ctx):
    L = public_loc(ctx['slug'])
    b = ctx['body']
    d = b.get('date') or today()
    if L['status'] != 'open' and d == today():
        raise ApiError('Online booking is paused today', 409)
    name = (b.get('name') or '').strip()
    if not name:
        raise ApiError('Add your name to confirm')
    phone = (b.get('phone') or '').strip()
    k = None
    if phone:
        k = one('SELECT * FROM clients WHERE location_id=? AND phone=?', (L['id'], phone))
    if not k:
        k = one('SELECT * FROM clients WHERE location_id=? AND lower(name)=lower(?)', (L['id'], name))
    email = (b.get('email') or '').strip()
    if not k and email:
        k = one('SELECT * FROM clients WHERE location_id=? AND lower(email)=lower(?)', (L['id'], email))
    kid = k['id'] if k else new_client(L['id'], name, phone or None, email or None)
    if b.get('card'):
        c4 = ''.join(ch for ch in str(b['card'].get('last4', '')) if ch.isdigit())[-4:]
        if len(c4) == 4:
            CON.execute('UPDATE clients SET card_brand=?, card_last4=? WHERE id=?', (str(b['card'].get('brand', 'Card'))[:20], c4, kid))
    if policy_of(L).get('require_card_online') and not one('SELECT card_last4 FROM clients WHERE id=?', (kid,))['card_last4']:
        raise ApiError('A card is needed to hold this booking', 409)
    r = book(L['id'], dict(service_id=b['service_id'], staff_id=b.get('staff_id') or None, client_id=kid, date=d, time=b['time'], source='online', addons=b.get('addons'), notes=b.get('notes')))
    s = staff_of(r['staff_id'])
    v = service_of(b['service_id'])
    a = one('SELECT manage_token, deposit FROM appointments WHERE id=?', (r['id'],))
    return dict(ok=True, id=r['id'], staff=s['name'], service=v['name'], date=d, time=b['time'], business=L['name'], manage='/manage/%s/%s' % (L['slug'], a['manage_token']), deposit=a.get('deposit') or 0)


# ---------------------------------------------------------------- HighLevel endpoints
def h_hl_status(ctx):
    cfg = hl.load_cfg()
    out = dict(configured=hl.configured(cfg), token=hl.masked_token(cfg), location_id=cfg['location_id'], app_location_id=cfg.get('app_location_id', ''),
               sync=cfg['sync'], workflows=cfg['workflows'], pipeline=cfg['pipeline'], intake_form_id=cfg.get('intake_form_id', ''), objects=cfg.get('objects') or {},
               location=None, error=None, summary=hl_summary(ctx['loc']),
               log=[dict(ts=r['ts'], action=r['action'], ok=bool(r['ok']), detail=r['detail']) for r in rows('SELECT * FROM hl_log WHERE location_id=? OR location_id IS NULL ORDER BY id DESC LIMIT 25', (ctx['loc'],))])
    if hl.configured(cfg):
        try:
            out['location'] = hl.location_info(cfg)
            HL_TZ[cfg['location_id']] = out['location'].get('timezone', '')
        except hl.HLError as e:
            out['error'] = e.message
    elif cfg['token']:
        out['error'] = 'Token saved. Enter the sub-account (location) id to connect.'
    return out


def h_hl_settings(ctx):
    b = ctx['body']
    cfg = hl.load_cfg()
    if 'location_id' in b:
        cfg['location_id'] = str(b['location_id']).strip()
        HL_TZ.clear()
    if 'token' in b and b['token']:
        cfg['token'] = str(b['token']).strip()
    if b.get('link') is True:
        cfg['app_location_id'] = ctx['loc']
    if b.get('link') is False:
        cfg['app_location_id'] = ''
    if isinstance(b.get('sync'), dict):
        cfg['sync'].update({k: bool(v) for k, v in b['sync'].items() if k in hl.DEFAULT_SYNC})
    if isinstance(b.get('workflows'), dict):
        cfg['workflows'].update({k: str(v or '') for k, v in b['workflows'].items() if k in hl.DEFAULT_MAPS['workflows']})
    if isinstance(b.get('pipeline'), dict):
        cfg['pipeline'].update({k: str(v or '') for k, v in b['pipeline'].items() if k in hl.DEFAULT_MAPS['pipeline']})
    if 'intake_form_id' in b:
        cfg['intake_form_id'] = str(b['intake_form_id'] or '')
    hl.save_cfg(cfg)
    return h_hl_status(ctx)


def h_hl_team(ctx):
    """Match local staff to HighLevel users (by name) and calendars (by team member or name); create missing calendars."""
    loc = ctx['loc']
    cfg = hl.load_cfg()
    if not hl.configured(cfg):
        raise ApiError('HighLevel is not connected', 409)
    users = hl.list_users(cfg)
    cals = hl.list_calendars(cfg)
    L = one('SELECT * FROM locations WHERE id=?', (loc,))
    report = []
    for s in rows('SELECT * FROM staff WHERE location_id=? ORDER BY sort', (loc,)):
        uid_ = s.get('hl_user_id')
        if not uid_:
            first = s['name'].split(' ')[0].lower()
            match = [u for u in users if u['name'].lower() == s['name'].lower()] or [u for u in users if u['name'].lower().startswith(first)]
            if not match and len(users) == 1:  # a one-person sub-account: every lane belongs to that user
                match = users
            uid_ = match[0]['id'] if match else None
        cal = s.get('hl_calendar_id')
        if not cal and uid_ and len(users) > 1:
            mine = [c for c in cals if uid_ in c['users'] and c['active']]
            cal = mine[0]['id'] if mine else None
        if not cal:
            byname = [c for c in cals if c['name'].lower() in ('appointments · ' + s['name'].lower(), s['name'].lower()) or s['name'].split(' ')[0].lower() in c['name'].lower()]
            cal = byname[0]['id'] if byname else None
        created = False
        if not cal and uid_ and ctx['body'].get('create', False):
            cal = hl_try(loc, 'calendar.create ' + s['name'], lambda: hl.create_calendar('Appointments · ' + s['name'], uid_, L['slot_min']))
            created = bool(cal)
        CON.execute('UPDATE staff SET hl_user_id=?, hl_calendar_id=? WHERE id=?', (uid_, cal, s['id']))
        report.append(dict(staff=s['name'], user=next((u['name'] for u in users if u['id'] == uid_), None), calendar=next((c['name'] for c in cals if c['id'] == cal), 'Appointments · ' + s['name'] if created else None), created=created))
    hl_log(loc, 'team.sync', True, '%d users, %d calendars in HighLevel' % (len(users), len(cals)))
    return dict(users=users, calendars=cals, report=report, state=state(loc))


def h_hl_push_clients(ctx):
    loc = ctx['loc']
    if not hl.configured():
        raise ApiError('HighLevel is not connected', 409)
    n = 0
    for k in rows('SELECT * FROM clients WHERE location_id=? AND hl_contact_id IS NULL ORDER BY rowid LIMIT 50', (loc,)):
        cid = hl_try(loc, 'contact.upsert ' + k['name'], lambda: hl.upsert_contact(k['name'], k['phone']))
        if cid:
            CON.execute('UPDATE clients SET hl_contact_id=? WHERE id=?', (cid, k['id']))
            n += 1
    return dict(pushed=n, state=state(loc))


def h_hl_import_contacts(ctx):
    loc = ctx['loc']
    if not hl.configured():
        raise ApiError('HighLevel is not connected', 409)
    contacts = hl.list_contacts(100)
    have_ids = {r['hl_contact_id'] for r in rows('SELECT hl_contact_id FROM clients WHERE location_id=? AND hl_contact_id IS NOT NULL', (loc,))}
    n = 0
    for c in contacts:
        if c['id'] in have_ids:
            continue
        local = one('SELECT id FROM clients WHERE location_id=? AND lower(name)=lower(?)', (loc, c['name']))
        if local:
            CON.execute('UPDATE clients SET hl_contact_id=? WHERE id=?', (c['id'], local['id']))
        else:
            kid = 'k' + uid()
            CON.execute('INSERT INTO clients(id,location_id,name,phone,visits,created_at,hl_contact_id) VALUES(?,?,?,?,0,?,?)',
                        (kid, loc, c['name'], c['phone'] or '', now_iso(), c['id']))
        n += 1
    hl_log(loc, 'contacts.import', True, '%d of %d contacts linked or created' % (n, len(contacts)))
    return dict(imported=n, total=len(contacts), state=state(loc))


def h_hl_transactions(ctx):
    if not hl.configured():
        raise ApiError('HighLevel is not connected', 409)
    return dict(transactions=hl.transactions(20))


def h_hl_test(ctx):
    cfg = hl.load_cfg()
    if not hl.configured(cfg):
        raise ApiError('HighLevel is not connected', 409)
    info = hl.location_info(cfg)
    users = hl.list_users(cfg)
    cals = hl.list_calendars(cfg)
    hl_log(ctx['loc'], 'connection.test', True, '%s · %d users · %d calendars' % (info['name'], len(users), len(cals)))
    return dict(location=info, users=len(users), calendars=len(cals))


def h_hl_services_push(ctx):
    """Create a HighLevel calendar for every service that has none; team = users of the staff who can do it."""
    loc = ctx['loc']
    cfg = hl.load_cfg()
    if not hl.configured(cfg):
        raise ApiError('HighLevel is not connected', 409)
    L = one('SELECT * FROM locations WHERE id=?', (loc,))
    users = [u['id'] for u in hl.list_users(cfg)]
    existing = {c['name'].lower(): c['id'] for c in hl.list_calendars(cfg)}
    n = 0
    for v in rows('SELECT * FROM services WHERE location_id=? AND hl_calendar_id IS NULL ORDER BY sort', (loc,)):
        cal = existing.get(v['name'].lower())
        if not cal:
            team = [r['hl_user_id'] for r in rows('SELECT s.hl_user_id FROM service_staff ss JOIN staff s ON s.id=ss.staff_id WHERE ss.service_id=? AND s.hl_user_id IS NOT NULL', (v['id'],))]
            team = sorted(set(team)) or users
            cal = hl_try(loc, 'calendar.create ' + v['name'], lambda: hl.create_service_calendar(v['name'], team, v['dur'], L['slot_min'], '%s · %d min · $%.2f' % (v['cat'], v['dur'], v['price'])))
        if cal:
            CON.execute('UPDATE services SET hl_calendar_id=? WHERE id=?', (cal, v['id']))
            n += 1
    return dict(pushed=n, state=state(loc))


def h_hl_services_import(ctx):
    """Bring the sub-account's calendars in as bookable services (duration from the slot length)."""
    loc = ctx['loc']
    cfg = hl.load_cfg()
    if not hl.configured(cfg):
        raise ApiError('HighLevel is not connected', 409)
    cals = hl.list_calendars(cfg)
    have = {r['hl_calendar_id'] for r in rows('SELECT hl_calendar_id FROM services WHERE location_id=? AND hl_calendar_id IS NOT NULL', (loc,))}
    staff_ids = [r['id'] for r in rows('SELECT id FROM staff WHERE location_id=?', (loc,))]
    n = 0
    order = len(rows('SELECT id FROM services WHERE location_id=?', (loc,)))
    for c in cals:
        if c['id'] in have or not c['active']:
            continue
        local = one('SELECT id FROM services WHERE location_id=? AND lower(name)=lower(?)', (loc, c['name']))
        if local:
            CON.execute('UPDATE services SET hl_calendar_id=? WHERE id=?', (c['id'], local['id']))
        else:
            vid = 'v' + uid()
            CON.execute('INSERT INTO services(id,location_id,cat,name,dur,price,emoji,online,sort,hl_calendar_id) VALUES(?,?,?,?,?,?,?,1,?,?)',
                        (vid, loc, 'From HighLevel', c['name'], int(c['slot'] or 30), 0, '📅', order, c['id']))
            order += 1
            for sid in staff_ids:
                CON.execute('INSERT OR IGNORE INTO service_staff VALUES(?,?)', (vid, sid))
        n += 1
    hl_log(loc, 'calendars.import', True, '%d of %d calendars linked or created as services' % (n, len(cals)))
    return dict(imported=n, total=len(cals), state=state(loc))


def h_hl_funnels(ctx):
    if not hl.configured():
        raise ApiError('HighLevel is not connected', 409)
    return dict(funnels=hl.list_funnels())


def h_hl_funnel_link(ctx):
    b = ctx['body']
    if b.get('id'):
        CON.execute('UPDATE locations SET hl_funnel_id=?, hl_funnel_name=?, hl_funnel_url=? WHERE id=?', (b['id'], b.get('name', ''), b.get('url', ''), ctx['loc']))
        hl_log(ctx['loc'], 'funnel.link ' + (b.get('name') or b['id']), True, b.get('url', ''))
    else:
        CON.execute('UPDATE locations SET hl_funnel_id=NULL, hl_funnel_name=NULL, hl_funnel_url=NULL WHERE id=?', (ctx['loc'],))
    return state(ctx['loc'])


def _need_hl():
    cfg = hl.load_cfg()
    if not hl.configured(cfg):
        raise ApiError('HighLevel is not connected', 409)
    return cfg


def h_hl_options(ctx):
    """Everything the HighLevel tab's selects need, in one call."""
    cfg = _need_hl()
    out = {}
    for key, fn in (('workflows', hl.list_workflows), ('pipelines', hl.list_pipelines), ('forms', hl.list_forms), ('surveys', hl.list_surveys), ('objects', hl.list_objects), ('products', hl.list_products), ('users', hl.list_users)):
        try:
            out[key] = fn(cfg)
        except hl.HLError as e:
            out[key] = []
            out.setdefault('errors', {})[key] = e.message
    return out


def h_hl_products_push(ctx):
    """Service → product with a one-time price; pass → product with a recurring monthly price for memberships."""
    loc = ctx['loc']
    cfg = _need_hl()
    existing = {p['name'].lower(): p['id'] for p in hl.list_products(cfg)}
    n = 0
    for v in rows('SELECT * FROM services WHERE location_id=? AND hl_product_id IS NULL ORDER BY sort', (loc,)):
        pid = existing.get(v['name'].lower()) or hl_try(loc, 'product.create ' + v['name'], lambda: hl.create_product(v['name'], '%s · %d min' % (v['cat'], v['dur']), 'SERVICE'))
        if pid:
            prid = hl_try(loc, 'price.create ' + v['name'], lambda: hl.create_price(pid, 'Standard', v['price']))
            CON.execute('UPDATE services SET hl_product_id=?, hl_price_id=? WHERE id=?', (pid, prid, v['id']))
            n += 1
    for p in rows('SELECT * FROM passes WHERE location_id=? AND hl_product_id IS NULL ORDER BY rowid', (loc,)):
        desc = p['descr'] or ('%d credits · %d days' % (p['credits'], p['days']) if p['credits'] else 'unlimited · renews every %d days' % p['days'])
        pid = existing.get(p['name'].lower()) or hl_try(loc, 'product.create ' + p['name'], lambda: hl.create_product(p['name'], desc, 'SERVICE'))
        if pid:
            months = 1 if p['type'] == 'unlimited' else None
            prid = hl_try(loc, 'price.create ' + p['name'], lambda: hl.create_price(pid, 'Monthly' if months else 'One-time', p['price'], months))
            CON.execute('UPDATE passes SET hl_product_id=?, hl_price_id=? WHERE id=?', (pid, prid, p['id']))
            n += 1
    return dict(pushed=n, state=state(loc))


def h_hl_reconcile(ctx):
    """Read-only: HighLevel's view of a day next to the desk's."""
    cfg = _need_hl()
    loc = ctx['loc']
    d = ctx['query'].get('date', today())
    users = [u['id'] for u in hl.list_users(cfg)]
    tz = hl_tz()
    events = hl.events_for_day(users, d, tz, cfg)
    tx = hl.transactions_between(d, d, cfg)
    invoices = [i for i in hl.list_invoices(100, cfg) if (i.get('when') or '')[:10] == d or True]
    local_appts = rows("SELECT status FROM appointments WHERE location_id=? AND date=? AND status!='cancelled'", (loc, d))
    local_sales = rows('SELECT total, tip, method FROM sales WHERE location_id=? AND date=?', (loc, d))
    by = lambda seq, key: {k: sum(1 for x in seq if x.get(key) == k) for k in sorted({x.get(key) for x in seq})}  # noqa: E731
    return dict(date=d,
                highlevel=dict(appointments=len(events), by_status=by(events, 'status'), transactions=len(tx), transactions_sum=round(sum(float(t['amount'] or 0) for t in tx), 2),
                               invoices_paid=sum(1 for i in invoices if i.get('status') == 'paid'), invoices_total=round(sum(float(i.get('amount_paid') or 0) for i in invoices), 2)),
                desk=dict(appointments=len(local_appts), by_status=by(local_appts, 'status'), sales=len(local_sales), sales_sum=round(sum(x['total'] + x['tip'] for x in local_sales), 2),
                          synced_appointments=one("SELECT COUNT(*) n FROM appointments WHERE location_id=? AND date=? AND hl_event_id IS NOT NULL", (loc, d))['n']))


def h_hl_submissions(ctx):
    cfg = _need_hl()
    k = client_of(ctx['id'])
    if not k.get('hl_contact_id'):
        return dict(submissions=[], intake_url=(hl.FORM_WIDGET + cfg['intake_form_id']) if cfg.get('intake_form_id') else '')
    subs = [s for s in hl.form_submissions(None, 100, cfg) + hl.survey_submissions(None, 100, cfg) if s['contactId'] == k['hl_contact_id']]
    subs.sort(key=lambda s: s['when'], reverse=True)
    return dict(submissions=subs, intake_url=(hl.FORM_WIDGET + cfg['intake_form_id']) if cfg.get('intake_form_id') else '')


def h_hl_email_lapsed(ctx):
    """One re-engagement email to every client with an email address and no visit in `days` days."""
    loc = ctx['loc']
    _need_hl()
    if not hl_active(loc, 'email'):
        raise ApiError('Turn on the Email switch first', 409)
    days = int(ctx['body'].get('days', 60))
    L = one('SELECT name FROM locations WHERE id=?', (loc,))
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    n = 0
    for k in rows("SELECT c.* FROM clients c WHERE c.location_id=? AND c.email LIKE '%@%' AND NOT EXISTS (SELECT 1 FROM appointments a WHERE a.client_id=c.id AND a.status='done' AND a.date>=?)", (loc, cutoff)):
        cid = hl_contact_for(loc, k['id'])
        if not cid:
            continue
        html = '<p>Hi %s,</p><p>It has been a while since your last visit to %s. We would love to see you again — book online any time.</p>' % (k['name'].split(' ')[0], L['name'])
        if hl_try(loc, 'email.lapsed ' + k['name'], lambda: hl.send_email(cid, 'We miss you at ' + L['name'], html)):
            n += 1
    return dict(sent=n, state=state(loc))


def h_hl_objects_setup(ctx):
    """Create the Pass, Class visit and Shift custom objects with their fields and a contact association. Idempotent."""
    loc = ctx['loc']
    cfg = _need_hl()
    have = {o['key']: o for o in hl.list_objects(cfg)}
    assocs = {a['key']: a['id'] for a in hl.list_associations(cfg)}
    report = {}
    for short, spec in hl.OBJECTS.items():
        key = 'custom_objects.' + short
        if key not in have:
            hl_try(loc, 'object.create ' + spec['plural'], lambda: hl.create_object(short, spec, cfg))
        try:
            present = {f['key'].split('.')[-1] for f in hl.object_fields(key, cfg)}
        except hl.HLError as e:
            present = set()
            hl_log(loc, 'object.fields ' + short, False, e.message)
        folder = None
        made = 0
        for fk, name, dtype in spec['fields']:
            if fk in present:
                continue
            folder = folder or hl_try(loc, 'object.folder ' + short, lambda: hl.object_folder(key, cfg))
            if hl_try(loc, 'field.create %s.%s' % (short, fk), lambda: hl.create_object_field(key, fk, name, dtype, folder, cfg)):
                made += 1
        aid = assocs.get(short + '_contact') or hl_try(loc, 'association.create ' + short, lambda: hl.create_association(short, spec['singular'], cfg))
        contact_first = True
        try:
            for a in hl.list_associations(cfg):
                if a['id'] == aid:
                    contact_first = (a['first'] == 'contact')
        except hl.HLError:
            pass
        cfg['objects'][short] = dict(schema=key, assoc=aid or '', contact_first=contact_first)
        report[short] = dict(schema=key, fields_added=made, association=bool(aid))
    hl.save_cfg(cfg)
    return dict(report=report, objects=cfg['objects'], state=state(loc))


def h_hl_objects_backfill(ctx):
    """Push existing passes, class visits and shifts that have no record yet (up to 50 each)."""
    loc = ctx['loc']
    _need_hl()
    if not hl_active(loc, 'objects'):
        raise ApiError('Turn on the Custom objects switch first', 409)
    n = 0
    for cp in rows('SELECT cp.*, c.name client_name, c.id client_id, p.name pass_name, p.type pass_type, p.price FROM client_passes cp JOIN clients c ON c.id=cp.client_id JOIN passes p ON p.id=cp.pass_id WHERE c.location_id=? AND cp.hl_record_id IS NULL LIMIT 50', (loc,)):
        cid = hl_contact_for(loc, cp['client_id'])
        rid = hl_record(loc, 'pass', dict(label='%s · %s' % (cp['pass_name'], cp['client_name']), pass_type=cp['pass_type'], remaining=cp['remaining'] if cp['remaining'] is not None else 0, expires=cp['expires'], client=cp['client_name'], price_usd=float(cp['price'])), cid)
        if rid:
            CON.execute('UPDATE client_passes SET hl_record_id=? WHERE id=?', (rid, cp['id']))
            n += 1
    for a in rows('SELECT a.*, k.name class_name, c.name client_name FROM attendance a JOIN classes k ON k.id=a.class_id JOIN clients c ON c.id=a.client_id WHERE k.location_id=? AND a.hl_record_id IS NULL LIMIT 50', (loc,)):
        cid = hl_contact_for(loc, a['client_id'])
        rid = hl_record(loc, 'attendance', dict(label='%s · %s · %s' % (a['class_name'], a['date'], a['client_name']), class_name=a['class_name'], visit_date=a['date'], client=a['client_name'], paid_with='drop-in'), cid)
        if rid:
            CON.execute('UPDATE attendance SET hl_record_id=? WHERE class_id=? AND date=? AND client_id=?', (rid, a['class_id'], a['date'], a['client_id']))
            n += 1
    for sh in rows('SELECT sh.*, st.name staff_name FROM shifts sh JOIN staff st ON st.id=sh.staff_id WHERE st.location_id=? AND sh.hl_record_id IS NULL LIMIT 50', (loc,)):
        hrs = round(max(0, mins(sh['clock_out']) - mins(sh['clock_in'])) / 60, 2)
        rid = hl_record(loc, 'shift', dict(label='%s · %s' % (sh['staff_name'], sh['date']), staff=sh['staff_name'], shift_date=sh['date'], clock_in=sh['clock_in'], clock_out=sh['clock_out'], hours=hrs))
        if rid:
            CON.execute('UPDATE shifts SET hl_record_id=? WHERE id=?', (rid, sh['id']))
            n += 1
    return dict(pushed=n, state=state(loc))


def h_hl_seed(ctx):
    """Push everything the desk knows into HighLevel: contacts, opportunities, upcoming appointments, products, records. Idempotent."""
    loc = ctx['loc']
    cfg = _need_hl()
    if cfg.get('app_location_id') != loc:
        raise ApiError('Link this business first', 409)
    done = {}
    # contacts
    n = 0
    for k in rows('SELECT * FROM clients WHERE location_id=? AND hl_contact_id IS NULL ORDER BY rowid', (loc,)):
        if hl_contact_for(loc, k['id']):
            n += 1
    done['contacts'] = n
    # opportunities: one per client; clients with paid visits land on the paid stage as won, others on the booked stage
    p = cfg.get('pipeline') or {}
    n = 0
    if p.get('id') and p.get('booked'):
        for k in rows('SELECT * FROM clients WHERE location_id=? AND hl_opportunity_id IS NULL AND hl_contact_id IS NOT NULL ORDER BY rowid', (loc,)):
            paid = one("SELECT COALESCE(SUM(total+tip),0) v FROM sales WHERE client_id=?", (k['id'],))['v']
            stage = p.get('paid') if (k['visits'] and p.get('paid')) else p['booked']
            oid = hl_try(loc, 'opportunity.create ' + k['name'], lambda: hl.create_opportunity(p['id'], stage, k['hl_contact_id'], k['name'] + (' · returning client' if k['visits'] else ' · first booking'), paid)) or hl_try(loc, 'opportunity.find ' + k['name'], lambda: hl.find_opportunity(k['hl_contact_id']))
            if oid:
                CON.execute('UPDATE clients SET hl_opportunity_id=? WHERE id=?', (oid, k['id']))
                if k['visits'] and p.get('paid'):
                    hl_try(loc, 'opportunity.paid ' + k['name'], lambda: hl.update_opportunity(oid, None, 'won'))
                n += 1
    done['opportunities'] = n
    # appointments from today on
    n = 0
    for a in rows("SELECT id FROM appointments WHERE location_id=? AND date>=? AND status IN ('booked','arrived') AND hl_event_id IS NULL ORDER BY date, time", (loc, today())):
        before = one('SELECT hl_event_id FROM appointments WHERE id=?', (a['id'],))['hl_event_id']
        hl_after_book(loc, a['id'])
        if not before and one('SELECT hl_event_id FROM appointments WHERE id=?', (a['id'],))['hl_event_id']:
            n += 1
    done['appointments'] = n
    # products
    ctx2 = dict(ctx)
    done['products'] = h_hl_products_push(ctx2)['pushed']
    # custom-object records
    if hl_active(loc, 'objects') and (cfg.get('objects') or {}):
        done['records'] = h_hl_objects_backfill(ctx2)['pushed']
    hl_log(loc, 'seed', True, ' · '.join('%s %d' % kv for kv in done.items()))
    return dict(done=done, state=state(loc))


def h_hl_seed_history(ctx):
    """Seed the last N days of real-looking activity (paid visits, no-shows, class check-ins, pass sales, shifts) and push
    all of it into every HighLevel tool that is switched on: contacts, calendars (events with their final status), workflows
    (booked / showed / no-show / pass sold), opportunities (won on payment), payments & invoices, products, custom objects.
    Days that already hold appointments are left alone, so running it again is a no-op for them. No texts or emails are sent."""
    loc = ctx['loc']
    import random
    body = ctx['body'] or {}
    days = max(1, min(30, int(body.get('days', 7))))
    rnd = random.Random(int(body.get('seed', 11)))
    cfg = hl.load_cfg()
    linked = hl.configured(cfg) and cfg.get('app_location_id') == loc
    L = one('SELECT * FROM locations WHERE id=?', (loc,))
    services = rows('SELECT * FROM services WHERE location_id=? ORDER BY sort', (loc,))
    staff = rows('SELECT * FROM staff WHERE location_id=? ORDER BY sort', (loc,))
    passes = rows('SELECT * FROM passes WHERE location_id=? ORDER BY rowid', (loc,))
    classes = rows('SELECT * FROM classes WHERE location_id=?', (loc,))
    done = dict(days=days, clients=0, appointments=0, paid=0, noshows=0, visits=0, passes=0, shifts=0)
    people = [k['id'] for k in rows('SELECT id FROM clients WHERE location_id=? ORDER BY rowid', (loc,))]
    for name, phone, email in DEMO_PEOPLE:
        k = one('SELECT id FROM clients WHERE location_id=? AND phone=?', (loc, phone))
        if k:
            people.append(k['id'])
        else:
            kid = 'k' + uid()
            CON.execute('INSERT INTO clients(id,location_id,name,phone,email,visits,created_at) VALUES(?,?,?,?,?,0,?)', (kid, loc, name, phone, email, now_iso()))
            done['clients'] += 1
            people.append(kid)
    made = []  # (appointment id, final status, method, tip)
    new_passes = []
    methods = ['Tap to pay', 'Tap to pay', 'Card on file', 'Cash']
    for i in range(days, 0, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        if one('SELECT 1 FROM appointments WHERE location_id=? AND date=?', (loc, d)):
            continue  # already has history for that day
        for s in staff:
            if not one('SELECT 1 FROM staff_hours WHERE staff_id=? AND dow=?', (s['id'], dow(d))):
                continue
            for _ in range(rnd.choice([2, 3, 3, 4])):
                v = rnd.choice(services)
                sl = slots(v['id'], s['id'], d, L['slot_min'])
                if not sl:
                    break
                t = rnd.choice(sl)
                if staff_busy(s['id'], d, t, int(v['dur']) + int(v.get('gap_min') or 0)):
                    continue
                kid = rnd.choice(people)
                src = rnd.choice(['online', 'online', 'online', 'phone', 'google'])
                r = book(loc, dict(service_id=v['id'], staff_id=s['id'], client_id=kid, date=d, time=t, source=src, no_sync=True), enforce=False)
                if rnd.random() < 0.12:
                    CON.execute("UPDATE appointments SET status='noshow' WHERE id=?", (r['id'],))
                    made.append((r['id'], 'noshow', None, 0))
                    done['noshows'] += 1
                else:
                    tip = rnd.choice([0, 0, 5, 8, 10, 12])
                    method = rnd.choice(methods)
                    CON.execute("UPDATE appointments SET status='done', paid=1, tip=?, total=? WHERE id=?", (tip, v['price'], r['id']))
                    CON.execute('UPDATE clients SET visits=visits+1 WHERE id=?', (kid,))
                    add_sale(loc, kid, s['id'], v['name'], v['price'], tip, method, d)
                    made.append((r['id'], 'done', method, tip))
                    done['paid'] += 1
                done['appointments'] += 1
            if not one('SELECT 1 FROM shifts WHERE staff_id=? AND date=?', (s['id'], d)):
                h = one('SELECT open_time, close_time FROM staff_hours WHERE staff_id=? AND dow=?', (s['id'], dow(d)))
                if h:
                    CON.execute('INSERT INTO shifts(id,staff_id,date,clock_in,clock_out) VALUES(?,?,?,?,?)', (uid(), s['id'], d, h['open_time'], h['close_time']))
                    done['shifts'] += 1
        w = str(dow(d))
        for c in classes:
            if w not in c['days'].split(','):
                continue
            have = set()
            for kid in rnd.sample(people, min(rnd.choice([2, 3, 4]), len(people))):
                if len(have) >= c['cap']:
                    break
                CON.execute('INSERT OR IGNORE INTO attendance(class_id,date,client_id,created_at) VALUES(?,?,?,?)', (c['id'], d, kid, now_iso()))
                CON.execute('UPDATE clients SET visits=visits+1 WHERE id=?', (kid,))
                add_sale(loc, kid, c['staff_id'], c['name'], c['price'], 0, 'Tap to pay', d)
                have.add(kid)
                done['visits'] += 1
        if passes and rnd.random() < 0.5:
            p = rnd.choice(passes)
            kid = rnd.choice(people)
            remaining = p['credits'] if p['type'] in ('pack', 'intro') and p['credits'] else None
            cpid = uid()
            CON.execute('INSERT INTO client_passes(id,client_id,pass_id,remaining,expires,created_at,status,next_billing,auto_renew,price) VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (cpid, kid, p['id'], remaining, (date.today() - timedelta(days=i) + timedelta(days=p['days'])).isoformat(), now_iso(), 'active',
                         (date.today() - timedelta(days=i) + timedelta(days=p['days'])).isoformat() if p['type'] == 'unlimited' else None, 1 if p['type'] == 'unlimited' else 0, p['price']))
            add_sale(loc, kid, staff[0]['id'] if staff else None, p['name'], p['price'], 0, 'Tap to pay', d)
            new_passes.append(kid)
            done['passes'] += 1
    # push into HighLevel, every switched-on tool
    pushed = {}
    if linked:
        mark = (one('SELECT COALESCE(MAX(id),0) m FROM hl_log') or {}).get('m', 0)
        for k in rows('SELECT id FROM clients WHERE location_id=? AND hl_contact_id IS NULL ORDER BY rowid', (loc,)):
            hl_contact_for(loc, k['id'])
        for aid, status, method, tip in made:
            hl_after_book(loc, aid, quiet=True)          # calendar event, "booked" workflow, opportunity
            hl_after_status(loc, aid, status)            # showed / no-show on the event, workflows, opportunity stage
            if status == 'done':
                hl_after_pay(loc, aid, method, tip, quiet=True)  # opportunity won, invoice + recorded payment
        for kid in new_passes:
            k = one('SELECT name, hl_contact_id FROM clients WHERE id=?', (kid,))
            hl_enrol(loc, k['hl_contact_id'], 'pass_sold', k['name'])
        ctx2 = dict(ctx)
        try:
            pushed['products'] = h_hl_products_push(ctx2)['pushed']
        except ApiError:
            pass
        if hl_active(loc, 'objects') and (cfg.get('objects') or {}):
            pushed['records'] = h_hl_objects_backfill(ctx2)['pushed']
        counts = {}
        for l in rows('SELECT action FROM hl_log WHERE id>? AND location_id=? AND ok=1', (mark, loc)):
            key = l['action'].split(' ')[0]
            group = {'contact.upsert': 'contacts', 'appointment.create': 'appointments', 'appointment.done': 'statuses', 'appointment.noshow': 'statuses',
                     'workflow.booked': 'workflows', 'workflow.showed': 'workflows', 'workflow.noshow': 'workflows', 'workflow.pass_sold': 'workflows',
                     'opportunity.create': 'opportunities', 'opportunity.paid': 'opportunities', 'opportunity.noshow': 'opportunities', 'invoice.create': 'invoices'}.get(key)
            if group:
                counts[group] = counts.get(group, 0) + 1
        pushed.update(counts)
        hl_log(loc, 'seed.history', True, '%d days · ' % days + ' · '.join('%s %d' % kv for kv in pushed.items()))
    return dict(done=done, pushed=pushed, linked=linked, state=state(loc))


DEMO_PEOPLE = [('Aarav Mehta', '+15550110201', 'aarav.mehta@example.com'), ('Sofia Rossi', '+15550110202', 'sofia.rossi@example.com'), ('Liam Walker', '+15550110203', 'liam.walker@example.com'),
               ('Noor Rahman', '+15550110204', 'noor.rahman@example.com'), ('Diego Alvarez', '+15550110205', 'diego.alvarez@example.com'), ('Hana Sato', '+15550110206', 'hana.sato@example.com'),
               ('Elena Petrova', '+15550110207', 'elena.petrova@example.com'), ('Marcus Lee', '+15550110208', 'marcus.lee@example.com'), ('Zara Khan', '+15550110209', 'zara.khan@example.com'),
               ('Tom Becker', '+15550110210', 'tom.becker@example.com'), ('Amara Okafor', '+15550110211', 'amara.okafor@example.com'), ('Julien Moreau', '+15550110212', 'julien.moreau@example.com')]


def h_seed_more(ctx):
    """Add a realistic week of demo data locally: named clients with phone and email, bookings across the week,
    pass sales, class check-ins and completed shifts. Nothing is sent to HighLevel here; run Seed for that."""
    loc = ctx['loc']
    import random
    rnd = random.Random(int(ctx['body'].get('seed', 7)))
    L = one('SELECT * FROM locations WHERE id=?', (loc,))
    services = rows('SELECT * FROM services WHERE location_id=? ORDER BY sort', (loc,))
    staff = rows('SELECT * FROM staff WHERE location_id=? ORDER BY sort', (loc,))
    passes = rows('SELECT * FROM passes WHERE location_id=? ORDER BY rowid', (loc,))
    classes = rows('SELECT * FROM classes WHERE location_id=?', (loc,))
    done = dict(clients=0, appointments=0, passes=0, visits=0, shifts=0)
    people = []
    for name, phone, email in DEMO_PEOPLE:
        k = one('SELECT id FROM clients WHERE location_id=? AND phone=?', (loc, phone))
        if not k:
            kid = 'k' + uid()
            CON.execute('INSERT INTO clients(id,location_id,name,phone,email,visits,created_at) VALUES(?,?,?,?,?,?,?)', (kid, loc, name, phone, email, rnd.choice([0, 0, 1, 3, 8]), now_iso()))
            done['clients'] += 1
            people.append(kid)
        else:
            people.append(k['id'])
    # bookings over the next 7 days, two or three per working staff member per day
    taken = set()
    for i in range(0, 7):
        d = (date.today() + timedelta(days=i)).isoformat()
        for s in staff:
            if not one('SELECT 1 FROM staff_hours WHERE staff_id=? AND dow=?', (s['id'], dow(d))):
                continue
            for _ in range(rnd.choice([1, 2, 2, 3])):
                v = rnd.choice(services)
                sl = slots(v['id'], s['id'], d, L['slot_min'])
                if not sl:
                    break
                t = rnd.choice(sl)
                if (s['id'], d, t) in taken or staff_busy(s['id'], d, t, int(v['dur']) + int(v.get('gap_min') or 0)):
                    continue  # never double-book a lane, even when the base seed already filled it
                taken.add((s['id'], d, t))
                kid = rnd.choice(people)
                src = rnd.choice(['online', 'online', 'phone', 'google', 'walkin'])
                status = 'booked'
                r = book(loc, dict(service_id=v['id'], staff_id=s['id'], client_id=kid, date=d, time=t, source=src if src != 'walkin' else 'phone', status=status, no_sync=True), enforce=False)
                if i == 0 and mins(t) < now_mins():
                    # earlier today: already paid, so End of day has something to reconcile
                    tip = rnd.choice([0, 5, 8, 10])
                    CON.execute("UPDATE appointments SET status='done', paid=1, tip=?, total=? WHERE id=?", (tip, v['price'], r['id']))
                    CON.execute('UPDATE clients SET visits=visits+1 WHERE id=?', (kid,))
                    add_sale(loc, kid, s['id'], v['name'], v['price'], tip, rnd.choice(['Tap to pay', 'Card on file', 'Cash']), d)
                done['appointments'] += 1
    # pass sales
    for kid in rnd.sample(people, min(4, len(people))):
        if not passes:
            break
        p = rnd.choice(passes)
        remaining = p['credits'] if p['type'] in ('pack', 'intro') and p['credits'] else None
        CON.execute('INSERT INTO client_passes(id,client_id,pass_id,remaining,expires,created_at) VALUES(?,?,?,?,?,?)', (uid(), kid, p['id'], remaining, (date.today() + timedelta(days=p['days'])).isoformat(), now_iso()))
        add_sale(loc, kid, staff[0]['id'] if staff else None, p['name'], p['price'], 0, 'Tap to pay')
        done['passes'] += 1
    # class check-ins today
    w = str(dow(today()))
    for c in classes:
        if w not in c['days'].split(','):
            continue
        have = {r['client_id'] for r in rows('SELECT client_id FROM attendance WHERE class_id=? AND date=?', (c['id'], today()))}
        for kid in rnd.sample(people, min(3, len(people))):
            if kid in have or len(have) >= c['cap']:
                continue
            CON.execute('INSERT OR IGNORE INTO attendance(class_id,date,client_id,created_at) VALUES(?,?,?,?)', (c['id'], today(), kid, now_iso()))
            CON.execute('UPDATE clients SET visits=visits+1 WHERE id=?', (kid,))
            add_sale(loc, kid, c['staff_id'], c['name'], c['price'], 0, 'Tap to pay', today())
            have.add(kid)
            done['visits'] += 1
    # completed shifts yesterday
    y = (date.today() - timedelta(days=1)).isoformat()
    for s in staff:
        if one('SELECT 1 FROM shifts WHERE staff_id=? AND date=?', (s['id'], y)):
            continue
        h = one('SELECT open_time, close_time FROM staff_hours WHERE staff_id=? AND dow=?', (s['id'], dow(y)))
        if h:
            CON.execute('INSERT INTO shifts(id,staff_id,date,clock_in,clock_out) VALUES(?,?,?,?,?)', (uid(), s['id'], y, h['open_time'], h['close_time']))
            done['shifts'] += 1
    return dict(done=done, state=state(loc))


def h_hl_verify(ctx):
    """Read every linked object back from HighLevel (newest 10 per domain) and report what is really there."""
    loc = ctx['loc']
    cfg = _need_hl()
    out = {}

    def check(name, items, fn):
        res = dict(linked=len(items), checked=0, ok=0, missing=[])
        for label, hid in items[:10]:
            res['checked'] += 1
            try:
                fn(hid)
                res['ok'] += 1
            except hl.HLError as e:
                res['missing'].append('%s (%s)' % (label, e.status))
        out[name] = res
    check('contacts', [(r['name'], r['hl_contact_id']) for r in rows('SELECT name, hl_contact_id FROM clients WHERE location_id=? AND hl_contact_id IS NOT NULL ORDER BY rowid DESC', (loc,))], lambda i: hl.get_contact(i, cfg))
    check('appointments', [(r['date'] + ' ' + r['time'], r['hl_event_id']) for r in rows('SELECT date, time, hl_event_id FROM appointments WHERE location_id=? AND hl_event_id IS NOT NULL ORDER BY rowid DESC', (loc,))], lambda i: hl.get_event(i, cfg))
    check('calendars', [(r['name'], r['hl_calendar_id']) for r in rows('SELECT name, hl_calendar_id FROM services WHERE location_id=? AND hl_calendar_id IS NOT NULL ORDER BY sort', (loc,))], lambda i: hl.get_calendar(i, cfg))
    check('opportunities', [(r['name'], r['hl_opportunity_id']) for r in rows('SELECT name, hl_opportunity_id FROM clients WHERE location_id=? AND hl_opportunity_id IS NOT NULL ORDER BY rowid DESC', (loc,))], lambda i: hl.get_opportunity(i, cfg))
    check('products', [(r['name'], r['hl_product_id']) for r in rows('SELECT name, hl_product_id FROM services WHERE location_id=? AND hl_product_id IS NOT NULL UNION ALL SELECT name, hl_product_id FROM passes WHERE location_id=? AND hl_product_id IS NOT NULL', (loc, loc))], lambda i: hl.get_product(i, cfg))
    check('invoices', [(r['date'], r['hl_invoice_id']) for r in rows('SELECT date, hl_invoice_id FROM appointments WHERE location_id=? AND hl_invoice_id IS NOT NULL ORDER BY rowid DESC', (loc,))], lambda i: hl.get_invoice(i, cfg))
    objs = cfg.get('objects') or {}
    recs = []
    if objs.get('pass'):
        recs += [('pass · ' + r['name'], objs['pass']['schema'], r['hl_record_id']) for r in rows('SELECT c.name, cp.hl_record_id FROM client_passes cp JOIN clients c ON c.id=cp.client_id WHERE c.location_id=? AND cp.hl_record_id IS NOT NULL ORDER BY cp.rowid DESC', (loc,))]
    if objs.get('attendance'):
        recs += [('visit · ' + r['date'], objs['attendance']['schema'], r['hl_record_id']) for r in rows('SELECT a.date, a.hl_record_id FROM attendance a JOIN classes k ON k.id=a.class_id WHERE k.location_id=? AND a.hl_record_id IS NOT NULL ORDER BY a.rowid DESC', (loc,))]
    if objs.get('shift'):
        recs += [('shift · ' + r['date'], objs['shift']['schema'], r['hl_record_id']) for r in rows('SELECT sh.date, sh.hl_record_id FROM shifts sh JOIN staff st ON st.id=sh.staff_id WHERE st.location_id=? AND sh.hl_record_id IS NOT NULL ORDER BY sh.rowid DESC', (loc,))]
    res = dict(linked=len(recs), checked=0, ok=0, missing=[])
    for label, schema, rid in recs[:12]:
        res['checked'] += 1
        try:
            hl.get_record(schema, rid, cfg)
            res['ok'] += 1
        except hl.HLError as e:
            res['missing'].append('%s (%s)' % (label, e.status))
    out['records'] = res
    # configuration health
    wf = cfg.get('workflows') or {}
    p = cfg.get('pipeline') or {}
    out['config'] = dict(workflows_mapped=sum(1 for v in wf.values() if v), pipeline=bool(p.get('id') and p.get('booked')), intake_form=bool(cfg.get('intake_form_id')),
                         objects_setup=len(objs), staff_linked=one('SELECT COUNT(*) n FROM staff WHERE location_id=? AND hl_user_id IS NOT NULL', (loc,))['n'], staff=one('SELECT COUNT(*) n FROM staff WHERE location_id=?', (loc,))['n'])
    total_missing = sum(len(v['missing']) for k, v in out.items() if k != 'config')
    hl_log(loc, 'verify', total_missing == 0, '%d checked, %d missing' % (sum(v['checked'] for k, v in out.items() if k != 'config'), total_missing))
    out['ok'] = total_missing == 0
    return out


def hl_expiring_passes_tick():
    """Hourly: passes expiring within 7 days get the pass_expiring workflow and, with Email on, a heads-up email. Once each."""
    cfg = hl.load_cfg()
    loc = cfg.get('app_location_id')
    if not (hl.configured(cfg) and loc):
        return
    soon = (date.today() + timedelta(days=7)).isoformat()
    with LOCK:
        try:
            for cp in rows("SELECT cp.*, c.name, c.email, c.hl_contact_id, p.name pass_name FROM client_passes cp JOIN clients c ON c.id=cp.client_id JOIN passes p ON p.id=cp.pass_id WHERE c.location_id=? AND cp.expiry_notified IS NULL AND cp.expires<=? AND cp.expires>=? AND (cp.remaining IS NULL OR cp.remaining>0)", (loc, soon, today())):
                cid = cp['hl_contact_id'] or hl_contact_for(loc, cp['client_id'])
                hl_enrol(loc, cid, 'pass_expiring', cp['name'])
                if hl_active(loc, 'email') and cid and cp.get('email') and '@' in cp['email']:
                    hl_try(loc, 'email.pass_expiring ' + cp['name'], lambda: hl.send_email(cid, 'Your %s ends on %s' % (cp['pass_name'], cp['expires']), '<p>Hi %s,</p><p>Your <b>%s</b> ends on %s. Renew at the front desk or reply to this email and we will sort it out.</p>' % (cp['name'].split(' ')[0], cp['pass_name'], cp['expires'])))
                CON.execute('UPDATE client_passes SET expiry_notified=? WHERE id=?', (now_iso(), cp['id']))
            CON.commit()
        except Exception:  # noqa
            CON.rollback()
    threading.Timer(3600, hl_expiring_passes_tick).start()


# ---------------------------------------------------------------- depth: policy, roles, cards, deposits & fees
def policy_of(L):
    p = dict(noshow_fee=50, late_cancel_fee=25, deposit_default=0, reminder_hours=24, require_card_online=False, waitlist=True, self_service=True)
    try:
        p.update(json.loads(L.get('policy') or '{}'))
    except ValueError:
        pass
    return p


def audit(loc_id, action, detail='', actor=None):
    CON.execute('INSERT INTO audit(ts,location_id,actor,action,detail) VALUES(?,?,?,?,?)', (now_iso(), loc_id, actor, action, str(detail)[:300]))
    CON.execute('DELETE FROM audit WHERE id NOT IN (SELECT id FROM audit ORDER BY id DESC LIMIT 500)')


def manage_token():
    return uuid.uuid4().hex[:20]


def h_auth_pin(ctx):
    """PIN sign-in on the shared tablet. Returns who is at the desk and what they may see."""
    pin = str(ctx['body'].get('pin', '')).strip()
    s = one('SELECT id,name,role,role_level,color FROM staff WHERE location_id=? AND pin=?', (ctx['loc'], pin))
    if not s:
        raise ApiError('That PIN does not match anyone on the team', 401)
    audit(ctx['loc'], 'signin', s['name'], s['name'])
    return dict(staff=dict(id=s['id'], name=s['name'], role=s['role'], level=s['role_level'] or 'staff', color=s['color']))


def h_client_update(ctx):
    b = ctx['body']
    k = client_of(ctx['id'])
    fields = dict(name=b.get('name', k['name']).strip() or k['name'], phone=(b.get('phone', k['phone']) or '').strip(), email=(b.get('email', k.get('email')) or '').strip() or None,
                  notes=b.get('notes', k.get('notes')), tags=','.join(sorted({t.strip() for t in (b['tags'] if isinstance(b.get('tags'), list) else str(b.get('tags', k.get('tags') or '')).split(',')) if t.strip()})) if 'tags' in b else k.get('tags'),
                  birthday=b.get('birthday', k.get('birthday')) or None, preferred_staff=b.get('preferred_staff', k.get('preferred_staff')) or None)
    CON.execute('UPDATE clients SET name=?,phone=?,email=?,notes=?,tags=?,birthday=?,preferred_staff=? WHERE id=?',
                (fields['name'], fields['phone'], fields['email'], fields['notes'], fields['tags'], fields['birthday'], fields['preferred_staff'], k['id']))
    if k.get('hl_contact_id') and hl_active(ctx['loc'], 'contacts') and (fields['name'] != k['name'] or fields['phone'] != k['phone'] or fields['email'] != k.get('email')):
        hl_try(ctx['loc'], 'contact.upsert ' + fields['name'], lambda: hl.upsert_contact_full(fields['name'], fields['phone'], fields['email'] or ''))
    return state(ctx['loc'])


def h_client_card(ctx):
    """Save a card on file. In production this is the token from HighLevel Payments' card element; here brand + last4 stand in."""
    b = ctx['body']
    k = client_of(ctx['id'])
    if ctx.get('method') == 'DELETE' or b.get('remove'):
        CON.execute('UPDATE clients SET card_brand=NULL, card_last4=NULL WHERE id=?', (k['id'],))
        audit(ctx['loc'], 'card.remove', k['name'])
    else:
        last4 = ''.join(ch for ch in str(b.get('last4', '')) if ch.isdigit())[-4:]
        if len(last4) != 4:
            raise ApiError('Card needs four last digits')
        CON.execute('UPDATE clients SET card_brand=?, card_last4=? WHERE id=?', (b.get('brand', 'Card')[:20], last4, k['id']))
        audit(ctx['loc'], 'card.save', '%s · %s ****%s' % (k['name'], b.get('brand', 'Card'), last4))
    return state(ctx['loc'])


def charge_card(loc_id, k, amount, label, appointment_id=None, staff_id=None):
    """Charge the card on file (simulated) and record the sale. Returns the sale amount or 0 when no card."""
    if not k.get('card_last4') or amount <= 0:
        return 0
    CON.execute('INSERT INTO sales(id,location_id,date,client_id,staff_id,label,total,tip,method,created_at,appointment_id,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (uid(), loc_id, today(), k['id'], staff_id, label, round(amount, 2), 0, 'Card on file', now_iso(), appointment_id, '%s ****%s' % (k.get('card_brand') or 'Card', k['card_last4'])))
    audit(loc_id, 'charge', '%s %s $%.2f' % (k['name'], label, amount))
    return round(amount, 2)


def take_deposit(loc_id, aid):
    a = one('SELECT a.*, v.deposit svc_deposit, v.name svc_name FROM appointments a JOIN services v ON v.id=a.service_id WHERE a.id=?', (aid,))
    if a:
        a = dict(a); a['deposit'], a['svc_deposit_val'] = a.get('deposit'), a.get('svc_deposit')
    if not a or not float(a.get('svc_deposit_val') or 0) or float(a.get('deposit') or 0):
        return 0
    L = one('SELECT * FROM locations WHERE id=?', (loc_id,))
    dep = float(a['svc_deposit_val'] or policy_of(L).get('deposit_default') or 0)
    k = client_of(a['client_id'])
    got = charge_card(loc_id, k, dep, 'Deposit · ' + a['svc_name'], aid, a['staff_id'])
    if got:
        CON.execute('UPDATE appointments SET deposit=? WHERE id=?', (got, aid))
    return got


def apply_fee(loc_id, aid, kind):
    """No-show or late-cancel fee from policy, charged to the card on file when there is one."""
    a = one('SELECT a.*, v.name svc_name FROM appointments a JOIN services v ON v.id=a.service_id WHERE a.id=?', (aid,))
    L = one('SELECT * FROM locations WHERE id=?', (loc_id,))
    pol = policy_of(L)
    fee = float(pol.get('noshow_fee' if kind == 'noshow' else 'late_cancel_fee') or 0)
    if a.get('deposit'):
        fee = max(0, fee - a['deposit'])  # the deposit is forfeited and counts toward the fee
        CON.execute('UPDATE appointments SET fee=? WHERE id=?', (a['deposit'], aid))
    if fee <= 0:
        return 0
    k = client_of(a['client_id'])
    got = charge_card(loc_id, k, fee, ('No-show fee' if kind == 'noshow' else 'Late cancellation') + ' · ' + a['svc_name'], aid, a['staff_id'])
    if got:
        CON.execute('UPDATE appointments SET fee=COALESCE(fee,0)+? WHERE id=?', (got, aid))
    return got


def is_late(a, L):
    if a['date'] > today():
        return False
    return (mins(a['time']) - now_mins()) < L['cancel_hours'] * 60 if a['date'] == today() else True


# ---------------------------------------------------------------- blocks & time off (availability aware)
def blocks_for(sid, d):
    return rows('SELECT * FROM blocks WHERE date=? AND (staff_id=? OR staff_id IS NULL)', (d, sid))


def h_block_create(ctx):
    b = ctx['body']
    loc = ctx['loc']
    kind = b.get('kind', 'block')
    if kind not in ('block', 'timeoff'):
        raise ApiError('kind must be block or timeoff')
    dates = b.get('dates') or [b.get('date') or today()]
    ids = []
    for d in dates:
        bid = 'b' + uid()
        CON.execute('INSERT INTO blocks(id,location_id,staff_id,date,start,end,kind,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                    (bid, loc, b.get('staff_id') or None, d, b.get('start', '00:00') if kind == 'block' else '00:00', b.get('end', '23:59') if kind == 'block' else '23:59', kind, (b.get('reason') or ('Time off' if kind == 'timeoff' else 'Blocked'))[:80], now_iso()))
        ids.append(bid)
    audit(loc, 'block.create', '%s %s' % (kind, ', '.join(dates)))
    return dict(ids=ids, state=state(loc))


def h_block_delete(ctx):
    CON.execute('DELETE FROM blocks WHERE id=? AND location_id=?', (ctx['id'], ctx['loc']))
    return state(ctx['loc'])


# ---------------------------------------------------------------- reschedule, recurring, self-service
def h_appt_update(ctx):
    """Move an appointment: new date, time or staff member. Re-checks availability."""
    b = ctx['body']
    a = appt_of(ctx['id'])
    if a['status'] not in ('booked', 'arrived'):
        raise ApiError('Only upcoming appointments can be moved', 409)
    v = service_of(a['service_id'])
    d, t, sid = b.get('date', a['date']), b.get('time', a['time']), b.get('staff_id', a['staff_id'])
    if sid not in qualified_staff(v['id']):
        raise ApiError('That staff member does not offer this service', 409)
    if not one('SELECT 1 FROM staff_hours WHERE staff_id=? AND dow=?', (sid, dow(d))):
        raise ApiError('Not working that day', 409)
    if staff_busy(sid, d, t, v['dur'] + (v.get('gap_min') or 0), exclude=a['id']):
        raise ApiError('That slot is taken', 409)
    CON.execute('UPDATE appointments SET date=?, time=?, staff_id=? WHERE id=?', (d, t, sid, a['id']))
    if 'notes' in b:
        CON.execute('UPDATE appointments SET notes=? WHERE id=?', (b['notes'], a['id']))
    audit(ctx['loc'], 'appointment.move', '%s → %s %s' % (a['id'], d, t))
    if a.get('hl_event_id') and hl_active(ctx['loc'], 'appointments'):
        tz = hl_tz()
        hl_try(ctx['loc'], 'appointment.move', lambda: hl.request('PUT', '/calendars/events/appointments/' + a['hl_event_id'], dict(startTime=hl.iso_at(d, t, tz), endTime=hl.iso_at(d, tstr(mins(t) + v['dur']), tz)), version='2021-04-15'))
    return state(ctx['loc'])


def book_series(loc_id, base, every_weeks, count):
    """Repeat a booking every N weeks for `count` more occurrences, skipping dates that are not available."""
    sid_series = 'r' + uid()
    made, skipped = [], []
    v = service_of(base['service_id'])
    L = one('SELECT * FROM locations WHERE id=?', (loc_id,))
    for i in range(1, count + 1):
        d = (date.fromisoformat(base['date']) + timedelta(weeks=every_weeks * i)).isoformat()
        if base['time'] in slots(v['id'], base['staff_id'], d, L['slot_min']):
            r = book(loc_id, dict(base, date=d, no_sync=False))
            CON.execute('UPDATE appointments SET series_id=? WHERE id=?', (sid_series, r['id']))
            made.append(d)
        else:
            skipped.append(d)
    return sid_series, made, skipped


def h_public_manage(ctx):
    """What the client sees from the link in their confirmation."""
    L = public_loc(ctx['slug'])
    a = one('SELECT a.*, v.name svc_name, v.dur, v.price, s.name staff_name, c.name client_name FROM appointments a JOIN services v ON v.id=a.service_id JOIN staff s ON s.id=a.staff_id JOIN clients c ON c.id=a.client_id WHERE a.manage_token=? AND a.location_id=?', (ctx['token'], L['id']))
    if not a:
        raise ApiError('This link is no longer valid', 404)
    pol = policy_of(L)
    return dict(business=L['name'], service=a['svc_name'], staff=a['staff_name'], staff_id=a['staff_id'], service_id=a['service_id'], client=a['client_name'], date=a['date'], time=a['time'], dur=a['dur'], price=a['price'],
                status=a['status'], deposit=a.get('deposit') or 0, cancel_hours=L['cancel_hours'], late_fee=pol.get('late_cancel_fee') or 0, late=is_late(a, L),
                self_service=bool(pol.get('self_service', True)), today=today())


def h_public_manage_cancel(ctx):
    L = public_loc(ctx['slug'])
    a = one('SELECT * FROM appointments WHERE manage_token=? AND location_id=?', (ctx['token'], L['id']))
    if not a or a['status'] not in ('booked',):
        raise ApiError('This booking cannot be cancelled online', 409)
    late = is_late(a, L)
    CON.execute("UPDATE appointments SET status='cancelled' WHERE id=?", (a['id'],))
    fee = apply_fee(L['id'], a['id'], 'late') if late else 0
    hl_after_status(L['id'], a['id'], 'cancelled')
    offer_waitlist(L['id'], a)
    audit(L['id'], 'appointment.cancel.self', a['id'] + (' late' if late else ''))
    return dict(ok=True, late=late, fee=fee)


def h_public_manage_move(ctx):
    L = public_loc(ctx['slug'])
    a = one('SELECT * FROM appointments WHERE manage_token=? AND location_id=?', (ctx['token'], L['id']))
    if not a or a['status'] != 'booked':
        raise ApiError('This booking cannot be moved online', 409)
    if is_late(a, L) and not policy_of(L).get('self_service', True):
        raise ApiError('Too close to the appointment to move it online. Please call.', 409)
    b = ctx['body']
    ctx2 = dict(ctx, loc=L['id'], id=a['id'], body=dict(date=b['date'], time=b['time'], staff_id=b.get('staff_id') or a['staff_id']))
    h_appt_update(ctx2)
    return dict(ok=True, date=b['date'], time=b['time'])


# ---------------------------------------------------------------- waitlist
def h_waitlist_add(ctx):
    b = ctx['body']
    loc = ctx['loc']
    k = client_of(b['client_id'])
    wid = 'w' + uid()
    CON.execute('INSERT INTO waitlist(id,location_id,client_id,class_id,service_id,staff_id,date,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                (wid, loc, k['id'], b.get('class_id') or None, b.get('service_id') or None, b.get('staff_id') or None, b.get('date') or today(), 'waiting', now_iso()))
    audit(loc, 'waitlist.add', k['name'])
    return dict(id=wid, state=state(loc))


def h_waitlist_remove(ctx):
    CON.execute('DELETE FROM waitlist WHERE id=? AND location_id=?', (ctx['id'], ctx['loc']))
    return state(ctx['loc'])


def h_waitlist_book(ctx):
    """Desk converts a waitlist entry into a booking or a class seat."""
    w = need(one('SELECT * FROM waitlist WHERE id=? AND location_id=?', (ctx['id'], ctx['loc'])), 'waitlist entry')
    b = ctx['body']
    if w['class_id']:
        h_attend_add(dict(ctx, id=w['class_id'], body=dict(client_id=w['client_id'], date=w['date'])))
    else:
        book(ctx['loc'], dict(service_id=w['service_id'], staff_id=w.get('staff_id') or b.get('staff_id'), client_id=w['client_id'], date=w['date'], time=b['time'], source='phone'))
    CON.execute("UPDATE waitlist SET status='booked' WHERE id=?", (w['id'],))
    return state(ctx['loc'])


def offer_waitlist(loc_id, freed):
    """A booking or seat was freed: offer it to the first person waiting for that class or that staff member and day."""
    if isinstance(freed, dict) and freed.get('class_id'):
        w = one("SELECT * FROM waitlist WHERE location_id=? AND class_id=? AND date=? AND status='waiting' ORDER BY created_at LIMIT 1", (loc_id, freed['class_id'], freed['date']))
    else:
        w = one("SELECT * FROM waitlist WHERE location_id=? AND date=? AND status='waiting' AND class_id IS NULL AND (staff_id IS NULL OR staff_id=?) AND (service_id IS NULL OR service_id=?) ORDER BY created_at LIMIT 1", (loc_id, freed['date'], freed['staff_id'], freed['service_id']))
    if not w:
        return None
    CON.execute("UPDATE waitlist SET status='offered', offered_at=? WHERE id=?", (now_iso(), w['id']))
    k = client_of(w['client_id'])
    L = one('SELECT * FROM locations WHERE id=?', (loc_id,))
    audit(loc_id, 'waitlist.offer', k['name'] + ' · ' + w['date'])
    if hl_active(loc_id, 'sms') and hl.clean_phone(k['phone']):
        cid = hl_contact_for(loc_id, k['id'])
        if cid:
            hl_try(loc_id, 'sms.waitlist ' + k['name'], lambda: hl.send_sms(cid, 'Good news from %s: a spot opened on %s. Reply YES to take it.' % (L['name'], w['date'])))
    return w['id']


# ---------------------------------------------------------------- memberships: billing, freeze, cancel
def h_pass_freeze(ctx):
    cp = need(one('SELECT cp.*, c.location_id FROM client_passes cp JOIN clients c ON c.id=cp.client_id WHERE cp.id=?', (ctx['id'],)), 'membership')
    until = ctx['body'].get('until') or (date.today() + timedelta(days=30)).isoformat()
    CON.execute("UPDATE client_passes SET status='frozen', frozen_until=? WHERE id=?", (until, cp['id']))
    audit(ctx['loc'], 'membership.freeze', cp['id'] + ' until ' + until)
    return state(ctx['loc'])


def h_pass_unfreeze(ctx):
    cp = need(one('SELECT * FROM client_passes WHERE id=?', (ctx['id'],)), 'membership')
    CON.execute("UPDATE client_passes SET status='active', frozen_until=NULL WHERE id=?", (cp['id'],))
    return state(ctx['loc'])


def h_pass_cancel(ctx):
    cp = need(one('SELECT * FROM client_passes WHERE id=?', (ctx['id'],)), 'membership')
    CON.execute("UPDATE client_passes SET status='cancelled', auto_renew=0 WHERE id=?", (cp['id'],))
    audit(ctx['loc'], 'membership.cancel', cp['id'])
    return state(ctx['loc'])


def bill_memberships_tick():
    """Daily: charge memberships that are due, extend them, mirror as an invoice when Invoices is on."""
    with LOCK:
        try:
            for cp in rows("SELECT cp.*, c.location_id, c.name, c.card_last4, p.name pass_name, p.days, p.price pprice FROM client_passes cp JOIN clients c ON c.id=cp.client_id JOIN passes p ON p.id=cp.pass_id WHERE cp.auto_renew=1 AND cp.status='active' AND cp.next_billing IS NOT NULL AND cp.next_billing<=?", (today(),)):
                amount = float(cp.get('price') or cp['pprice'] or 0)
                k = client_of(cp['client_id'])
                got = charge_card(cp['location_id'], k, amount, 'Membership · ' + cp['pass_name'])
                if not got:
                    CON.execute('INSERT INTO sales(id,location_id,date,client_id,staff_id,label,total,tip,method,created_at,note) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                                (uid(), cp['location_id'], today(), k['id'], None, 'Membership · ' + cp['pass_name'], amount, 0, 'Due at desk', now_iso(), 'no card on file'))
                nxt = (date.fromisoformat(cp['next_billing']) + timedelta(days=cp['days'])).isoformat()
                CON.execute('UPDATE client_passes SET next_billing=?, expires=? WHERE id=?', (nxt, nxt, cp['id']))
                audit(cp['location_id'], 'membership.bill', '%s %s $%.2f%s' % (k['name'], cp['pass_name'], amount, '' if got else ' (no card, due at desk)'))
            # frozen memberships thaw
            CON.execute("UPDATE client_passes SET status='active', frozen_until=NULL WHERE status='frozen' AND frozen_until<=?", (today(),))
            CON.commit()
        except Exception:  # noqa
            CON.rollback()
    threading.Timer(6 * 3600, bill_memberships_tick).start()


def h_bill_now(ctx):
    """Run the billing cycle immediately (demo and month-end)."""
    before = one('SELECT COUNT(*) n FROM sales WHERE location_id=?', (ctx['loc'],))['n']
    threading.Timer(0, lambda: None).cancel()
    # inline version of the tick for this request (LOCK already held by the dispatcher)
    for cp in rows("SELECT cp.*, c.location_id, c.name, p.name pass_name, p.days, p.price pprice FROM client_passes cp JOIN clients c ON c.id=cp.client_id JOIN passes p ON p.id=cp.pass_id WHERE c.location_id=? AND cp.auto_renew=1 AND cp.status='active' AND cp.next_billing IS NOT NULL AND cp.next_billing<=?", (ctx['loc'], today())):
        amount = float(cp.get('price') or cp['pprice'] or 0)
        k = client_of(cp['client_id'])
        got = charge_card(ctx['loc'], k, amount, 'Membership · ' + cp['pass_name'])
        if not got:
            CON.execute('INSERT INTO sales(id,location_id,date,client_id,staff_id,label,total,tip,method,created_at,note) VALUES(?,?,?,?,?,?,?,?,?,?,?)', (uid(), ctx['loc'], today(), k['id'], None, 'Membership · ' + cp['pass_name'], amount, 0, 'Due at desk', now_iso(), 'no card on file'))
        nxt = (date.fromisoformat(cp['next_billing']) + timedelta(days=cp['days'])).isoformat()
        CON.execute('UPDATE client_passes SET next_billing=?, expires=? WHERE id=?', (nxt, nxt, cp['id']))
    after = one('SELECT COUNT(*) n FROM sales WHERE location_id=?', (ctx['loc'],))['n']
    return dict(billed=after - before, state=state(ctx['loc']))


# ---------------------------------------------------------------- gift cards & refunds
def h_giftcard_create(ctx):
    b = ctx['body']
    amount = float(b.get('amount', 0) or 0)
    if amount <= 0:
        raise ApiError('Amount must be positive')
    import random
    code = 'GC-' + ''.join(random.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(8))
    CON.execute('INSERT INTO giftcards(id,location_id,code,balance,initial,client_id,created_at) VALUES(?,?,?,?,?,?,?)', (uid(), ctx['loc'], code, amount, amount, b.get('client_id') or None, now_iso()))
    add_sale(ctx['loc'], b.get('client_id'), None, 'Gift card ' + code, amount, 0, b.get('method', 'Tap to pay'))
    return dict(code=code, state=state(ctx['loc']))


def redeem_giftcard(loc_id, code, amount):
    g = one('SELECT * FROM giftcards WHERE location_id=? AND code=?', (loc_id, (code or '').strip().upper()))
    if not g:
        raise ApiError('Gift card not found', 404)
    if g['balance'] < amount - 0.005:
        raise ApiError('Gift card balance is $%.2f' % g['balance'], 409)
    CON.execute('UPDATE giftcards SET balance=balance-? WHERE id=?', (amount, g['id']))
    return g


def h_refund(ctx):
    s = need(one('SELECT * FROM sales WHERE id=? AND location_id=?', (ctx['id'], ctx['loc'])), 'sale')
    if s.get('refund_of') or one('SELECT 1 FROM sales WHERE refund_of=?', (s['id'],)):
        raise ApiError('Already refunded', 409)
    amount = float(ctx['body'].get('amount') or (s['total'] + s['tip']))
    CON.execute('INSERT INTO sales(id,location_id,date,client_id,staff_id,label,total,tip,method,created_at,refund_of,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (uid(), ctx['loc'], today(), s['client_id'], s['staff_id'], 'Refund · ' + s['label'], -round(amount, 2), 0, s['method'], now_iso(), s['id'], ctx['body'].get('reason', '')[:120]))
    audit(ctx['loc'], 'refund', '%s $%.2f' % (s['label'], amount))
    return state(ctx['loc'])


# ---------------------------------------------------------------- kiosk
def h_public_kiosk(ctx):
    """Self check-in: phone digits or membership code. Checks in today's appointment or class and reports membership status."""
    L = public_loc(ctx['slug'])
    q = ''.join(ch for ch in str(ctx['body'].get('q', '')) if ch.isdigit())
    if len(q) < 4:
        raise ApiError('Enter the last 4 digits of your mobile number')
    cands = [c for c in rows('SELECT * FROM clients WHERE location_id=?', (L['id'],)) if ''.join(ch for ch in (c['phone'] or '') if ch.isdigit()).endswith(q)]
    if not cands:
        raise ApiError('We could not find you. Ask at the desk.', 404)
    if len(cands) > 1 and len(q) < 7:
        raise ApiError('More than one match. Enter more digits.', 409)
    k = cands[0]
    out = dict(name=k['name'].split(' ')[0], checked_in=[], membership=None)
    for a in rows("SELECT a.*, v.name svc_name, s.name staff_name FROM appointments a JOIN services v ON v.id=a.service_id JOIN staff s ON s.id=a.staff_id WHERE a.client_id=? AND a.date=? AND a.status='booked' AND ABS(?-(CAST(substr(a.time,1,2) AS INT)*60+CAST(substr(a.time,4,2) AS INT)))<=90", (k['id'], today(), now_mins())):
        CON.execute("UPDATE appointments SET status='arrived' WHERE id=?", (a['id'],))
        out['checked_in'].append('%s with %s at %s' % (a['svc_name'], a['staff_name'].split(' ')[0], a['time']))
    w = str(dow(today()))
    for c in rows('SELECT * FROM classes WHERE location_id=?', (L['id'],)):
        if w in c['days'].split(',') and abs(mins(c['time']) - now_mins()) <= 45:
            have = [r['client_id'] for r in rows('SELECT client_id FROM attendance WHERE class_id=? AND date=?', (c['id'], today()))]
            if k['id'] not in have and len(have) < c['cap']:
                try:
                    h_attend_add(dict(ctx, loc=L['id'], id=c['id'], body=dict(client_id=k['id'], date=today())))
                    out['checked_in'].append(c['name'] + ' at ' + c['time'])
                except ApiError:
                    pass
    cp = one("SELECT cp.*, p.name pass_name FROM client_passes cp JOIN passes p ON p.id=cp.pass_id WHERE cp.client_id=? AND cp.expires>=? ORDER BY cp.rowid DESC LIMIT 1", (k['id'], today()))
    if cp:
        out['membership'] = dict(name=cp['pass_name'], status=cp.get('status') or 'active', remaining=cp['remaining'], expires=cp['expires'])
    audit(L['id'], 'kiosk.checkin', k['name'] + ' · ' + ('; '.join(out['checked_in']) or 'nothing due'))
    return out


# ---------------------------------------------------------------- insights
def h_insights(ctx):
    loc = ctx['loc']
    days = int(ctx['query'].get('days', 30))
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    sales = rows('SELECT date,total,tip,method,label,client_id,staff_id FROM sales WHERE location_id=? AND date>=?', (loc, since))
    appts = rows('SELECT a.*, v.name svc_name FROM appointments a JOIN services v ON v.id=a.service_id WHERE a.location_id=? AND a.date>=? AND a.date<=?', (loc, since, today()))
    by_day = {}
    for i in range(days):
        by_day[(date.today() - timedelta(days=days - 1 - i)).isoformat()] = 0.0
    for s in sales:
        if s['date'] in by_day:
            by_day[s['date']] += s['total'] + s['tip']
    src = {}
    for a in appts:
        src[a['source']] = src.get(a['source'], 0) + 1
    top = {}
    for a in appts:
        if a['status'] == 'done':
            top[a['svc_name']] = top.get(a['svc_name'], 0) + 1
    done = [a for a in appts if a['status'] == 'done']
    noshow = [a for a in appts if a['status'] == 'noshow']
    clients_seen = {}
    for a in done:
        clients_seen[a['client_id']] = clients_seen.get(a['client_id'], 0) + 1
    returning = sum(1 for n in clients_seen.values() if n >= 2)
    by_staff = {}
    for s in sales:
        if s['staff_id']:
            by_staff[s['staff_id']] = by_staff.get(s['staff_id'], 0) + s['total'] + s['tip']
    staff_names = {r['id']: r['name'] for r in rows('SELECT id,name FROM staff WHERE location_id=?', (loc,))}
    fill = []
    for c in rows('SELECT * FROM classes WHERE location_id=?', (loc,)):
        n = one('SELECT COUNT(*) n FROM attendance WHERE class_id=? AND date>=?', (c['id'], since))['n']
        held = sum(1 for i in range(days) if str(dow((date.today() - timedelta(days=i)).isoformat())) in c['days'].split(','))
        fill.append(dict(name=c['name'], rate=round(n / (c['cap'] * held) * 100) if held else 0, held=held))
    lapsed = rows("SELECT c.id,c.name FROM clients c WHERE c.location_id=? AND c.visits>0 AND NOT EXISTS (SELECT 1 FROM appointments a WHERE a.client_id=c.id AND a.status='done' AND a.date>=?)", (loc, (date.today() - timedelta(days=45)).isoformat()))
    active_memberships = one("SELECT COUNT(*) n FROM client_passes cp JOIN clients c ON c.id=cp.client_id JOIN passes p ON p.id=cp.pass_id WHERE c.location_id=? AND p.type='unlimited' AND cp.status='active' AND cp.expires>=?", (loc, today()))['n']
    mrr = one("SELECT COALESCE(SUM(COALESCE(cp.price,p.price)),0) v FROM client_passes cp JOIN clients c ON c.id=cp.client_id JOIN passes p ON p.id=cp.pass_id WHERE c.location_id=? AND p.type='unlimited' AND cp.status='active' AND cp.expires>=?", (loc, today()))['v']
    return dict(days=days, revenue=round(sum(by_day.values()), 2), revenue_by_day=[dict(date=k, value=round(v, 2)) for k, v in by_day.items()],
                visits=len(done), noshow_rate=round(len(noshow) / len(appts) * 100) if appts else 0, avg_ticket=round(sum(s['total'] + s['tip'] for s in sales) / len(sales), 2) if sales else 0,
                by_source=src, top_services=sorted(top.items(), key=lambda x: -x[1])[:6], returning=returning, unique_clients=len(clients_seen),
                by_staff=sorted([(staff_names.get(k, '—'), round(v, 2)) for k, v in by_staff.items()], key=lambda x: -x[1]), class_fill=fill,
                lapsed=[dict(id=l['id'], name=l['name']) for l in lapsed[:10]], lapsed_count=len(lapsed), active_memberships=active_memberships, mrr=round(mrr, 2),
                by_method=sorted({m: round(sum(s['total'] + s['tip'] for s in sales if s['method'] == m), 2) for m in {s['method'] for s in sales}}.items(), key=lambda x: -x[1]))


def h_settings(ctx):
    """Business settings: hours, policy, brand."""
    b = ctx['body']
    L = one('SELECT * FROM locations WHERE id=?', (ctx['loc'],))
    if 'open' in b or 'close' in b:
        CON.execute('UPDATE locations SET open_time=?, close_time=? WHERE id=?', (b.get('open', L['open_time']), b.get('close', L['close_time']), L['id']))
    if isinstance(b.get('policy'), dict):
        pol = policy_of(L)
        for k, v in b['policy'].items():
            if k in pol:
                pol[k] = bool(v) if isinstance(pol[k], bool) else float(v or 0)
        CON.execute('UPDATE locations SET policy=? WHERE id=?', (json.dumps(pol), L['id']))
    if 'name' in b and b['name'].strip():
        CON.execute('UPDATE locations SET name=? WHERE id=?', (b['name'].strip()[:60], L['id']))
    if 'city' in b:
        CON.execute('UPDATE locations SET city=? WHERE id=?', (b['city'].strip()[:60], L['id']))
    if 'brand' in b:
        CON.execute('UPDATE locations SET brand=? WHERE id=?', (json.dumps(b['brand']), L['id']))
    audit(ctx['loc'], 'settings.update', ', '.join(b.keys()))
    return state(ctx['loc'])


def h_audit(ctx):
    return dict(events=[dict(ts=r['ts'], actor=r['actor'], action=r['action'], detail=r['detail']) for r in rows('SELECT * FROM audit WHERE location_id=? ORDER BY id DESC LIMIT 60', (ctx['loc'],))])


def h_search(ctx):
    """Command palette search: clients, services, classes, upcoming appointments."""
    q = (ctx['query'].get('q') or '').strip().lower()
    loc = ctx['loc']
    if len(q) < 2:
        return dict(results=[])
    like = '%' + q + '%'
    out = []
    for c in rows('SELECT id,name,phone,email FROM clients WHERE location_id=? AND (lower(name) LIKE ? OR phone LIKE ? OR lower(email) LIKE ?) LIMIT 6', (loc, like, like, like)):
        out.append(dict(kind='client', id=c['id'], title=c['name'], sub=c['phone'] or c['email'] or ''))
    for v in rows('SELECT id,name,dur,price FROM services WHERE location_id=? AND lower(name) LIKE ? LIMIT 4', (loc, like)):
        out.append(dict(kind='service', id=v['id'], title=v['name'], sub='%d min · $%.2f' % (v['dur'], v['price'])))
    for a in rows("SELECT a.id,a.date,a.time,c.name,v.name svc FROM appointments a JOIN clients c ON c.id=a.client_id JOIN services v ON v.id=a.service_id WHERE a.location_id=? AND a.date>=? AND a.status IN ('booked','arrived') AND lower(c.name) LIKE ? ORDER BY a.date,a.time LIMIT 5", (loc, today(), like)):
        out.append(dict(kind='appointment', id=a['id'], title='%s · %s' % (a['name'], a['svc']), sub='%s %s' % (a['date'], a['time'])))
    return dict(results=out)


# ---------------------------------------------------------------- routing
ROUTES = [
    ('GET',    r'/api/locations$', h_locations),
    ('GET',    r'/api/locations/(?P<loc>\w+)/state$', h_state),
    ('PATCH',  r'/api/locations/(?P<loc>\w+)$', h_patch_location),
    ('POST',   r'/api/locations/(?P<loc>\w+)/demo/reset$', h_reset),
    ('POST',   r'/api/locations/(?P<loc>\w+)/demo/rush$', h_rush),
    ('GET',    r'/api/locations/(?P<loc>\w+)/availability$', h_availability),
    ('POST',   r'/api/locations/(?P<loc>\w+)/staff$', h_staff_create),
    ('PATCH',  r'/api/locations/(?P<loc>\w+)/staff/(?P<id>\w+)$', h_staff_update),
    ('DELETE', r'/api/locations/(?P<loc>\w+)/staff/(?P<id>\w+)$', h_staff_delete),
    ('PUT',    r'/api/locations/(?P<loc>\w+)/staff/(?P<id>\w+)/hours$', h_staff_hours),
    ('POST',   r'/api/locations/(?P<loc>\w+)/staff/(?P<id>\w+)/clock$', h_staff_clock),
    ('POST',   r'/api/locations/(?P<loc>\w+)/services$', h_service_create),
    ('PATCH',  r'/api/locations/(?P<loc>\w+)/services/(?P<id>\w+)$', h_service_update),
    ('DELETE', r'/api/locations/(?P<loc>\w+)/services/(?P<id>\w+)$', h_service_delete),
    ('POST',   r'/api/locations/(?P<loc>\w+)/classes$', h_class_create),
    ('PATCH',  r'/api/locations/(?P<loc>\w+)/classes/(?P<id>\w+)$', h_class_update),
    ('DELETE', r'/api/locations/(?P<loc>\w+)/classes/(?P<id>\w+)$', h_class_delete),
    ('POST',   r'/api/locations/(?P<loc>\w+)/classes/(?P<id>\w+)/attendance$', h_attend_add),
    ('DELETE', r'/api/locations/(?P<loc>\w+)/classes/(?P<id>\w+)/attendance/(?P<sub>\w+)$', h_attend_remove),
    ('POST',   r'/api/locations/(?P<loc>\w+)/passes$', h_pass_create),
    ('DELETE', r'/api/locations/(?P<loc>\w+)/passes/(?P<id>\w+)$', h_pass_delete),
    ('POST',   r'/api/locations/(?P<loc>\w+)/passes/(?P<id>\w+)/sell$', h_pass_sell),
    ('POST',   r'/api/locations/(?P<loc>\w+)/clients$', h_client_create),
    ('POST',   r'/api/locations/(?P<loc>\w+)/appointments$', h_appt_create),
    ('POST',   r'/api/locations/(?P<loc>\w+)/appointments/(?P<id>\w+)/checkin$', h_checkin),
    ('POST',   r'/api/locations/(?P<loc>\w+)/appointments/(?P<id>\w+)/unarrive$', h_unarrive),
    ('POST',   r'/api/locations/(?P<loc>\w+)/appointments/(?P<id>\w+)/noshow$', h_noshow),
    ('POST',   r'/api/locations/(?P<loc>\w+)/appointments/(?P<id>\w+)/cancel$', h_cancel),
    ('POST',   r'/api/locations/(?P<loc>\w+)/appointments/(?P<id>\w+)/pay$', h_pay),
    ('PATCH',  r'/api/locations/(?P<loc>\w+)/pages/(?P<id>\w+)$', h_page_update),
    ('GET',    r'/api/locations/(?P<loc>\w+)/hl/status$', h_hl_status),
    ('PATCH',  r'/api/locations/(?P<loc>\w+)/hl/settings$', h_hl_settings),
    ('POST',   r'/api/locations/(?P<loc>\w+)/hl/team$', h_hl_team),
    ('POST',   r'/api/locations/(?P<loc>\w+)/hl/push-clients$', h_hl_push_clients),
    ('POST',   r'/api/locations/(?P<loc>\w+)/hl/import-contacts$', h_hl_import_contacts),
    ('GET',    r'/api/locations/(?P<loc>\w+)/hl/transactions$', h_hl_transactions),
    ('POST',   r'/api/locations/(?P<loc>\w+)/hl/test$', h_hl_test),
    ('POST',   r'/api/locations/(?P<loc>\w+)/hl/services/push$', h_hl_services_push),
    ('POST',   r'/api/locations/(?P<loc>\w+)/hl/services/import$', h_hl_services_import),
    ('GET',    r'/api/locations/(?P<loc>\w+)/hl/funnels$', h_hl_funnels),
    ('POST',   r'/api/locations/(?P<loc>\w+)/hl/funnel$', h_hl_funnel_link),
    ('GET',    r'/api/locations/(?P<loc>\w+)/hl/options$', h_hl_options),
    ('POST',   r'/api/locations/(?P<loc>\w+)/hl/products/push$', h_hl_products_push),
    ('GET',    r'/api/locations/(?P<loc>\w+)/hl/reconcile$', h_hl_reconcile),
    ('GET',    r'/api/locations/(?P<loc>\w+)/hl/clients/(?P<id>\w+)/submissions$', h_hl_submissions),
    ('POST',   r'/api/locations/(?P<loc>\w+)/hl/email/lapsed$', h_hl_email_lapsed),
    ('POST',   r'/api/locations/(?P<loc>\w+)/hl/objects/setup$', h_hl_objects_setup),
    ('POST',   r'/api/locations/(?P<loc>\w+)/hl/objects/backfill$', h_hl_objects_backfill),
    ('POST',   r'/api/locations/(?P<loc>\w+)/hl/seed$', h_hl_seed),
    ('POST',   r'/api/locations/(?P<loc>\w+)/hl/seed-history$', h_hl_seed_history),
    ('GET',    r'/api/locations/(?P<loc>\w+)/hl/verify$', h_hl_verify),
    ('POST',   r'/api/locations/(?P<loc>\w+)/demo/seed-more$', h_seed_more),
    ('POST',   r'/api/locations/(?P<loc>\w+)/auth/pin$', h_auth_pin),
    ('PATCH',  r'/api/locations/(?P<loc>\w+)/clients/(?P<id>\w+)$', h_client_update),
    ('POST',   r'/api/locations/(?P<loc>\w+)/clients/(?P<id>\w+)/card$', h_client_card),
    ('DELETE', r'/api/locations/(?P<loc>\w+)/clients/(?P<id>\w+)/card$', h_client_card),
    ('PATCH',  r'/api/locations/(?P<loc>\w+)/appointments/(?P<id>\w+)$', h_appt_update),
    ('POST',   r'/api/locations/(?P<loc>\w+)/blocks$', h_block_create),
    ('DELETE', r'/api/locations/(?P<loc>\w+)/blocks/(?P<id>\w+)$', h_block_delete),
    ('POST',   r'/api/locations/(?P<loc>\w+)/waitlist$', h_waitlist_add),
    ('DELETE', r'/api/locations/(?P<loc>\w+)/waitlist/(?P<id>\w+)$', h_waitlist_remove),
    ('POST',   r'/api/locations/(?P<loc>\w+)/waitlist/(?P<id>\w+)/book$', h_waitlist_book),
    ('POST',   r'/api/locations/(?P<loc>\w+)/client-passes/(?P<id>\w+)/freeze$', h_pass_freeze),
    ('POST',   r'/api/locations/(?P<loc>\w+)/client-passes/(?P<id>\w+)/unfreeze$', h_pass_unfreeze),
    ('POST',   r'/api/locations/(?P<loc>\w+)/client-passes/(?P<id>\w+)/cancel$', h_pass_cancel),
    ('POST',   r'/api/locations/(?P<loc>\w+)/billing/run$', h_bill_now),
    ('POST',   r'/api/locations/(?P<loc>\w+)/giftcards$', h_giftcard_create),
    ('POST',   r'/api/locations/(?P<loc>\w+)/sales/(?P<id>\w+)/refund$', h_refund),
    ('GET',    r'/api/locations/(?P<loc>\w+)/insights$', h_insights),
    ('PATCH',  r'/api/locations/(?P<loc>\w+)/settings$', h_settings),
    ('GET',    r'/api/locations/(?P<loc>\w+)/audit$', h_audit),
    ('GET',    r'/api/locations/(?P<loc>\w+)/search$', h_search),
    ('GET',    r'/api/public/(?P<slug>[a-z]+)/manage/(?P<token>\w+)$', h_public_manage),
    ('POST',   r'/api/public/(?P<slug>[a-z]+)/manage/(?P<token>\w+)/cancel$', h_public_manage_cancel),
    ('POST',   r'/api/public/(?P<slug>[a-z]+)/manage/(?P<token>\w+)/move$', h_public_manage_move),
    ('POST',   r'/api/public/(?P<slug>[a-z]+)/kiosk$', h_public_kiosk),
    ('GET',    r'/api/public/(?P<slug>[a-z]+)$', h_public_info),
    ('GET',    r'/api/public/(?P<slug>[a-z]+)/availability$', h_public_availability),
    ('POST',   r'/api/public/(?P<slug>[a-z]+)/book$', h_public_book),
]
COMPILED = [(m, re.compile(p), h) for m, p, h in ROUTES]
MIME = {'.html': 'text/html; charset=utf-8', '.js': 'application/javascript', '.mjs': 'application/javascript', '.css': 'text/css', '.png': 'image/png', '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.json': 'application/json', '.woff2': 'font/woff2', '.woff': 'font/woff', '.txt': 'text/plain', '.map': 'application/json'}


class Handler(BaseHTTPRequestHandler):
    server_version = 'Appointments/1.0'

    def log_message(self, fmt, *args):
        sys.stderr.write('%s %s\n' % (self.command, self.path))

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def _static(self, name, base=None):
        path = os.path.join(base or STATIC, name)
        if not os.path.isfile(path):
            return self._json(404, dict(error='not found'))
        with open(path, 'rb') as f:
            data = f.read()
        self.send_response(200)
        self.send_header('Content-Type', MIME.get(os.path.splitext(name)[1], 'application/octet-stream'))
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def _dispatch(self):
        u = urlparse(self.path)
        if not u.path.startswith('/api/'):
            if self.command != 'GET':
                return self._json(405, dict(error='method not allowed'))
            if DIST:  # production: the built React app, with SPA fallback for its routes
                rel = u.path.lstrip('/').replace('..', '')
                if rel and os.path.isfile(os.path.join(DIST, rel)):
                    return self._static(rel, DIST)
                return self._static('index.html', DIST)
            if u.path in ('/', '/index.html'):
                return self._static('index.html')
            if u.path.startswith('/book/'):
                return self._static('book.html')
            if u.path.startswith('/manage/'):
                return self._static('manage.html')
            if u.path.startswith('/kiosk/'):
                return self._static('kiosk.html')
            return self._static(u.path.lstrip('/').replace('..', ''))
        body = {}
        n = int(self.headers.get('Content-Length') or 0)
        if n:
            try:
                body = json.loads(self.rfile.read(n) or b'{}')
            except ValueError:
                return self._json(400, dict(error='invalid JSON'))
        query = {k: v[0] for k, v in parse_qs(u.query).items()}
        for method, rx, fn in COMPILED:
            m = rx.match(u.path)
            if m and method == self.command:
                ctx = dict(body=body, query=query, method=self.command)
                ctx.update(m.groupdict())
                with LOCK:
                    try:
                        out = fn(ctx)
                        CON.commit()
                    except ApiError as e:
                        CON.rollback()
                        return self._json(e.code, dict(error=str(e)))
                    except hl.HLError as e:
                        CON.rollback()
                        return self._json(502, dict(error='HighLevel: ' + e.message))
                    except KeyError as e:
                        CON.rollback()
                        return self._json(400, dict(error='missing field ' + str(e)))
                    except Exception as e:  # noqa
                        CON.rollback()
                        import traceback
                        traceback.print_exc()
                        return self._json(500, dict(error=str(e)))
                return self._json(200, out)
        for method, rx, fn in COMPILED:
            if rx.match(u.path):
                return self._json(405, dict(error='method not allowed'))
        return self._json(404, dict(error='no such route'))

    do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = _dispatch


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    print('Appointments running on http://localhost:%d  (db: %s)' % (port, db.DB_PATH))
    threading.Timer(5, hl_expiring_passes_tick).start()
    threading.Timer(8, bill_memberships_tick).start()
    ThreadingHTTPServer(('', port), Handler).serve_forever()
