#!/bin/bash

ARCHIVE_DIR=easybuild/easyconfigs/__archive__
mkdir -p $ARCHIVE_DIR
cat > $ARCHIVE_DIR/README << EOF
This directory contains archived easyconfig files.

Reasons for archiving easyconfigs include:
* old/obsolete software versions
* use of deprecated toolchains

These easyconfig may or may not work with current version of EasyBuild.
They are no longer actively maintained, and they are no longer included in the regression testing that is done for every new EasyBuild release.

Use them with care, and consider to use more recent easyconfigs for the respective software packages instead.
EOF

#for pattern in 'cgmpolf-1.1.6' 'cgoolf-1.1.7' 'cgmpich-1.1.6' 'cgmvapich2-1.1.12rc1' 'cgmvapich2-1.2.7' 'cgompi-1.1.7' 'ClangGCC-1.1.3' 'ClangGCC-1.2.3' 'ClangGCC-1.3.0' 'cgmvolf-1.1.12rc1' 'cgmvolf-1.2.7'
#for pattern in 'gimkl-1.5.9' 'gimpi-1.5.9'
#for pattern in 'gmacml-1.7.0' 'gmpolf-1.4.8' 'gmpich-1.4.8'
#for pattern in 'gmvolf-1.7.12' 'gmvolf-1.7.12rc1' 'gmvapich2-1.1.0' 'gmvapich2-1.6.7' 'gmvapich2-1.7.12' 'gmvapich2-1.7.12rc1' 'gmvapich2-1.7.9a2'
#for pattern in 'goalf-1.1.0' 'goalf-1.5.12' 'gompi-1.1.0' 'gompi-1.5.12'
#for pattern in 'goolf-1.4.10-no-OFED' 'gompi-1.4.10-no-OFED' 'goolf-1.5.14-no-OFED' 'gompi-1.5.14-no-OFED' 'gompi-1.4.12-no-OFED'
#for pattern in 'goolfc-1.3.12' 'gompi-1.3.12' 'goolfc-1.4.10' 'goolfc-2.6.10' 'goolfc-2.6.10' 'gompic-2.6.10' 'gcccuda-2.6.10'
#for pattern in 'ictce-3.2.2.u3' 'iimpi-3.2.2.u3' 'iccifort-11.1.073' 'ictce-3.2.2.u3-32bit' 'iimpi-3.2.2.u3-32bit' 'iccifort-11.1.073-32bit' 'ictce-4.0.6' 'iimpi-4.0.6' 'iccifort-2011.6.233' 'ictce-4.0.10' 'iimpi-4.0.10' 'iccifort-2011.10.319' 'ictce-4.1.13' 'iimpi-4.1.13' 'iccifort-2011.13.367' 'ictce-5.1.1' 'iimpi-5.1.1' 'iccifort-2013.1.117' 'ictce-6.0.5' 'iimpi-6.0.5' 'iccifort-2013_sp1.0.080' 'ictce-6.3.5' 'iimpi-6.3.5' 'iccifort-2013_sp1.3.174'
#for pattern in 'intel-para-2014.12' 'ipsmpi-2014.12' 'gpsolf-2014.12' 'gpsmpi-2014.12'
#for pattern in 'iomkl-4.6.13' 'iompi-4.6.13' 'iomkl-6.6.2' 'iompi-6.6.2' 'iompi-6.6.4'
for pattern in 'iqacml' 'iiqmpi' 'QLogicMPI'

do
    name=$(echo $pattern | cut -f1 -d'-')
    for ec in `find easybuild/easyconfigs -name "*-${pattern}*.eb" | grep -v $ARCHIVE_DIR` `find easybuild/easyconfigs -name "${pattern}*.eb" | grep -v $ARCHIVE_DIR`
    do
        subdir=`dirname $ec | sed 's@^easybuild/easyconfigs/@@g'`
        mkdir -p $ARCHIVE_DIR/$subdir
        git mv $ec $ARCHIVE_DIR/$subdir/$(basename $ec)
    done
done
