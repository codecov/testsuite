#!/bin/bash

set -e

git config --global user.email "hello@codecov.io"
git config --global user.name "Codecov Test Bot"

for repo in 'example-java' 'example-scala' 'example-xcode'
do
    git clone -b future git@github.com:codecov/$repo.git
    cd "$repo"
    git commit --allow-empty -m "circle #$CIRCLE_BUILD_NUM"
    git push origin future
    cd ../
done

# wait for all ci to complete
# ...todo
