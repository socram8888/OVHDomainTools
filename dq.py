#!/usr/bin/env python3

from cmd import Cmd
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import timedelta
from enum import Enum
from threading import Lock
import argparse
import configparser
import lxml.html
import os
import json
import re
import requests
import sys
import traceback

class Sorting(Enum):
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
			'alphabetic': Sorting.ALPHABETIC
	}
	SORTING_DIRECTION = {
			'ascending': True,
			'descending': False
	}
	RESETLINE = "\x1b[1K\r"
	CART_TIMEOUT = timedelta(minutes=5)

	def __init__(self, config_file):
		super().__init__()

		self.config_file = config_file

		# Full list of TLDs
		self.all_tlds = None

		# Domain filtering
		self.include_intl = False
		self.include_sld = False
		self.max_length = None
		self.max_renew = None
		self.max_order = None

		# Sorting
		self.sorting = Sorting.ALPHABETIC
		self.sort_ascending = True

		# Cart id
		self.cart_id = None
		self.cart_time = None
		self.cart_lock = Lock()

		# Status querying
		self.data_lock = Lock()
		self.print_lock = Lock()
		self.domain_info = None
		self.failed_domains = 0
		self.check_aborted = False

	def load_config(self):
		if self.config_file is not None:
			parser = configparser.ConfigParser()
			parser.read(self.config_file)

			if parser.has_section('filter'):
				self.include_intl = parser.getboolean('filter', 'include_intl', fallback=self.include_intl)
				self.include_sld = parser.getboolean('filter', 'include_sld', fallback=self.include_sld)
				self.max_length = parser.getint('filter', 'max_length', fallback=self.max_length)
				self.max_renew = parser.getfloat('filter', 'max_renew', fallback=self.max_renew)
				self.max_order = parser.getfloat('filter', 'max_order', fallback=self.max_order)

			if parser.has_section('sorting'):
				if parser.has_option('sorting', 'sorting'):
					self.sorting = Sorting[parser.get('sorting', 'sorting')]
				self.sort_ascending = parser.getboolean('sorting', 'ascending', fallback=self.sort_ascending)

	def save_config(self):
		if self.config_file is not None:
			writer = configparser.ConfigParser()
			writer.add_section('filter')
			writer.set('filter', 'include_intl', str(self.include_intl))
			writer.set('filter', 'include_sld', str(self.include_sld))
			if self.max_length is not None:
				writer.set('filter', 'max_length', str(self.max_length))
			if self.max_renew is not None:
				writer.set('filter', 'max_renew', str(self.max_renew))
			if self.max_order is not None:
				writer.set('filter', 'max_order', str(self.max_order))

			writer.add_section('sorting')
			writer.set('sorting', 'sorting', self.sorting.name)
			writer.set('sorting', 'ascending', str(self.sort_ascending))

			try:
				with open(self.config_file, 'w') as f:
					writer.write(f)
			except Exception as e:
				print('Failed to save configuration', file=sys.stderr)

	def cmdloop(self, *args):
		while True:
			try:
				super().cmdloop(*args)
			except KeyboardInterrupt:
				print('', file=sys.stderr)
				pass

	# Do nothing on empty line
	def emptyline(self):
		return

	def default(self, arg):
		if arg == 'EOF':
			self.save_config()
			sys.exit(0)

		self.do_check(arg)

	def do_maxorder(self, arg):
		self._update_optional_number('max_order', int, 'Max order price', 0, arg)

	def do_maxrenew(self, arg):
		self._update_optional_number('max_renew', int, 'Max renew price', 0, arg)

	def do_maxlen(self, arg):
		self._update_optional_number('max_length', int, 'Max TLD length', 2, arg)

	def _update_optional_number(self, field, fieldType, fieldName, minValue, arg):
		arg = arg.strip().casefold()

		if arg != '':
			enabled = True
			try:
				enabled = self._parse_bool(arg)
			except:
				pass

			if enabled:
				try:
					value = fieldType(arg)
				except ValueError:
					print('Cannot parse "%s"' % arg, file=sys.stderr)
					return

				if value < minValue:
					print('%s may not be less than %s' % (fieldName, minValue), file=sys.stderr)
					return
			else:
				value = None

			self.__dict__[field] = value

		else:
			value = self.__dict__[field]

		if value is None:
			print('%s is disabled' % fieldName, file=sys.stderr)
		else:
			print('%s is %s' % (fieldName, str(value)), file=sys.stderr)

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
			self.sort_ascending = new_ascending

		print('Sorting by %s %s' % (self.sorting.name.lower(), 'ascending' if self.sort_ascending else 'descending'))

	def do_updatetld(self, arg):
		self._fetch_tlds()

	def _fetch_tlds(self):
		print('Fetching TLD list... ', file=sys.stderr, end='', flush=True)

		try:
			page = lxml.html.fromstring(requests.get('https://www.ovh.es/dominios/precios/').content)
			tlds = []
			for extensionTr in page.xpath("//table[@id='dataTable']/tbody/tr"):
				tldTd, buyTd, renewTd = extensionTr.findall("td")[:3]
				tldName = tldTd.find("a").text_content().strip().strip('.').lower()
				buyPrice = float(buyTd.attrib['data-order'])
				renewPrice = float(renewTd.attrib['data-order'])

				tlds.append(DomainInfo(tldName, buyPrice, renewPrice))

			tlds.sort(key=lambda x: x.name)
			print('got %d' % len(tlds), file=sys.stderr)

			self.all_tlds = tlds
			return True
		except Exception as e:
			print('cannot fetch', file=sys.stderr)
			traceback.print_last()
			return False

	def do_tld(self, arg):
		self.do_tlds(None)

	def do_tlds(self, arg):
		tlds = self._get_valid_tlds()
		if tlds:
			self._sort_domain_list(tlds)
			self._print_domain_header()
			for tld in tlds:
				self._print_domain_entry(tld)

	def _get_valid_tlds(self):
		if self.all_tlds is None:
			if not self._fetch_tlds():
				return None

		return [tld for tld in self.all_tlds if self._tld_valid(tld)]

	def _tld_valid(self, tld):
		if not self.include_sld and '.' in tld.name:
			return False

		if not self.include_intl and re.search(r'[^a-z.]', tld.name):
			return False

		if self.max_length is not None and len(tld.name) > self.max_length:
			return False

		if self.max_order is not None and tld.order > self.max_order:
			return False

		if self.max_renew is not None and tld.renew > self.max_renew:
			return False

		return True

	def do_hack(self, arg):
		names = arg.split()
		if len(names) == 0:
			print('At least one argument should be provided', file=sys.stderr)
			return

		to_check = self._domain_hack_list(names)
		if to_check is not None:
			print(', '.join(to_check))

	def _domain_hack_list(self, names):
		valid_tlds = self._get_valid_tlds()
		if not valid_tlds:
			print('Unable to get valid TLDs', file=sys.stderr)
			return None

		to_check = set()
		for tld in valid_tlds:
			tldend = tld.name.replace('.', '')
			for name in names:
				name = name.casefold()
				if len(name) > len(tldend) and name.endswith(tldend):
					to_check.add('%s.%s' % (name[:-len(tldend)], tld.name))

		return sorted(to_check)

	def do_check(self, arg):
		to_check = self._domain_check_list(arg.split())
		if not to_check:
			return

		self._check_list(to_check)

	def do_hackcheck(self, arg):
		to_check = self._domain_hack_list(arg.split())
		if not to_check:
			return

		self._check_list(to_check)

	def _check_list(self, to_check):
		# Reset variables
		if self.sorting == Sorting.ALPHABETIC:
			self._print_domain_header()
			self._run_domain_threads(self._check_and_update, to_check)
		else:
			self.domain_info = []
			self.failed_domains = 0

			self._run_domain_threads(self._check_and_update_sorted, to_check)

			if not self.check_aborted:
				self._sort_domain_list(self.domain_info)
				self._print_process()
				self._print_domain_header()
				for info in self.domain_info:
					self._print_domain_entry(info)

	def _run_domain_threads(self, func, to_check):
		self.check_aborted = False
		executor = ThreadPoolExecutor(max_workers=10)

		for domain in to_check:
			try:
				executor.submit(func, domain)
			except KeyboardInterrupt:
				print('Aborting, hold on...', file=sys.stderr)
				self.check_aborted = True
				break

		while True:
			try:
				executor.shutdown()
				break
			except KeyboardInterrupt:
				print('Aborting, hold on...', file=sys.stderr)
				self.check_aborted = True
				pass

	def _print_process(self, line=None):
		print(DomainCmd.RESETLINE, file=sys.stderr, end='', flush=True)
		if line:
			print(line, file=sys.stderr, end='', flush=True)

	def _print_domain_header(self):
		print('domain\trenew\torder')

	def _print_domain_entry(self, info):
		print('%s\t%.2f\t%.2f' % (info.name, info.renew, info.order))

	def _domain_check_list(self, domains):
		to_check = set()
		valid_tlds = None
		for domain in domains:
			encoded = domain.encode('idna').decode('ascii').lower()

			if not re.match(r'([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)*[a-z0-9]([a-z0-9-]*[a-z0-9])?$', encoded):
				print('Invalid domain "%s"' % domain, file=sys.stderr)
				continue

			if '.' in encoded:
				to_check.add(encoded)
				continue

			if valid_tlds is None:
				valid_tlds = self._get_valid_tlds()
				if valid_tlds is None:
					print('Cannot check %s' % domain, file=sys.stderr)
					return None

			for tld in valid_tlds:
				to_check.add('%s.%s' % (encoded, tld.name))

		return sorted(to_check)

	def _check_and_update(self, domain):
		if self.check_aborted:
			return

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
		if self.check_aborted:
			return

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
		if not self._refresh_cart_id():
			return None

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
		if not info['orderable'] or info['action'] != 'create':
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

	def _refresh_cart_id(self):
		if self.cart_time is None or datetime.utcnow() - self.cart_time >= DomainCmd.CART_TIMEOUT:
			with self.cart_lock:
				if self.cart_time is None or datetime.utcnow() - self.cart_time >= DomainCmd.CART_TIMEOUT:
					return self._fetch_cart_id()

		return True

	def _fetch_cart_id(self):
		cart_id_response = requests.post('https://www.ovh.es/engine/apiv6/order/cart', json={'description': '_ovhcom_legacy_order_cart_', 'ovhSubsidiary': 'ES'})
		try:
			cart_id_response.raise_for_status()
		except Exception as e:
			print('Could not get cart ID', file=sys.stderr)
			traceback.print_last()
			return False

		self.cart_id = cart_id_response.json()['cartId']
		self.cart_time = datetime.utcnow()

		return True

	def _sort_domain_list(self, domains):
		if self.sorting == Sorting.ALPHABETIC:
			func = lambda x: x.name
		elif self.sorting == Sorting.PRICE:
			func = lambda x: max(x.renew, x.order)
		elif self.sorting == Sorting.RENEW:
			func = lambda x: x.renew
		elif self.sorting == Sorting.ORDER:
			func = lambda x: x.order
		else:
			raise Exception('What the fuck %s' % str(self.sorting))

		domains.sort(key=func)
		if not self.sort_ascending:
			domains.reverse()

	def do_exit(self, arg):
		self.save_config()
		sys.exit(0)

	def do_quit(self, arg):
		self.save_config()
		sys.exit(0)

if __name__ == '__main__':
	parser = argparse.ArgumentParser(description='Queries domain status using OVH\'s API.')
	parser.add_argument('--noconfig', help='Disables user configuration', action='store_true')
	args = parser.parse_args()

	configFile = None
	if not args.noconfig:
		if os.name == 'nt':
			configFile = os.path.join(os.getenv('APPDATA'), 'ovhdomainquery.ini')
		else:
			configFile = os.path.expanduser('~/.ovhdomainquery.ini')

	cmd = DomainCmd(configFile)
	cmd.load_config()
	cmd.cmdloop()
