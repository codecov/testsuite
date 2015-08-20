#!/bin/bash

set -e

function set_state() {
    # set head of wip to pending
    curl -X POST "https://api.github.com/repos/codecov/$1/statuses/$2" \
         -H 'Authorization: token $GITHUB_TOKEN'
         -d "{\"state\": \"$3\",\
              \"target_url\": \"https://circleci.com/gh/codecov/testsuite/$CIRCLE_BUILD_NUM\",\
              \"description\": \"$4\",\
              \"context\": \"ci/testsuite\"}"
}

function get_head() {
    res=$(curl -sX GET "https://api.github.com/repos/codecov/$1/git/refs/heads/wip" | python -c "import sys,json;print(json.loads(sys.stdin.read())['object']['sha'])")
    echo "$res"
}

# get head of wip branches
codecovbash=$(get_head 'codecov-bash')
codecovpython=$(get_head 'codecov-python')

# set pending status for heads
set_state "codecov-bash" "$codecovbash" "pending" "Pending..."
set_state "codecov-python" "$codecovpython" "pending" "Pending..."

git config --global user.email "hello@codecov.io"
git config --global user.name "Codecov Test Bot"

repos=('example-java' 'example-scala' 'example-xcode')
total="${#repos[@]}"
passed=0

urls=()
for repo in ${repos[*]}
do
    git clone -b future git@github.com:codecov/$repo.git
    cd "$repo"
    git commit --allow-empty -m "circle #$CIRCLE_BUILD_NUM"
    # https://developer.github.com/v3/repos/statuses/#get-the-combined-status-for-a-specific-ref
    urls+=("https://api.github.com/repos/codecov/$repo/commits/$(git rev-parse --HEAD)/status")
    git push origin future
    cd ../
done

# wait for travis to pick up builds
sleep 30

while [ "${#urls[@]}" != "0" ]
do
    sleep 10
    # collect build numbers
    for url in ${urls[*]}
    do
        echo "Checking $url..."
        state=$(curl -sX GET "$url" | python -c "import sys,json;print(json.loads(sys.stdin.read())['state'])")
        echo -n "$state"
        if [ "$state" = "success" ];
        then
            # no longer need to check
            url=${urls[@]/"$url"}
            # record passed
            passed=$(expr $passed + 1)
        elif [ "$state" != "pending" ];
        then
            # no longer need to check
            url=${urls[@]/"$url"}
        fi
    done
done

# submit states
if [ "$passed" = "$total" ];
then
  state="success"
else
  state="failure"
fi

# set state status for heads
set_state "codecov-bash" "$codecovbash" "$state" "$passed/$total successful"
set_state "codecov-python" "$codecovpython" "$state" "$passed/$total successful"

if [ "$state" != "success" ];
then
  exit 1;
fi
