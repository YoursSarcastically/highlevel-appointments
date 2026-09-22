"""End-to-end tests: boot the real server on a fresh SQLite file and drive it over HTTP.

Run:  python3 tests/test_e2e.py            (from the appointments-app folder)
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = int(os.environ.get('TEST_PORT', '8790'))
BASE = 'http://localhost:%d' % PORT
DBFILE = os.path.join(tempfile.mkdtemp(prefix='appt-test-'), 'test.db')
PROC = None


def start_server():
    global PROC
    env = dict(os.environ, APPT_DB=DBFILE, HL_CONFIG=os.path.join(os.path.dirname(DBFILE), 'hl.json'), HL_TOKEN='', HL_LOCATION_ID='')  # never touch a real HighLevel account from tests
    PROC = subprocess.Popen([sys.executable, os.path.join(ROOT, 'server.py'), str(PORT)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for _ in range(50):
        try:
            urllib.request.urlopen(BASE + '/api/locations', timeout=0.5).read()
            return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError('server did not start: ' + PROC.stderr.read().decode())


def stop_server():
    global PROC
    if PROC:
        PROC.terminate()
        PROC.wait(timeout=5)
        PROC = None


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, method=method, data=data,
                                 headers={'Content-Type': 'application/json'} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {'raw': raw.decode('utf-8', 'replace')}


def ok(method, path, body=None):
    st, r = call(method, path, body)
    assert st == 200, '%s %s -> %s %s' % (method, path, st, r)
    return r


def S(r):
    """Unwrap a mutation response to the state object."""
    return r['state'] if isinstance(r, dict) and 'state' in r else r


DOW = lambda d: date.fromisoformat(d).weekday()  # noqa: E731  Monday = 0


def workday(offset=1, avoid=()):
    """First date from tomorrow(+offset) that is not Sunday and not in `avoid` weekdays."""
    d = date.today() + timedelta(days=offset)
    while d.weekday() == 6 or d.weekday() in avoid:
        d += timedelta(days=1)
    return d.isoformat()


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        locs = ok('GET', '/api/locations')
        cls.locs = {l['type']: l for l in locs}
        for l in locs:  # every test class starts from a clean seed of each location
            ok('POST', '/api/locations/%s/demo/reset' % l['id'], {'onboarded': True})
        cls.salon = cls.locs['salon']['id']
        cls.gym = cls.locs['gym']['id']
        cls.studio = cls.locs['studio']['id']

    def L(self, loc, path=''):
        return '/api/locations/%s%s' % (loc, path)

    def state(self, loc):
        return ok('GET', self.L(loc, '/state'))

    def find(self, seq, **kw):
        for x in seq:
            if all(x.get(k) == v for k, v in kw.items()):
                return x
        self.fail('not found: %r' % kw)

    def slots(self, loc, service_id, staff_id, d):
        q = 'service_id=%s&date=%s' % (service_id, d) + ('&staff_id=%s' % staff_id if staff_id else '')
        return ok('GET', self.L(loc, '/availability?' + q))['slots']


class T01_SeedAndShape(Base):
    def test_three_locations_in_order(self):
        locs = ok('GET', '/api/locations')
        self.assertEqual([l['type'] for l in locs], ['salon', 'gym', 'studio'])
        self.assertEqual([l['slug'] for l in locs], ['blushblade', 'ironworksgym', 'emberyogapilates'])

    def test_salon_seed(self):
        st = self.state(self.salon)
        for k in ('id', 'slug', 'type', 'vocab', 'status', 'onboarded', 'rules', 'staff', 'services', 'classes', 'passes', 'clients', 'appts', 'sales', 'attend', 'pages', 'today', 'now', 'locations'):
            self.assertIn(k, st)
        self.assertEqual(len(st['staff']), 3)
        self.assertEqual(len(st['services']), 7)
        self.assertEqual(len(st['classes']), 0)
        self.assertEqual(len(st['passes']), 3)
        self.assertEqual(len(st['clients']), 8)
        self.assertEqual(len(st['pages']), 5)
        today = [a for a in st['appts'] if a['date'] == st['today']]
        self.assertEqual(len(today), 8)
        self.assertEqual(sorted(a['status'] for a in today), ['arrived'] + ['booked'] * 5 + ['done'] * 2)
        self.assertEqual(len([a for a in st['appts'] if a['date'] > st['today']]), 1)
        self.assertEqual(len(st['sales']), 2)
        self.assertEqual(st['rules'], dict(cancelHours=24, slot=30, open='09:00', close='19:00', deposit=0))
        self.assertEqual(st['vocab']['section'], 'SALON SERVICES')
        self.assertTrue(st['staff'][0]['clockIn'] and st['staff'][1]['clockIn'] and not st['staff'][2]['clockIn'])
        self.assertIsNone(st['staff'][1]['hours']['0'], 'second stylist is off Monday')
        self.assertIsNone(st['staff'][0]['hours']['6'], 'everyone off Sunday')
        self.assertEqual(st['staff'][0]['hours']['5'], ['09:00', '16:00'], 'Saturday closes at 4')
        blow = self.find(st['passes'], name='5 blowouts')
        self.assertEqual((blow['type'], blow['credits'], blow['svc']), ('pack', 5, 'Blowout'))
        self.assertEqual(st['clients'][0]['passes'][0]['remaining'], 3)
        self.assertEqual(st['clients'][1]['passes'][0]['remaining'], 1, 'intro pass carries its one credit')
        self.assertIsNone(st['clients'][3]['passes'][0]['remaining'], 'membership is unlimited')

    def test_gym_and_studio_seed(self):
        g = self.state(self.gym)
        self.assertEqual([c['name'] for c in g['classes']], ['Strength 60', 'Sweat circuit', 'Kettlebell flow'])
        self.assertEqual(len(g['services']), 3)
        self.assertEqual(g['vocab']['staff'], 'Trainers')
        k = g['classes'][0]['id'] + '_' + g['today']
        self.assertEqual(len(g['attend'][k]), 3)
        self.assertEqual(len(g['attend'][g['classes'][1]['id'] + '_' + g['today']]), 5)
        s = self.state(self.studio)
        self.assertEqual(len(s['classes']), 4)
        self.assertEqual(s['clients'][0]['name'], 'Jordan Lee')
        self.assertEqual(s['slug'], 'emberyogapilates')


class T02_Onboarding(Base):
    def test_fresh_reset_without_onboarding_then_complete(self):
        st = S(ok('POST', self.L(self.salon, '/demo/reset'), {'onboarded': False}))
        self.assertFalse(st['onboarded'])
        st = ok('PATCH', self.L(self.salon), {'onboarded': True})
        self.assertTrue(st['onboarded'])

    def test_status_switch(self):
        for s in ('busy', 'closed', 'open'):
            self.assertEqual(ok('PATCH', self.L(self.salon), {'status': s})['status'], s)
        self.assertEqual(call('PATCH', self.L(self.salon), {'status': 'lunch'})[0], 400)


class T03_FrontDeskPay(Base):
    def test_checkin_pay_with_tip_updates_everything(self):
        st = self.state(self.salon)
        a = self.find([x for x in st['appts'] if x['date'] == st['today']], status='booked')
        sv = self.find(st['services'], id=a['serviceId'])
        client_before = self.find(st['clients'], id=a['clientId'])
        sales_before = len(st['sales'])
        st = ok('POST', self.L(self.salon, '/appointments/%s/checkin' % a['id']))
        self.assertEqual(self.find(st['appts'], id=a['id'])['status'], 'arrived')
        tip = round(sv['price'] * 0.2, 2)
        r = ok('POST', self.L(self.salon, '/appointments/%s/pay' % a['id']), {'method': 'Tap to pay', 'tip': tip})
        self.assertAlmostEqual(r['charged'], sv['price'] + tip)
        st = r['state']
        done = self.find(st['appts'], id=a['id'])
        self.assertEqual((done['status'], done['paid'], done['tip'], done['total']), ('done', True, tip, sv['price']))
        self.assertEqual(len(st['sales']), sales_before + 1)
        sale = st['sales'][-1]
        self.assertEqual((sale['staffId'], sale['method'], sale['total'], sale['tip'], sale['label']), (a['staffId'], 'Tap to pay', sv['price'], tip, sv['name']))
        self.assertEqual(self.find(st['clients'], id=a['clientId'])['visits'], client_before['visits'] + 1)
        # end-of-day aggregation by staff and by method is derivable from sales
        by_staff = sum(x['total'] for x in st['sales'] if x['staffId'] == a['staffId'])
        self.assertGreaterEqual(by_staff, sv['price'])

    def test_pay_twice_and_pay_cancelled_are_rejected(self):
        st = self.state(self.salon)
        a = self.find([x for x in st['appts'] if x['date'] == st['today']], status='booked')
        ok('POST', self.L(self.salon, '/appointments/%s/pay' % a['id']), {'method': 'Cash', 'tip': 0})
        self.assertEqual(call('POST', self.L(self.salon, '/appointments/%s/pay' % a['id']), {'method': 'Cash'})[0], 409)
        b = self.find([x for x in self.state(self.salon)['appts'] if x['date'] == st['today']], status='booked')
        st2 = ok('POST', self.L(self.salon, '/appointments/%s/cancel' % b['id']))
        self.assertEqual(self.find(st2['appts'], id=b['id'])['status'], 'cancelled')
        self.assertEqual(call('POST', self.L(self.salon, '/appointments/%s/pay' % b['id']), {'method': 'Cash'})[0], 409)
        self.assertEqual(call('POST', self.L(self.salon, '/appointments/%s/checkin' % b['id']))[0], 409)

    def test_noshow_and_bad_method(self):
        st = self.state(self.salon)
        a = self.find([x for x in st['appts'] if x['date'] == st['today']], status='booked')
        st = ok('POST', self.L(self.salon, '/appointments/%s/noshow' % a['id']))
        self.assertEqual(self.find(st['appts'], id=a['id'])['status'], 'noshow')
        b = self.find([x for x in st['appts'] if x['date'] == st['today']], status='booked')
        self.assertEqual(call('POST', self.L(self.salon, '/appointments/%s/pay' % b['id']), {'method': 'Bitcoin'})[0], 400)


class T04_Passes(Base):
    def test_redeem_pack_decrements_and_restriction_enforced(self):
        st = self.state(self.salon)
        c1 = st['clients'][0]  # holds "5 blowouts" with 3 left, restricted to Blowout
        blow = self.find(st['services'], name='Blowout')
        men = self.find(st['services'], name="Men's cut")
        d = workday(1)
        sl = self.slots(self.salon, blow['id'], st['staff'][0]['id'], d)
        self.assertTrue(sl)
        r = ok('POST', self.L(self.salon, '/appointments'), dict(service_id=blow['id'], staff_id=st['staff'][0]['id'], client_id=c1['id'], date=d, time=sl[0], source='phone'))
        aid = r['id']
        r = ok('POST', self.L(self.salon, '/appointments/%s/pay' % aid), {'method': 'Pass', 'tip': 5})
        st = r['state']
        self.assertEqual(self.find(st['clients'], id=c1['id'])['passes'][0]['remaining'], 2)
        done = self.find(st['appts'], id=aid)
        self.assertEqual((done['status'], done['total'], done['tip']), ('done', 0, 5))
        self.assertEqual(st['sales'][-1]['method'], 'Pass')
        # the same client cannot use the blowout pass on a men's cut
        sl2 = self.slots(self.salon, men['id'], st['staff'][1]['id'], d)
        r = ok('POST', self.L(self.salon, '/appointments'), dict(service_id=men['id'], staff_id=st['staff'][1]['id'], client_id=c1['id'], date=d, time=sl2[0], source='phone'))
        stc, err = call('POST', self.L(self.salon, '/appointments/%s/pay' % r['id']), {'method': 'Pass'})
        self.assertEqual(stc, 409, err)

    def test_sell_then_redeem_membership_keeps_unlimited(self):
        st = self.state(self.salon)
        k = st['clients'][7]
        self.assertEqual(k['passes'], [])
        glow = self.find(st['passes'], name='Glow membership')
        sales_before = len(st['sales'])
        st = ok('POST', self.L(self.salon, '/passes/%s/sell' % glow['id']), {'client_id': k['id']})
        cp = self.find(st['clients'], id=k['id'])['passes']
        self.assertEqual(len(cp), 1)
        self.assertIsNone(cp[0]['remaining'])
        self.assertEqual(cp[0]['expires'], (date.today() + timedelta(days=30)).isoformat())
        self.assertEqual(len(st['sales']), sales_before + 1)
        self.assertEqual(st['sales'][-1]['label'], 'Glow membership')
        sv = st['services'][0]
        d = workday(2)
        sl = self.slots(self.salon, sv['id'], st['staff'][0]['id'], d)
        r = ok('POST', self.L(self.salon, '/appointments'), dict(service_id=sv['id'], staff_id=st['staff'][0]['id'], client_id=k['id'], date=d, time=sl[0]))
        st = ok('POST', self.L(self.salon, '/appointments/%s/pay' % r['id']), {'method': 'Pass'})['state']
        self.assertIsNone(self.find(st['clients'], id=k['id'])['passes'][0]['remaining'])

    def test_create_and_delete_pass(self):
        st = ok('POST', self.L(self.salon, '/passes'), dict(name='3 cuts', type='pack', credits=3, price=90, days=60))
        p = self.find(st['passes'], name='3 cuts')
        self.assertEqual(p['credits'], 3)
        self.assertEqual(call('POST', self.L(self.salon, '/passes'), dict(name='x', type='forever', price=1, days=1))[0], 400)
        st = ok('DELETE', self.L(self.salon, '/passes/%s' % p['id']))
        self.assertFalse([x for x in st['passes'] if x['id'] == p['id']])


class T05_WalkinAndClients(Base):
    def test_walkin_new_client_is_arrived_now(self):
        st = self.state(self.salon)
        n = len(st['clients'])
        r = ok('POST', self.L(self.salon, '/clients'), {'name': 'Walk In Wendy'})
        self.assertEqual(len(r['state']['clients']), n + 1)
        kid = r['id']
        sv = self.find(st['services'], name="Men's cut")
        r = ok('POST', self.L(self.salon, '/appointments'), dict(walkin=True, service_id=sv['id'], client_id=kid))
        a = self.find(r['state']['appts'], id=r['id'])
        self.assertEqual((a['status'], a['source'], a['date']), ('arrived', 'walkin', r['state']['today']))
        self.assertIn(a['staffId'], sv['staff'])
        st = ok('POST', self.L(self.salon, '/appointments/%s/pay' % a['id']), {'method': 'Cash', 'tip': 0})['state']
        self.assertEqual(self.find(st['clients'], id=kid)['visits'], 1)

    def test_client_requires_name(self):
        self.assertEqual(call('POST', self.L(self.salon, '/clients'), {'name': '  '})[0], 400)
        self.assertEqual(call('POST', self.L(self.salon, '/clients'), {})[0], 400)


class T06_Booking(Base):
    def test_slots_respect_hours_step_and_conflicts(self):
        st = self.state(self.salon)
        sv = self.find(st['services'], name="Women's cut & style")  # 60 min
        s0 = st['staff'][0]
        d = workday(2)  # the seed already holds 10:00 tomorrow
        sl = self.slots(self.salon, sv['id'], s0['id'], d)
        self.assertEqual(sl[0], '09:00')
        close = s0['hours'][str(DOW(d))][1]
        self.assertTrue(all(int(t[:2]) * 60 + int(t[3:]) + 60 <= int(close[:2]) * 60 + int(close[3:]) for t in sl))
        self.assertTrue(all(t[3:] in ('00', '30') for t in sl))
        r = ok('POST', self.L(self.salon, '/appointments'), dict(service_id=sv['id'], staff_id=s0['id'], client_id=st['clients'][2]['id'], date=d, time='10:00'))
        after = self.slots(self.salon, sv['id'], s0['id'], d)
        self.assertNotIn('10:00', after)
        self.assertNotIn('10:30', after, '60-min appointment blocks the half-hour inside it')
        self.assertNotIn('09:30', after, 'a 60-min service starting 09:30 would overlap 10:00')
        self.assertIn('11:00', after)
        # double booking the same slot is refused; cancelling frees it
        self.assertEqual(call('POST', self.L(self.salon, '/appointments'), dict(service_id=sv['id'], staff_id=s0['id'], client_id=st['clients'][3]['id'], date=d, time='10:00'))[0], 409)
        ok('POST', self.L(self.salon, '/appointments/%s/cancel' % r['id']))
        self.assertIn('10:00', self.slots(self.salon, sv['id'], s0['id'], d))

    def test_slot_interval_rule(self):
        st = self.state(self.salon)
        sv = st['services'][1]
        st = ok('PATCH', self.L(self.salon), {'slot': 15})
        self.assertEqual(st['rules']['slot'], 15)
        sl = self.slots(self.salon, sv['id'], st['staff'][0]['id'], workday(3))
        self.assertIn('09:15', sl)
        ok('PATCH', self.L(self.salon), {'slot': 30})

    def test_anyone_picks_free_staff_and_rejects_unqualified(self):
        st = self.state(self.salon)
        sv = st['services'][0]
        d = workday(1)
        r = ok('POST', self.L(self.salon, '/appointments'), dict(service_id=sv['id'], client_id=st['clients'][4]['id'], date=d, time='14:00'))
        self.assertIn(r['staff_id'], sv['staff'])
        # restrict the service to one stylist, then ask for another
        st = ok('PATCH', self.L(self.salon, '/services/%s' % sv['id']), {'staff': [st['staff'][0]['id']]})
        self.assertEqual(self.find(st['services'], id=sv['id'])['staff'], [st['staff'][0]['id']])
        stc, err = call('POST', self.L(self.salon, '/appointments'), dict(service_id=sv['id'], staff_id=st['staff'][1]['id'], client_id=st['clients'][4]['id'], date=d, time='15:00'))
        self.assertEqual(stc, 409, err)
        self.assertEqual(self.slots(self.salon, sv['id'], None, workday(4)), self.slots(self.salon, sv['id'], st['staff'][0]['id'], workday(4)))

    def test_booking_off_day_rejected(self):
        st = self.state(self.salon)
        s1 = st['staff'][1]  # off Monday
        d = date.today() + timedelta(days=1)
        while d.weekday() != 0:
            d += timedelta(days=1)
        self.assertEqual(self.slots(self.salon, st['services'][0]['id'], s1['id'], d.isoformat()), [])
        stc, _ = call('POST', self.L(self.salon, '/appointments'), dict(service_id=st['services'][0]['id'], staff_id=s1['id'], client_id=st['clients'][0]['id'], date=d.isoformat(), time='10:00'))
        self.assertEqual(stc, 409)


class T07_Team(Base):
    def test_toggle_day_off_and_on(self):
        st = self.state(self.salon)
        s0 = st['staff'][0]
        d = date.today() + timedelta(days=1)
        while d.weekday() != 1:
            d += timedelta(days=1)
        tue = d.isoformat()
        self.assertTrue(self.slots(self.salon, st['services'][0]['id'], s0['id'], tue))
        st = ok('PUT', self.L(self.salon, '/staff/%s/hours' % s0['id']), {'1': None})
        self.assertIsNone(self.find(st['staff'], id=s0['id'])['hours']['1'])
        self.assertEqual(self.slots(self.salon, st['services'][0]['id'], s0['id'], tue), [])
        st = ok('PUT', self.L(self.salon, '/staff/%s/hours' % s0['id']), {'1': True})
        self.assertEqual(self.find(st['staff'], id=s0['id'])['hours']['1'], ['09:00', '19:00'])
        self.assertTrue(self.slots(self.salon, st['services'][0]['id'], s0['id'], tue))

    def test_clock_out_writes_shift_and_clock_in_again(self):
        st = self.state(self.salon)
        s0 = st['staff'][0]
        self.assertTrue(s0['clockIn'])
        r = ok('POST', self.L(self.salon, '/staff/%s/clock' % s0['id']))
        self.assertEqual(r['action'], 'out')
        me = self.find(r['state']['staff'], id=s0['id'])
        self.assertIsNone(me['clockIn'])
        self.assertEqual(len(me['shifts']), 1)
        self.assertEqual(me['shifts'][0][0], s0['clockIn'])
        r = ok('POST', self.L(self.salon, '/staff/%s/clock' % s0['id']))
        self.assertEqual(r['action'], 'in')
        self.assertTrue(self.find(r['state']['staff'], id=s0['id'])['clockIn'])

    def test_add_edit_remove_staff(self):
        st = ok('POST', self.L(self.salon, '/staff'), dict(name='Kai Rivera', role='Junior', rate=20, pin='4321'))
        kai = self.find(st['staff'], name='Kai Rivera')
        self.assertEqual(kai['hours']['0'], ['09:00', '19:00'])
        self.assertIsNone(kai['hours']['6'])
        self.assertTrue(all(kai['id'] in v['staff'] for v in st['services']), 'new staff added to every service')
        st = ok('PATCH', self.L(self.salon, '/staff/%s' % kai['id']), dict(role='Stylist', rate=30))
        self.assertEqual(self.find(st['staff'], id=kai['id'])['role'], 'Stylist')
        st = ok('DELETE', self.L(self.salon, '/staff/%s' % kai['id']))
        self.assertFalse([s for s in st['staff'] if s['id'] == kai['id']])
        self.assertTrue(all(kai['id'] not in v['staff'] for v in st['services']), 'cascade removes from services')
        self.assertEqual(call('DELETE', self.L(self.salon, '/staff/nope'))[0], 404)


class T08_ServicesAndPages(Base):
    def test_service_crud_and_online_toggle(self):
        st = ok('POST', self.L(self.salon, '/services'), dict(name='Gloss treatment', cat='Color', dur=30, price=40))
        v = self.find(st['services'], name='Gloss treatment')
        self.assertTrue(v['online'])
        self.assertEqual(len(v['staff']), 3)
        st = ok('PATCH', self.L(self.salon, '/services/%s' % v['id']), dict(online=False, price=45))
        v2 = self.find(st['services'], id=v['id'])
        self.assertEqual((v2['online'], v2['price']), (False, 45))
        pub = ok('GET', '/api/public/blushblade')
        self.assertNotIn(v['id'], [x['id'] for x in pub['services']], 'offline service hidden from public page')
        st = ok('DELETE', self.L(self.salon, '/services/%s' % v['id']))
        self.assertFalse([x for x in st['services'] if x['id'] == v['id']])

    def test_pages_toggle_reaches_public(self):
        st = ok('PATCH', self.L(self.salon, '/pages/book'), {'on': False})
        self.assertFalse(self.find(st['pages'], id='book')['on'])
        self.assertFalse(ok('GET', '/api/public/blushblade')['pages']['book'])
        ok('PATCH', self.L(self.salon, '/pages/book'), {'on': True})
        self.assertTrue(ok('GET', '/api/public/blushblade')['pages']['book'])


class T09_Public(Base):
    def test_public_info_and_book_creates_client_and_lands_online(self):
        pub = ok('GET', '/api/public/blushblade')
        self.assertEqual(pub['name'], 'Blush & Blade')
        self.assertEqual(len(pub['services']), 7)
        self.assertEqual(len(pub['staff']), 3)
        sv = pub['services'][0]
        d = workday(1)
        sl = ok('GET', '/api/public/blushblade/availability?service_id=%s&date=%s' % (sv['id'], d))['slots']
        self.assertTrue(sl)
        before = len(self.state(self.salon)['clients'])
        r = ok('POST', '/api/public/blushblade/book', dict(service_id=sv['id'], date=d, time=sl[-1], name='Nora Web', phone='(555) 010-2000'))
        self.assertTrue(r['ok'])
        st = self.state(self.salon)
        self.assertEqual(len(st['clients']), before + 1)
        a = self.find(st['appts'], id=r['id'])
        self.assertEqual((a['source'], a['status'], a['date'], a['time']), ('online', 'booked', d, sl[-1]))
        # second booking with the same phone reuses the contact
        sl2 = ok('GET', '/api/public/blushblade/availability?service_id=%s&date=%s' % (sv['id'], d))['slots']
        ok('POST', '/api/public/blushblade/book', dict(service_id=sv['id'], date=d, time=sl2[0], name='N. Web', phone='(555) 010-2000'))
        self.assertEqual(len(self.state(self.salon)['clients']), before + 1)

    def test_public_validation(self):
        pub = ok('GET', '/api/public/blushblade')
        sv = pub['services'][0]
        d = workday(1)
        self.assertEqual(call('POST', '/api/public/blushblade/book', dict(service_id=sv['id'], date=d, time='10:00', name=''))[0], 400)
        self.assertEqual(call('POST', '/api/public/blushblade/book', dict(service_id=sv['id'], date=d, time='03:00', name='Nobody'))[0], 409)
        self.assertEqual(call('GET', '/api/public/doesnotexist')[0], 404)

    def test_busy_and_closed_block_today_only(self):
        pub = ok('GET', '/api/public/blushblade')
        sv = pub['services'][0]
        td = date.today().isoformat()
        d = workday(1)
        ok('PATCH', self.L(self.salon), {'status': 'busy'})
        r = ok('GET', '/api/public/blushblade/availability?service_id=%s&date=%s' % (sv['id'], td))
        self.assertEqual((r['slots'], r['reason']), ([], 'No slots today'))
        self.assertTrue(ok('GET', '/api/public/blushblade/availability?service_id=%s&date=%s' % (sv['id'], d))['slots'], 'tomorrow still bookable')
        self.assertEqual(call('POST', '/api/public/blushblade/book', dict(service_id=sv['id'], date=td, time='18:00', name='Late Larry'))[0], 409)
        ok('PATCH', self.L(self.salon), {'status': 'open'})


class T10_Classes(Base):
    def test_add_class_blocks_instructor_and_roster_fills(self):
        g = self.state(self.gym)
        dana = g['staff'][0]
        d = workday(1, avoid=())
        wd = DOW(d)
        st = ok('POST', self.L(self.gym, '/classes'), dict(name='Mobility', time='10:00', dur=60, cap=2, price=15, staffId=dana['id'], days=[wd]))
        cls = self.find(st['classes'], name='Mobility')
        self.assertEqual(cls['days'], [wd])
        pt = self.find(st['services'], name='PT session · 60')
        sl = self.slots(self.gym, pt['id'], dana['id'], d)
        self.assertNotIn('10:00', sl)
        self.assertNotIn('10:30', sl)
        self.assertNotIn('09:30', sl)
        self.assertIn('11:00', sl)
        # roster: two seats, third refused, duplicate refused
        k = st['clients']
        r = ok('POST', self.L(self.gym, '/classes/%s/attendance' % cls['id']), dict(client_id=k[7]['id'], date=d))
        self.assertFalse(r['pass_used'])
        self.assertEqual(r['state']['sales'][-1]['method'], 'Tap to pay')
        self.assertEqual(r['state']['sales'][-1]['total'], 15)
        self.assertEqual(call('POST', self.L(self.gym, '/classes/%s/attendance' % cls['id']), dict(client_id=k[7]['id'], date=d))[0], 409)
        r = ok('POST', self.L(self.gym, '/classes/%s/attendance' % cls['id']), dict(client_id=k[4]['id'], date=d))  # holds 10-class pack, 10 left
        self.assertTrue(r['pass_used'])
        self.assertEqual(self.find(r['state']['clients'], id=k[4]['id'])['passes'][0]['remaining'], 9)
        self.assertEqual(r['state']['sales'][-1]['method'], 'Pass')
        stc, err = call('POST', self.L(self.gym, '/classes/%s/attendance' % cls['id']), dict(client_id=k[5]['id'], date=d))
        self.assertEqual((stc, err['error']), (409, 'Class is full'))
        self.assertEqual(len(r['state']['attend'][cls['id'] + '_' + d]), 2)
        st = ok('DELETE', self.L(self.gym, '/classes/%s/attendance/%s?date=%s' % (cls['id'], k[7]['id'], d)))
        self.assertEqual(st['attend'][cls['id'] + '_' + d], [k[4]['id']])
        st = ok('PATCH', self.L(self.gym, '/classes/%s' % cls['id']), dict(cap=20, online=False))
        c2 = self.find(st['classes'], id=cls['id'])
        self.assertEqual((c2['cap'], c2['online']), (20, False))
        st = ok('DELETE', self.L(self.gym, '/classes/%s' % cls['id']))
        self.assertFalse([c for c in st['classes'] if c['id'] == cls['id']])

    def test_membership_attendance_keeps_unlimited(self):
        g = self.state(self.gym)
        k4 = g['clients'][6]  # unlimited monthly
        self.assertIsNone(k4['passes'][0]['remaining'])
        cls = g['classes'][2]
        d = workday(2)
        r = ok('POST', self.L(self.gym, '/classes/%s/attendance' % cls['id']), dict(client_id=k4['id'], date=d))
        self.assertTrue(r['pass_used'])
        self.assertIsNone(self.find(r['state']['clients'], id=k4['id'])['passes'][0]['remaining'])


class T11_DemoAndIsolation(Base):
    def test_rush_then_reset(self):
        g = self.state(self.gym)
        before = len(g['clients'])
        r = ok('POST', self.L(self.gym, '/demo/rush'))
        self.assertGreaterEqual(r['booked'], 0)
        self.assertEqual(len(r['state']['clients']), before + r['booked'])
        st = S(ok('POST', self.L(self.gym, '/demo/reset'), {'onboarded': True}))
        self.assertEqual(len(st['clients']), 8)
        self.assertEqual(len([a for a in st['appts'] if a['date'] == st['today']]), 8)
        self.assertTrue(st['onboarded'])
        self.assertEqual(st['id'], self.gym, 'reset keeps the location id')

    def test_locations_are_isolated(self):
        salon_before = self.state(self.salon)
        ok('POST', self.L(self.studio, '/clients'), {'name': 'Studio Only'})
        salon_after = self.state(self.salon)
        self.assertEqual(len(salon_after['clients']), len(salon_before['clients']))
        self.assertIn('Studio Only', [c['name'] for c in self.state(self.studio)['clients']])
        self.assertEqual(call('GET', '/api/locations/zzz/state')[0], 404)


class T11b_Features(Base):
    def test_defaults_per_type(self):
        self.assertEqual(self.state(self.salon)['features'], dict(booking=True, passes=True, classes=False, tips=True, reminders=True, google=False))
        self.assertTrue(self.state(self.gym)['features']['classes'])

    def test_toggle_tools_and_booking_switch_drives_public_page(self):
        st = ok('PATCH', self.L(self.salon), {'features': {'passes': False, 'tips': False, 'bogus': True}})
        f = st['features']
        self.assertEqual((f['passes'], f['tips'], f['booking']), (False, False, True))
        self.assertNotIn('bogus', f)
        st = ok('PATCH', self.L(self.salon), {'features': {'booking': False}})
        self.assertFalse(st['features']['booking'])
        self.assertFalse(self.find(st['pages'], id='book')['on'])
        pub = ok('GET', '/api/public/blushblade')
        self.assertFalse(pub['pages']['book'])
        self.assertFalse(pub['features']['booking'])
        st = ok('PATCH', self.L(self.salon), {'features': {'booking': True, 'passes': True, 'tips': True}})
        self.assertTrue(self.find(st['pages'], id='book')['on'])
        self.assertTrue(self.state(self.salon)['features']['passes'], 'persisted')

    def test_onboarding_adds_services_and_team(self):
        st = S(ok('POST', self.L(self.studio, '/demo/reset'), {'onboarded': False}))
        self.assertFalse(st['onboarded'])
        n_svc, n_staff = len(st['services']), len(st['staff'])
        st = ok('POST', self.L(self.studio, '/services'), dict(name='Sound bath', cat='Privates', dur=45, price=30))
        self.assertEqual(len(st['services']), n_svc + 1)
        st = ok('POST', self.L(self.studio, '/staff'), dict(name='Lina Park', role='Pilates', rate=40, pin='2468'))
        self.assertEqual(len(st['staff']), n_staff + 1)
        lina = self.find(st['staff'], name='Lina Park')
        self.assertIn(lina['id'], self.find(st['services'], name='Sound bath')['staff'], 'new team member can do the new service')
        st = ok('POST', self.L(self.studio, '/classes'), dict(name='Mat Pilates', time='08:00', dur=50, cap=14, price=20, staffId=lina['id'], days=[0, 1, 2, 3, 4, 5]))
        self.assertIn('Mat Pilates', [c['name'] for c in st['classes']])
        st = ok('PATCH', self.L(self.studio), {'onboarded': True})
        self.assertTrue(st['onboarded'])


class T11c_HighLevelUnconfigured(Base):
    def test_status_and_settings_without_credentials(self):
        st = ok('GET', self.L(self.salon, '/hl/status'))
        self.assertFalse(st['configured'])
        sm = st['summary']
        self.assertEqual((sm['configured'], sm['linked'], sm['contacts'], sm['appts'], sm['staff'], sm['services'], sm['products'], sm['opportunities'], sm['records']), (False, False, 0, 0, 0, 0, 0, 0, 0))
        self.assertEqual(sm['sync'], dict(contacts=True, appointments=True, invoices=False, sms=False, workflows=True, opportunities=True, email=False, objects=False))
        self.assertEqual(st['workflows'], dict(booked='', noshow='', showed='', pass_sold='', pass_expiring=''))
        self.assertEqual(st['pipeline'], dict(id='', booked='', paid='', noshow=''))
        st = ok('PATCH', self.L(self.salon, '/hl/settings'), {'sync': {'sms': True}, 'link': True})
        self.assertTrue(st['sync']['sms'])
        self.assertEqual(st['app_location_id'], self.salon)
        self.assertFalse(st['configured'], 'a link without a token does not connect')
        for path in ('/hl/team', '/hl/push-clients', '/hl/import-contacts', '/hl/test', '/hl/services/push', '/hl/services/import', '/hl/products/push', '/hl/email/lapsed', '/hl/objects/setup', '/hl/objects/backfill', '/hl/seed'):
            self.assertEqual(call('POST', self.L(self.salon, path), {})[0], 409, path)
        for path in ('/hl/transactions', '/hl/funnels', '/hl/options', '/hl/reconcile', '/hl/clients/%s/submissions' % self.state(self.salon)['clients'][0]['id']):
            self.assertEqual(call('GET', self.L(self.salon, path))[0], 409, path)
        # mappings persist even before a token exists
        st = ok('PATCH', self.L(self.salon, '/hl/settings'), {'workflows': {'booked': 'wf1', 'bogus': 'x'}, 'pipeline': {'id': 'p1', 'booked': 's1'}, 'intake_form_id': 'f1'})
        self.assertEqual((st['workflows']['booked'], st['pipeline']['id'], st['pipeline']['booked'], st['intake_form_id']), ('wf1', 'p1', 's1', 'f1'))
        self.assertNotIn('bogus', st['workflows'])
        ok('PATCH', self.L(self.salon, '/hl/settings'), {'workflows': {'booked': ''}, 'pipeline': {'id': '', 'booked': ''}, 'intake_form_id': ''})
        ok('PATCH', self.L(self.salon, '/hl/settings'), {'sync': {'sms': False}, 'link': False})

    def test_front_desk_flows_unaffected_when_disconnected(self):
        st = self.state(self.salon)
        self.assertIn('hl', st)
        r = ok('POST', self.L(self.salon, '/clients'), {'name': 'Offline Olga', 'email': 'olga@example.com'})
        olga = self.find(r['state']['clients'], id=r['id'])
        self.assertIsNone(olga['hlContactId'])
        self.assertEqual(olga['email'], 'olga@example.com')
        sv = st['services'][1]
        d = workday(3)
        sl = self.slots(self.salon, sv['id'], st['staff'][2]['id'], d)
        r = ok('POST', self.L(self.salon, '/appointments'), dict(service_id=sv['id'], staff_id=st['staff'][2]['id'], client_id=r['id'], date=d, time=sl[0]))
        a = self.find(r['state']['appts'], id=r['id'])
        self.assertIsNone(a['hlEventId'])
        self.assertEqual(ok('GET', self.L(self.salon, '/hl/status'))['log'], [], 'no sync attempts are logged while unlinked')


class T11d_SeedMore(Base):
    def test_seed_more_adds_a_week_of_data_locally(self):
        st = self.state(self.gym)
        n_clients, n_appts, n_sales = len(st['clients']), len(st['appts']), len(st['sales'])
        r = ok('POST', self.L(self.gym, '/demo/seed-more'), {'seed': 3})
        d = r['done']
        self.assertEqual(d['clients'], 12)
        self.assertGreater(d['appointments'], 10)
        self.assertGreaterEqual(d['passes'], 1)
        self.assertGreaterEqual(d['shifts'], 1)
        st2 = r['state']
        self.assertEqual(len(st2['clients']), n_clients + 12)
        self.assertGreater(len(st2['appts']), n_appts + 10)
        self.assertGreater(len(st2['sales']), n_sales)
        self.assertTrue(all(c['email'] for c in st2['clients'] if c['name'] in ('Aarav Mehta', 'Zara Khan')))
        # no double-booking was introduced
        seen = set()
        for a in st2['appts']:
            if a['status'] in ('booked', 'arrived', 'done'):
                key = (a['staffId'], a['date'], a['time'])
                self.assertNotIn(key, seen)
                seen.add(key)
        r2 = ok('POST', self.L(self.gym, '/demo/seed-more'), {'seed': 3})
        self.assertEqual(r2['done']['clients'], 0, 'same people are not created twice')
        self.assertEqual(call('GET', self.L(self.gym, '/hl/verify'))[0], 409)


class T12_ProtocolAndPersistence(Base):
    def test_errors_and_static(self):
        self.assertEqual(call('GET', '/api/nothing')[0], 404)
        self.assertEqual(call('DELETE', '/api/locations')[0], 405)
        req = urllib.request.Request(BASE + '/api/locations/%s/clients' % self.salon, method='POST', data=b'{bad json', headers={'Content-Type': 'application/json'})
        try:
            urllib.request.urlopen(req)
            self.fail('expected 400')
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
        for path in ('/', '/book/blushblade', '/manage/blushblade/x', '/kiosk/blushblade'):
            with urllib.request.urlopen(BASE + path) as r:  # the plain pages or the built React app, whichever is present
                self.assertEqual(r.status, 200, path)
                self.assertIn(b'<title>', r.read())
        # path traversal is neutralised: either a 404 or (with a built app) the SPA shell, never the source
        req = urllib.request.Request(BASE + '/static/../server.py')
        try:
            with urllib.request.urlopen(req) as r:
                body = r.read()
                self.assertNotIn(b'import sqlite3', body)
                self.assertIn(b'<!doctype', body.lower())
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    def test_zz_data_survives_restart(self):
        st = ok('POST', self.L(self.salon, '/clients'), {'name': 'Persist Pat'})['state']
        n = len(st['clients'])
        stop_server()
        start_server()
        st2 = self.state(self.salon)
        self.assertEqual(len(st2['clients']), n)
        self.assertIn('Persist Pat', [c['name'] for c in st2['clients']])
        self.assertEqual([l['type'] for l in ok('GET', '/api/locations')], ['salon', 'gym', 'studio'], 'no duplicate seed on restart')


if __name__ == '__main__':
    start_server()
    try:
        suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    finally:
        stop_server()
    sys.exit(0 if result.wasSuccessful() else 1)
