#!/usr/bin/env python

import git
import os
import re
import sys

from easybuild.framework.easyconfig.parser import EasyConfigParser
from easybuild.tools.build_log import EasyBuildError
from easybuild.tools.filetools import read_file
from easybuild.tools.toolchain import DUMMY_TOOLCHAIN_NAME
from vsc.utils.generaloption import simple_option


def doit(path, pattern):
    """Do it for all easyconfigs in specified location"""
    repo = git.Repo.init(path)

    timestamp_regex = re.compile('Date:\s*(?P<timestamp>.*)', re.M)
    tc_regex = re.compile(r"^\s*toolchain\s*=\s*(.*)$", re.M)

    easyconfigs, toolchains = [], set()

    specs = []
    for (subpath, _, filenames) in os.walk(path, topdown=True):
        if subpath.startswith(os.path.join(path, '.git')):
            continue

        for filename in filenames:
            if filename.endswith('.eb') and filename != 'TEMPLATE.eb' and pattern in filename:

                specs.append(os.path.join(path, subpath, filename))

    for idx, spec in enumerate(specs):
        print "\rprocessed %d of %d easyconfigs" % (idx, len(specs)),

        ec = EasyConfigParser(filename=spec).get_config_dict()

        if ec['toolchain']['name'] == DUMMY_TOOLCHAIN_NAME:
            toolchain = 'dummy'
        else:
            toolchain = '%(name)s-%(version)s' % ec['toolchain']
        toolchains.add(toolchain)

        logtxt = repo.git.log('--reverse', '--date=iso-local', spec)
        res = timestamp_regex.search(logtxt)
        if res:
            timestamp = str(res.group('timestamp'))
        else:
            raise EasyBuildError("No timestamp found in git log for %s: %s", spec, logtxt)

        easyconfigs.append((timestamp, os.path.basename(spec), toolchain))

    print ''
    print 'found %d different toolchains' % len(toolchains)

    for toolchain in sorted(toolchains):
        # datestamp first for correct sorting!
        ecs = sorted([(datestamp, ec) for (datestamp, ec, tc) in easyconfigs if tc == toolchain])

        print '%s (%d)' % (toolchain, len(ecs))
        print '\toldest: %s (%s)' % (ecs[0][1], ecs[0][0])
        print '\tnewest: %s (%s)' % (ecs[-1][1], ecs[-1][0])


## MAIN ##

opts = {
    'easyconfigs-repo': ("Path to easyconfigs repository", None, 'store', '.', 'p'),
    'pattern': ("Filter pattern to use on easyconfig file names", None, 'store', '', 'f'),
}

go = simple_option(go_dict=opts, descr="Script to help figure out which toolchains to deprecate")

doit(go.options.easyconfigs_repo, go.options.pattern)
