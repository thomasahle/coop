import requests
import collections
import pickle
from lxml import html
from urllib.parse import urlparse, parse_qs
import json
import pathlib
import re
import argparse
import contextlib
import csv
import sys
import logging

try:
    import editdistance
    editdist = editdistance.eval
except ImportError:
    print('Using slow editdistance.')
    print('Please run `pip install editdistance`')
    def editdist(s1, s2):
        if len(s1) > len(s2):
            s1, s2 = s2, s1
        distances = range(len(s1) + 1)
        for i2, c2 in enumerate(s2):
            distances_ = [i2+1]
            for i1, c1 in enumerate(s1):
                if c1 == c2:
                    distances_.append(distances[i1])
                else:
                    distances_.append(1 + min((distances[i1], distances[i1 + 1], distances_[-1])))
            distances = distances_
        return distances[-1]

class Coop:
    def __init__(self, cookies_path):
        self.cookies_path = cookies_path
        self.s = requests.Session()
        if cookies_path.is_file():
            with cookies_path.open('rb') as f:
                self.s.cookies.update(pickle.load(f))

        self.context = self.get_user_context()
        self.zip = self.context['zipCode']

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        with self.cookies_path.open('wb') as f:
            pickle.dump(self.s.cookies, f)

    def __login(self, url, username, password):
        r = self.s.get(url)
        tree = html.fromstring(r.text)
        veri_token = tree.xpath('/html/body/div/div[1]/form/input/@value')[0]
        action = tree.xpath('/html/body/div/div[1]/form/@action')[0]
        params = parse_qs(urlparse(action).query)
        r = self.s.post('https://accounts.cl.coop.dk/Account/Login',
                params = params,
                data = {
                    '__RequestVerificationToken': veri_token,
                    'UserName': username,
                    'Password': password,
                })
        return self.__login_cb(r)

    def login(self, username, password, skip_context=False):
        success = self.__login('https://butik.mad.coop.dk/api/authentication/loginsrc', username, password)
        if success:
            # Update context to make sure zip is correct
            self.context = self.get_user_context()
            self.zip = self.context['zipCode']
        return success

    def __login_cb(self, r):
        if 'Adgangskoden er forkert' in r.text:
            return False
        tree = html.fromstring(r.text)
        names = tree.xpath('/html/body/form/input/@name')
        values = tree.xpath('/html/body/form/input/@value')
        data = dict(zip(names, values))
        action = html.fromstring(r.text).xpath('/html/body/form/@action')[0]
        # This call just returns a page asking us to go back
        # 'window.parent.location.href = window.parent.location.href;'
        self.s.post(action, data=data)
        return True

    def get(self, path, **kwargs):
        r = self.s.get(path, **kwargs)
        if '<noscript><button>Click to continue</button>' in r.text:
        #if "action='https://coop.dk/login/login/logincallback'" in r.text:
            print('Using login_callback')
            return self.__login_cb(r)
        if 'Authorization has been denied for this request' in r.text:
            print('No longer logged in. Please remove cookies and try again')
            # TODO: We could do this
            return r
        if r.status_code != 200:
            print('Status code:', r)
            print('Text:', r.text)
        if r.status_code == 500:
            print('Try again later.')
        return r

    def post(self, path, **kwargs):
        r = self.s.post(path, **kwargs)
        if r.status_code != 200:
            print('Status code:', r)
            print('Text:', r.text)
        return r

    def get_latest_editable_order(self):
        r = coop.get('https://butik.mad.coop.dk/api/orderhistory/latesteditableorder')
        return json.loads(r.text)

    @contextlib.contextmanager
    def edit_order(self, order_identifier):
        try:
            r = self.post('https://butik.mad.coop.dk/api/editorder/initEdit',
                    json=dict(orderIdentifier=order_identifier, mergeCurrentBasket=False))
            assert r.status_code == 200
        finally:
            r = self.post('https://butik.mad.coop.dk/api/editorder/cancelEditOrderMode')
            # FIXME: Check status code

    def get_stores(self, is_new_site=True):
        r = self.get('https://butik.mad.coop.dk/api/store/get',
                params=dict(isNewSite=is_new_site,
                            zipCode=self.zip))
        return json.loads(r.text)

    def get_timeslots(self, is_home_delivery=True, store_id='000005'):
        r = self.get('https://butik.mad.coop.dk/api/timeslot/gettimeslots',
                params=dict(isHomeDelivery=is_home_delivery,
                            storeid=store_id,
                            zipCode=self.zip))
        return json.loads(r.text)

    def check_slot(self, time_slot_id, store_id):
        r = self.post('https://butik.mad.coop.dk/api/timeslot/checkslot',
                json=dict(id=time_slot_id, storeid=store_id, zipCode=self.zip))
        # Success: {"totalPriceChange":null,"unavailableProducts":null,"changedProducts":null,"isCheaper":null}
        return json.loads(r.text)

    def set_delivery_options(self, time_slot_id, store_id, is_home_delivery=True, umbraco=1064):
        # Note they use a capital I in storeId here...
        r = self.post('https://butik.mad.coop.dk/api/timeslot/SetDeliveryOptions',
                json=dict(timeSlotId=time_slot_id, storeId=store_id, zipCode=self.zip,
                          isHomeDelivery=True, umbracoPageId=umbraco))
        # Same type of response as update_basked requests.
        return json.loads(r.text)

    def get_invoiced_orders(self, n=100, page=0):
        r = self.get('https://butik.mad.coop.dk/api/orderhistory/invoicedorders',
                params=dict(page=page, pageSize=n))
        return json.loads(r.text)

    def get_order_history_detail(self, order_identifier):
        r = self.get('https://butik.mad.coop.dk/api/orderhistory/orderhistorydetail',
                params=dict(orderIdentifier=order_identifier))
        return json.loads(r.text)

    def get_basket(self, refresh=False):
        r = self.get('https://butik.mad.coop.dk/api/basket/get',
                params=dict(refresh=refresh))
        return json.loads(r.text)

    def multi_update_basket(self, id_qs):
        d = dict(lineItemUpdates=[dict(productId=pi, quantity=q, lineItemId=None)
                                    for pi, q in id_qs])
        r = self.post('https://butik.mad.coop.dk/api/basket/update', json=d)
        return json.loads(r.text)

    def update_basket(self, product_id, quantity, line_item_id=None):
        if line_item_id is not None:
            print('Warning: lineItemId currently not supported')
        return self.multi_update_basket([product_id, quantity])

    def get_stock(self, pids, order_identifier, store_id, timeslot_id):
        r = self.post('https://butik.mad.coop.dk/api/stock/stock', json=dict(
            orderIdentifier=order_identifier,
            productIds=pids,
            storeId=store_id,
            timeSlotId=timeslot_id,
            zipCode=self.zip))
        # Returns a list of dicts.
        # For sold out products:
            # cutOffDate: null
            # cutOffExceeded: false
            # date: null
            # itemId: "5700382425297"
            # label: "Udsolgt"
            # quantity: 0
        # For available products:
            # cutOffDate: null
            # cutOffExceeded: false
            # date: null
            # itemId: "5718146713030"
            # label: ""
            # quantity: 77
        return json.loads(r.text)

    def is_login(self):
        # Unfortunately we're not allowed to just call HEAD.
        r = self.get('https://butik.mad.coop.dk/api/coopmember/get')
        return (r.status_code == 200), json.loads(r.text)

    def get_user_context(self, r=None):
        # User context contains this stuff:
        # userContext: {"name":"Rune Ytting Hellden","isAuthenticated":true,"showProfileNavigation":true,"isCsUser":false,"email":"nebbegaardsbakken52@gmail.com","impersonator":null,"zipCode":"2400","isShadowLogin":false,"coopMemberType":3,"memberNumber":"33084191"},
        # We can get the context from any page request (doesn't use the api)
        if r is None:
            r = self.get('https://butik.mad.coop.dk/min-profil/profiloplysninger')
        userContext = re.search('userContext: ({.*?})', r.text).group(1)
        return json.loads(userContext)

    def get_all_products(self, n=10, page=0):
        path = pathlib.Path(f'products_n{n}_p{page}.cached')
        if not path.is_file():
            r = self.get('https://butik.mad.coop.dk/api/search/products', params=dict(
                pageSize=n, page=page))
            prods = json.loads(r.text)['products']
            with path.open('w') as file:
                json.dump(prods, file)
            return prods
        else:
            with path.open('r') as file:
                return json.load(file)

    def search(self, n=10):
        print('Henter varer...')
        # Hvad er forskellen til search/products?
        # GET https://butik.mad.coop.dk/api/search/products?term=%2a&categories=326&lastFacet=sortby&sortby=Offers&pageSize=14
        r = self.get('https://butik.mad.coop.dk/api/search/search', params=dict(
            categories=229,
            lastFacet='categories',
            pageSize=n))
        for vare in json.loads(r.text)['products']:
            #print(vare)
            print(vare['displayName'], vare['isInAssortment'])

# API CALLS:
# GET https://butik.mad.coop.dk/api/search/getbyids?productids=82896001453,...,8028752820020,7804320032290&pageSize=21&offersOnly=true
# Hvis et produkt er udgaaet kommer det ikke med i soegning, men kan stadig komme i tophundred.
# Det har isInAssortment=false, hvor normale produkter har isInAssortment=true.



def bold_text(text):
    return f"\033[1m{text}\033[0m"

class ProductFinder:
    def __init__(self, coop):
        self.prods = []
        print('Vent venligst mens vi downloader alle coops produkter')
        for i in range(100):
            print('.', end='', flush=True)
            chunk = coop.get_all_products(n=1000, page=i)
            self.prods += chunk
            if len(chunk) < 1000:
                break
        print(f'\nHentede {len(self.prods)} produkter.')

    def byid(self, pid):
        return next((p for p in self.prods if p['id'] == pid), None)

    def suggestions(self, prod_name, n=10):
        for p in sorted(self.prods, key=lambda p: editdist(prod_name, p['displayName']))[:n]:
            p = p.copy()
            p['name_original'] = prod_name
            yield p

def basket(coop, args):
    if args.clear: basket_clear(coop, args)
    elif args.write: basket_write(coop, args)
    elif args.read: basket_read(coop, args)
    else: basket_show(coop, args)

def basket_clear(coop, args):
    updates = []
    for item in coop.get_basket()['lineItems']:
        updates.append((item['product']['id'], -item['quantity']))
    if updates:
        # Don't clear if there's nothing in the basket.
        res = coop.multi_update_basket(updates)
        assert len(res["lineItems"]) == 0

def basket_write(coop, args):
    writer = csv.writer(args.write, dialect='excel')
    for item in coop.get_basket()['lineItems']:
        display_name, pid = item['product']['displayName'], item['product']['id']
        writer.writerow([item['quantity'], f"{display_name} [{pid}]"])

def basket_read(coop, args):
    pids = set() # All pids we want stock info for. Pid -> Product Dict
    quantities = [] # Amount we want of each product. Pid -> Int
    reader = csv.reader(args.read)
    pf = ProductFinder(coop)
    for row in reader:
        if not row or row[0].strip()[0] == '#':
            # Ignoring comments
            continue
        q, prod_name, *ids = row
        prods = []
        for pid in ids:
            # Remove comments
            pid = re.sub('\(.*?\)', '', pid).strip()
            p = pf.byid(pid)
            if p is None:
                print(f'Kunne ikke se id {pid} ({prod_name}) i kataloget. Slet cache filer?')
            else:
                prods.append(p)
        # Add suggestions, unless they are already there
        for sug in pf.suggestions(prod_name, n=5):
            if not any(p['id'] == sug['id'] for p in prods):
                prods.append(sug)

        quantities.append((int(q), prods)) # Also the amount of each product to actually order
        for p in prods:
            pids.add(p['id'])

    print('Checker om varerne er tilgængelige...')
    basket = coop.get_basket()
    if basket['timeSlot'] is None:
        print('Intet tidspunkt valgt. Kan ikke checke stock.')
        print('Kør "coop.py tidspunkt -pick" for automatisk at vælge et tidspunkt.')
        to_order = [(prods[0], q) for q, prods in quantities]
    else:
        stock = coop.get_stock(
                list(pids),
                basket['orderIdentifier'],
                basket['store']['id'],
                basket['timeSlot']["timeSlotId"])
        available = {s['itemId']:s for s in stock}
        to_order = []
        for wanted, prods in quantities:
            p = prods[0]
            pid = p['id']
            q = available[pid]['quantity']
            to_order.append((p, min(wanted, q)))
            if wanted > q:
                amount = f'kun {q}' if q > 0 else 'ingen'
                print(f'Der er {amount} "{p["displayName"]}" tilbage.', available[pid]['label'])
                wanted -= q
                for alt in prods[1:]:
                    q = available[alt['id']]['quantity']
                    take = min(q, wanted)
                    if take > 0:
                        print(f'Tager {take} "{alt["displayName"]}" som alternativ.')
                        to_order.append((alt, take))
                        wanted -= take
                if wanted > 0:
                    print(f'Ingen alternativer tilbage for {wanted} "{p["displayName"]}".')

    print('Vent venligst...')
    if to_order:
        for p, q in to_order:
            name_original = p.get('name_original')
            if name_original:
                print(f'VIGTIG: {q} {p["displayName"]} er valgt automatisk ud fra at minde om {name_original}.')
        if not args.test:
            order = [(p['id'], q) for p, q in to_order]
            res = coop.multi_update_basket(order)
            print(f'Kurven har nu {len(res["lineItems"])} varer.')

def basket_show(coop, args):
    basket = coop.get_basket()
    #   print(json.dumps(basket, indent=3))
    categories = collections.defaultdict(list)
    for item in basket['lineItems']:
        categories[item['product']['category']].append(item)
    for category, items in categories.items():
        print(bold_text(category))
        for item in items:
            spot_text = item['product']['spotText'].replace('\n', ' ')
            print(f"{item['quantity']} {item['product']['displayName']} ({spot_text})")
        print()
    # totals: total, subTotal, packing, delivery
    count = sum(item['quantity'] for item in basket['lineItems'])
    print(f'{count} varer i kurven')
    print(basket['progressBar']['achievement'])
    print('Total pris: kr.', basket["totals"]["subTotal"]["formattedAmountLong"])
    print()
    if basket['id'] == 0 or basket['timeSlot'] is None:
        print('Leveringstidspunkt ikke valgt.')
        print('Kør "coop.py tidspunkt --pick" for automatisk at vælge et tidspunkt.')
    else:
        print('Leveringstidspunkt:', basket['timeSlot']['deliveryDescription'])
        print('Butik:', basket['store']['address'])

def orders(coop, args):
    N = 10
    orders = coop.get_invoiced_orders(n=N)
    orders.sort(key=lambda o: o['orderNumber'], reverse=True)
    if args.n is None:
        print('Tidligere ordrer:')
        for i, order in enumerate(orders):
            e = '(e)' if order['isEditable'] else ''
            print(f"[{i}] {order['deliveryTime']}. Pris: {order['price']['formattedAmount']} {e}")
    else:
        assert args.n in range(N)
        order = orders[args.n]
        details = coop.get_order_history_detail(order['orderIdentifier'])
        if args.write:
            writer = csv.writer(args.write, dialect='excel')
            for cat in details['categories']:
                writer.writerow([f"# {cat['name']}"])
                for item in cat['lineItems']:
                    display_name = item['displayName']
                    # TODO: This image url thing is a hack :(
                    pid = re.search('products/(\d+?).png', str(item['imageUrl']))
                    writer.writerow([item['quantity'], display_name, pid.group(1) if pid else ''])
        else:
            for cat in sorted(details['categories'], key=lambda c:c['name']):
                print(bold_text(cat['name']))
                for item in sorted(cat['lineItems'], key=lambda i:i['displayName']):
                    # There is also something called originalQuantity
                    pid = re.search('products/(\d+?).png', str(item['imageUrl']))
                    print(item['quantity'], item['displayName'], pid.group(1) if pid else '')
                print()

def slot_loss(slot, best_time):
    """ Returns the distance between the given slot and best_time """
    if slot['soldOut'] or slot['isSpecialSlot']:
        return 10
    m = re.search('(\d+)-(\d+)', slot['displayName'])
    a, b = map(int, m.groups())
    # Compute distance from best_time to the interval [a,b]
    return max(best_time - b, a - best_time, 0)

def pick_timeslot(coop, day, hour):
    stores = coop.get_stores()
    if len(stores) > 1:
        print('Der er flere butikker tilgængelige:', ', '.join(s['address'] for s in stores))
    store_id = stores[0]['id']
    slots = coop.get_timeslots(is_home_delivery=True, store_id=store_id)
    for slot_day in slots['timeSlotDeliveryDays']:
        # Pick first Wednesday
        if slot_day['deliveryDateFormattedShort'].startswith(day):
            # Pick timeslot not too far away fom 6pm
            q, _, best = min((slot_loss(s, hour), s['displayName'], s) for s in slot_day['timeSlots'])
            if q > 1:
                print(f'Kunne ikke finde godt tidspunkt {day}.')
                return None
            res = coop.check_slot(best['timeSlotId'], store_id)
            # {"totalPriceChange":null,"unavailableProducts":null,"changedProducts":null,"isCheaper":null}
            res = coop.set_delivery_options(best['timeSlotId'], store_id)
            return best, res

def timeslot(coop, args):
    if args.pick:
        slot, basket = pick_timeslot(coop, args.day, int(args.hour))
        print('Valgte:', slot["deliveryDescription"])
        if not 'deliveryCheckoutMessage' in basket:
            pass
            #print(json.dumps(basket, indent=3))
        else:
            print(basket['deliveryCheckoutMessage'])
    else:
        stores = coop.get_stores()
        if len(stores) > 1:
            print('Der er flere butikker tilgængelige:', ', '.join(s['address'] for s in stores))
        print('Butik:', stores[0]['address'])
        store_id = stores[0]['id']
        slots = coop.get_timeslots(is_home_delivery=True, store_id=store_id)
        i = 0
        for slot_day in slots['timeSlotDeliveryDays']:
            print(bold_text(slot_day['deliveryDateFormattedLong']))
            for slot in slot_day['timeSlots']:
                avail = 'optaget' if slot['soldOut'] else 'fri'
                if slot['isSpecialSlot']:
                    avail += ', special'
                print(f"[{i}] {slot['displayName']} ({avail})")
                i += 1
            print()

def test(coop, args):
    r = coop.post('https://butik.mad.coop.dk/api/basket/update', json=
    #{"productId":"5701975300939","quantity":1,"lineItemId":"13071940"},
    {"lineItemUpdates":[{"productId":"5701975300939","quantity":1,"lineItemId":None}]},
                headers=dict(referer='https://butik.mad.coop.dk/', origin='https://butik.mad.coop.dk'))
    print(r)
    print(r.text)

def user(coop, args):
    res = coop.get_user_context()
    for key, value in res.items():
        print(f'{key}: {value}')

def help(coop, args):
    parser.print_help()


parser = argparse.ArgumentParser(description='''Gejst madbestilling.

Eksempler:

Se hjælp: python3 coop.py
Vælg et tidspunkt
    python3 coop.py tidspunkt --pick
Se hvad der er i kurven
    python3 coop.py kurv
Slet hvad der er i kurven
    python3 coop.py kurv --clear
Skriv hvad der er i kurven til en fil
    python3 coop.py kurv --write FILNAVN
Tilføj en fil til kurven
    python3 coop.py kurv --read FILNAVN
Se de 10 seneste ordrer
    python3 coop.py ordrer
Skriv en tidligere ordre til en fil
    python3 coop.py ordrer N --write FILNAVN
''', formatter_class=argparse.RawTextHelpFormatter)
parser.add_argument('--debug', action='store_true')
parser.add_argument('--username', default='')
parser.add_argument('--password', default='')
parser.set_defaults(func=help)
subparsers = parser.add_subparsers()

timeslot_parser = subparsers.add_parser('tidspunkt', help='Vis og vælg leveringstidspunkt')
timeslot_parser.set_defaults(func=timeslot)
timeslot_parser.add_argument('--pick', action='store_true', help='Automatically pick best timeslot')
timeslot_parser.add_argument('--day', default='Ons', help='Preferred day to autopick')
timeslot_parser.add_argument('--hour', default=18, help='Preferred time to autopick')

#test_parser = subparsers.add_parser('test', help='Thomas\'s test ting')
#test_parser.set_defaults(func=test)

user_parser = subparsers.add_parser('bruger', help='Hvem er jeg?')
user_parser.set_defaults(func=user)

basket_parser = subparsers.add_parser('kurv', help='Vis og opdater kurven')
basket_parser.set_defaults(func=basket)
basket_parser.add_argument('--write', type=argparse.FileType('w'), metavar='FILE_NAME', help='Write basket as csv')
basket_parser.add_argument('--read', type=argparse.FileType('r'), metavar='FILE_NAME', help='Read csv and add to basket')
basket_parser.add_argument('--clear', action='store_true', help='Fjern alt fra kurven')
basket_parser.add_argument('--test', action='store_true', help='Check availability of products, but don\'t actually add them to the basket.')

order_parser = subparsers.add_parser('ordrer', help='Vis gamle bestillinger')
order_parser.add_argument('n', type=int, nargs='?', help='Write the nth order')
order_parser.add_argument('--write', type=argparse.FileType('w'), metavar='FILE_NAME', help='Write basket as csv')
order_parser.set_defaults(func=orders)

def main():
    args = parser.parse_args()
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    cookies_path = pathlib.Path('cookies.coop')

    print('Logging in...')
    with Coop(cookies_path) as coop:
        while not coop.context['isAuthenticated']:
            username, password = args.username, args.password
            while not username:
                username = input('Username: ').strip()
            while not password:
                password = input('Password: ').strip()
            success = coop.login(username, password)
            if not success:
                print('Mistake in username or password')

        print(f'Hej {coop.context["name"]}!')
        args.func(coop, args)

if __name__ == '__main__':
    main()

