#!/bin/bash

if [ $# -ne 1 ]
then
    echo "Usage: $0 <prefix>" >&2
    exit 1
fi

PREFIX=$1

echo "{"
for mod in $(find ${PREFIX}/modules/all -type f | sed 's@.*modules/all/@@g');
do
    app=$(echo $mod | cut -f1 -d'/')
    binpaths=$($LMOD_CMD bash show $mod 2>&1 | grep 'prepend_path.*"PATH"' | sed 's/.*"\(.*\)")$/\1/g')
    for binpath in $binpaths
    do
        bins=$(find $binpath -type f -perm -0100 | sed 's@.*/@@g' | sed "s/^/'/g" | sed "s/$/'/g" | tr '\n' ',')
        echo "    '$app': [$bins],"
    done
done
echo "}"

#for app in $(ls $PREFIX);
#do
#    for bin in $(find ${PREFIX}/${app} -type f -perm -0100)
#    do
#        echo "$app => $bin"
#    done
#done
