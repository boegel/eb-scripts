#!/usr/bin/env python3
import copy
import os
import pprint
import re
import sys
from distutils.version import LooseVersion

from blessings import Terminal
import requests
from bs4 import BeautifulSoup

from easybuild.framework.easyconfig.tools import det_easyconfig_paths, parse_easyconfigs
from easybuild.tools.config import build_option
from easybuild.tools.filetools import search_file, write_file
from easybuild.tools.module_naming_scheme.utilities import det_full_ec_version
from easybuild.tools.modules import modules_tool
from easybuild.tools.options import set_up_configuration
from easybuild.tools.robot import resolve_dependencies
from easybuild.tools.toolchain.toolchain import SYSTEM_TOOLCHAIN_NAME
from easybuild.tools.utilities import nub


term = Terminal()


def error(msg):
    """Print error and exit."""
    sys.stderr.write(term.red("ERROR: %s\n" % msg))
    sys.exit(1)


def warning(msg):
    """Print warning."""
    sys.stderr.write(term.red("WARNING: %s\n" % msg))


def print_bold(msg):
    print(term.bold(msg))


def update_version(ec):
    """Return updated software version of specified easyconfig."""

    print(term.bold_cyan("\nUpdating %s version %s..." % (ec.name, ec.version)))

    digit_regex = re.compile('^[0-9]')
    letter_regex = re.compile('[a-zA-Z]')
    version_regex = re.compile(re.escape(ec.version))

    cand_versions = []

    for src_fn in ec['sources']:
        src_fn_pattern = r'^' + src_fn.replace(ec.version, '(?P<version>.*)') + r'$'
        regex = re.compile(src_fn_pattern)

        for url in ec['source_urls']:
            print("Considering URL %s (using pattern '%s')..." % (url, regex.pattern))
            if version_regex.search(url):
                warning("Found version '%s' in URL: %s" % (ec.version, url))
            try:
                page = requests.get(url).text
                soup = BeautifulSoup(page, 'html.parser')
                href_targets = nub([node.get('href') for node in soup.find_all('a')])
                #print(href_targets)
            except:
                href_targets = []

            for cand in href_targets:
                res = regex.search(cand)
                if res:
                    cand_version = res.group('version')
                    if digit_regex.match(ec.version) and not digit_regex.match(cand_version):
                        # ignore candidate version that don't start with a digit
                        # (but only if current version also starts with a digit)
                        print(term.yellow("Ignoring version '%s' (doesn't start with a digit)" % cand_version))
                    elif letter_regex.search(cand_version):
                        print(term.yellow("Ignoring version '%s' (contains a letter)" % cand_version))
                    else:
                        cand_versions.append(LooseVersion(cand_version))

            # if we have a match, stop considering other source URLs
            if cand_versions:
                break
        # if we have a match, stop considering other sources
        if cand_versions:
            break

    if cand_versions:
        sorted_versions = sorted(cand_versions)
        latest_version = sorted_versions[-1]

        if latest_version > LooseVersion(ec.version):
            latest_version = str(latest_version)
            print(term.green("Latest %s version: %s" % (ec.name, latest_version)))
        elif latest_version == LooseVersion(ec.version):
            latest_version = ec.version
            print(term.magenta("Current version (%s) is already latest" % ec.version))
        else:
            error("Latest version found (%s) is older than current version!")
    else:
        warning("No versions found for %s!" % ec.name)
        latest_version = None

    return latest_version


def main():
    """Main function."""
    if len(sys.argv) == 3:
        ec = sys.argv[1]
        tc = sys.argv[2]
    else:
        error("Usage %s <easyconfig> <toolchain> [<name=version>]" % sys.argv[0])

    tc_name, tc_ver = tc.split('/')
    print("Updating %s for %s toolchain version %s..." % (ec, tc_name, tc_ver))

    set_up_configuration(silent=True)
    modtool = modules_tool()
    robot_path = build_option('robot_path')

    ec_path = det_easyconfig_paths([ec])[0]
    print("Found %s easyconfig file at %s" % (ec, ec_path))

    parsed_ecs, _ = parse_easyconfigs([(ec_path, False)], validate=False)

    print("Resolving dependencies... ", end='')
    ecs = resolve_dependencies(parsed_ecs, modtool, retain_all_deps=True)
    print("found stack of %d easyconfigs" % len(ecs))

    print("Filtering toolchain and its dependencies...")
    ec_tc = parsed_ecs[0]['ec']['toolchain']
    ecs_to_remove = [{'name': ec_tc['name'], 'version': ec_tc['version'], 'toolchain': {'name': SYSTEM_TOOLCHAIN_NAME}}]

    updated_ecs = {}

    # if GCCcore is used as toolchain, determine binutils version to use
    if tc_name == 'GCCcore':
        binutils_pattern = '^binutils.*-%s-%s.*.eb$' % (tc_name, tc_ver)
        _, res = search_file(robot_path, binutils_pattern)
        if res:
            if len(res) == 1:
                parsed_ecs, _ = parse_easyconfigs([(res[0], False)])
                binutils_ec = parsed_ecs[0]
                tc = copy.copy(binutils_ec['ec']['toolchain'])
                ecs_to_remove.append({'name': 'binutils', 'version': binutils_ec['ec'].version, 'toolchain': tc})
            else:
                error("Found more than one easyconfig matching '%s': %s" % (binutils_pattern, res))
        else:
            error("No easyconfig file found for binutils using pattern '%s'" % binutils_pattern)

    while(ecs_to_remove):
        to_remove = ecs_to_remove.pop(0)
        print("Removing %(name)s/%(version)s (toolchain: %(toolchain)s)" % to_remove)
        for ec in ecs:
            if ec['ec'].name == to_remove['name'] and ec['ec'].version == to_remove['version'] and \
               ec['ec']['toolchain']['name'] == to_remove['toolchain']['name']:
                ecs.remove(ec)
                ecs_to_remove.extend(dep for dep in ec['ec']['dependencies'] + ec['ec']['builddependencies'])
                updated_ecs[ec['full_mod_name']] = {
                    'builddependencies': [],
                    'dependencies': [],
                    'toolchain': copy.copy(ec['ec']['toolchain']),
                    'version': ec['ec'].version,
                }
                break

    ecs_to_write = []
    for ec in ecs:
        ec_fn = os.path.basename(ec['spec'])
        print(term.bold("Determining version for %s..." % ec_fn))
        full_mod_name = ec['full_mod_name']
        ec_tc = copy.copy(ec['ec']['toolchain'])

        # update toolchain (unless it's SYSTEM)
        if ec_tc['name'] != SYSTEM_TOOLCHAIN_NAME:
            if ec_tc['name'] == tc_name:
                ec_tc['version'] = tc_ver
            else:
                error("Don't know how to update toolchain %s" % ec_tc['name'])

        # update (build) dependencies
        build_deps = []
        for dep in ec['ec']['builddependencies']:
            new_dep_ver = updated_ecs[dep['full_mod_name']]['version']
            build_deps.append((dep['name'], new_dep_ver))
        deps = []
        for dep in ec['ec']['dependencies']:
            new_dep_ver = updated_ecs[dep['full_mod_name']]['version']
            deps.append((dep['name'], new_dep_ver))

        # determine software version to use;
        # first, try searching for an existing easyconfig with specified toolchain;
        # if that fails, try to determine latest upstream version
        ec_pattern = '^%s.*-%s-%s.*.eb$' % (ec['ec'].name, tc_name, tc_ver)
        _, res = search_file(robot_path, ec_pattern)
        if res:
            if len(res) == 1:
                parsed_ecs, _ = parse_easyconfigs([(res[0], False)])
                ec = parsed_ecs[0]
                new_version = ec['ec'].version
                print(term.green("Found existing easyconfig, sticking to version %s" % new_version))
            else:
                error("Multiple hits found using '%s': %s" % (res, ec_pattern))
        else:
            new_version = update_version(ec['ec'])
            ecs_to_write.append(ec)

        if new_version is None:
            print(term.yellow("No new version found for %s, using existing version" % full_mod_name))
            new_version = ec['ec'].version

        updated_ecs[full_mod_name] = {
            'builddependencies': build_deps,
            'dependencies': deps,
            'toolchain': ec_tc,
            'version': new_version,
        }

    for ec in ecs_to_write:
        full_mod_name = ec['full_mod_name']
        pprint.pprint(full_mod_name)

        ec = ec['ec']
        ectxt = ec.rawtxt

        key_pattern = r'^%s\s*=.*'
        list_key_pattern = r'^%s\s*=\s*\[([^\]]|\n)*\s*\]'

        new_version = updated_ecs[full_mod_name]['version']
        if ec.version != new_version:
            regex = re.compile(key_pattern % 'version', re.M)
            ectxt = regex.sub("version = '%s'" % new_version, ectxt)
            # if version got updated, also wipe the checksums
            regex = re.compile(list_key_pattern % 'checksums', re.M)
            ectxt = regex.sub("checksums = []", ectxt)

        # toolchain
        tc_str = "toolchain = {'name': '%(name)s', 'version': '%(version)s'}" % updated_ecs[full_mod_name]['toolchain']
        regex = re.compile(key_pattern % 'toolchain', re.M)
        ectxt = regex.sub(tc_str, ectxt)

        # dependencies
        for key in ('builddependencies', 'dependencies'):
            deps_str = '%s = [\n' % key
            for dep in updated_ecs[full_mod_name][key]:
                deps_str += '    ' + str(dep) + ',\n'
            deps_str += ']'
            regex = re.compile(list_key_pattern % key, re.M)
            ectxt = regex.sub(deps_str, ectxt)

        specs = {
            'name': ec.name,
            'toolchain': updated_ecs[full_mod_name]['toolchain'],
            'version': new_version,
            'versionsuffix': ec['versionsuffix'],
        }

        ec_fn = '%s-%s.eb' % (ec.name, det_full_ec_version(specs))
        write_file(ec_fn, ectxt)
        print(term.green("%s written" % ec_fn))


if __name__ == '__main__':
    main()
