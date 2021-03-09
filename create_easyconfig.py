#!/usr/bin/env python3
import os
import pprint
import re
import sys

from easybuild.framework.easyconfig.easyconfig import EasyConfig
from easybuild.tools.build_log import EasyBuildError
from easybuild.tools.config import module_classes
from easybuild.tools.filetools import find_extension
from easybuild.tools.module_naming_scheme.utilities import det_full_ec_version
from easybuild.tools.options import set_up_configuration
from easybuild.tools.toolchain.utilities import search_toolchain
from easybuild.tools.utilities import quote_str


BLUE = '\033[94m'
RED = '\033[31m'
YELLOW = '\033[33m'


def color(txt, col):
    return col + txt + '\033[0m'


def blue(txt):
    return color(txt, BLUE)


def red(txt):
    return color(txt, RED)


def yellow(txt):
    return color(txt, YELLOW)


def error(msg):
    sys.stderr.write(red("ERROR: %s\n" % msg))
    sys.exit(1)


def info(msg):
    print(blue('>>> ' + msg))


def found(key):
    print("Found value for '%s': %s" % (yellow(key), cfg[key]))


def not_found_yet(key):
    return key not in cfg


def looks_like_sentence(spec):
    return bool(len(spec.split(' ')) > 1)


def looks_like_word(spec):
    return not looks_like_sentence(spec)


def looks_like_url(spec):
    return spec.startswith('http')


def looks_like_version(spec):
    return bool(re.match('^[0-9.]+$', spec))


def parse_explicit_param(spec):

    key, val = None, None

    param_regex = re.compile('^(?P<key>[a-z_]+)=(?P<val>.+)')
    res = param_regex.match(spec)
    if res:
        key, val = res.group('key'), res.group('val')

    return key, val


# =================================================================================================

if len(sys.argv) < 2:
    error("Usage: %s <space-separated set of values for easyconfig parameters>" % os.path.basename(sys.argv[0]))

specs = sys.argv[1:]

cfg = {}

set_up_configuration()

known_module_classes = module_classes()
tc_names = sorted([tc.NAME for tc in search_toolchain('')[1]], key=len, reverse=True)

for spec in specs:
    info("Processing spec: %s" % spec)

    key = None

    key, val = parse_explicit_param(spec)
    if key:
        info("Found explicit parameter spec for '%s': %s" % (key, val))
        spec = val

        if key in ['builddependencies', 'dependencies']:
            deps = []
            for depspec in spec.split(';'):
                deps.append(tuple(depspec.split(',')))
            spec = deps

    # if spec looks like a version and we haven't found the software version yet,
    # assume it's the software version
    elif looks_like_version(spec) and not_found_yet('version'):
        key = 'version'

    elif looks_like_url(spec):

        # try to determine command to use to unpack;
        # if no command can be determine, given URL is probably not a download URL
        fn = os.path.basename(spec)
        try:
            ext = find_extension(fn)
        except EasyBuildError:
            ext = None

        if ext is None:
            # could be homepage
            if not_found_yet('homepage'):
                key = 'homepage'
        elif not_found_yet('source_urls'):
            key = 'source_urls'

    elif looks_like_word(spec):

        if not_found_yet('name'):
            key = 'name'

        elif any(spec.startswith(x) for x in tc_names):
            key = 'toolchain'
            if spec.lower() == 'system':
                spec = {'name': 'system', 'version': 'system'}
            else:
                tc_name, tc_version = spec.split('/', 1)
                spec = {'name': tc_name, 'version': tc_version}

        elif spec in known_module_classes:
            key = 'moduleclass'

    elif looks_like_sentence(spec):
        key = 'description'

    if key:
        cfg[key] = spec
        found(key)
    else:
        error("Don't know how to interpret '%s'" % spec)


# name of source file may be included in source URL
if 'source_urls' in cfg and not_found_yet('sources'):
    url, fn = os.path.split(cfg['source_urls'])
    cfg['source_urls'] = [url]
    cfg['sources'] = [fn]

# use PYPI_SOURCE when appropriate
source_urls = cfg.get('source_urls', [])
for idx, source_url in enumerate(source_urls):
    if 'files.pythonhosted.org/packages' in source_url:
        cfg['source_urls'][idx] = 'PYPI_SOURCE'

# add versionsuffix if Python is specified as a dependency
if any(dep[0] == 'Python' for dep in cfg.get('dependencies', [])):
    if not_found_yet('versionsuffix'):
        cfg['versionsuffix'] = '-Python-%(pyver)s'

# add empty sanity_check_paths
if 'sanity_check_paths' not in cfg:
    cfg['sanity_check_paths'] = {'files': [], 'dirs': []}

# enable use_pip & sanity_pip_check
if cfg.get('easyblock') in ['PythonBundle', 'PythonPackage']:
    cfg.update({
        'use_pip': True,
        'sanity_pip_check': True,
    })

# enable download_dep_fail
if cfg.get('easyblock') == 'PythonPackage':
    cfg['download_dep_fail'] = True

pprint.pprint(cfg)

ec_raw = '\n'.join("%s = %s" % (key, quote_str(cfg[key])) for key in cfg)
ec = EasyConfig(None, rawtxt=ec_raw)

full_ec_ver = det_full_ec_version(ec)
fn = os.path.join('%s-%s.eb' % (cfg['name'], full_ec_ver))

ec.dump(fn)
info("Easyconfig file created: %s" % fn)
