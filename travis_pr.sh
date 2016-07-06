#!/bin/bash

if [ -z $TRAVIS_TOKEN ]
then
    echo "ERROR: Travis token is not available, use 'travis token --org' to obtain it"
    exit 1
fi

if [ -z $1 ]
then
    echo "ERROR: Usage: $0 <PR#>"
    exit 2
fi
PR=$1

body="{
\"request\": {
    \"branch\": \"travis_framework\"
}}"
body="{
\"request\": {
    \"pull_request\": true,
    \"pull_request_number\": 900,
    \"branch\": \"develop\",
    \"event_type\": \"pull_request\"
}}"

echo "body: $body"

curl -s -X POST \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -H "Travis-API-Version: 3" \
  -H "Authorization: token $TRAVIS_TOKEN" \
  -d "$body" \
  https://api.travis-ci.org/repo/hpcugent%2Feasybuild-easyblocks/requests
