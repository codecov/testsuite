#!/bin/bash

set -e

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
