"""HighLevel (LeadConnector) API v2 client used by the Appointments server.

Credentials live in hl.config.json next to this file (never in source, never in the zip),
or in the environment: HL_TOKEN, HL_LOCATION_ID. The token is only ever sent to
https://services.leadconnectorhq.com.
"""
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

BASE = 'https://services.leadconnectorhq.com'
CFG_PATH = os.environ.get('HL_CONFIG', os.path.join(os.path.dirname(__file__), 'hl.config.json'))
DEFAULT_SYNC = dict(contacts=True, appointments=True, invoices=False, sms=False, workflows=True, opportunities=True, email=False, objects=False)
DEFAULT_MAPS = dict(workflows=dict(booked='', noshow='', showed='', pass_sold='', pass_expiring=''), pipeline=dict(id='', booked='', paid='', noshow=''), intake_form_id='', objects={})
TAG = 'appointments-app'


class HLError(Exception):
    def __init__(self, status, message):
        super().__init__('%s: %s' % (status, message))
        self.status = status
        self.message = message


# ---------------------------------------------------------------- config
def load_cfg():
    cfg = dict(token='', location_id='', app_location_id='', sync=dict(DEFAULT_SYNC), workflows=dict(DEFAULT_MAPS['workflows']), pipeline=dict(DEFAULT_MAPS['pipeline']), intake_form_id='', objects={})
    try:
        with open(CFG_PATH) as f:
            data = json.load(f)
        cfg.update({k: v for k, v in data.items() if k not in ('sync', 'workflows', 'pipeline', 'objects')})
        cfg['sync'].update({k: bool(v) for k, v in (data.get('sync') or {}).items() if k in DEFAULT_SYNC})
        cfg['workflows'].update({k: str(v or '') for k, v in (data.get('workflows') or {}).items() if k in DEFAULT_MAPS['workflows']})
        cfg['pipeline'].update({k: str(v or '') for k, v in (data.get('pipeline') or {}).items() if k in DEFAULT_MAPS['pipeline']})
        cfg['objects'] = dict(data.get('objects') or {})
    except (OSError, ValueError):
        pass
    cfg['token'] = os.environ.get('HL_TOKEN') or cfg.get('token') or ''
    cfg['location_id'] = os.environ.get('HL_LOCATION_ID') or cfg.get('location_id') or ''
    return cfg


def save_cfg(cfg):
    tmp = CFG_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(cfg, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CFG_PATH)


def configured(cfg=None):
    cfg = cfg or load_cfg()
    return bool(cfg['token'] and cfg['location_id'])


def masked_token(cfg=None):
    t = (cfg or load_cfg())['token']
    return (t[:4] + '…' + t[-4:]) if len(t) > 10 else ('set' if t else '')


# ---------------------------------------------------------------- transport
def request(method, path, body=None, version='2021-07-28', timeout=12, cfg=None):
    cfg = cfg or load_cfg()
    if not cfg['token']:
        raise HLError(0, 'No HighLevel token configured')
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, method=method, data=data, headers={
        'Authorization': 'Bearer ' + cfg['token'], 'Version': version, 'Accept': 'application/json',
        'Content-Type': 'application/json', 'User-Agent': 'Appointments/1.0 (HighLevel integration)'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace')
        try:
            msg = json.loads(raw)
            msg = msg.get('message') or msg.get('error') or raw
            if isinstance(msg, list):
                msg = '; '.join(str(m) for m in msg)
        except ValueError:
            msg = raw[:200]
        raise HLError(e.code, str(msg))
    except urllib.error.URLError as e:
        raise HLError(0, 'network: %s' % e.reason)
    except (socket.timeout, TimeoutError):
        raise HLError(0, 'HighLevel did not answer within %ss' % timeout)
    except OSError as e:
        raise HLError(0, 'network: %s' % e)
    except ValueError:
        raise HLError(0, 'HighLevel returned a non-JSON response')


def q(params):
    return '?' + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, '')})


# ---------------------------------------------------------------- reads
def location_info(cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/locations/' + cfg['location_id'], cfg=cfg)
    loc = r.get('location', r)
    return dict(id=loc.get('id', cfg['location_id']), name=loc.get('name', ''), timezone=loc.get('timezone', ''),
                city=loc.get('city', ''), country=loc.get('country', ''), phone=loc.get('phone', ''), email=loc.get('email', ''))


def list_users(cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/users/' + q(dict(locationId=cfg['location_id'])), cfg=cfg)
    return [dict(id=u.get('id'), name=(u.get('name') or ((u.get('firstName') or '') + ' ' + (u.get('lastName') or '')).strip()),
                 email=u.get('email', ''), role=(u.get('roles') or {}).get('role', '')) for u in r.get('users', [])]


def list_calendars(cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/calendars/' + q(dict(locationId=cfg['location_id'])), version='2021-04-15', cfg=cfg)
    out = []
    for c in r.get('calendars', []):
        out.append(dict(id=c.get('id'), name=c.get('name', ''), type=c.get('calendarType', ''), active=c.get('isActive', True),
                        users=[m.get('userId') for m in (c.get('teamMembers') or []) if m.get('userId')],
                        slot=c.get('slotDuration') or 30, description=c.get('description') or '', widget=c.get('widgetSlug') or '',
                        booking_url='https://api.leadconnectorhq.com/widget/booking/' + str(c.get('id'))))
    return out


def create_service_calendar(name, user_ids, duration, slot_min=30, description='', cfg=None):
    """One HighLevel calendar per bookable service; team members are the staff who can do it."""
    cfg = cfg or load_cfg()
    members = [dict(userId=u, priority=0.5) for u in user_ids if u]
    body = dict(locationId=cfg['location_id'], name=name, description=description or ('Created by the Appointments app'),
                calendarType='round_robin', eventType='RoundRobin_OptimizeForAvailability', teamMembers=members,
                slotDuration=int(duration), slotDurationUnit='mins', slotInterval=int(slot_min), slotIntervalUnit='mins',
                isActive=True, widgetType='classic', eventTitle='{{contact.name}} · ' + name, appoinmentPerSlot=1)
    r = request('POST', '/calendars/', body, version='2021-04-15', cfg=cfg)
    return (r.get('calendar') or r).get('id')


def list_funnels(cfg=None):
    """Funnels and websites of the sub-account, with their steps (pages)."""
    cfg = cfg or load_cfg()
    r = request('GET', '/funnels/funnel/list' + q(dict(locationId=cfg['location_id'], limit=50)), cfg=cfg)
    out = []
    for f in r.get('funnels', []):
        steps = []
        for st in f.get('steps') or []:
            steps.append(dict(id=st.get('id'), name=st.get('name', ''), url=st.get('url', ''), type=st.get('type', '')))
        out.append(dict(id=f.get('_id'), name=f.get('name', ''), type=f.get('type', 'funnel'), url=f.get('url') or '',
                        domain=f.get('domainId') or '', updated=f.get('updatedAt') or f.get('dateUpdated') or f.get('dateAdded') or '', steps=steps))
    return out


def list_contacts(limit=100, cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/contacts/' + q(dict(locationId=cfg['location_id'], limit=min(limit, 100))), cfg=cfg)
    return [dict(id=c.get('id'), name=(c.get('contactName') or ((c.get('firstName') or '') + ' ' + (c.get('lastName') or '')).strip() or c.get('email') or 'Unnamed'),
                 phone=c.get('phone') or '', email=c.get('email') or '', tags=c.get('tags') or []) for c in r.get('contacts', [])]


def transactions(limit=20, cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/payments/transactions' + q(dict(altId=cfg['location_id'], altType='location', limit=limit)), cfg=cfg)
    out = []
    for t in r.get('data', []):
        out.append(dict(id=t.get('_id'), amount=t.get('amount'), currency=t.get('currency', ''), status=t.get('status', ''),
                        method=t.get('paymentProviderType') or t.get('entitySourceType') or '', name=t.get('contactName') or (t.get('contactSnapshot') or {}).get('name') or '',
                        when=t.get('createdAt', '')))
    return out


# ---------------------------------------------------------------- writes
def split_name(name):
    parts = (name or '').strip().split(' ', 1)
    return parts[0], (parts[1] if len(parts) > 1 else '')


def clean_phone(phone):
    p = ''.join(ch for ch in (phone or '') if ch.isdigit() or ch == '+')
    if '•' in (phone or '') or len(p.strip('+')) < 10:
        return ''  # masked demo numbers and short strings are not real phones
    if not p.startswith('+'):
        p = '+1' + p if len(p) == 10 else '+' + p
    return p


def upsert_contact(name, phone='', cfg=None):
    cfg = cfg or load_cfg()
    first, last = split_name(name)
    body = dict(locationId=cfg['location_id'], firstName=first, lastName=last, tags=[TAG], source='Appointments app')
    ph = clean_phone(phone)
    if ph:
        body['phone'] = ph
    r = request('POST', '/contacts/upsert', body, cfg=cfg)
    c = r.get('contact', r)
    return c.get('id')


def create_calendar(name, user_id, slot_min=30, cfg=None):
    cfg = cfg or load_cfg()
    body = dict(locationId=cfg['location_id'], name=name, description='Created by the Appointments app', calendarType='round_robin',
                eventType='RoundRobin_OptimizeForAvailability', teamMembers=[dict(userId=user_id, priority=0.5)],
                slotDuration=slot_min, slotDurationUnit='mins', slotInterval=slot_min, slotIntervalUnit='mins',
                isActive=True, widgetType='classic', eventTitle='{{contact.name}}', appoinmentPerSlot=1)
    r = request('POST', '/calendars/', body, version='2021-04-15', cfg=cfg)
    return (r.get('calendar') or r).get('id')


def iso_at(d, t, tz):
    """Local date 'YYYY-MM-DD' + 'HH:MM' in the location's timezone → ISO 8601 with offset."""
    naive = datetime.strptime(d + ' ' + t, '%Y-%m-%d %H:%M')
    try:
        from zoneinfo import ZoneInfo
        aware = naive.replace(tzinfo=ZoneInfo(tz)) if tz else naive.astimezone()
    except Exception:
        aware = naive.astimezone()
    return aware.isoformat(timespec='seconds')


def create_appointment(calendar_id, contact_id, start_iso, end_iso, title, user_id=None, cfg=None):
    cfg = cfg or load_cfg()
    body = dict(calendarId=calendar_id, locationId=cfg['location_id'], contactId=contact_id, startTime=start_iso, endTime=end_iso,
                title=title, appointmentStatus='confirmed', ignoreDateRange=True, toNotify=False, ignoreFreeSlotValidation=True)
    if user_id:
        body['assignedUserId'] = user_id
    r = request('POST', '/calendars/events/appointments', body, version='2021-04-15', cfg=cfg)
    return r.get('id') or (r.get('event') or {}).get('id')


STATUS_MAP = dict(booked='confirmed', arrived='confirmed', done='showed', noshow='noshow', cancelled='cancelled')


def update_appointment_status(event_id, local_status, cfg=None):
    cfg = cfg or load_cfg()
    request('PUT', '/calendars/events/appointments/' + event_id, dict(appointmentStatus=STATUS_MAP.get(local_status, 'confirmed')),
            version='2021-04-15', cfg=cfg)


def send_sms(contact_id, message, cfg=None):
    cfg = cfg or load_cfg()
    r = request('POST', '/conversations/messages', dict(type='SMS', contactId=contact_id, message=message), version='2021-04-15', cfg=cfg)
    return r.get('messageId') or r.get('conversationId')


PAY_MODE = {'Cash': 'cash', 'Card on file': 'card', 'Tap to pay': 'card', 'Pass': 'other'}


def invoice_and_record_payment(contact_id, contact_name, phone, label, amount, tip, method, product_id=None, price_id=None, cfg=None):
    cfg = cfg or load_cfg()
    today = date.today()
    items = [dict(name=label, currency='USD', amount=round(float(amount), 2), qty=1)]
    if product_id:
        items[0]['productId'] = product_id
    if price_id:
        items[0]['priceId'] = price_id
    if tip:
        items.append(dict(name='Tip', currency='USD', amount=round(float(tip), 2), qty=1))
    contact = dict(id=contact_id, name=contact_name)
    ph = clean_phone(phone)
    if ph:
        contact['phoneNo'] = ph
    try:
        biz = location_info(cfg).get('name') or 'Business'
    except HLError:
        biz = 'Business'
    body = dict(altId=cfg['location_id'], altType='location', name='Appointments · ' + label, currency='USD', items=items,
                contactDetails=contact, businessDetails=dict(name=biz), discount=dict(type='percentage', value=0),
                issueDate=today.isoformat(), dueDate=today.isoformat(), liveMode=True,
                title='INVOICE', termsNotes='Recorded by the Appointments app')
    inv = request('POST', '/invoices/', body, cfg=cfg)
    inv_id = inv.get('_id') or inv.get('id')
    total = round(float(amount) + float(tip or 0), 2)
    if inv_id and total > 0:
        request('POST', '/invoices/%s/record-payment' % inv_id,
                dict(altId=cfg['location_id'], altType='location', mode=PAY_MODE.get(method, 'other'), amount=total,
                     notes='Paid at the front desk (%s)' % method), cfg=cfg)
    return inv_id


def list_invoices(limit=20, cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/invoices/' + q(dict(altId=cfg['location_id'], altType='location', limit=limit, offset=0)), cfg=cfg)
    return [dict(id=i.get('_id'), name=i.get('name', ''), number=i.get('invoiceNumber', ''), status=i.get('status', ''),
                 total=i.get('total'), amount_paid=i.get('amountPaid'), contact=(i.get('contactDetails') or {}).get('name', ''), when=i.get('createdAt', ''))
            for i in r.get('invoices', [])]


def delete_invoice(inv_id, cfg=None):
    cfg = cfg or load_cfg()
    request('DELETE', '/invoices/%s' % inv_id + q(dict(altId=cfg['location_id'], altType='location')), cfg=cfg)


# ---------------------------------------------------------------- products & store
def list_products(cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/products/' + q(dict(locationId=cfg['location_id'], limit=100)), cfg=cfg)
    return [dict(id=p.get('_id'), name=p.get('name', ''), type=p.get('productType', ''), store=bool(p.get('availableInStore'))) for p in r.get('products', [])]


def create_product(name, description='', ptype='SERVICE', in_store=True, cfg=None):
    cfg = cfg or load_cfg()
    body = dict(locationId=cfg['location_id'], name=name, productType=ptype, description=description or name, availableInStore=bool(in_store))
    r = request('POST', '/products/', body, cfg=cfg)
    return r.get('_id') or (r.get('product') or {}).get('_id')


def create_price(product_id, name, amount, recurring_months=None, cfg=None):
    cfg = cfg or load_cfg()
    body = dict(name=name, type='recurring' if recurring_months else 'one_time', currency='USD', amount=round(float(amount), 2), locationId=cfg['location_id'])
    if recurring_months:
        body['recurring'] = dict(interval='month', intervalCount=int(recurring_months))
    r = request('POST', '/products/%s/price' % product_id, body, cfg=cfg)
    return r.get('_id') or (r.get('price') or {}).get('_id')


# ---------------------------------------------------------------- workflows
def list_workflows(cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/workflows/' + q(dict(locationId=cfg['location_id'])), cfg=cfg)
    return [dict(id=w.get('id'), name=w.get('name', ''), status=w.get('status', '')) for w in r.get('workflows', [])]


def enrol_workflow(contact_id, workflow_id, cfg=None):
    cfg = cfg or load_cfg()
    request('POST', '/contacts/%s/workflow/%s' % (contact_id, workflow_id), dict(eventStartTime=datetime.now().astimezone().isoformat(timespec='seconds')), cfg=cfg)
    return workflow_id


# ---------------------------------------------------------------- opportunities & pipelines
def list_pipelines(cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/opportunities/pipelines' + q(dict(locationId=cfg['location_id'])), cfg=cfg)
    return [dict(id=p.get('id'), name=p.get('name', ''), stages=[dict(id=s.get('id'), name=s.get('name', '')) for s in sorted(p.get('stages') or [], key=lambda s: s.get('position', 0))])
            for p in r.get('pipelines', [])]


def create_opportunity(pipeline_id, stage_id, contact_id, name, value=0, cfg=None):
    cfg = cfg or load_cfg()
    body = dict(pipelineId=pipeline_id, locationId=cfg['location_id'], name=name, pipelineStageId=stage_id, status='open', contactId=contact_id, monetaryValue=round(float(value or 0), 2), source='Appointments app')
    r = request('POST', '/opportunities/', body, cfg=cfg)
    return (r.get('opportunity') or r).get('id')


def find_opportunity(contact_id, cfg=None):
    """The opportunity already on a contact, if any (HighLevel allows one per contact per pipeline)."""
    cfg = cfg or load_cfg()
    r = request('GET', '/opportunities/search' + q(dict(location_id=cfg['location_id'], contact_id=contact_id, limit=5)), cfg=cfg)
    ops = r.get('opportunities', [])
    return ops[0].get('id') if ops else None


def update_opportunity(opp_id, stage_id=None, status=None, value=None, cfg=None):
    cfg = cfg or load_cfg()
    body = {}
    if stage_id:
        body['pipelineStageId'] = stage_id
    if status:
        body['status'] = status
    if value is not None:
        body['monetaryValue'] = round(float(value), 2)
    if body:
        request('PUT', '/opportunities/%s' % opp_id, body, cfg=cfg)
    return opp_id


# ---------------------------------------------------------------- reporting (read-only)
def events_for_day(user_ids, day, tz, cfg=None):
    """All appointments in the sub-account on `day` (YYYY-MM-DD) across the given users, de-duplicated."""
    cfg = cfg or load_cfg()
    start = datetime.fromisoformat(iso_at(day, '00:00', tz))
    end = start + timedelta(days=1)
    seen = {}
    for u in user_ids:
        r = request('GET', '/calendars/events' + q(dict(locationId=cfg['location_id'], userId=u, startTime=int(start.timestamp() * 1000), endTime=int(end.timestamp() * 1000))), version='2021-04-15', cfg=cfg)
        for e in r.get('events', []):
            seen[e.get('id')] = dict(id=e.get('id'), status=e.get('appointmentStatus', ''), title=e.get('title', ''), start=e.get('startTime', ''), contactId=e.get('contactId'))
    return list(seen.values())


def transactions_between(start_day, end_day, cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/payments/transactions' + q(dict(altId=cfg['location_id'], altType='location', limit=100, startAt=start_day, endAt=end_day)), cfg=cfg)
    return [dict(id=t.get('_id'), amount=t.get('amount'), status=t.get('status', ''), when=t.get('createdAt', ''), name=t.get('contactName') or (t.get('contactSnapshot') or {}).get('name') or '') for t in r.get('data', [])]


# ---------------------------------------------------------------- forms & surveys
FORM_WIDGET = 'https://api.leadconnectorhq.com/widget/form/'
SURVEY_WIDGET = 'https://api.leadconnectorhq.com/widget/survey/'


def list_forms(cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/forms/' + q(dict(locationId=cfg['location_id'], limit=50)), cfg=cfg)
    return [dict(id=f.get('id'), name=f.get('name', ''), url=FORM_WIDGET + str(f.get('id'))) for f in r.get('forms', [])]


def list_surveys(cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/surveys/' + q(dict(locationId=cfg['location_id'], limit=50)), cfg=cfg)
    return [dict(id=s.get('id'), name=s.get('name', ''), url=SURVEY_WIDGET + str(s.get('id'))) for s in r.get('surveys', [])]


def _norm_submissions(subs, kind):
    out = []
    for s in subs:
        others = s.get('others') or {}
        answers = {k: v for k, v in others.items() if not k.startswith('__') and k not in ('eventData', 'fieldsOriSequance', 'location', 'page_url', 'ip')}
        out.append(dict(id=s.get('id'), kind=kind, contactId=s.get('contactId'), formId=s.get('formId') or s.get('surveyId'), name=s.get('name', ''), when=s.get('createdAt', ''), answers=answers))
    return out


def form_submissions(form_id=None, limit=100, cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/forms/submissions' + q(dict(locationId=cfg['location_id'], formId=form_id, limit=limit)), cfg=cfg)
    return _norm_submissions(r.get('submissions', []), 'form')


def survey_submissions(survey_id=None, limit=100, cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/surveys/submissions' + q(dict(locationId=cfg['location_id'], surveyId=survey_id, limit=limit)), cfg=cfg)
    return _norm_submissions(r.get('submissions', []), 'survey')


# ---------------------------------------------------------------- email (Conversations)
def send_email(contact_id, subject, html, cfg=None):
    cfg = cfg or load_cfg()
    r = request('POST', '/conversations/messages', dict(type='Email', contactId=contact_id, subject=subject, html=html), version='2021-04-15', cfg=cfg)
    return r.get('messageId') or r.get('emailMessageId') or r.get('conversationId')


def upsert_contact_full(name, phone='', email='', cfg=None):
    cfg = cfg or load_cfg()
    first, last = split_name(name)
    body = dict(locationId=cfg['location_id'], firstName=first, lastName=last, tags=[TAG], source='Appointments app')
    ph = clean_phone(phone)
    if ph:
        body['phone'] = ph
    if email and '@' in email:
        body['email'] = email.strip()
    if 'phone' not in body and 'email' not in body:
        # upsert needs a phone or an email to match on; a name-only client is simply created once
        r = request('POST', '/contacts/', body, cfg=cfg)
        return (r.get('contact') or r).get('id')
    r = request('POST', '/contacts/upsert', body, cfg=cfg)
    return (r.get('contact') or r).get('id')


# ---------------------------------------------------------------- custom objects
OBJECTS = {
    'pass': dict(singular='Pass', plural='Passes', primary='label', fields=[('label', 'Pass', 'TEXT'), ('pass_type', 'Type', 'TEXT'), ('remaining', 'Credits left', 'NUMERICAL'), ('expires', 'Expires', 'TEXT'), ('client', 'Client', 'TEXT'), ('price_usd', 'Price (USD)', 'NUMERICAL')]),
    'attendance': dict(singular='Class visit', plural='Class visits', primary='label', fields=[('label', 'Visit', 'TEXT'), ('class_name', 'Class', 'TEXT'), ('visit_date', 'Date', 'TEXT'), ('client', 'Client', 'TEXT'), ('paid_with', 'Paid with', 'TEXT')]),
    'shift': dict(singular='Shift', plural='Shifts', primary='label', fields=[('label', 'Shift', 'TEXT'), ('staff', 'Staff', 'TEXT'), ('shift_date', 'Date', 'TEXT'), ('clock_in', 'Clock in', 'TEXT'), ('clock_out', 'Clock out', 'TEXT'), ('hours', 'Hours', 'NUMERICAL')]),
}


def list_objects(cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/objects/' + q(dict(locationId=cfg['location_id'])), cfg=cfg)
    return [dict(id=o.get('id'), key=o.get('key'), singular=(o.get('labels') or {}).get('singular', ''), plural=(o.get('labels') or {}).get('plural', ''), primary=o.get('primaryDisplayProperty')) for o in r.get('objects', [])]


def object_fields(schema_key, cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/custom-fields/object-key/' + schema_key + q(dict(locationId=cfg['location_id'])), cfg=cfg)
    return [dict(id=f.get('id'), key=f.get('fieldKey') or f.get('key'), name=f.get('name'), type=f.get('dataType')) for f in r.get('fields', r.get('customFields', []))]


def create_object(short_key, spec, cfg=None):
    """Create a custom object schema custom_objects.<short_key> with its primary display property."""
    cfg = cfg or load_cfg()
    key = 'custom_objects.' + short_key
    body = dict(labels=dict(singular=spec['singular'], plural=spec['plural']), key=key,
                description='Created by the Appointments app', locationId=cfg['location_id'],
                primaryDisplayPropertyDetails=dict(key=key + '.' + spec['primary'], name=dict(spec['fields'])[spec['primary']] if False else [n for k, n, t in spec['fields'] if k == spec['primary']][0], dataType='TEXT'))
    r = request('POST', '/objects/', body, cfg=cfg)
    return (r.get('object') or r).get('key') or key


def object_folder(schema_key, cfg=None):
    """Custom fields on an object live in a folder; find or create the app's folder."""
    cfg = cfg or load_cfg()
    r = request('GET', '/custom-fields/object-key/' + schema_key + q(dict(locationId=cfg['location_id'])), cfg=cfg)
    for f in r.get('folders', []):
        if f.get('name') == 'Appointments':
            return f.get('id')
    r = request('POST', '/custom-fields/folder', dict(locationId=cfg['location_id'], objectKey=schema_key, name='Appointments'), cfg=cfg)
    return (r.get('folder') or r).get('id')


def create_object_field(schema_key, field_key, name, dtype, folder_id=None, cfg=None):
    cfg = cfg or load_cfg()
    folder_id = folder_id or object_folder(schema_key, cfg)
    body = dict(locationId=cfg['location_id'], name=name, dataType=dtype, fieldKey=schema_key + '.' + field_key, objectKey=schema_key, parentId=folder_id, showInForms=False)
    r = request('POST', '/custom-fields/', body, cfg=cfg)
    return (r.get('field') or r).get('id')


def create_record(schema_key, properties, cfg=None):
    cfg = cfg or load_cfg()
    r = request('POST', '/objects/%s/records' % schema_key, dict(locationId=cfg['location_id'], properties=properties), cfg=cfg)
    return (r.get('record') or r).get('id')


def list_associations(cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/associations/' + q(dict(locationId=cfg['location_id'], limit=100, skip=0)), cfg=cfg)
    return [dict(id=a.get('id'), key=a.get('key'), first=a.get('firstObjectKey'), second=a.get('secondObjectKey')) for a in r.get('associations', [])]


def create_association(short_key, singular, cfg=None):
    cfg = cfg or load_cfg()
    body = dict(locationId=cfg['location_id'], key=short_key + '_contact', firstObjectLabel=singular, firstObjectKey='custom_objects.' + short_key, secondObjectLabel='Contact', secondObjectKey='contact')
    r = request('POST', '/associations/', body, cfg=cfg)
    return (r.get('association') or r).get('id')


def relate(association_id, record_id, contact_id, contact_first=True, cfg=None):
    """HighLevel stores the association with the contact as the first object; send the ids in that order."""
    cfg = cfg or load_cfg()
    first, second = (contact_id, record_id) if contact_first else (record_id, contact_id)
    r = request('POST', '/associations/relations', dict(locationId=cfg['location_id'], associationId=association_id, firstRecordId=first, secondRecordId=second), cfg=cfg)
    return (r.get('relation') or r).get('id')


# ---------------------------------------------------------------- read-back checks (used by Verify)
def get_contact(cid, cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/contacts/' + cid, cfg=cfg)
    return (r.get('contact') or r).get('id')


def get_event(eid, cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/calendars/events/appointments/' + eid, version='2021-04-15', cfg=cfg)
    e = r.get('appointment') or r.get('event') or r
    return dict(id=e.get('id'), status=e.get('appointmentStatus', ''), start=e.get('startTime', ''))


def get_opportunity(oid, cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/opportunities/' + oid, cfg=cfg)
    o = r.get('opportunity') or r
    return dict(id=o.get('id'), status=o.get('status', ''), stage=o.get('pipelineStageId', ''))


def get_product(pid, cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/products/' + pid + q(dict(locationId=cfg['location_id'])), cfg=cfg)
    return (r.get('product') or r).get('_id') or (r.get('product') or r).get('id')


def get_calendar(cid, cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/calendars/' + cid, version='2021-04-15', cfg=cfg)
    return (r.get('calendar') or r).get('id')


def get_record(schema_key, rid, cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/objects/%s/records/%s' % (schema_key, rid), cfg=cfg)
    return (r.get('record') or r).get('id')


def get_invoice(iid, cfg=None):
    cfg = cfg or load_cfg()
    r = request('GET', '/invoices/' + iid + q(dict(altId=cfg['location_id'], altType='location')), cfg=cfg)
    return dict(id=r.get('_id'), status=r.get('status', ''), paid=r.get('amountPaid'))
