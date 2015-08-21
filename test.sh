#!/bin/bash

set -e

function set_state() {
    # set head of wip to pending
    _=$(curl -sX POST "https://api.github.com/repos/codecov/$1/statuses/$2" \
             -H "Authorization: token $GITHUB_TOKEN" \
             -d "{\"state\": \"$3\",\
                  \"target_url\": \"https://circleci.com/gh/codecov/testsuite/$CIRCLE_BUILD_NUM\",\
                  \"description\": \"$4\",\
                  \"context\": \"ci/testsuite\"}")
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

# set git globals
git config --global user.email "hello@codecov.io"
git config --global user.name "Codecov Test Bot"

repos=('example-java' 'example-scala' 'example-xcode')
total="${#repos[@]}"

urls=()
for repo in ${repos[*]}
do
    git clone -b future git@github.com:codecov/$repo.git
    cd "$repo"
    git commit --allow-empty -m "circle #$CIRCLE_BUILD_NUM"
    # https://developer.github.com/v3/repos/statuses/#get-the-combined-status-for-a-specific-ref
    url="$repo/commits/$(git rev-parse HEAD)/status"
    urls[$url]=url
    git push origin future
    cd ../
done

# wait for travis to pick up builds
echo -n "Waiting 2 minutes..."
sleep 120
echo "ok"

passed=0
while [ "${#urls[@]}" != "0" ]
do
    echo -n "Waiting 1 minute..."
    sleep 60
    echo "ok"
    # collect build numbers
    for i in ${!urls[@]}
    do
        url=urls[$i]
        echo -n "Checking $url..."
        state=$(curl -sX GET "https://api.github.com/repos/codecov/$url" | python -c "import sys,json;print(json.loads(sys.stdin.read())['state'])")
        echo "$state"
        if [ "$state" = "success" ];
        then
            # no longer need to check
            unset urls[$i]
            # record passed
            passed=$(expr $passed + 1)
        elif [ "$state" != "pending" ];
        then
            # no longer need to check
            unset urls[$i]
        fi
    done
done

# submit states
if [ "$passed" = "$total" ];
then
  status="success"
else
  status="failure"
fi

# set state status for heads
set_state "codecov-bash" "$codecovbash" "$status" "$passed/$total successful"
set_state "codecov-python" "$codecovpython" "$status" "$passed/$total successful"

if [ "$status" != "success" ];
then
  exit 1;
fi
