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
Plot PR stats.

@author: Kenneth Hoste (Ghent University)
"""
import cPickle
import datetime

import matplotlib
matplotlib.use('PDF')  # must be done before next matplotlib import
import matplotlib.pyplot as plt
import pandas as pd

from vsc.utils import fancylogger
from vsc.utils.dateandtime import date_parser, datetime_parser
from vsc.utils.generaloption import simple_option
from vsc.utils.rest import RestClient

import easybuild.tools.build_log  # required to obtain an EasyBuild logger
from easybuild.tools.github import GITHUB_API_URL, fetch_github_token

from pr_overview import fetch_prs_data


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


def fetch_pr_data(pickle_file, github_user, github_account, repository):
    """Fetch PR data; either download or load from pickle file."""
    if pickle_file:
        print("Loading PR data from %s" % pickle_file)
        prs = cPickle.load(open(pickle_file, 'r'))

    else:
        github_token = fetch_github_token(github_user)
        github = RestClient(GITHUB_API_URL, username=github_user, token=github_token, user_agent='eb-pr-stats')
        gh_repo = github.repos[github_account][repository]

        downloading_msg = "Downloading PR data for %s/%s repo..." % (github_account, repository)
        print(downloading_msg)

        prs = fetch_prs_data(github, github_account, repository, downloading_msg)
        pickle_file = PICKLE_FILE % repository
        cPickle.dump(prs, open(pickle_file, 'w'))
        print("PR data dumped to %s" % pickle_file)

    return prs


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

    pd_df_all = pd.DataFrame(ages_all, days, columns=['all']+GROUP_LABELS[::-1]).sort().fillna(method='ffill')
    res = pd_df_all.plot(kind='area', stacked=False, title="open %s PRs, by age" % repository)
    res.legend(ncol=len(GROUP_LABELS)+1, fontsize='small')
    plt.savefig('%s_PR_stats_all' % repository)

    res = pd_df_all.plot(kind='area', stacked=False, title="open %s PRs, by age (zoomed)" % repository)
    res.set_ylim([0, 50])
    res.legend(ncol=len(GROUP_LABELS)+1, fontsize='small')
    plt.savefig('%s_PR_stats_all_zoomed' % repository)

    pd_df = pd.DataFrame(ages, days, columns=GROUP_LABELS[::-1]).sort().fillna(method='ffill')
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

    pd_df = pd.DataFrame(ages_rev, days, columns=GROUP_LABELS).sort().fillna(method='ffill')
    res = pd_df.plot(kind='area', stacked=True, title="open %s PRs, by age (stacked)" % repository)
    res.legend(ncol=len(GROUP_LABELS), fontsize='small')
    plt.savefig('%s_PR_stats_all_stacked_rev' % repository)

    res = pd_df.plot(kind='area', stacked=True, title="open %s PRs, by age (stacked, zoomed)" % repository)
    res.set_ylim([0, 50])
    res.legend(ncol=len(GROUP_LABELS), fontsize='small')
    plt.savefig('%s_PR_stats_all_stacked_rev_zoomed' % repository)


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

    pd_df = pd.DataFrame(opened_closed, days, columns=['open', 'opened', 'closed']).sort()
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


def main():
    opts = {
        'github-account': ("GitHub account where repository is located", None, 'store', 'hpcugent', 'a'),
        'github-user': ("GitHub user to use (for authenticated access)", None, 'store', 'boegel', 'u'),
        'repository': ("Repository to use", None, 'store', 'easybuild-easyconfigs', 'r'),
    }
    go = simple_option(go_dict=opts, descr="Script to print overview of pull requests for a GitHub repository")

    pickle_file = None
    if go.args:
        pickle_file = go.args[0]

    prs = fetch_pr_data(pickle_file, go.options.github_user, go.options.github_account, go.options.repository)

    created_ats = [datetime_parser(pr['created_at'].split('T')[0]) for pr in prs]
    closed_ats = [datetime_parser((pr['closed_at'] or 'T').split('T')[0] or 'ENDNEXTMONTH') for pr in prs]

    print('Plotting...')
    plot_historic_PR_ages(created_ats, closed_ats, go.options.repository)
    plot_open_closed_PRs(created_ats, closed_ats, go.options.repository)


if __name__ == '__main__':
    main()
