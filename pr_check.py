#!/usr/bin/env python
##
# Copyright 2016-2016 Ghent University
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
Pull request checker for EasyBuild repositories

@author: Kenneth Hoste (Ghent University)
"""
import os
import re
import sys
import travispy
from vsc.utils.generaloption import simple_option
from vsc.utils.rest import RestClient

from easybuild.tools.github import GITHUB_API_URL, fetch_github_token, post_comment_in_issue
from easybuild.tools.ordereddict import OrderedDict
from easybuild.tools.run import run_cmd

from pr_overview import fetch_pr_data


TRAVIS_URL = 'https://travis-ci.org'

DRY_RUN = False
MERGE_USER = 'boegel'


def error(msg):
    """Print error message and exit."""
    sys.stderr.write("ERROR: %s\n" % msg)
    sys.exit(1)


def info(msg):
    """Print info message."""
    print "%s... %s" % (msg, ('', '[DRY RUN]')[DRY_RUN])


def usage():
    """Print usage and exit."""
    error("Usage: %s <PR#>\n" % sys.argv[0])

#######################################################################################################################

def print_pr_info(pr_data, key, indent='', label=None):
    """Print PR information for given key."""
    if isinstance(key, basestring):
        if label is None:
            label = key
        if pr_data is None:
            print indent + "* %s: (none)" % label
        elif isinstance(pr_data[key], dict):
            print indent + "* %s:" % label
            for key2 in sorted(pr_data[key].keys()):
                print_pr_info(pr_data[key], key2, indent=indent + '  ')
        elif not key.startswith('_'):
            print indent + "* %s: %s" % (label, pr_data.get(key))

    elif isinstance(key, tuple):
        if len(key) > 1:
            if label is None:
                label = '->'.join(key)
            print_pr_info(pr_data[key[0]], key[1:], indent=indent, label=label)
        else:
            print_pr_info(pr_data, key[0], indent=indent, label=label)


def print_raw_pr_info(pr_data):
    """Print raw PR info."""
    print "Raw PR info:"
    for key in sorted(pr_data.keys()):
        print_pr_info(pr_data, key)


def print_pr_summary(pr_data):
    """Print PR summary."""
    keys = OrderedDict([
        ('html_url', 'URL'),
        ('title', None),
        (('user', 'login'), "issued by"),
        (('head', 'ref'), "branch"),
        (('base', 'ref'), "target branch"),
        ('state', "status"),
        (('milestone', 'title'), None),
    ])
    target = '%s/%s' % (pr_data['base']['repo']['owner']['login'], pr_data['base']['repo']['name'])
    print "\nSummary for %s PR #%s:\n" % (target, pr_data['number'])
    for key in keys:
        print_pr_info(pr_data, key, label=keys[key])
    print ''

#######################################################################################################################

def comment(github, github_user, repository, pr_data, msg, check_msg=None, verbose=True):
    """Post a comment in the pull request."""
    # decode message first, if needed
    known_msgs = {
        'jok': "Jenkins: ok to test",
        'jt': "Jenkins: test this please",
    }
    if msg.startswith(':'):
        if msg[1:] in known_msgs:
            msg = known_msgs[msg[1:]]
        elif msg.startswith(':r'):
            github_login = msg[2:]
            try:
                github.users[github_login].get()
                msg = "@%s: please review?" % github_login
            except:
                error("No such user on GitHub: %s" % github_login)
        else:
            error("Unknown coded comment message: %s" % msg)

    # only actually post comment if it wasn't posted before
    if check_msg:
        msg_regex = re.compile(check_msg, re.M)
        for comment in pr_data['issue_comments']['bodies']:
            if msg_regex.search(comment):
                print "Message already found (using pattern '%s'), not posting comment again!" % check_msg
                return
        print "Message not found yet (using pattern '%s'), stand back for posting!" % check_msg

    target = '%s/%s' % (pr_data['base']['repo']['owner']['login'], pr_data['base']['repo']['name'])
    if verbose:
        info("Posting comment as user '%s' in %s PR #%s: \"%s\"" % (github_user, target, pr_data['number'], msg))
    else:
        info("Posting comment as user '%s' in %s PR #%s" % (github_user, target, pr_data['number']))
    if not DRY_RUN:
        post_comment_in_issue(pr_data['number'], msg, repo=repository, github_user=github_user)
    print "Done!"

#######################################################################################################################

def merge(github, github_user, github_account, repository, pr_data, force=False):
    """Merge pull request."""
    if github_user != MERGE_USER:
        error("Only @%s should merge pull requests!" % MERGE_USER)

    target = '%s/%s' % (pr_data['base']['repo']['owner']['login'], pr_data['base']['repo']['name'])
    info("Merging %s PR #%s, but not before review shows it's OK to do so" % (target, pr_data['number']))
    if review(pr_data) or force:
        info("Review %s merging pull request" % ("OK,", "FAILed, yet forcibly")[force])

        if pr_data['user']['login'] != github_user:
            comment(github, github_user, repository, pr_data, "Going in, thanks @%s!" % pr_data['user']['login'])

        if not DRY_RUN:
            body = {
                'commit_message': pr_data['title'],
                'sha': pr_data['head']['sha'],
            }
            status, data = github.repos[github_account][repository].pulls[pr_data['number']].merge.put(body=body)
            if status == 200:
                print "Done: %s" % data['message']
            elif status in [405, 409]:
                error("FAILED: %s" % data['message'])
            else:
                error("FAILED: %s" % data.get('message', "(unknown reason)"))

        # delete branch on GitHub if this was own PR
        if github_user == pr_data['head']['repo']['owner']['login']:
            info("Deleting branch '%s' in %s/%s" % (pr_data['head']['ref'], github_user, repository))
            if not DRY_RUN:
                status, data = github.repos[github_user][repository].git.refs['heads'][pr_data['head']['ref']].delete()
                if status == 204:
                    print "Done!"
                else:
                    error("FAILED! (status: %s)" % status)
    else:
        error("Review indicates this PR should not be merged (use -f/--force to do so anyway)")

#######################################################################################################################

def check_target_branch(pr_data, target_branch='develop'):
    """Verify target branch."""
    res = pr_data['base']['ref'] == target_branch
    print "* checking whether target branch is '%s'... %s" % (target_branch, ('FAILED', 'OK')[res])
    return res


def check_style_human(pr_data):
    """Check whether code style has been reviewed by a human."""
    print "* checking whether code style has been reviewed by a human...",

    review_requested_regex = re.compile(r"^@\S+: please review\?")

    res = False
    review_requested = False
    for comment in pr_data['issue_comments']['bodies']:
        if review_requested_regex.match(comment):
            review_requested = True
        if 'lgtm' in comment:
            res = True

    print ('FAILED', 'OK')[res],
    if res:
        print ''
    else:
        print "(requested: %s)" % ("no, use '-C :r<name>'", 'yes')[review_requested]

    return res


def check_test_reports(pr_data):
    """Check whether last test reports are successful."""
    print "* checking whether last test report(s) are successful...",

    test_report_regex = re.compile(r"^Test report by @\S+")

    res = False
    cnt = 0
    last_success = 0
    for comment in pr_data['issue_comments']['bodies']:
        if test_report_regex.search(comment):
            cnt += 1
            if 'SUCCESS' in comment:
                res = True
                last_success += 1
            elif 'FAILED' in comment:
                res = False
                last_success = 0
            else:
                error("Failed to determine outcome of test report for comment:\n%s" % comment)

    print ("FAILED (use '-T')", 'OK')[res],
    if res:
        print " (last %s/%s)" % (last_success, cnt)
    else:
        print ''

    return res


def check_unit_tests(pr_data):
    """Check whether unit tests were run with success."""
    print "* checking whether unit tests are run with success...",

    res = pr_data['combined_status'] == 'success'
    if res:
        print 'OK'
    elif pr_data['combined_status'] == 'pending':
        print "(pending, use '-C :jok')"
    elif pr_data['combined_status'] in ['error', 'failure']:
        print 'FAILED'
    else:
        print 'UNKNOWN'

    return res


def review(pr_data):
    """Review PR by running all available checks."""
    res = True
    cands = globals()

    print "Reviewing PR..."
    print ''
    for check_function in sorted([cands[f] for f in cands if callable(cands[f]) and f.startswith('check_')]):
        res &= check_function(pr_data)

    if res:
        print "\nAll checks passed. Let's merge (-M)?\n"
    else:
        print "\nOne or more checks FAILed.\n"

    return res

#######################################################################################################################

def test(pr_data, arg):
    """Submit job to upload test report to pull request."""
    target = '%s/%s' % (pr_data['base']['repo']['owner']['login'], pr_data['base']['repo']['name'])
    print "Submitting job to upload test report for %s PR #%s..." % (target, pr_data['number'])

    extra = ''
    if isinstance(arg, basestring):
        extra = "module swap cluster/%s && " % arg

    cmd = "ssh vsc40023@login.hpc.ugent.be \"%s\"" % ' && '.join([
        "source /etc/profile.d/modules.sh",
        "source /etc/profile.d/vsc.sh",
        "%sqsub eb_from_pr.sh -t %s" % (extra, pr_data['number']),
    ])
    out, ec = run_cmd(cmd, simple=False, force_in_dry_run=True, trace=False)
    if ec == 0:
        print "Done, job ID: %s" % out
    else:
        error("FAILED: %s" % out)

#######################################################################################################################

def travis(github_account, repository, github_token, owner=None):
    """Scan Travis test runs for failures, and return notification to be sent to PR if one is found"""
    travis = travispy.TravisPy.github_auth(github_token)

    repo_slug = '%s/%s' % (github_account, repository)
    last_builds = travis.builds(slug=repo_slug, event_type='pull_request')

    done_prs = []

    res = []
    for build in last_builds:
        bid, pr = build.number, build.pull_request_number

        if pr in done_prs:
            print "(skipping test suite run for already processed PR #%s)" % pr
            continue

        done_prs.append(pr)

        if build.successful:
            print "(skipping successful test suite run %s for PR %s)" % (bid, pr)

        else:
            build_url = os.path.join(TRAVIS_URL, repo_slug, 'builds', str(build.id))
            print "[id: %s] PR #%s - %s - %s" % (bid, pr, build.state, build_url)

            jobs = [(str(job_id), travis.jobs(ids=[job_id])[0]) for job_id in sorted(build.job_ids)]
            jobs_ok = [job.successful for (_, job) in jobs]

            pr_comment = "Travis test report: %d/%d runs failed - " % (jobs_ok.count(False), len(jobs))
            pr_comment += "see %s\n" % build_url
            check_msg = pr_comment.strip()

            jobs = [(job_id, job) for (job_id, job) in jobs if job.unsuccessful]
            if jobs:
                job_url = os.path.join(TRAVIS_URL, repo_slug, 'jobs', jobs[0][0])

                pr_comment += "\nOnly showing partial log for 1st failed test suite run %s;\n" % jobs[0][1].number
                pr_comment += "full log at %s\n" % job_url

                # try to filter log to just the stuff that matters
                retained_log_lines = jobs[0][1].log.body.split('\n')
                for idx, log_line in enumerate(retained_log_lines):
                    if repository == 'easybuild-easyconfigs':
                        if log_line.startswith('FAIL:') or log_line.startswith('ERROR:'):
                            retained_log_lines = retained_log_lines[idx:]
                            break
                    elif log_line.strip().endswith("$ python -O -m test.%s.suite" % repository.split('-')[-1]):
                        retained_log_lines = retained_log_lines[idx:]
                        break

                pr_comment += '```\n...\n'
                pr_comment += '\n'.join(retained_log_lines[-100:])
                pr_comment += '\n```\n'

                for (job_id, job) in jobs[1:]:
                    job_url = os.path.join(TRAVIS_URL, repo_slug, 'jobs', job_id)
                    pr_comment += "* %s - %s => %s\n" % (job.number, job.state, job_url)

                if owner:
                    pr_comment += "\n*(bleep, bloop, I'm just a bot, "
                    pr_comment += "please talk to my owner @%s if you notice you me acting stupid)*" % owner

                res.append((pr, pr_comment, check_msg))

    print "Processed %d builds, found %d PRs with failed builds to report back on" % (len(last_builds), len(res))

    return res

#######################################################################################################################


def main():

    opts = {
        'dry-run': ("Dry run, don't actually post/push/merge anything", None, 'store_true', False, 'x'),
        'force': ("Use force to execute the specified action", None, 'store_true', False, 'f'),
        'github-account': ("GitHub account where repository is located", None, 'store', 'easybuilders', 'a'),
        'github-user': ("GitHub user to use (for authenticated access)", None, 'store', 'boegel', 'u'),
        'owner': ("Owner of the bot account that is used", None, 'store', None),
        'repository': ("Repository to use", None, 'store', 'easybuild-easyconfigs', 'r'),
        # actions
        'comment': ("Post a comment in the pull request", None, 'store', None, 'C'),
        'merge': ("Merge the pull request", None, 'store_true', False, 'M'),
        'review': ("Review the pull request", None, 'store_true', False, 'R'),
        'test': ("Submit job to upload test report", None, 'store_or_None', None, 'T'),
        'travis': ("Scan Travis test results, notify of failed tests in PRs", None, 'store_true', False),
    }

    actions = ['comment', 'merge', 'review', 'test', 'travis']

    go = simple_option(go_dict=opts)

    # determine which action should be taken
    selected_action = None
    for action in sorted(actions):
        action_value = getattr(go.options, action)
        if isinstance(action_value, bool):
            if action_value:
                selected_action = (action, action_value)
                break
        elif action_value is not None:
            selected_action = (action, action_value)
            break  # FIXME: support multiple actions, loop over them (e.g. -C :jok,lgtm -T)

    if selected_action is None:
        avail_actions = ', '.join(["%s (-%s)" % (a, a[0].upper()) for a in sorted(actions)])
        error("No action specified, pick one: %s" % avail_actions)
    else:
        info("Selected action: %s" % selected_action[0])

    global DRY_RUN
    DRY_RUN = go.options.dry_run
    force = go.options.force
    github_account = go.options.github_account
    github_user = go.options.github_user
    owner = go.options.owner
    repository = go.options.repository

    pr = None
    check_msg = None
    github_token = fetch_github_token(github_user)

    # prepare using GitHub API
    github = RestClient(GITHUB_API_URL, username=github_user, token=github_token, user_agent='eb-pr-check')

    if selected_action[0] == 'travis':
        res = travis(github_account, repository, github_token, owner=owner)
        if res:
            for pr, pr_comment, check_msg in res:
                pr_data = fetch_pr_data(github, github_account, repository, pr)
                comment(github, github_user, repository, pr_data, pr_comment, check_msg=check_msg, verbose=DRY_RUN)
        else:
            print "Found no PRs to notify, all done here!"

    else:
        if len(go.args) == 1:
            pr = go.args[0]
        else:
            usage()

        print "Fetching PR information ",
        print "(using GitHub token for user '%s': %s)... " % (github_user, ('no', 'yes')[bool(github_token)]),
        sys.stdout.flush()
        pr_data = fetch_pr_data(github, github_account, repository, pr)
        print ''

        #print_raw_pr_info(pr_data)

        print_pr_summary(pr_data)

        if selected_action[0] == 'comment':
            comment(github, github_user, repository, pr_data, selected_action[1], check_msg=check_msg)
        elif selected_action[0] == 'merge':
            merge(github, github_user, github_account, repository, pr_data, force=force)
        elif selected_action[0] == 'review':
            review(pr_data)
        elif selected_action[0] == 'test':
            test(pr_data, selected_action[1])
        else:
            error("Handling action '%s' not implemented yet" % selected_action[0])


if __name__ == '__main__':
    main()
