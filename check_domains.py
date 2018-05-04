#!/usr/bin/env python3

import requests
import sys
from queue import Queue
from threading import Lock, Thread
from concurrent.futures import ThreadPoolExecutor
import traceback

RESETLINE = "\x1b[1K\r"

if len(sys.argv) != 1:
	print('Usage: %s' % sys.argv[0], file=sys.stderr)
	sys.exit(2)

print('Requesting cart ID... ', file=sys.stderr, end='', flush=True)

cart_id_response = requests.post('https://www.ovh.es/engine/apiv6/order/cart', json={'description': '_ovhcom_legacy_order_cart_', 'ovhSubsidiary': 'ES'})
cart_id_response.raise_for_status()
cart_id = cart_id_response.json()['cartId']

print('got %s' % cart_id, file=sys.stderr)

data_lock = Lock()
print_lock = Lock()
domain_info = []
failed_domains = 0

class DomainInfo:
	def __init__(self, name, order, renew):
		self.name = name
		self.order = order
		self.renew = renew

def check_tld_status(domain):
	global domain_info, failed_domains

	with print_lock:
		print(RESETLINE + '%i/%i: %s' % (len(domain_info), failed_domains, domain), file=sys.stderr, end='', flush=True)

	try:
		params = {
				'domain': domain
		}
		info = requests.get('https://www.ovh.es/engine/apiv6/order/cart/%s/domain' % cart_id, params=params).json()

		# Get first (and only) offer
		try:
			info = info[0]
		except:
			with data_lock:
				failed_domains += 1
			return

		# Skip if not available
		if not info['orderable']:
			return

		# Extract price
		orderprice, renewprice = None, None
		for price in info['prices']:
			if price['label'] == 'TOTAL':
				orderprice = price['price']['value']
			elif price['label'] == 'RENEW':
				renewprice = price['price']['value']

		# Skip if any pricing information is not available
		if orderprice is None or renewprice is None:
			with data_lock:
				failed_domains += 1
			return

		with data_lock:
			domain_info.append(DomainInfo(domain, orderprice, renewprice))
	except Exception as e:
		with print_lock:
			traceback.print_last()

with ThreadPoolExecutor(max_workers=10) as executor:
	for domain in sys.stdin:
		domain = domain.strip()
		domain = domain.encode('idna').decode('ascii')

		if not '.' in domain:
			continue

		future = executor.submit(check_tld_status, domain)

# Sort according to renew now
domain_info.sort(key=lambda x: max(x.renew, x.order))

# Print info
print(RESETLINE, file=sys.stderr, end='', flush=True)
print('domain\trenew\torder')
for info in domain_info:
	print('%s\t%s\t%s' % (info.name, info.renew, info.order))
