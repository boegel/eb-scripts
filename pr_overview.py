#!/usr/bin/env python
##
# Copyright 2014 Ghent University
#
# This file is part of EasyBuild,
# originally created by the HPC team of the University of Ghent (http://ugent.be/hpc).
#
# http://github.com/hpcugent/easybuild
#
# EasyBuild is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation v2.
#
# EasyBuild is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with EasyBuild.  If not, see <http://www.gnu.org/licenses/>.
##

"""
Print overview of pull requests for specified GitHub repository.

@author: Kenneth Hoste (Ghent University)
"""
import socket
import sys
from vsc.utils import fancylogger
from vsc.utils.generaloption import simple_option
from vsc.utils.rest import RestClient

from easybuild.tools.build_log import EasyBuildError  # required to obtain an EasyBuild logger
from easybuild.tools.github import GITHUB_API_URL, GITHUB_MAX_PER_PAGE, fetch_github_token


log = fancylogger.getLogger()

def fetch_pr_data(github, github_account, repository, pr):
    """Fetch data for a single PR."""
    pr_data = None
    try:
        gh_repo = github.repos[github_account][repository]
        status, pr_data = gh_repo.pulls[pr].get()
        sys.stdout.write("[data]")

        # enhance PR data with test result for last commit
        pr_data['unit_test_result'] = 'UNKNOWN'
        if 'head' in pr_data:
            sha = pr_data['head']['sha']
            try:
                gh_repo = github.repos[github_account][repository]
                status, status_data = gh_repo.commits[sha].status.get()
            except socket.gaierror, err:
                raise EasyBuildError("Failed to download commit status for PR %s: %s", pr, err)
            log.debug("status: %d, commit status data: %s", status, status_data)
            if status_data:
                pr_data['combined_status'] = status_data['state']
            sys.stdout.write("[status]")

        # also pull in issue comments (note: these do *not* include review comments or commit comments)
        gh_repo = github.repos[github_account][repository]
        status, comments_data = gh_repo.issues[pr].comments.get()
        pr_data['issue_comments'] = {
            'users': [c['user']['login'] for c in comments_data],
            'bodies': [c['body'] for c in comments_data],
        }
        sys.stdout.write("[comments], ")

    except socket.gaierror, err:
        raise EasyBuildError("Failed to download PR #%s: %s", pr, err)

    return pr_data

def fetch_prs_data(github, github_account, repository, msg):
    """Fetch data for all PRs."""
    try:
        gh_repo = github.repos[github_account][repository]
        status, prs = gh_repo.pulls.get(per_page=GITHUB_MAX_PER_PAGE)
    except socket.gaierror, err:
        raise EasyBuildError("Failed to download list of pull requests: %s" % err)
    log.debug("status: %d, prs: %s" % (status, prs))
    max_pr = max([pr['number'] for pr in prs])

    # check all issues in chunks of GITHUB_MAX_PER_PAGE, filter out PRs
    prs_data, pr_nrs = [], []
    since = '1970-01-01T00:00:01Z'
    last_issue_nr = 0
    while last_issue_nr < max_pr:
        sys.stdout.write("\n%s %s/%s (since %s)\n\n" % (msg, last_issue_nr, max_pr, since))
        sys.stdout.flush()  # flush so progress is show with 'tee' too

        try:
            gh_repo = github.repos[github_account][repository]
            status, issues_data = gh_repo.issues.get(since=since, per_page=GITHUB_MAX_PER_PAGE,
                                                     state='all', sort='updated', direction='asc')
        except socket.gaierror, err:
            raise EasyBuildError("Failed to download issues since %s: %s", since, err)

        log.debug("status: %d, issues data since %s: %s", status, since, issues_data)

        for issue in issues_data:
            if 'pull_request' in issue and not issue['number'] in pr_nrs:
                sys.stdout.write("* PR #%s" % issue['number'])
                if issue['state'] == 'open':
                    sys.stdout.write(" [open]")
                    pr_data = fetch_pr_data(github, github_account, repository, issue['number'])
                else:
                    # for closed PRs, just issue data suffices
                    pr_data = issue
                    sys.stdout.write(' [closed], ')

                sys.stdout.flush()
                prs_data.append(pr_data)
                pr_nrs.append(pr_data['number'])

        sys.stdout.write('\n')
        # update last issue nr and since timestamp
        last_issue_nr = sorted([issue['number'] for issue in issues_data])[-1]
        since = [issue for issue in issues_data if issue['number'] == last_issue_nr][0]['updated_at']

    print("%s DONE!" % msg)
    return prs_data

def create_pr_overview(prs_data, gh_repo):
    """Create overview of PRs using supplied data."""
    print("Composing overview...")
    by_user = {}
    total_open_cnt = 0
    total_cnt = 0
    print [pr_data for pr_data in prs_data if pr_data['state'] == 'open'][0]
    for pr_data in prs_data:
        user = pr_data['user']['login']
        if not user in by_user:
            by_user[user] = []
        by_user[user].append({
            'number': pr_data['number'],
            'created': pr_data['created_at'],
            'head': None,  # not always there (?)
            'state': pr_data['state'],
            'title': pr_data['title'],
            'url': pr_data['html_url'],
        })
        if 'head' in pr_data:
            by_user[user][-1]['head'] = pr_data['head']
            
        if pr_data['state'] == 'open':
            total_open_cnt += 1

    tup = (len(prs_data), total_open_cnt, len(by_user))
    print("Overview of %s pull requests (%s open), by user (%s users in total):\n" % tup)
    cnts_by_user = [len([pr for pr in by_user[user] if pr['state'] == 'open']) for user in by_user]
    sorted_users = [user for (_, user) in sorted(zip(cnts_by_user, by_user.keys()))]
    for user in sorted_users:
        open_cnt = len([pr for pr in by_user[user] if pr['state'] == 'open'])
        if open_cnt:
            print("%s (open: %s/%s):" % (user, open_cnt, len(by_user[user])))
            for pr in [pr for (_, pr) in sorted((pr['number'], pr) for pr in by_user[user])]:
                if pr['state'] == 'open':
                    nr = pr['number']
                    state = pr['combined_status']
                    print("\t#%s [state: %s]: %s (created %s)" % (nr, state, pr['title'], pr['created']))

def main():

    opts = {
        'github-account': ("GitHub account where repository is located", None, 'store', 'hpcugent', 'a'),
        'github-user': ("GitHub user to use (for authenticated access)", None, 'store', 'boegel', 'u'),
        'repository': ("Repository to use", None, 'store', 'easybuild-easyconfigs', 'r'),
    }
    go = simple_option(go_dict=opts, descr="Script to print overview of pull requests for a GitHub repository")

    github_token = fetch_github_token(go.options.github_user)
    github = RestClient(GITHUB_API_URL, username=go.options.github_user, token=github_token, user_agent='eb-pr-overview')

    downloading_msg = "Downloading PR data for %s/%s repo..." % (go.options.github_account, go.options.repository)
    print(downloading_msg)

    prs_data = fetch_prs_data(github, go.options.github_account, go.options.repository, downloading_msg)
    gh_repo = github.repos[go.options.github_account][go.options.repository]
    create_pr_overview(prs_data, gh_repo)


if __name__ == '__main__':
    main()
