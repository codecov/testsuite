import os
import sys
import time
import requests
from json import dumps


headers = {"Authorization": "token "+os.getenv("GITHUB_TOKEN")}


def set_state(repo, commit, state):
    # set head of wip to pending
    res = requests.post("https://api.github.com/repos/codecov/%s/statuses/%s" % (repo, commit),
                        headers=headers,
                        data=dumps(dict(state=state,
                                        target_url="https://circleci.com/gh/codecov/testsuite/"+os.getenv("CIRCLE_BUILD_NUM"),
                                        context="ci/testsuite")))
    print(res.text)
    res.raise_for_status()


def get_head(repo, branch):
    res = requests.get("https://api.github.com/repos/codecov/%s/git/refs/heads/%s" % (repo, branch), headers=headers)
    print(res.text)
    res.raise_for_status()
    return res.json()['object']['sha']


def get_tree(repo, commit):
    res = requests.get("https://api.github.com/repos/codecov/%s/git/trees/%s" % (repo, commit), headers=headers)
    print(res.text)
    res.raise_for_status()
    return res.json()['sha']


# get head of wip branches
codecovbash = get_head('codecov-bash', 'wip')
codecovpython = get_head('codecov-python', 'wip')

# set pending status for heads
set_state("codecov-bash", codecovbash, "pending")
set_state("codecov-python", codecovpython, "pending")

try:
    repos = ['example-java', 'example-scala', 'example-xcode', 'example-c', 'example-lua', 'example-go', 'example-python', 'example-php']
    total = len(repos)

    # Make empty commit
    commits = []
    for repo in repos:
        # https://developer.github.com/v3/git/commits/#create-a-commit
        res = requests.post("https://api.github.com/repos/codecov/%s/git/commits" % repo, headers=headers,
                            data=dumps(dict(message="circle #$CIRCLE_BUILD_NUM",
                                            tree=get_tree(repo, 'future'),
                                            parents=[get_head(repo, 'future')],
                                            author=dict(name="Codecov Test Bot", email="hello@codecov.io"))))
        res.raise_for_status()
        commits[repo] = res.json()['sha']

    # wait for travis to pick up builds
    print("Waiting 4 minutes...")
    time.sleep(240)

    # Wait for CI Status
    passed = 0
    while len(commits) > 0:
        print("Waiting 1 minutes...")
        time.sleep(60)
        # collect build numbers
        for repo, commit in commits.items():
            print("Checking Travis %s at %s..." % (repo, commit))
            res = requests.get("https://api.github.com/repos/codecov/%s/commits/%s/status" % (repo, commit), headers=headers)
            print(res.text)
            res.raise_for_status()
            state = res.json()['state']
            print(state)
            assert state in ('success', 'pending')
            if state == 'success':
                print("Checking Codecov %s at %s..." % (repo, commit))
                future = requests.get("https://codecov.io/api/gh/codecov/%s?ref=%s" % (repo, commit))
                print(future.text)
                if future.status_code == 404:
                    assert commit in future.json()['queue'], "%s at %.7s is not in Codecov upload queue" % (repo, commit)
                    continue
                assert future.status_code == 200

                master = requests.get("https://codecov.io/api/gh/codecov/%s?branch=master" % repo)
                print(master.text)
                assert master.status_code == 200

                assert master.json()['report'] == future.json()['report'], "%s at %.7s reports do not match" % (repo, commit)

                commits.pop()
                passed = passed + 1

    # submit states
    status = 'success' if len(commits) == 0 else 'failure'

    # set state status for heads
    set_state("codecov-bash", codecovbash, status)
    set_state("codecov-python", codecovpython, status)

    sys.exit(status == 'failure')

except Exception:
    # set state status for heads
    set_state("codecov-bash", codecovbash, 'error')
    set_state("codecov-python", codecovpython, 'error')
    raise
