#!/usr/bin/env python3

from cmd import Cmd
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from threading import Lock
import json
import re
import requests
import sys
import traceback

class Sorting(Enum):
	NONE = 0
	PRICE = 1
	RENEW = 2
	ORDER = 3
	ALPHABETIC = 4

class DomainInfo:
	def __init__(self, name, order, renew):
		self.name = name
		self.order = order
		self.renew = renew

class DomainCmd(Cmd):
	prompt = '(dq) '
	BOOLEANS = {
			'yes': True,
			'enabled': True,
			'true': True,
			'on': True,

			'no': False,
			'disabled': False,
			'false': False,
			'off': False
	}
	SORTING_NAMES = {
			'price': Sorting.PRICE,
			'renew': Sorting.RENEW,
			'order': Sorting.ORDER,
			'alphabetic': Sorting.ALPHABETIC,
			'no': Sorting.NONE,
			'disabled': Sorting.NONE,
			'false': Sorting.NONE,
			'off': Sorting.NONE
	}
	SORTING_DIRECTION = {
			'ascending': True,
			'descending': False
	}
	RESETLINE = "\x1b[1K\r"

	def __init__(self):
		super().__init__()

		# Domain filtering
		self.all_tlds = None
		self.include_intl = False
		self.include_sld = False
		self.max_length = None

		# Sorting
		self.sorting = Sorting.NONE
		self.sort_ascendending = True

		# Status querying
		self.cart_id = None
		self.data_lock = Lock()
		self.print_lock = Lock()
		self.domain_info = None
		self.failed_domains = 0

	# Do nothing on empty line
	def emptyline(self):
		return

	def default(self, arg):
		if arg == 'EOF':
			sys.exit(0)

		self.do_check(arg)

	def do_maxlen(self, arg):
		arg = arg.strip().casefold()

		if arg != '':
			maxlen_enabled = True
			try:
				maxlen_enabled = self._parse_bool(arg)
			except:
				pass

			if maxlen_enabled:
				try:
					value = int(arg)
				except ValueError:
					print('Invalid integer "%s"' % arg, file=sys.stderr)
					return

				if value < 4:
					print('Max length may not be less than 4', file=sys.stderr)
					return
			else:
				value = None

			self.max_length = value

		if self.max_length is None:
			print('Max TLD length is disabled', file=sys.stderr)
		else:
			print('Max TLD length is %s' % str(self.max_length).lower(), file=sys.stderr)

	def do_intl(self, arg):
		arg = arg.strip()

		if arg != '':
			try:
				self.include_intl = self._parse_bool(arg)
			except ValueError as e:
				print('Cannot set internationalized domain status. %s' % str(e), file=sys.stderr)
				return

		print('Internationalized domains are %s' % ('enabled' if self.include_intl else 'disabled'), file=sys.stderr)

	def do_sld(self, arg):
		arg = arg.strip()

		if arg != '':
			try:
				self.include_sld = self._parse_bool(arg)
			except ValueError as e:
				print('Cannot set second-level domain status. %s' % str(e), file=sys.stderr)
				return

		print('Second-level domains are %s' % ('enabled' if self.include_sld else 'disabled'), file=sys.stderr)

	def _partial_key_match(self, map, partial):
		partial = partial.casefold()
		keys = list()
		values = set()

		for key, value in map.items():
			if key.startswith(partial):
				keys.append(key)
				values.add(value)

		if len(values) == 0:
			raise ValueError('Expected any of %s' % str(list(map.keys())))
		elif len(values) > 1:
			raise ValueError('"%s" may refer to any of %s' % (partial, str(keys)))

		return next(iter(values))

	def _parse_bool(self, text):
		return self._partial_key_match(DomainCmd.BOOLEANS, text)

	def do_sort(self, args):
		args = args.split()

		if len(args) > 2:
			print('Too many arguments', file=sys.stderr)
			return

		if len(args) > 0:
			try:
				new_sorting = self._partial_key_match(DomainCmd.SORTING_NAMES, args[0])
			except ValueError as e:
				print('Cannot parse mode. %s' % str(e), file=sys.stderr)
				return

			new_ascending = True
			if len(args) > 1:
				try:
					new_ascending = self._partial_key_match(DomainCmd.SORTING_DIRECTION, args[1])
				except ValueError as e:
					print('Cannot parse direction. %s' % str(e), file=sys.stderr)
					return

			self.sorting = new_sorting
			self.sort_ascendending = new_ascending

		if self.sorting == Sorting.NONE:
			print('Sorting disabled', file=sys.stderr)
		else:
			print('Sorting by %s %s' % (self.sorting.name.lower(), 'ascending' if self.sort_ascendending else 'descending'))

	def do_updatetld(self, arg):
		self._fetch_tlds()

	def _fetch_tlds(self):
		print('Fetching TLD list... ', file=sys.stderr, end='', flush=True)

		try:
			tlds = requests.get('https://www.ovh.es/engine/apiv6/domain/data/extension?country=ES').json()
			self.all_tlds = [tld.encode('utf-8').decode('idna').lower() for tld in tlds]
			print('got %d' % len(tlds), file=sys.stderr)
			return True
		except Exception as e:
			print('cannot fetch', file=sys.stderr)
			traceback.print_last()
			return False

	def do_tld(self, arg):
		self.do_tlds()

	def do_tlds(self, arg):
		tlds = self._get_valid_tlds()
		if tlds:
			print(', '.join(tlds))

	def _get_valid_tlds(self):
		if self.all_tlds is None:
			if not self._fetch_tlds():
				return None

		return [tld for tld in self.all_tlds if self._tld_valid(tld)]

	def _tld_valid(self, tld):
		if not self.include_sld and '.' in tld:
			return False

		if not self.include_intl and re.search(r'[^a-z.]', tld):
			return False

		if self.max_length is not None and len(tld) > self.max_length:
			return False

		return True

	def do_check(self, arg):
		to_check = self._domain_check_list(arg.split())
		if to_check is None:
			return

		print('Requesting cart ID... ', file=sys.stderr, end='', flush=True)

		cart_id_response = requests.post('https://www.ovh.es/engine/apiv6/order/cart', json={'description': '_ovhcom_legacy_order_cart_', 'ovhSubsidiary': 'ES'})
		try:
			cart_id_response.raise_for_status()
		except Exception as e:
			traceback.print_last()
			return

		self.cart_id = cart_id_response.json()['cartId']
		print('got %s' % self.cart_id, file=sys.stderr)

		# Reset variables
		if self.sorting == Sorting.NONE:
			self._print_domain_header()
			with ThreadPoolExecutor(max_workers=10) as executor:
				for domain in to_check:
					executor.submit(self._check_and_update, domain)
		else:
			self.domain_info = []
			self.failed_domains = 0

			with ThreadPoolExecutor(max_workers=10) as executor:
				for domain in to_check:
					executor.submit(self._check_and_update_sorted, domain)

			self._sort_domain_list()
			self._print_process()
			self._print_domain_header()
			for info in self.domain_info:
				self._print_domain_entry(info)

	def _print_process(self, line=None):
		print(DomainCmd.RESETLINE, file=sys.stderr, end='', flush=True)
		if line:
			print(line, file=sys.stderr, end='', flush=True)

	def _print_domain_header(self):
		print('domain\trenew\torder')

	def _print_domain_entry(self, info):
		print('%s\t%s\t%s' % (info.name, info.renew, info.order))

	def _domain_check_list(self, domains):
		to_check = set()
		valid_tlds = None
		for domain in domains:
			if '.' in domain:
				to_check.add(domain)
				continue

			if valid_tlds is None:
				valid_tlds = self._get_valid_tlds()
				if valid_tlds is None:
					print('Cannot check %s' % domain, file=sys.stderr)
					return None

			for tld in valid_tlds:
				to_check.add('%s.%s' % (domain, tld))

		return to_check

	def _check_and_update(self, domain):
		try:
			info = self._check_domain_status(domain)
		except Exception as e:
			with self.print_lock:
				traceback.print_last()
			info = None

		with self.print_lock:
			if info is not None:
				self._print_domain_entry(info)

	def _check_and_update_sorted(self, domain):
		with self.print_lock:
			self._print_process('%i/%i: %s' % (len(self.domain_info), self.failed_domains, domain))

		try:
			info = self._check_domain_status(domain)
		except Exception as e:
			with self.print_lock:
				traceback.print_last()
			info = None

		with self.data_lock:
			if info is not None:
				self.domain_info.append(info)
			else:
				self.failed_domains += 1

	def _check_domain_status(self, domain):
		params = {
				'domain': domain
		}
		info = requests.get('https://www.ovh.es/engine/apiv6/order/cart/%s/domain' % self.cart_id, params=params).json()

		# Get first (and only) offer
		try:
			info = info[0]
		except:
			return None

		# Skip if not available
		if not info['orderable']:
			return None

		# Extract price
		orderprice, renewprice = None, None
		for price in info['prices']:
			if price['label'] == 'TOTAL':
				orderprice = price['price']['value']
			elif price['label'] == 'RENEW':
				renewprice = price['price']['value']

		# Skip if any pricing information is not available
		if orderprice is None or renewprice is None:
			return None

		return DomainInfo(domain, orderprice, renewprice)

	def _sort_domain_list(self):
		if self.sorting == Sorting.PRICE:
			func = lambda x: max(x.renew, x.order)
		elif self.sorting == Sorting.RENEW:
			func = lambda x: x.renew
		elif self.sorting == Sorting.ORDER:
			func = lambda x: x.order
		elif self.sorting == Sorting.ALPHABETIC:
			func = lambda x: x.name
		else:
			raise Exception('What the fuck %s' % str(self.sorting))

		self.domain_info.sort(key=func)
		if not self.sort_ascendending:
			self.domain_info.reverse()

	def do_exit(self, arg):
		sys.exit(0)

	def do_quit(self, arg):
		sys.exit(0)

if __name__ == '__main__':
	DomainCmd().cmdloop()
