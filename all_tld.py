#!/usr/bin/env python3

import requests
import sys

if len(sys.argv) != 2:
	print('Usage: %s <domain>' % sys.argv[0], file=sys.stderr)
	sys.exit(2)

wanted_domain = sys.argv[1].encode('idna').decode('ascii')
if wanted_domain == '':
	print('Domain cannot be empty', file=sys.stderr)
	sys.exit(1)

print('Fetching TLD list... ', file=sys.stderr, end='', flush=True)
tlds = requests.get('https://www.ovh.es/engine/apiv6/domain/data/extension?country=ES').json()
print('got %d' % len(tlds), file=sys.stderr)

for tld in tlds:
	if '.' in tld:
		continue

	domain = '%s.%s' % (wanted_domain, tld)
	print(domain)
