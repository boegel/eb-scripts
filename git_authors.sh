#!/bin/bash
for author in `git log | grep Author | sort -u | sed 's/^Author: .* <\(.*\)>/\1/g'`
do
    echo -n "$author;"
    git log --author "$author" | grep ^Date | sed 's/^Date:[ ]*//g' | tail -1 | sed 's/^[^ ]*[ ]*\([^ ]*\)[ ]*\([^ ]*\)[ ]*[^ ]*[ ]*\([^ ]*\)[ ]*.*/\2 \1 \3/g'
done
