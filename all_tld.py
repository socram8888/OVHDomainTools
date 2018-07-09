#!/usr/bin/env python3

import argparse
import re
import requests
import sys

LONG_HELP = """Fetches existing public TLDs from OVH.

If no domain is given, this script will output all public TLDs as-is. Otherwise, this script will IDNA-encode domains and append all possible TLDs to them.

This script may also optionally filter TLDs according to regular expressions. Valid and invalid filters are checked in order, and domains will be accepted/discarded (according to filter type) on the first match."""

parser = argparse.ArgumentParser(description=LONG_HELP, formatter_class=argparse.RawTextHelpFormatter)
parser.add_argument('domains', metavar='domain', help='domain list', nargs='*')
parser.add_argument('-m', '--match', metavar='regex', help='valid TLD regex', action='append', dest='filters', type=lambda x: (re.compile(x), True))
parser.add_argument('-n', '--no-match', metavar='regex', help='invalid TLD regex', action='append', dest='filters', type=lambda x: (re.compile(x), False))
parser.add_argument('-l', '--max-length', metavar='maxlength', help='filters out domains longer than this', type=int)
parser.add_argument('-s', '--include-sld', help='include second-level domains', action='store_const', const=True)
parser.add_argument('-i', '--include-intl', help='include internationalized TLDs', action='store_const', const=True)

args = parser.parse_args()

print('Fetching TLD list... ', file=sys.stderr, end='', flush=True)
tlds = requests.get('https://www.ovh.es/engine/apiv6/domain/data/extension?country=ES').json()
print('got %d' % len(tlds), file=sys.stderr)

def tld_valid(tld, args):
	if not args.include_sld and '.' in tld:
		return False

	if not args.include_intl and re.search(r'[^a-z]', tld):
		return False

	if args.max_length is not None and len(tld) > args.max_length:
		return False

	if args.filters is None:
		return True

	valid = True
	for regex, mustMatch in args.filters:
		matches = regex.search(tld) is not None
		if matches == mustMatch:
			return matches

		valid = matches
	return valid

validTLDs = []
for tld in tlds:
	tld = tld.encode('utf-8').decode('idna').lower()
	if tld_valid(tld, args):
		validTLDs.append(tld)

if len(args.domains) == 0:
	for tld in validTLDs:
		tld = tld.encode('idna').decode('ascii')
		print(tld)
else:
	for domain in args.domains:
		for tld in validTLDs:
			fqdn = '%s.%s' % (domain, tld)
			fqdn = fqdn.encode('idna').decode('ascii')
			print(fqdn)
