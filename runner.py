import os
import sys
import time
import logging
import requests
import subprocess
from json import dumps
from difflib import unified_diff

# https://urllib3.readthedocs.org/en/latest/security.html#insecureplatformwarning
logging.captureWarnings(True)

headers = {"Authorization": "token "+os.getenv("GITHUB_TOKEN")}
circleurl = "https://circleci.com/gh/codecov/testsuite/"+os.getenv("CIRCLE_BUILD_NUM")


def curl(method, reraise=True, *args, **kwargs):
    "wrapper to only print on errors"
    res = getattr(requests, method)(*args, **kwargs)
    try:
        res.raise_for_status()
    except:
        print(res.text)
        if reraise:
            raise
    return res


def bash(cmd):
    return subprocess.check_output(cmd, shell=True).decode('utf-8')


def set_state(slug, commit, state, context, description=None):
    # set head of wip to pending
    return curl('post', "https://api.github.com/repos/%s/statuses/%s" % (slug, commit),
                headers=headers,
                data=dumps(dict(state=state,
                                description=description,
                                target_url=circleurl,
                                context=context)))


def get_head(slug, branch):
    print(slug + " \033[92mGet head\033[0m")
    res = curl('get', "https://api.github.com/repos/%s/git/refs/heads/%s" % (slug, branch), headers=headers)
    return res.json()['object']['sha']


def get_tree(slug, commit):
    print(slug + " \033[92mGet tree\033[0m")
    res = curl('get', "https://api.github.com/repos/%s/git/commits/%s" % (slug, commit), headers=headers)
    return res.json()['tree']['sha']


def update_reference(slug, ref, commit):
    print(slug + " \033[92mPatch reference\033[0m")
    curl('patch', "https://api.github.com/repos/%s/git/refs/heads/%s" % (slug, ref), headers=headers,
         data=dumps(dict(sha=commit)))
    return True


try:
    repos = ['codecov/example-java', 'codecov/example-scala', 'codecov/example-xcode', 'codecov/example-c',
             'codecov/example-lua', 'codecov/example-go', 'codecov/example-python', 'codecov/example-php',
             'codecov/example-d', 'codecov/example-fortran', 'codecov/example-swift']
    total = len(repos)

    lang = os.getenv('TEST_LANG', 'bash')
    slug = os.getenv('TEST_SLUG', 'codecov/codecov-'+lang)
    sha = os.getenv('TEST_SHA', 'master')
    cmd = os.getenv('TEST_CMD', None)
    if not cmd:
        if lang == 'python':
            repos.remove('codecov/example-swift')  # bash only atm because https://travis-ci.org/codecov/example-xcode/builds/83448813
            repos.remove('codecov/example-xcode')  # bash only atm because https://travis-ci.org/codecov/example-xcode/builds/83448813
            cmd = 'pip install --user git+https://github.com/%s.git@%s && codecov' % (slug, sha)
        elif lang == 'bash':
            repos.remove('codecov/example-c')  # python only
            cmd = 'bash <(curl -s https://raw.githubusercontent.com/%s/%s/codecov)' % (slug, sha)

    # Make empty commit
    commits = {}
    for _slug in repos:
        # set pending status
        set_state(slug, sha, "pending", _slug)

        # https://developer.github.com/v3/git/commits/#create-a-commit
        head = get_head(_slug, 'future')
        tree = get_tree(_slug, head)
        print(_slug + " \033[92mPost commit\033[0m")
        args = (os.getenv('CIRCLE_BUILD_NUM'), circleurl, cmd.replace(' --user', '') if 'python' in _slug else cmd)
        res = curl('post', "https://api.github.com/repos/%s/git/commits" % _slug,
                   headers=headers,
                   data=dumps(dict(message="Circle build #%s\n%s\n%s" % args,
                                   tree=tree,
                                   parents=[head],
                                   author=dict(name="Codecov Test Bot", email="hello@codecov.io"))))
        _sha = res.json()['sha']
        print("    Sha: " + _sha)
        update_reference(_slug, 'future', _sha)
        commits[_slug] = _sha

    # wait for travis to pick up builds
    print("Waiting 4 minutes...")
    time.sleep(240)

    # Wait for CI Status
    passed, total = 0, len(commits)
    while len(commits) > 0:
        print("Waiting 1 minutes...")
        time.sleep(60)
        # collect build numbers
        for _slug, commit in commits.items():
            try:
                res = curl('get', "https://api.github.com/repos/%s/commits/%s/status" % (_slug, commit), headers=headers).json()
                state = res['state']
                if state == 'pending':
                    continue

                print(_slug)
                print('    \033[92mCI Status:\033[0m ' + state + ' @ ' + res['statuses'][0]['target_url'])

                # ASSERT status must be successful
                assert state == 'success', "CI status %s" % state

                # get future report
                future = curl('get', "https://codecov.io/api/gh/%s?ref=%s" % (_slug, commit), reraise=False)
                if future.status_code == 404:
                    # ASSERT is queued for processing
                    assert commit in future.json()['queue'], "%s at %.7s is not in Codecov upload queue" % (_slug, commit)
                    # it is...try again later
                    print("   In queue...")
                    continue

                assert future.status_code == 200, "Codecov returned %d" % future.status_code

                future = future.json()['report']

                # get master report to compare against
                master = curl('get', "https://codecov.io/api/gh/%s?branch=master" % _slug).json()['report']
                # reports must be 100% identical
                if master == future:
                    print("    Report passed!")
                    set_state(slug, sha, 'success', _slug)
                    passed += 1

                else:
                    diff = unified_diff(dumps(master, indent=2, sort_keys=True).split('\n'),
                                        dumps(future, indent=2, sort_keys=True).split('\n'),
                                        fromfile='master', tofile='future')
                    # https://developer.github.com/v3/gists/#edit-a-gist
                    res = curl('post', 'https://api.github.com/gists', headers=headers,
                               data=dumps(dict(description=_slug, files={"diff.diff": {"content": "".join((diff.next(), diff.next(), diff.next(), "\n".join(diff)))}})))
                    print("    Report Failed.")
                    set_state(slug, sha, 'failed', _slug, res.json()['html_url'])

                del commits[_slug]

            except Exception as e:
                set_state(slug, sha, 'error', _slug, str(e))
                print('    '+str(e))
                del commits[_slug]

    sys.exit(passed < total)

except Exception:
    [set_state(slug, sha, 'error', _slug, str(e)) for _slug in commits.keys()]
    raise
