import os
import sys
import time
import logging
import requests
import traceback
from json import dumps
from difflib import unified_diff

# https://urllib3.readthedocs.org/en/latest/security.html#insecureplatformwarning
logging.captureWarnings(True)

headers = {'Authorization': 'token '+os.getenv("GITHUB_TOKEN"), 'User-Agent': 'Codecov Debug'}
circleurl = "https://circleci.com/gh/codecov/testsuite/"+os.getenv("CIRCLE_BUILD_NUM")


def curl(method, *args, **kwargs):
    "wrapper to only print on errors"
    reraise = kwargs.pop('reraise', None)
    res = getattr(requests, method)(*args, **kwargs)
    try:
        res.raise_for_status()
    except:
        print(str(res.status_code) + ' -> ' + res.text)
        if reraise:
            raise
    return res


def set_state(slug, commit, state, context, description=None, url=None):
    # set head of wip to pending
    print("    \033[92mpost status\033[0m " + state)
    return curl('post', "https://api.github.com/repos/%s/statuses/%s" % (slug, commit),
                headers=headers,
                data=dumps(dict(state=state,
                                description=description,
                                target_url=url or circleurl,
                                context=context)))


def get_head(slug, branch):
    print("    \033[92mget head\033[0m")
    res = curl('get', "https://api.github.com/repos/%s/git/refs/heads/%s" % (slug, branch), headers=headers)
    return res.json()['object']['sha']


def get_tree(slug, commit):
    print("    \033[92mget tree\033[0m")
    res = curl('get', "https://api.github.com/repos/%s/git/commits/%s" % (slug, commit), headers=headers)
    return res.json()['tree']['sha']


def update_reference(slug, ref, commit):
    print("    \033[92mpatch reference\033[0m")
    curl('patch', "https://api.github.com/repos/%s/git/refs/heads/%s" % (slug, ref), headers=headers,
         data=dumps(dict(sha=commit)))
    return True


repos = ['codecov/example-java', 'codecov/example-scala', 'codecov/example-xcode', 'codecov/example-c',
         'codecov/example-lua', 'codecov/example-go', 'codecov/example-python', 'codecov/example-php',
         'stevepeak/pykafka',  # contains python and C
         'codecov/example-node', 'codecov/example-d', 'codecov/example-fortran', 'codecov/example-swift']

lang = os.getenv('TEST_LANG')
if lang is None:
    sys.exit(0)

slug = os.getenv('TEST_SLUG')
sha = os.getenv('TEST_SHA')
cmd = os.getenv('TEST_CMD', None)
codecov_url = os.getenv('TEST_URL', 'https://codecov.io')
if not cmd:
    if lang == 'python':
        repos.remove('codecov/example-swift')  # bash only atm because https://travis-ci.org/codecov/example-xcode/builds/83448813
        repos.remove('codecov/example-xcode')  # bash only atm because https://travis-ci.org/codecov/example-xcode/builds/83448813
        cmd = 'pip install --user git+https://github.com/%s.git@%s && codecov -u %s' % (slug, sha, codecov_url)
    elif lang == 'bash':
        repos.remove('codecov/example-c')  # python only
        cmd = 'bash <(curl -s https://raw.githubusercontent.com/%s/%s/codecov) -u %s' % (slug, sha, codecov_url)
    elif lang == 'node':
        repos.remove('codecov/example-xcode')
        repos.remove('codecov/example-swift')
        cmd = 'npm install -g %s#%s && codecov -u %s' % (slug, sha, codecov_url)

try:
    # Make empty commit
    commits = {}
    for _slug in repos:
        print(_slug)
        # set pending status
        set_state(slug, sha, "pending", _slug)

        # https://developer.github.com/v3/git/commits/#create-a-commit
        head = get_head(_slug, 'future')
        tree = get_tree(_slug, head)
        print("    \033[92mpost commit\033[0m")
        args = (os.getenv('CIRCLE_BUILD_NUM'), circleurl, cmd.replace(' --user', '') if 'python' in _slug else cmd)
        res = curl('post', "https://api.github.com/repos/%s/git/commits" % _slug,
                   headers=headers,
                   data=dumps(dict(message="Circle build #%s\n%s\n%s" % args,
                                   tree=tree,
                                   parents=[head],
                                   author=dict(name="Codecov Test Bot", email="hello@codecov.io"))))
        _sha = res.json()['sha']
        print("    new commit: " + _sha)
        update_reference(_slug, 'future', _sha)
        commits[_slug] = _sha

    # wait for travis to pick up builds
    print("==================================================\nWaiting 3 minutes...\n==================================================")
    time.sleep(60 * 3)

    # Wait for CI Status
    passed = 0
    while len(commits) > 0:
        print("====================================================\nWaiting 1 minute...\n====================================================")
        time.sleep(60)
        # collect build numbers
        for _slug, commit in commits.items():
            try:
                res = curl('get', "https://api.github.com/repos/%s/commits/%s/status" % (_slug, commit), headers=headers).json()
                state = res['state']
                print(_slug)
                if len(res['statuses']) == 0:
                    continue
                travis_target_url = res['statuses'][0]['target_url']
                print('    \033[92mCI Status:\033[0m ' + state + ' @ ' + travis_target_url)

                if state == 'pending':
                    set_state(slug, sha, 'pending', _slug, url=travis_target_url)
                    continue

                # ASSERT status must be successful
                assert state == 'success', "CI status %s" % state

                # get future report
                future = curl('get', codecov_url+'/api/gh/%s?ref=%s' % (_slug, commit), reraise=False)
                if future.status_code == 404:
                    # ASSERT is queued for processing
                    assert commit in future.json()['queue'], "%s at %.7s is not in Codecov upload queue" % (_slug, commit)
                    # it is...try again later
                    print("   In queue...")
                    continue

                assert future.status_code == 200, "Codecov returned %d" % future.status_code

                future = future.json()
                if future['waiting']:
                    print("   In processing queue...")
                    continue

                future = future['report']

                # get master report to compare against
                master = curl('get', codecov_url+'/api/gh/%s?branch=master' % _slug).json()['report']
                # reports must be 100% identical
                if master == future:
                    print("    Report passed!")
                    set_state(slug, sha, 'success', _slug, url=travis_target_url)
                    passed += 1

                else:
                    diff = unified_diff(dumps(master, indent=2, sort_keys=True).split('\n'),
                                        dumps(future, indent=2, sort_keys=True).split('\n'),
                                        fromfile='master', tofile='future')
                    print("    \033[92mcreate gist\033[0m")
                    # https://developer.github.com/v3/gists/#create-a-gist
                    res = curl('post', 'https://api.github.com/gists', headers=headers,
                               data=dumps(dict(description=_slug.replace('/', ' '),
                                               files={"diff.diff": {"content": "".join((diff.next(), diff.next(), diff.next(), "\n".join(diff)))}})))
                    gist_url = res.json()['html_url']
                    print("    Report Failed. " + gist_url)
                    set_state(slug, sha, 'failure', _slug, url=gist_url)

                del commits[_slug]

            except Exception as e:
                set_state(slug, sha, 'error', _slug, str(e), url=travis_target_url)
                traceback.print_exception(*sys.exc_info())
                del commits[_slug]

    sys.exit(passed < len(repos))

except Exception as e:
    [set_state(slug, sha, 'error', _slug, str(e)) for _slug in commits.keys()]
    raise
