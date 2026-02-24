#!/usr/bin/env python3

import json
import pprint
import sys

path = sys.argv[1]
eb_version = sys.argv[2]

with open(path) as fp:
    prs = json.load(fp)

print(f"Found {len(prs)} PRs")

retained_prs = []
for pr in prs:
    milestone = pr['milestone']
    milestone = milestone['title'] if milestone else ''
    if eb_version in milestone:
        retained_prs.append(pr)

print(f"Retained {len(retained_prs)} PRs")

processed_prs = []
for group in ('bug fix', 'enhancement', 'change', 'code cleanup', 'new', 'update', None):
    print(f'\n{group}:\n')
    for pr in retained_prs[::-1]:
        labels = [x['name'] for x in pr['labels']]
        if (group in labels or group is None) and pr['number'] not in processed_prs:
            print(f"- {pr['title']} (#{pr['number']})")
            processed_prs.append(pr['number'])
