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
Generate PR overview HTML page, with interactive table.

@author: Kenneth Hoste (Ghent University)
"""
from datetime import datetime

from vsc.utils.dateandtime import date_parser, datetime_parser
from vsc.utils.generaloption import simple_option
from vsc.utils.missing import nub

from plot_pr_stats import fetch_pr_data

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

if __name__ == '__main__':
    main()
