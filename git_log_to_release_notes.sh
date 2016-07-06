 git log | grep 'Merge pull' -A 5 | egrep -v "^commit|^Author|^Merge: " | sed 's/^--/@/g' | tr '\n' ' ' | tr '@' '\n' | sed 's/.*Merge pull request #\([^ ]*\).* from [^ ]*[ ]*\(.*\)/\2 (#\1)/g'
