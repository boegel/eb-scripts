#!/usr/bin/env python
##
# Copyright 2014 Ghent University
#
# This file is part of EasyBuild,
# originally created by the HPC team of the University of Ghent (http://ugent.be/hpc).
#
# http://github.com/easybuilders/easybuild
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
import pickle
import datetime
import re
import socket
import sys
import time

if sys.version_info[0] < 3:
    sys.stderr.write("Use Python 3!\n")
    sys.exit(1)

try:
    import pandas as pd
    import matplotlib
    matplotlib.use('PDF')  # must be done before next matplotlib import
    import matplotlib.pyplot as plt
except ImportError:
    pass

from easybuild.base import fancylogger
from easybuild.base.generaloption import simple_option
from easybuild.base.rest import RestClient

from easybuild.tools.build_log import EasyBuildError  # required to obtain an EasyBuild logger
from easybuild.tools.github import GITHUB_API_URL, GITHUB_MAX_PER_PAGE, fetch_github_token
from easybuild.tools.utilities import nub

from vsc.utils.dateandtime import date_parser, datetime_parser

log = fancylogger.getLogger()

GROUPS = [
    datetime.timedelta(7, 0, 0),  # 1 week
    datetime.timedelta(14, 0, 0),  # 2 weeks
    datetime.timedelta(30, 0, 0),  # ~1 month
    datetime.timedelta(60, 0, 0),  # ~2 months
    datetime.timedelta(180, 0, 0),  # ~6 months
]
ENDGROUP = datetime.timedelta(10**6, 0, 0)  # very large
GROUP_LABELS = ['<1w', '1-2w', '2w-1m', '1-2m', '2-6m', '>6m']
LAST_MONTH = datetime_parser('TODAY') - datetime.timedelta(30, 0, 0)
LAST_YEAR = datetime_parser('TODAY') - datetime.timedelta(365, 0, 0)
PICKLE_FILE = '%s_prs.dat'
ONE_DAY = datetime.timedelta(1, 0, 0)

HTML_FILE = '%s_pr_overview.html'
HTML_HEADER = """
<html>
  <head>
    <script type="text/javascript" src="https://www.google.com/jsapi"></script>
    <script type="text/javascript">
      google.load("visualization", "1", {packages:["table"]});
      google.setOnLoadCallback(drawTable);
      function drawTable() {
        var data = new google.visualization.DataTable();
"""
HTML_FOOTER = """
        var table = new google.visualization.Table(document.getElementById('table_div'));
        var red_or_green = new google.visualization.ColorFormat();
        red_or_green.addRange(null, 0, 'red', 'white');
        red_or_green.addRange(1, null, 'green', 'white');
        red_or_green.format(data, 5);
        red_or_green.format(data, 6);
        red_or_green.format(data, 7);
        var red_green_gradient = new google.visualization.ColorFormat();
        red_green_gradient.addGradientRange(-3, 4, 'black', '#DD0000', '#00BB00');
        red_green_gradient.format(data, 8);
        table.draw(data, {allowHtml: true, showRowNumber: false, sortColumn: 0});
      }
    </script>
  </head>
  <body>
    <p align='center'>
    Overview of <strong>open</strong> %(repo)s pull requests (%(pr_cnt)s):<br>
    <i>(tip: columns are sortable)</i><br>
    [last update: %(timestamp)s (UTC) - <strong>%(merged_today)s PRs merged today</strong>]<br>
    </p>
    <p align='center'>
        <div id="table_div" align='center'></div>
    </p>
  </body>
</html>
"""

TEST_VALUES_MAP = {
    'error': -1,
    'failure': -1,
    'pending': 0,
    'success': 1,
}
TEST_RESULTS = [
    "{v: -1, f: 'FAIL'}",
    "{v:  0, f: '???'}",
    "{v:  1, f: 'OK'}",
]

DRY_RUN = False
MERGE_USER = 'boegel'


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
            gh_repo = github.repos[github_account][repository]
            status, status_data = gh_repo.commits[sha].status.get()
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

    except socket.gaierror as err:
        raise EasyBuildError("Failed to download PR #%s: %s", pr, err)

    return pr_data


def in_range(pr_nr, pr_range):
    """Check whether specified PR number is within specified range."""
    if pr_range:
        pr_nr = int(pr_nr)
        pr_range_low = int(pr_range[0])
        pr_range_high = int(pr_range[1])
        res = pr_range_low <= pr_nr <= pr_range_high
    else:
        res = True

    return res


def fetch_prs_data(pickle_file, github, github_account, repository, msg, pr_range=None, update=False, since=None):
    """Fetch data for all PRs."""
    pr_range_low, pr_range_high = None, None
    if pr_range:
        pr_range_low = int(pr_range[0])
        pr_range_high = int(pr_range[1])

    # check format of specified timestamp, add time if needed
    if since:
        since_regex = re.compile('^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
        if not since_regex.match(since):
            raise EasyBuildError("Incorrect format for --since value: %s (does not match pattern '%s')",
                                 since, since_regex.pattern)

        since = '%sT23:59:59' % since

    if pickle_file:
        print("Loading PR data from %s" % pickle_file)
        prs_data = pickle.load(open(pickle_file, 'rb'))
        pr_nrs = [pr['number'] for pr in prs_data]
    else:
        prs_data, pr_nrs = [], []

    if prs_data and not update:
        # early return if no range is specified and PR data was read from file
        return prs_data

    # determine which PRs to fetch data for; either:
    # * PRs in specified range
    # * all PRs
    # * none
    if pr_range_low is not None and pr_range_high is not None:
        pr_cnt = pr_range_high - pr_range_low + 1
        # determine creation date of oldest PR
        pr_data = fetch_pr_data(github, github_account, repository, pr_range_low)

        if since is None:
            since = pr_data['created_at']
        else:
            raise EasyBuildError("Should not specify both --range and --since!")
    else:
        try:
            gh_repo = github.repos[github_account][repository]
            status, prs = gh_repo.pulls.get(per_page=GITHUB_MAX_PER_PAGE)
        except socket.gaierror as err:
            raise EasyBuildError("Failed to download list of pull requests: %s" % err)
        log.debug("status: %d, prs: %s" % (status, prs))
        pr_cnt = max([pr['number'] for pr in prs])

        if since is None:
            since = '1970-01-01T00:00:01Z'

    # check all issues in chunks of GITHUB_MAX_PER_PAGE, filter out PRs
    last_issue_nr = pr_range_low or 1
    max_pr = pr_range_high or pr_cnt
    while last_issue_nr < max_pr:
        sys.stdout.write("\n%s %s/%s (since %s)\n\n" % (msg, last_issue_nr, max_pr, since))
        sys.stdout.flush()  # flush so progress is show with 'tee' too

        ok = False
        sleep_secs = 0
        while not ok:
            try:
                gh_repo = github.repos[github_account][repository]
                status, issues_data = gh_repo.issues.get(since=since, per_page=GITHUB_MAX_PER_PAGE,
                                                         state='all', sort='updated', direction='asc')
                ok = True
            except socket.gaierror as err:
                raise EasyBuildError("Failed to download issues since %s: %s", since, err)
            except Exception as err:
                print("Ignoring exception: %s" % err)
                sleep_secs += 1
                print("Sleeping for %d seconds..." % sleep_secs)
                time.sleep(sleep_secs)

        log.debug("status: %d, issues data since %s: %s", status, since, issues_data)

        for issue in issues_data:
            if 'pull_request' in issue and (update or issue['number'] not in pr_nrs):
                sys.stdout.write("* PR #%s" % issue['number'])

                if not in_range(issue['number'], pr_range):
                    sys.stdout.write(' [out-of-range], ')
                    continue

                if issue['state'] == 'open':
                    sys.stdout.write(" [open]")
                    pr_data = fetch_pr_data(github, github_account, repository, issue['number'])
                else:
                    # for closed PRs, just issue data suffices
                    pr_data = issue
                    gh_repo = github.repos[github_account][repository]
                    status, more_pr_data = gh_repo.pulls[issue['number']].get()
                    pr_data['is_merged'] = more_pr_data['merged']
                    if pr_data['is_merged']:
                        pr_data['merged_by'] = more_pr_data['merged_by']
                        sys.stdout.write(' [MERGED], ')
                    else:
                        sys.stdout.write(' [closed], ')

                sys.stdout.flush()

                pr_nr = pr_data['number']
                if pr_nr in pr_nrs:
                    for idx in range(len(prs_data)):
                        if prs_data[idx]['number'] == pr_nr:
                            prs_data[idx] = pr_data
                            break
                    if idx == len(prs_data):
                        sys.stderr.write("Failed to replace PR data for PR #%s!" % pr_nr)
                        sys.exit(1)
                else:
                    prs_data.append(pr_data)
                    pr_nrs.append(pr_data['number'])

            elif issue['number'] in pr_nrs:
                sys.stdout.write("* known PR #%s, " % issue['number'])
            else:
                sys.stdout.write("* issue #%s [IGNORED], " % issue['number'])

        sys.stdout.write('\n')
        # update last issue nr and since timestamp
        sorted_issues = sorted([(issue['updated_at'], issue['number']) for issue in issues_data])
        last_since = since
        if sorted_issues:
            last_issue_nr = max(last_issue_nr, max(x[1] for x in sorted_issues))
            res = [issue for issue in issues_data if issue['number'] == last_issue_nr]
            if res:
                since = res[0]['updated_at']
            else:
                last_issue_nr = max(x[1] for x in sorted_issues)
                since = [issue for issue in issues_data if issue['number'] == last_issue_nr][0]['updated_at']
        else:
            since = last_since

        if last_since == since:
            if isinstance(since, str):
                since = datetime.datetime.strptime(since)
            since = since + datetime.timedelta(hours=1)
            print("new since: ", since)

    print("%s DONE!" % msg)
    pickle_file = PICKLE_FILE % repository
    pickle.dump(prs_data, open(pickle_file, 'wb'))
    print("PR data dumped to %s" % pickle_file)

    return prs_data


def pr_overview(prs_data, go):
    """Create overview of PRs using supplied data, print to stdout"""
    downloading_msg = "Downloading PR data for %s/%s repo..." % (go.options.github_account, go.options.repository)
    print(downloading_msg)

    print("Composing overview...")
    by_user = {}
    total_open_cnt = 0
    print([pr_data for pr_data in prs_data if pr_data['state'] == 'open'][0])
    for pr_data in prs_data:
        user = pr_data['user']['login']
        if user not in by_user:
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
            by_user[user][-1]['combined_status'] = pr_data['combined_status']
            total_open_cnt += 1

    tup = (len(prs_data), total_open_cnt, len(by_user))
    print("Overview of %s pull requests (%s open), by user (%s users in total):\n" % tup)
    cnts_by_user = [len([pr for pr in by_user[u] if pr['state'] == 'open']) for u in by_user]
    sorted_users = [u for (_, u) in sorted(zip(cnts_by_user, by_user.keys()))]
    for user in sorted_users:
        open_cnt = len([pr for pr in by_user[user] if pr['state'] == 'open'])
        if open_cnt:
            print("%s (open: %s/%s):" % (user, open_cnt, len(by_user[user])))
            for pr in [pr for (_, pr) in sorted((pr['number'], pr) for pr in by_user[user])]:
                if pr['state'] == 'open':
                    nr = pr['number']
                    state = pr['combined_status']
                    print("\t#%s [state: %s]: %s (created %s)" % (nr, state, pr['title'], pr['created']))


def plot_pr_stats(prs, go):
    """ Create plot overview of prs, save in pdf format"""
    created_ats = [datetime_parser(pr['created_at'].split('T')[0]) for pr in prs]
    closed_ats = [datetime_parser((pr['closed_at'] or 'T').split('T')[0] or 'ENDNEXTMONTH') for pr in prs]
    authors = [pr['user']['login'] for pr in prs]

    print('Plotting...')
    plot_historic_PR_ages(created_ats, closed_ats, go.options.repository)
    plot_open_closed_PRs(created_ats, closed_ats, go.options.repository)
    plot_prs_by_author(created_ats, authors, go.options.repository)
    print_prs_uniq_authors(created_ats, authors, go.options.repository)
    plot_prs_merged(created_ats, prs, go.options.repository)


def plot_historic_PR_ages(created_ats, closed_ats, repository):
    """Plot historic PR ages."""
    day = min(created_ats)
    days = []
    ages, ages_all, ages_rev = [], [], []
    while day <= datetime_parser('TODAY') + ONE_DAY:
        days.append(day)

        open_counts = [0]*(len(GROUPS)+1)
        for idx in range(0, len(created_ats)):
            if created_ats[idx] <= day and closed_ats[idx] > day:
                for i, grp in enumerate(GROUPS + [ENDGROUP]):
                    if day - created_ats[idx] < grp:
                        open_counts[i] += 1
                        break
        ages.append(open_counts[::-1])
        ages_rev.append(open_counts[:])
        open_counts.append(sum(open_counts))
        ages_all.append(open_counts[::-1])

        day += ONE_DAY

    print("%s: open = %s" % (days[-1], open_counts[-1]))

    days = pd.to_datetime(days)

    pd_df_all = pd.DataFrame(ages_all, days, columns=['all']+GROUP_LABELS[::-1]).sort_index().fillna(method='ffill')
    res = pd_df_all.plot(kind='area', stacked=False, title="open %s PRs, by age" % repository)
    res.legend(ncol=len(GROUP_LABELS)+1, fontsize='small')
    plt.savefig('%s_PR_stats_all' % repository)

    res = pd_df_all.plot(kind='area', stacked=False, title="open %s PRs, by age (zoomed)" % repository)
    res.set_ylim([0, 50])
    res.legend(ncol=len(GROUP_LABELS)+1, fontsize='small')
    plt.savefig('%s_PR_stats_all_zoomed' % repository)

    pd_df = pd.DataFrame(ages, days, columns=GROUP_LABELS[::-1]).sort_index().fillna(method='ffill')
    res = pd_df.plot(kind='area', stacked=True, title="open %s PRs, by age (stacked)" % repository)
    res.legend(ncol=len(GROUP_LABELS), fontsize='small')
    plt.savefig('%s_PR_stats_all_stacked' % repository)

    pd_df_all_year = pd_df_all.loc[LAST_YEAR:]
    res = pd_df_all_year.plot(kind='area', stacked=False, title="open %s PRs, by age (last year)" % repository)
    res.legend(ncol=len(GROUP_LABELS)+1, fontsize='small')
    plt.savefig('%s_PR_stats_year' % repository)

    pd_df_all_month = pd_df_all.loc[LAST_MONTH:]
    res = pd_df_all_month.plot(kind='area', stacked=False, title="open %s PRs, by age (last month)" % repository)
    res.legend(ncol=len(GROUP_LABELS)+1, fontsize='small')
    plt.savefig('%s_PR_stats_month' % repository)

    pd_df = pd.DataFrame(ages_rev, days, columns=GROUP_LABELS).sort_index().fillna(method='ffill')
    res = pd_df.plot(kind='area', stacked=True, title="open %s PRs, by age (stacked)" % repository)
    res.legend(ncol=len(GROUP_LABELS), fontsize='small')
    plt.savefig('%s_PR_stats_all_stacked_rev' % repository)

    res = pd_df.plot(kind='area', stacked=True, title="open %s PRs, by age (stacked, zoomed)" % repository)
    res.set_ylim([0, 50])
    res.legend(ncol=len(GROUP_LABELS), fontsize='small')
    plt.savefig('%s_PR_stats_all_stacked_rev_zoomed' % repository)


def gen_pr_overview_page(prs, go):
    """ Create pr overview html page """
    html_file = HTML_FILE % go.options.repository
    print("Generating %s..." % html_file)
    handle = open(html_file, 'w')
    handle.write(HTML_HEADER)
    handle.write(gen_table_header())
    pr_cnt, table_rows, merged_today, last_update = gen_table_rows(prs)
    handle.write(table_rows)
    handle.write(HTML_FOOTER % {
        'merged_today': merged_today,
        'pr_cnt': pr_cnt,
        'repo': '%s/%s' % (go.options.github_account, go.options.repository),
        'timestamp': last_update,  # datetime.now().strftime(format='%d %B %Y %H:%M:%S'),
    })
    handle.close()


def plot_open_closed_PRs(created_ats, closed_ats, repository):
    """Plot open/closed PRs (in total) per day."""
    opened_closed = []
    days = []
    day = min(created_ats)
    while day <= datetime_parser('TODAY'):
        days.append(day)

        opened = sum([d.date() == day.date() for d in created_ats])
        closed = sum([d.date() == day.date() for d in closed_ats])
        open_cnt = opened - closed
        opened_closed.append((open_cnt, opened, closed))

        day += ONE_DAY

    pd_df = pd.DataFrame(opened_closed, days, columns=['open', 'opened', 'closed']).sort_index()
    res = pd_df.plot(kind='bar', title="open/opened/closed PRs")
    res.legend(loc='upper left', ncol=3, fontsize='small')
    plt.savefig('%s_opened_closed_PRs' % repository)

    pd_df_month = pd_df.loc[LAST_MONTH:]
    res = pd_df_month.plot(kind='bar', title="open/opened/closed PRs (last month)")
    res.legend(loc='upper left', ncol=3, fontsize='small')
    plt.savefig('%s_opened_closed_PRs_month' % repository)

    pd_df_total = pd_df.cumsum()
    res = pd_df_total.plot(kind='line', title="total open/opened/closed %s PRs" % repository)
    res.legend(loc='upper left', ncol=3, fontsize='small')
    plt.savefig('%s_opened_closed_PRs_cumulative' % repository)

    pd_df_total_year = pd_df_total.loc[LAST_YEAR:]
    res = pd_df_total_year.plot(kind='line', title="total open/opened/closed %s PRs (last year)" % repository)
    res.legend(loc='upper left', ncol=3, fontsize='small')
    plt.savefig('%s_opened_closed_PRs_cumulative_year' % repository)

    pd_df_total_month = pd_df_total.loc[LAST_MONTH:]
    res = pd_df_total_month.plot(kind='line', title="total open/opened/closed %s PRs (last month)" % repository)
    res.legend(loc='upper left', ncol=3, fontsize='small')
    plt.savefig('%s_opened_closed_PRs_cumulative_month' % repository)


def plot_prs_by_author(created_ats, authors, repository):
    """Plot overview of PR authors."""
    day = min(created_ats)
    days = []

    uniq_authors = nub(authors)
    print("Found %d unique PR authors for %s repository" % (len(uniq_authors), repository))
    author_counts = []

    while day <= datetime_parser('TODAY'):
        days.append(day)

        counts = [0] * len(uniq_authors)
        for idx in range(0, len(created_ats)):
            if created_ats[idx] <= day:
                for i, author in enumerate(uniq_authors):
                    if authors[idx] == author:
                        counts[i] += 1
                        break

        author_counts.append(counts)

        day += ONE_DAY

    # filter author counts, only show top 10 author, collapse remaining authors into 'other'
    sorted_author_counts = sorted(enumerate(author_counts[-1]), key=lambda x: x[1], reverse=True)
    top_idxs = [idx for (idx, _) in sorted_author_counts[:30]]
    other_idxs = [idx for idx in range(len(uniq_authors)) if idx not in top_idxs]

    plot_author_counts = []
    for day_counts in author_counts:
        plot_day_counts = []

        for idx in top_idxs[::-1]:
            plot_day_counts.append(day_counts[idx])

        other_day_count = 0
        for idx in other_idxs:
            other_day_count += day_counts[idx]
        plot_day_counts.append(other_day_count)

        plot_author_counts.append(plot_day_counts)

    plot_authors = []
    for idx in top_idxs[::-1]:
        plot_authors.append('%s (%d)' % (uniq_authors[idx], author_counts[-1][idx]))
    plot_authors.append('OTHER (%d)' % plot_author_counts[-1][-1])

    pd_df = pd.DataFrame(plot_author_counts, days, columns=plot_authors).sort_index().fillna(method='ffill')
    res = pd_df.plot(kind='area', stacked=True, title="%s PRs by author (stacked)" % repository)
    res.legend(ncol=2, fontsize='small', loc='best')
    plt.savefig('%s_PR_per_author_stacked' % repository)


def print_prs_uniq_authors(created_ats, authors, repository):

    known_authors = set()
    for pr_date, pr_author in zip(created_ats, authors):
        if pr_author not in known_authors:
            known_authors.add(pr_author)
            print('%s\t%s\t%s' % (pr_date, pr_author, len(known_authors)))


def plot_prs_merged(created_ats, prs, repository):
    """Plot stats on merged PRs."""
    init_year = min(created_ats).year
    curr_year = datetime_parser('TODAY').year

    start_end_years = {}
    for year in range(init_year, curr_year + 1):
        start_year = datetime_parser('%s-01-01' % year)
        end_year = datetime_parser('%s-12-31 23:59:59' % year)
        start_end_years[year] = (start_year, end_year)
        print((year, len([x for x in created_ats if x >= start_year and x <= end_year])))

    prs_by_year = {}
    for (created_at, pr) in zip(created_ats, prs):
        for year, (start_year, end_year) in start_end_years.items():
            if created_at >= start_year and created_at <= end_year:
                prs_by_year.setdefault(year, []).append(pr)

    gh_logins = ['boegel', 'verdurin', 'pescobar', 'vanzod', 'wpoely86', 'JensTimmerman', 'migueldiascosta']
    maintainers = ['boegel', 'verdurin', 'pescobar', 'vanzod', 'wpoely86', 'migueldiascosta', 'akesandgren',
                   'BartOldeman', 'damianam', 'ocaisa', 'Micket', 'zao', 'smoors', 'lexming',
                   'casparvl', 'branfosj']
    # (+ wpoely86 in 2016...)
    hpcugent = ['JensTimmerman', 'Caylo', 'stdweird', 'itkovian', 'piojo', 'hpcugent', 'nudded', 'boegel']
    gh_logins_bis = gh_logins + ['hajgato', 'fgeorgatos', 'RvDijk', 'JackPerdue', 'smoors', 'geimer', 'SimonPinches']
    gh_logins_bis += ['Helios07', 'cstackpole', 'akesandgren', 'rubendibattista', 'BartOldeman', 'damianam', 'ocaisa',
                      'stdweird', 'nudded', 'piojo', 'Caylo', 'hpcugent', 'Darkless012', 'zarybnicky', 'deniskristak']

    for year in sorted(start_end_years.keys()):
        prs_cnt = len(prs_by_year[year])
        prs_cnt_maintainers = len([pr for pr in prs_by_year[year] if pr['user']['login'] in maintainers])
        prs_cnt_hpcugent = len([pr for pr in prs_by_year[year] if pr['user']['login'] in hpcugent])
        prs_cnt_by = {}
        for gh_login in gh_logins_bis:
            prs_cnt_by[gh_login] = len([pr for pr in prs_by_year[year] if pr['user']['login'] == gh_login])

        merged_prs = [pr for pr in prs_by_year[year] if pr.get('is_merged', False)]
        merged_cnt = len(merged_prs)
        merged_cnt_by = {}
        for gh_login in gh_logins:
            merged_cnt_by[gh_login] = len([pr for pr in merged_prs if pr['merged_by']['login'] == gh_login])

        closed_cnt = len([pr for pr in prs_by_year[year] if pr['state'] == 'closed'])
        open_cnt = len([pr for pr in prs_by_year[year] if pr['state'] == 'open'])

        print('* %s: %s PRs' % (year, prs_cnt))
        print('* %s unique contributors' % len(nub(pr['user']['login'] for pr in prs_by_year[year])))
        print('* PRs by maintainers: %s' % prs_cnt_maintainers)
        print('* PRs by HPC-UGent: %s' % prs_cnt_hpcugent)
        for gh_login in gh_logins_bis:
            print('- PRs by %s: ' % gh_login, prs_cnt_by[gh_login])
        print('- merged: ', merged_cnt)
        for gh_login in gh_logins:
            print('- merged by %s: ' % gh_login, merged_cnt_by[gh_login])
        print('- closed: ', closed_cnt - merged_cnt)
        print('- open: ', open_cnt)
        new_pr_tag = "created using `eb --new-pr`"
        print('- opened with --new-pr: ', len([pr for pr in prs_by_year[year] if new_pr_tag in (pr['body'] or '')]))
        print('')


def gen_table_header():
    """Generate table header."""
    columns = [
        ('PR #', 'number'),
        ('user', 'string'),
        ('title', 'string'),
        ('age', 'number'),
        ('last update (UTC)', 'datetime'),
        ('unit tests', 'number'),
        ('style', 'number'),
        ('test report(s)', 'number'),
        ('status', 'number'),
        ('signed off', 'boolean'),
        ('# participants', 'number'),
        ('votes', 'number'),
    ]

    lines = []
    for label, typ in columns:
        lines.append("        data.addColumn('%s', '%s');" % (typ, label))

    return '\n'.join(lines) + '\n'


def gen_table_rows(prs):
    """Generate table rows."""
    col_tmpls = [
        "{v: %(number)s, f: '<a href=\"%(html_url)s\">#%(number)s</a>'}",
        "'%(user_login)s'",
        "'%(title)s'",
        "{v: %(age)s, f: '%(age)s days'}",
        "%(last_update)s",
        "%(unit_test)s",
        "%(style_check)s",
        "%(test_reports)s",
        "%(status)s",
        "%(signed_off)s",
        "%(participants)s",  # FIXME
        "0",  # FIXME
    ]
    row_tmpl = "            [" + ', '.join(col_tmpls) + "],"

    merged_today = 0
    todays_date = date_parser('TODAY')

    last_update = datetime_parser(prs[0]['updated_at'].replace('T', ' ')[:-1])
    lines = ['        data.addRows([']
    for pr in prs:

        if pr['closed_at']:
            if date_parser(pr['closed_at'].split('T')[0]) == todays_date:
                merged_today += 1

        # only consider open PRs
        if pr['state'] != 'open':
            continue

        # shorten long titles, escape single quotes
        if len(pr['title']) > 30:
            pr['title'] = pr['title'][:30] + '...'
        pr['title'] = pr['title'].replace("'", "\\'")

        # determine status based on results of unit tests, style check and test reports
        unit_test = TEST_VALUES_MAP[pr['combined_status']]

        # initial value: '???' (unknown)
        style_check = 0
        test_reports = 0
        signed_off = False
        # iterate over all comments, last hit wins
        for comment in pr['issue_comments']['bodies']:
            if 'lgtm' in comment.lower() or 'style review ok' in comment.lower():
                style_check = 1

            if comment.lower().startswith('test report'):
                if 'SUCCESS' in comment:
                    test_reports = 1
                elif 'FAILED' in comment:
                    test_reports = -1

            if 'good to go' in comment.lower():
                signed_off = True

        status = unit_test + style_check + test_reports

        participants = len(nub(pr['issue_comments']['users']))

        raw_updated_at = pr['updated_at'].replace('T', '@')[:-1].replace(':', '@').replace('-', '@')
        year, month, day, hour, minutes, seconds = raw_updated_at.split('@')
        pr.update({
            'age': (datetime_parser('TODAY') - datetime_parser(pr['created_at'].split('T')[0])).days,
            'last_update': "new Date(%s, %s, %s, %s, %s, %s)" % (year, month, day, hour, minutes, seconds),
            'participants': participants,
            'signed_off': ['', 'true'][signed_off],
            'status': status,
            'style_check': TEST_RESULTS[style_check+1],
            'test_reports': TEST_RESULTS[test_reports+1],
            'unit_test': TEST_RESULTS[unit_test+1],
            'user_login': pr['user']['login'],
        })

        lines.append(row_tmpl % pr)

        last_update = max(datetime_parser(pr['updated_at'].replace('T', ' ')[:-1]), last_update)

    lines.append('        ]);')

    return len(lines)-2, '\n'.join(lines), merged_today, last_update


def dump_data(prs, go):
    print('PR#,user,state,created,merged,new_pr')
    for pr in sorted(prs, key=(lambda x: x['number'])):
        created = pr['created_at'].replace('T', ' ').replace('Z', '')
        state = pr['state']
        is_merged = pr.get('is_merged')
        using_new_pr = "eb --new-pr" in (pr['body'] or '')
        print("%s,%s,%s,%s,%s,%s" % (pr['number'], pr['user']['login'], state, created, is_merged, using_new_pr))


def main():
    types = {
        'dump': dump_data,
        'html': gen_pr_overview_page,
        'plot': plot_pr_stats,
        'print': pr_overview,
    }

    opts = {
        'github-account': ("GitHub account where repository is located", None, 'store', 'easybuilders', 'a'),
        'github-user': ("GitHub user to use (for authenticated access)", None, 'store', 'boegel', 'u'),
        'range': ("Range for PRs to take into account", None, 'store', None, 'x'),
        'repository': ("Repository to use", None, 'store', 'easybuild-easyconfigs', 'r'),
        'since': ("Date to use to select range of issues for which to pull in data (e.g. 2019-10-24)",
                  None, 'store', None, 's'),
        'type': ("Type of overview: 'dump', 'plot', 'print', or 'html'", 'choice',
                 'store_or_None', 'print', list(types.keys()), 't'),
        'update': ("Update existing data", None, 'store_true', False),
    }

    go = simple_option(go_dict=opts, descr="Script to print overview of pull requests for a GitHub repository")

    github_account = go.options.github_account
    github_user = go.options.github_user

    pr_range = None
    if go.options.range:
        print(go.options.range)
        range_regex = re.compile('^[0-9]+-[0-9]+$')
        if range_regex.match(go.options.range):
            pr_range = go.options.range.split('-')
        else:
            sys.stderr.write("Range '%s' does not match pattern '%s'\n" % (go.options.range, range_regex.pattern))
            sys.exit(1)

    github_token = fetch_github_token(github_user)
    github = RestClient(GITHUB_API_URL, username=github_user, token=github_token, user_agent='eb-pr-overview')

    pickle_file = None
    if go.args:
        pickle_file = go.args[0]

    downloading_msg = "Downloading PR data for %s/%s repo..." % (github_account, go.options.repository)
    print(downloading_msg)
    prs = fetch_prs_data(pickle_file, github, github_account, go.options.repository, downloading_msg,
                         pr_range=pr_range, update=go.options.update, since=go.options.since)

    if go.options.type in types:
        types[go.options.type](prs, go)


if __name__ == '__main__':
    main()
