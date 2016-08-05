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
import cPickle
import datetime
import matplotlib
matplotlib.use('PDF')  # must be done before next matplotlib import
import matplotlib.pyplot as plt
import pandas as pd
import socket
import sys

from vsc.utils import fancylogger
from vsc.utils.dateandtime import date_parser, datetime_parser
from vsc.utils.generaloption import simple_option
from vsc.utils.missing import nub
from vsc.utils.rest import RestClient

from easybuild.tools.build_log import EasyBuildError  # required to obtain an EasyBuild logger
from easybuild.tools.github import GITHUB_API_URL, GITHUB_MAX_PER_PAGE, fetch_github_token, post_comment_in_issue
from easybuild.tools.ordereddict import OrderedDict
from easybuild.tools.run import run_cmd

log = fancylogger.getLogger()

GROUPS = [
    datetime.timedelta(7,0,0),  # 1 week
    datetime.timedelta(14,0,0),  # 2 weeks
    datetime.timedelta(30,0,0),  # ~1 month
    datetime.timedelta(60,0,0),  # ~2 months
    datetime.timedelta(180,0,0),  # ~6 months
]
ENDGROUP = datetime.timedelta(10**6, 0, 0) # very large
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


def fetch_prs_data(pickle_file, github, github_account, repository, msg):
    """Fetch data for all PRs."""
    if pickle_file:
        print("Loading PR data from %s" % pickle_file)
        prs_data = cPickle.load(open(pickle_file, 'r'))

    else:
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
    pickle_file = PICKLE_FILE % repository
    cPickle.dump(prs_data, open(pickle_file, 'w'))
    print("PR data dumped to %s" % pickle_file)

    return prs_data


def pr_overview(prs_data, go):
    """Create overview of PRs using supplied data, print to stdout"""
    downloading_msg = "Downloading PR data for %s/%s repo..." % (go.options.github_account, go.options.repository)
    print(downloading_msg)

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
            by_user[user][-1]['combined_status'] = pr_data['combined_status']
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


def plot_pr_stats(prs, go):
    """ Create plot overview of prs, save in pdf format"""
    created_ats = [datetime_parser(pr['created_at'].split('T')[0]) for pr in prs]
    closed_ats = [datetime_parser((pr['closed_at'] or 'T').split('T')[0] or 'ENDNEXTMONTH') for pr in prs]

    print('Plotting...')
    plot_historic_PR_ages(created_ats, closed_ats, go.options.repository)
    plot_open_closed_PRs(created_ats, closed_ats, go.options.repository)


def plot_historic_PR_ages(created_ats, closed_ats, repository):
    """Plot historic PR ages."""
    day = min(created_ats)
    days = []
    ages, ages_all, ages_rev = [], [], []
    while day <= datetime_parser('TODAY'):
        days.append(day)

        open_counts = [0]*(len(GROUPS)+1)
        for idx in xrange(0, len(created_ats)):
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

    pd_df_all_year = pd_df_all.select(lambda d: d > LAST_YEAR)
    res = pd_df_all_year.plot(kind='area', stacked=False, title="open %s PRs, by age (last year)" % repository)
    res.legend(ncol=len(GROUP_LABELS)+1, fontsize='small')
    plt.savefig('%s_PR_stats_year' % repository)

    pd_df_all_month = pd_df_all.select(lambda d: d > LAST_MONTH)
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
        'timestamp': last_update, #datetime.now().strftime(format='%d %B %Y %H:%M:%S'),
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

    pd_df_month = pd_df.select(lambda d: d > LAST_MONTH)
    res = pd_df_month.plot(kind='bar', title="open/opened/closed PRs (last month)")
    res.legend(loc='upper left', ncol=3, fontsize='small')
    plt.savefig('%s_opened_closed_PRs_month' % repository)

    pd_df_total = pd_df.cumsum()
    res = pd_df_total.plot(kind='line', title="total open/opened/closed %s PRs" % repository)
    res.legend(loc='upper left', ncol=3, fontsize='small')
    plt.savefig('%s_opened_closed_PRs_cumulative' % repository)

    pd_df_total_year = pd_df_total.select(lambda d: d > LAST_YEAR)
    res = pd_df_total_year.plot(kind='line', title="total open/opened/closed %s PRs (last year)" % repository)
    res.legend(loc='upper left', ncol=3, fontsize='small')
    plt.savefig('%s_opened_closed_PRs_cumulative_year' % repository)

    pd_df_total_month = pd_df_total.select(lambda d: d > LAST_MONTH)
    res = pd_df_total_month.plot(kind='line', title="total open/opened/closed %s PRs (last month)" % repository)
    res.legend(loc='upper left', ncol=3, fontsize='small')
    plt.savefig('%s_opened_closed_PRs_cumulative_month' % repository)



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


    example_ut = [-1, -1, -1, -1, -1, -1, -1, -1, -1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1]
    example_sc = [-1, -1, -1,  0,  0,  0,  1,  1,  1, -1, -1, -1,  0,  0,  0,  1,  1,  1, -1, -1, -1,  0,  0,  0,  1,  1,  1]
    example_tr = [-1,  0,  1, -1,  0,  1, -1,  0,  1, -1,  0,  1, -1,  0,  1, -1,  0,  1, -1,  0,  1, -1,  0,  1, -1,  0,  1]
    example_statuses = [-3, -2, -1, -2, -1, 0, -1, 0, 1, -2, -1, 0, -1, 0, 1, 0, 1, 2, -1, 0, 1, 0, 1, 2, 1, 2, 3]

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

        year, month, day, hour, minutes, seconds = pr['updated_at'].replace('T', '@')[:-1].replace(':', '@').replace('-', '@').split('@')
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


def main():
    types = {
        'html': gen_pr_overview_page,
        'plot': plot_pr_stats,
        'print': pr_overview,
    }

    opts = {
        'github-account': ("GitHub account where repository is located", None, 'store', 'hpcugent', 'a'),
        'github-user': ("GitHub user to use (for authenticated access)", None, 'store', 'boegel', 'u'),
        'repository': ("Repository to use", None, 'store', 'easybuild-easyconfigs', 'r'),
        'type': ("Type of overview: 'print', html' or 'plot'", 'choice',
                 'store_or_None', 'print', types.keys(), 't'),
    }


    go = simple_option(go_dict=opts, descr="Script to print overview of pull requests for a GitHub repository")

    github_token = fetch_github_token(go.options.github_user)
    github = RestClient(GITHUB_API_URL, username=go.options.github_user, token=github_token, user_agent='eb-pr-overview')

    pickle_file = None
    if go.args:
        pickle_file = go.args[0]

    downloading_msg = "Downloading PR data for %s/%s repo..." % (go.options.github_account, go.options.repository)
    print(downloading_msg)
    prs = fetch_prs_data(pickle_file, github, go.options.github_account, go.options.repository, downloading_msg)

    # put options here
    types[go.options.type](prs, go)


if __name__ == '__main__':
    main()
