#!/bin/bash

ebver=eb521

for repo in framework easyblocks easyconfigs; do
    out="${repo}-merged-prs-${ebver}.json"
    full_repo="easybuilders/easybuild-${repo}"
    echo "Collecting overview of merged PRs for ${full_repo}... (see ${out})"
    gh pr list --repo ${full_repo} --limit 10000 --state merged --json number,title,labels,milestone > ${out}
done
ls -lrt *-merged-prs-${ebver}.json
