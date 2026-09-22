"""Depth features: deposits, fees, self-service, waitlist, memberships, blocks, gift cards, refunds, insights, kiosk, search, roles.
Appended to test_e2e.py at build time (see the marker at the end of that file)."""


class T13_CardsDepositsFees(Base):
    def test_card_on_file_deposit_and_noshow_fee(self):
        st = self.state(self.salon)
        k = st['clients'][2]
        bal = self.find(st['services'], name='Balayage')  # $220, 25 deposit in the seed
        self.assertEqual(bal['deposit'], 25)
        self.assertEqual(call('POST', self.L(self.salon, '/clients/%s/card' % k['id']), {'brand': 'Visa', 'last4': '12'})[0], 400)
        st = ok('POST', self.L(self.salon, '/clients/%s/card' % k['id']), {'brand': 'Visa', 'last4': '4242'})
        self.assertEqual(self.find(st['clients'], id=k['id'])['card'], {'brand': 'Visa', 'last4': '4242'})
        d = workday(2)
        sl = self.slots(self.salon, bal['id'], st['staff'][0]['id'], d)
        n_sales = len(st['sales'])
        r = ok('POST', self.L(self.salon, '/appointments'), dict(service_id=bal['id'], staff_id=st['staff'][0]['id'], client_id=k['id'], date=d, time=sl[0]))
        a = self.find(r['state']['appts'], id=r['id'])
        self.assertEqual(a['deposit'], 25, 'deposit charged to the card at booking')
        self.assertEqual(len(r['state']['sales']), n_sales + 1)
        self.assertEqual(r['state']['sales'][-1]['label'], 'Deposit · Balayage')
        self.assertEqual(r['state']['sales'][-1]['method'], 'Card on file')
        # paying later only charges the remainder
        ok('POST', self.L(self.salon, '/appointments/%s/checkin' % a['id']))
        pay = ok('POST', self.L(self.salon, '/appointments/%s/pay' % a['id']), {'method': 'Card on file', 'tip': 0})
        self.assertEqual(pay['charged'], 195.0)
        # a no-show on a second booking charges the fee (50) to the card
        sl2 = self.slots(self.salon, st['services'][1]['id'], st['staff'][1]['id'], d)
        r2 = ok('POST', self.L(self.salon, '/appointments'), dict(service_id=st['services'][1]['id'], staff_id=st['staff'][1]['id'], client_id=k['id'], date=d, time=sl2[0]))
        st2 = ok('POST', self.L(self.salon, '/appointments/%s/noshow' % r2['id']), {})
        self.assertEqual(st2['fee'], 50)
        self.assertEqual(self.find(st2['appts'], id=r2['id'])['fee'], 50)
        self.assertEqual(st2['sales'][-1]['label'], 'No-show fee · ' + st['services'][1]['name'])
        # remove the card: no more charges are possible
        st3 = ok('DELETE', self.L(self.salon, '/clients/%s/card' % k['id']))
        self.assertIsNone(self.find(st3['clients'], id=k['id'])['card'])

    def test_no_card_means_no_fee(self):
        st = self.state(self.salon)
        k = st['clients'][5]
        self.assertIsNone(k['card'])
        d = workday(2)
        sl = self.slots(self.salon, st['services'][1]['id'], st['staff'][2]['id'], d)
        r = ok('POST', self.L(self.salon, '/appointments'), dict(service_id=st['services'][1]['id'], staff_id=st['staff'][2]['id'], client_id=k['id'], date=d, time=sl[0]))
        st2 = ok('POST', self.L(self.salon, '/appointments/%s/noshow' % r['id']), {})
        self.assertEqual(st2['fee'], 0)


class T14_SelfServiceAndSeries(Base):
    def test_manage_link_move_and_cancel(self):
        st = self.state(self.salon)
        sv = st['services'][1]
        d = workday(3)
        sl = self.slots(self.salon, sv['id'], st['staff'][0]['id'], d)
        r = ok('POST', self.L(self.salon, '/appointments'), dict(service_id=sv['id'], staff_id=st['staff'][0]['id'], client_id=st['clients'][0]['id'], date=d, time=sl[0]))
        a = self.find(r['state']['appts'], id=r['id'])
        self.assertTrue(a['token'])
        m = ok('GET', '/api/public/blushblade/manage/%s' % a['token'])
        self.assertEqual((m['service'], m['date'], m['time'], m['status'], m['late']), (sv['name'], d, sl[0], 'booked', False))
        mv = ok('POST', '/api/public/blushblade/manage/%s/move' % a['token'], {'date': d, 'time': sl[2]})
        self.assertEqual(mv['time'], sl[2])
        self.assertEqual(self.find(self.state(self.salon)['appts'], id=a['id'])['time'], sl[2])
        self.assertEqual(call('POST', '/api/public/blushblade/manage/%s/move' % a['token'], {'date': d, 'time': '03:00'})[0], 409)
        c = ok('POST', '/api/public/blushblade/manage/%s/cancel' % a['token'])
        self.assertEqual((c['late'], c['fee']), (False, 0))
        self.assertEqual(self.find(self.state(self.salon)['appts'], id=a['id'])['status'], 'cancelled')
        self.assertEqual(call('POST', '/api/public/blushblade/manage/%s/cancel' % a['token'])[0], 409, 'cannot cancel twice')
        self.assertEqual(call('GET', '/api/public/blushblade/manage/nope')[0], 404)

    def test_recurring_booking_and_series_cancel(self):
        st = self.state(self.salon)
        sv = st['services'][2]
        d = workday(2, avoid=(0,))
        sl = self.slots(self.salon, sv['id'], st['staff'][1]['id'], d)
        r = ok('POST', self.L(self.salon, '/appointments'), dict(service_id=sv['id'], staff_id=st['staff'][1]['id'], client_id=st['clients'][3]['id'], date=d, time=sl[0], repeat={'every_weeks': 1, 'count': 3}))
        self.assertEqual(len(r['series']['made']) + len(r['series']['skipped']), 3)
        series = [a for a in r['state']['appts'] if a['seriesId'] == r['series']['id']]
        self.assertEqual(len(series), 1 + len(r['series']['made']))
        st2 = ok('POST', self.L(self.salon, '/appointments/%s/cancel' % r['id']), {'series': True})
        self.assertTrue(all(a['status'] == 'cancelled' for a in st2['appts'] if a['seriesId'] == r['series']['id']))

    def test_move_endpoint_rechecks_availability(self):
        st = self.state(self.salon)
        booked = [a for a in st['appts'] if a['status'] == 'booked' and a['date'] > st['today']]
        a = booked[0]
        d = workday(4)
        sl = self.slots(self.salon, a['serviceId'], a['staffId'], d)
        st2 = ok('PATCH', self.L(self.salon, '/appointments/%s' % a['id']), {'date': d, 'time': sl[0]})
        self.assertEqual(self.find(st2['appts'], id=a['id'])['date'], d)
        self.assertEqual(call('PATCH', self.L(self.salon, '/appointments/%s' % a['id']), {'date': d, 'time': sl[0], 'staff_id': 'nope'})[0], 409)


class T15_BlocksWaitlistMemberships(Base):
    def test_blocked_time_and_day_off_remove_slots_and_waitlist_offers(self):
        st = self.state(self.salon)
        sv = st['services'][1]
        s0 = st['staff'][0]
        d = workday(2)
        before = self.slots(self.salon, sv['id'], s0['id'], d)
        self.assertIn('12:00', before)
        r = ok('POST', self.L(self.salon, '/blocks'), dict(staff_id=s0['id'], date=d, start='12:00', end='13:00', reason='Lunch'))
        after = self.slots(self.salon, sv['id'], s0['id'], d)
        self.assertNotIn('12:00', after)
        self.assertNotIn('12:30', after)
        self.assertIn('13:00', after)
        st = ok('DELETE', self.L(self.salon, '/blocks/%s' % r['ids'][0]))
        self.assertIn('12:00', self.slots(self.salon, sv['id'], s0['id'], d))
        off = ok('POST', self.L(self.salon, '/blocks'), dict(staff_id=s0['id'], date=d, kind='timeoff', reason='Holiday'))
        self.assertEqual(self.slots(self.salon, sv['id'], s0['id'], d), [])
        ok('DELETE', self.L(self.salon, '/blocks/%s' % off['ids'][0]))
        # waitlist: a cancellation offers the spot to the first person waiting for that day and stylist
        sl = self.slots(self.salon, sv['id'], s0['id'], d)
        booked = ok('POST', self.L(self.salon, '/appointments'), dict(service_id=sv['id'], staff_id=s0['id'], client_id=st['clients'][0]['id'], date=d, time=sl[0]))
        w = ok('POST', self.L(self.salon, '/waitlist'), dict(client_id=st['clients'][1]['id'], service_id=sv['id'], staff_id=s0['id'], date=d))
        self.assertEqual(self.find(w['state']['waitlist'], id=w['id'])['status'], 'waiting')
        st2 = ok('POST', self.L(self.salon, '/appointments/%s/cancel' % booked['id']), {})
        self.assertEqual(self.find(st2['waitlist'], id=w['id'])['status'], 'offered')
        st3 = ok('POST', self.L(self.salon, '/waitlist/%s/book' % w['id']), {'time': sl[0]})
        self.assertFalse([x for x in st3['waitlist'] if x['id'] == w['id'] and x['status'] != 'booked'])
        self.assertTrue(any(a['clientId'] == st['clients'][1]['id'] and a['date'] == d and a['time'] == sl[0] for a in st3['appts']))

    def test_class_waitlist_offer_on_seat_freed(self):
        g = self.state(self.gym)
        cls = g['classes'][1]  # Sweat circuit, 16 seats
        d = workday(1)
        st = ok('PATCH', self.L(self.gym, '/classes/%s' % cls['id']), dict(cap=1))
        ok('POST', self.L(self.gym, '/classes/%s/attendance' % cls['id']), dict(client_id=g['clients'][0]['id'], date=d))
        self.assertEqual(call('POST', self.L(self.gym, '/classes/%s/attendance' % cls['id']), dict(client_id=g['clients'][1]['id'], date=d))[0], 409)
        w = ok('POST', self.L(self.gym, '/waitlist'), dict(client_id=g['clients'][1]['id'], class_id=cls['id'], date=d))
        st = ok('DELETE', self.L(self.gym, '/classes/%s/attendance/%s?date=%s' % (cls['id'], g['clients'][0]['id'], d)))
        self.assertEqual(self.find(st['waitlist'], id=w['id'])['status'], 'offered')
        ok('PATCH', self.L(self.gym, '/classes/%s' % cls['id']), dict(cap=16))

    def test_membership_billing_freeze_cancel(self):
        g = self.state(self.gym)
        k = g['clients'][2]
        unl = self.find(g['passes'], name='Unlimited monthly')
        ok('POST', self.L(self.gym, '/clients/%s/card' % k['id']), {'brand': 'Visa', 'last4': '1111'})
        st = ok('POST', self.L(self.gym, '/passes/%s/sell' % unl['id']), {'client_id': k['id']})
        cp = self.find(st['clients'], id=k['id'])['passes'][-1]
        self.assertTrue(cp['autoRenew'])
        self.assertEqual(cp['nextBilling'], cp['expires'])
        self.assertEqual(cp['status'], 'active')
        n_sales = len(st['sales'])
        r = ok('POST', self.L(self.gym, '/billing/run'), {})
        self.assertEqual(r['billed'], 0, 'not due yet')
        st = ok('POST', self.L(self.gym, '/client-passes/%s/freeze' % cp['id']), {})
        self.assertEqual(self.find(st['clients'], id=k['id'])['passes'][-1]['status'], 'frozen')
        st = ok('POST', self.L(self.gym, '/client-passes/%s/unfreeze' % cp['id']), {})
        self.assertEqual(self.find(st['clients'], id=k['id'])['passes'][-1]['status'], 'active')
        st = ok('POST', self.L(self.gym, '/client-passes/%s/cancel' % cp['id']), {})
        x = self.find(st['clients'], id=k['id'])['passes'][-1]
        self.assertEqual((x['status'], x['autoRenew']), ('cancelled', False))
        self.assertEqual(len(st['sales']), n_sales)


class T16_MoneyAndTools(Base):
    def test_gift_card_sell_and_redeem_and_refund(self):
        st = self.state(self.salon)
        self.assertEqual(call('POST', self.L(self.salon, '/giftcards'), {'amount': 0})[0], 400)
        g = ok('POST', self.L(self.salon, '/giftcards'), {'amount': 100, 'client_id': st['clients'][0]['id']})
        self.assertTrue(g['code'].startswith('GC-'))
        gc = self.find(g['state']['giftcards'], code=g['code'])
        self.assertEqual((gc['balance'], gc['initial']), (100, 100))
        a = self.find([x for x in st['appts'] if x['date'] == st['today']], status='booked')
        ok('POST', self.L(self.salon, '/appointments/%s/checkin' % a['id']))
        self.assertEqual(call('POST', self.L(self.salon, '/appointments/%s/pay' % a['id']), {'method': 'Gift card', 'code': 'GC-NOPE'})[0], 404)
        pay = ok('POST', self.L(self.salon, '/appointments/%s/pay' % a['id']), {'method': 'Gift card', 'code': g['code'], 'tip': 5})
        gc2 = self.find(pay['state']['giftcards'], code=g['code'])
        self.assertAlmostEqual(gc2['balance'], 100 - pay['charged'])
        sale = pay['state']['sales'][-1]
        self.assertEqual(sale['method'], 'Gift card')
        ref = ok('POST', self.L(self.salon, '/sales/%s/refund' % sale['id']), {'reason': 'test'})
        self.assertEqual(ref['sales'][-1]['refundOf'], sale['id'])
        self.assertEqual(ref['sales'][-1]['total'], -(sale['total'] + sale['tip']))
        self.assertEqual(call('POST', self.L(self.salon, '/sales/%s/refund' % sale['id']), {})[0], 409)

    def test_insights_search_settings_audit_pin(self):
        st = self.state(self.salon)
        ins = ok('GET', self.L(self.salon, '/insights?days=7'))
        self.assertEqual(len(ins['revenue_by_day']), 7)
        for k in ('revenue', 'visits', 'noshow_rate', 'by_source', 'top_services', 'lapsed', 'active_memberships', 'mrr', 'by_method'):
            self.assertIn(k, ins)
        s = ok('GET', self.L(self.salon, '/search?q=priya'))
        self.assertTrue(any(r['kind'] == 'client' and 'Priya' in r['title'] for r in s['results']))
        self.assertEqual(ok('GET', self.L(self.salon, '/search?q=p'))['results'], [])
        st = ok('PATCH', self.L(self.salon, '/settings'), {'name': 'Blush & Blade Studio', 'policy': {'noshow_fee': 40, 'require_card_online': True}})
        self.assertEqual((st['name'], st['policy']['noshow_fee'], st['policy']['require_card_online']), ('Blush & Blade Studio', 40.0, True))
        # public booking now requires a card
        pub = ok('GET', '/api/public/blushblade')
        self.assertTrue(pub['policy']['require_card'])
        d = workday(2)
        sl = ok('GET', '/api/public/blushblade/availability?service_id=%s&date=%s' % (pub['services'][1]['id'], d))['slots']
        self.assertEqual(call('POST', '/api/public/blushblade/book', dict(service_id=pub['services'][1]['id'], date=d, time=sl[-1], name='Card Less'))[0], 409)
        r = ok('POST', '/api/public/blushblade/book', dict(service_id=pub['services'][1]['id'], date=d, time=sl[-1], name='Card Full', card={'brand': 'Visa', 'last4': '9999'}))
        self.assertTrue(r['manage'].startswith('/manage/blushblade/'))
        ok('PATCH', self.L(self.salon, '/settings'), {'name': 'Blush & Blade', 'policy': {'noshow_fee': 50, 'require_card_online': False}})
        aud = ok('GET', self.L(self.salon, '/audit'))
        self.assertTrue(any(e['action'] == 'settings.update' for e in aud['events']))
        self.assertEqual(call('POST', self.L(self.salon, '/auth/pin'), {'pin': '0000'})[0], 401)
        me = ok('POST', self.L(self.salon, '/auth/pin'), {'pin': '1111'})
        self.assertEqual((me['staff']['name'], me['staff']['level']), ('Ava Lopez', 'owner'))
        self.assertEqual(ok('POST', self.L(self.salon, '/auth/pin'), {'pin': '2222'})['staff']['level'], 'staff')

    def test_kiosk_checks_in_by_phone_digits(self):
        st = self.state(self.gym)
        r = ok('POST', self.L(self.gym, '/clients'), {'name': 'Kiosk Kim', 'phone': '+15550107777'})
        kid = r['id']
        sv = st['services'][0]
        # a booking within the next 90 minutes for this client, if the desk is open now; otherwise just membership lookup
        self.assertEqual(call('POST', '/api/public/ironworksgym/kiosk', {'q': '12'})[0], 400)
        self.assertEqual(call('POST', '/api/public/ironworksgym/kiosk', {'q': '0000'})[0], 404)
        out = ok('POST', '/api/public/ironworksgym/kiosk', {'q': '7777'})
        self.assertEqual(out['name'], 'Kiosk')
        self.assertIsInstance(out['checked_in'], list)
        unl = self.find(st['passes'], name='Unlimited monthly')
        ok('POST', self.L(self.gym, '/passes/%s/sell' % unl['id']), {'client_id': kid})
        out = ok('POST', '/api/public/ironworksgym/kiosk', {'q': '7777'})
        self.assertEqual(out['membership']['name'], 'Unlimited monthly')
        self.assertEqual(sv['id'], sv['id'])

    def test_client_update_and_addons_and_processing_time(self):
        st = self.state(self.salon)
        k = st['clients'][4]
        st = ok('PATCH', self.L(self.salon, '/clients/%s' % k['id']), {'notes': 'Allergic to PPD', 'tags': 'vip, colour', 'birthday': '1990-05-04', 'email': 'sam@example.com'})
        c = self.find(st['clients'], id=k['id'])
        self.assertEqual((c['notes'], c['tags'], c['birthday'], c['email']), ('Allergic to PPD', ['colour', 'vip'], '1990-05-04', 'sam@example.com'))
        cut = self.find(st['services'], name="Women's cut & style")  # has two add-ons in the seed
        self.assertEqual(len(cut['addons']), 2)
        d = workday(2)
        sl = self.slots(self.salon, cut['id'], st['staff'][0]['id'], d)
        r = ok('POST', self.L(self.salon, '/appointments'), dict(service_id=cut['id'], staff_id=st['staff'][0]['id'], client_id=k['id'], date=d, time=sl[0], addons=[cut['addons'][0]['name']]))
        a = self.find(r['state']['appts'], id=r['id'])
        self.assertEqual(a['total'], cut['price'] + cut['addons'][0]['price'])
        self.assertEqual(len(a['addons']), 1)
        # the add-on's extra minutes block the following slot
        after = self.slots(self.salon, cut['id'], st['staff'][0]['id'], d)
        self.assertNotIn(tstr_(mins_(sl[0]) + 60), after)
        # processing time: a 90-min colour with 30 min processing blocks 2 hours of the stylist's lane
        root = self.find(st['services'], name='Root touch-up')
        self.assertEqual(root['gap'], 30)
        d2 = workday(3)
        sl2 = self.slots(self.salon, root['id'], st['staff'][2]['id'], d2)
        ok('POST', self.L(self.salon, '/appointments'), dict(service_id=root['id'], staff_id=st['staff'][2]['id'], client_id=k['id'], date=d2, time=sl2[0]))
        after2 = self.slots(self.salon, st['services'][1]['id'], st['staff'][2]['id'], d2)
        self.assertNotIn(tstr_(mins_(sl2[0]) + 90), after2, 'processing time keeps the lane busy')
        self.assertIn(tstr_(mins_(sl2[0]) + 120), after2)


def mins_(t):
    return int(t[:2]) * 60 + int(t[3:5])


def tstr_(m):
    return '%02d:%02d' % ((m // 60) % 24, m % 60)
