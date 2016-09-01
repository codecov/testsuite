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

headers = {'Authorization': 'token '+os.getenv('GITHUB_TOKEN'), 'User-Agent': 'Codecov Debug'}
circleurl = 'https://circleci.com/gh/codecov/testsuite/%s' % os.getenv('CIRCLE_BUILD_NUM')


def save(path, filename, data):
    folder = os.path.join(os.getenv('CIRCLE_ARTIFACTS'), path)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    with open(os.path.join(folder, filename), 'w+') as f:
        f.write(data)


def curl(method, *args, **kwargs):
    "wrapper to only print on errors"
    reraise = kwargs.pop('reraise', None)
    res = getattr(requests, method)(*args, **kwargs)
    try:
        res.raise_for_status()
    except:
        print str(res.status_code) + ' -> ' + res.text
        if reraise:
            raise
    return res


def post_slack(text):
    requests.post(os.getenv('SLACK_URL'),
                  headers={'Content-Type': 'application/json'},
                  data=dumps(dict(text=text,
                                  author='Nightly Testsuite',
                                  author_link=url)))
    

def set_state(slug, commit, state, context, description=None, url=None):
    return curl('post', "https://api.github.com/repos/%s/statuses/%s" % (slug, commit),
                headers=headers,
                data=dumps(dict(state=state,
                                description=description,
                                target_url=url or circleurl,
                                context=context)))


def get_head(slug, branch):
    res = curl('get', "https://api.github.com/repos/%s/git/refs/heads/%s" % (slug, branch), headers=headers)
    return res.json()['object']['sha']


def get_tree(slug, commit):
    res = curl('get', "https://api.github.com/repos/%s/git/commits/%s" % (slug, commit), headers=headers)
    return res.json()['tree']['sha']


def update_reference(slug, ref, commit):
    curl('patch', "https://api.github.com/repos/%s/git/refs/heads/%s" % (slug, ref), headers=headers,
         data=dumps(dict(sha=commit)))
    return True


repos = ['codecov/example-java', 'codecov/example-scala', 'codecov/example-objc', 'codecov/example-c',
         'codecov/example-lua', 'codecov/example-go', 'codecov/example-python', 'codecov/example-php',
         'codecov/example-d', 'codecov/example-fortran', 'codecov/example-swift']
no_py_user = ['codecov/example-python', ]
total = len(repos)

lang = os.getenv('TEST_LANG')
if lang is None:
    sys.exit(0)

slug = os.getenv('TEST_SLUG')
sha = os.getenv('TEST_SHA')
if len(sha) != 40:
    # get head of branch
    sha = get_head(slug, sha)

cmd = os.getenv('TEST_CMD', None)
codecov_url = os.getenv('TEST_URL', 'https://codecov.io')
if not cmd:
    if lang == 'python':
        repos.remove('codecov/example-swift')  # bash only atm because https://travis-ci.org/codecov/example-objc/builds/83448813
        repos.remove('codecov/example-objc')  # bash only atm because https://travis-ci.org/codecov/example-objc/builds/83448813
        cmd = 'pip install --user git+https://github.com/%s.git@%s && codecov -v -u %s' % (slug, sha, codecov_url)
    elif lang == 'bash':
        repos.remove('codecov/example-c')  # python only
        cmd = 'bash <(curl -s https://raw.githubusercontent.com/%s/%s/codecov) -v -u %s' % (slug, sha, codecov_url)
    elif lang == 'node':
        repos.remove('codecov/example-objc')
        repos.remove('codecov/example-swift')
        cmd = 'npm install -g %s#%s && codecov -u %s' % (slug, sha, codecov_url)


set_state(slug, sha, "pending", 'testsuite')

try:
    # Make empty commit
    commits = {}
    for _slug in repos:
        print '\n'+_slug
        # set pending status
        set_state(slug, sha, "pending", _slug)

        # https://developer.github.com/v3/git/commits/#create-a-commit
        head = get_head(_slug, 'future')
        tree = get_tree(_slug, head)
        print "    \033[92mpost commit\033[0m"
        args = (os.getenv('CIRCLE_BUILD_NUM'), circleurl, cmd.replace(' --user', '') if _slug in no_py_user else cmd)
        res = curl('post', 'https://api.github.com/repos/%s/git/commits' % _slug,
                   headers=headers,
                   data=dumps(dict(message="Circle build #%s\n%s\n%s" % args,
                                   tree=tree,
                                   parents=[head],
                                   author=dict(name="Codecov Test Bot", email="hello@codecov.io"))))
        _sha = res.json()['sha']
        print "    \033[92mnew commit\033[0m " + _sha
        update_reference(_slug, 'future', _sha)
        commits[_slug] = _sha

    # wait for travis to pick up builds
    print "==================================================\nWaiting 3 minutes...\n=================================================="
    time.sleep(60 * 3)

    # Wait for CI Status
    passed = 0
    while len(commits) > 0:
        print "====================================================\nWaiting 1 minute...\n===================================================="
        time.sleep(60)
        # collect build numbers
        for _slug, commit in commits.items():
            try:
                res = curl('get', 'https://api.github.com/repos/%s/commits/%s/status' % (_slug, commit),
                           headers=headers).json()
                state = res['state']
                print _slug
                if len(res['statuses']) == 0:
                    continue
                travis_target_url = res['statuses'][0]['target_url']
                print '    \033[92mCI Status:\033[0m ' + state + ' @ ' + travis_target_url

                if state == 'pending':
                    set_state(slug, sha, 'pending', _slug, url=travis_target_url)
                    continue

                # ASSERT status must be successful
                assert state == 'success', "CI status %s" % state

                # get future report
                future = curl('get', '%s/api/gh/%s/commit/%s?src=extension' % (codecov_url, _slug, commit),
                              reraise=False)

                # assert commit found
                assert future.status_code == 200, "Codecov returned %d" % future.status_code

                future = future.json()
                # retry if pending
                if future['commit']['state'] == 'pending':
                    print "   State: pending"
                    continue

                future = dumps(future['commit']['report'], indent=2, sort_keys=True)
                save(_slug, 'future.json', future)

                # get master report to compare against
                head_of_master = curl('get', '%s/api/gh/%s/branch/master' % (codecov_url, _slug)).json()['commit']['commitid']
                master = curl('get', '%s/api/gh/%s/commit/%s?src=extension' % (codecov_url, _slug, head_of_master))
                master = dumps(master.json()['commit']['report'], indent=2, sort_keys=True)
                save(_slug, 'master.json', master)

                # reports must be 100% identical
                if master == future:
                    print "    Report passed!"
                    set_state(slug, sha, 'success', _slug, url=travis_target_url)
                    passed += 1

                else:
                    diff = unified_diff(master.split('\n'), future.split('\n'),
                                        fromfile='master', tofile='future')
                    diff = ''.join((diff.next(), diff.next(), diff.next(), '\n'.join(list(diff))))
                    print diff
                    save(_slug, 'report.diff', diff)

                    print "    Report Failed. "
                    set_state(slug, sha, 'failure', _slug, circleurl+'#artifacts')

                del commits[_slug]

            except Exception as e:
                set_state(slug, sha, 'error', _slug, str(e), url=travis_target_url)
                if type(e) is AssertionError:
                    print "    \033[91mFailure\033[0m", str(e)
                else:
                    traceback.print_exception(*sys.exc_info())
                del commits[_slug]

    set_state(slug, sha, 'success' if passed == len(repos) else 'failure', 'testsuite', '%s/%s passed' % (passed, total))
    post_slacck('%s passed, %s failed' % (passed, total))
    sys.exit(passed < len(repos))

except Exception as e:
    [set_state(slug, sha, 'error', _slug, str(e)) for _slug in commits.keys()]
    set_state(slug, sha, 'error', 'testsuite', '%s/%s passed' % (passed, total))
    post_slacck('%s passed, %s failed' % (passed, total))
    raise
