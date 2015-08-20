#!/bin/bash

set -e

git config --global user.email "hello@codecov.io"
git config --global user.name "Codecov Test Bot"

repos=('example-java' 'example-scala' 'example-xcode')
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
        state=$(curl -s "$url" | python -c "import sys,json;print(json.loads(sys.stdin.read())['state'])")
        echo -n "$state"
        if [ "$state" = "success" ];
        then
            url=${urls[@]/"$url"}
        elif [ "$state" != "pending" ];
        then
            exit 1;
        fi
    done
done
