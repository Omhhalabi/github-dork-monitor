"""Self-check for the run-to-run diff. Offline, stdlib only.

The diff is the whole point of this tool, and it is the part that fails
silently: a broken identity key does not crash, it just reports everything as
new for ever. These assertions pin the two ways that happened.

    python3 test_github_dorks.py
"""
import sys
import types

# the module imports requests at import time; this test never makes a request
sys.modules.setdefault("requests", types.ModuleType("requests"))
sys.modules["requests"].Response = object       # used in a type annotation
sys.modules["requests"].exceptions = types.SimpleNamespace(
    ConnectionError=ConnectionError, RequestException=Exception)

import github_dorks as g  # noqa: E402

fails = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)


# A previous run holding one code hit and one repo hit.
previous = {"results": [{
    "dork": 'org:targetorg password',
    "code_results": [{"html_url": "https://github.com/o/r/blob/abc1234/src/a b.py"}],
    "repo_results": [{"full_name": "o/r",
                      "html_url": "https://github.com/o/r"}],
}]}
seen = g.build_seen_index(previous)['org:targetorg password']

# 1. The regression this repo was published with fixed: repo-search hits carry a
#    top-level html_url, so keying them by URL never matched the full_name set
#    and every repository came back "new" on every single run.
check("a repo hit seen last run is not new again",
      g.diff_results([{"full_name": "o/r",
                       "html_url": "https://github.com/o/r"}], seen["repos"]) == [])

check("an unseen repo IS new",
      len(g.diff_results([{"full_name": "o/other",
                           "html_url": "https://github.com/o/other"}],
                         seen["repos"])) == 1)

# 2. Code hits must survive both drifts canon_code_url exists for: the blob SHA
#    changes on every commit touching the repo, and GitHub returns the same
#    path's spaces as ' ' or '%20' inconsistently within one run.
check("a code hit with a new blob SHA is not new again",
      g.diff_results([{"html_url":
                       "https://github.com/o/r/blob/9999999/src/a b.py"}],
                     seen["code"]) == [])

check("a code hit percent-encoded differently is not new again",
      g.diff_results([{"html_url":
                       "https://github.com/o/r/blob/abc1234/src/a%20b.py"}],
                     seen["code"]) == [])

check("a genuinely different file IS new",
      len(g.diff_results([{"html_url":
                           "https://github.com/o/r/blob/abc1234/src/other.py"}],
                         seen["code"])) == 1)

# 3. First run has nothing to compare against, so everything is new.
check("an empty previous run reports every hit as new",
      len(g.diff_results([{"full_name": "o/r"}], set())) == 1)

print()
if fails:
    print(f"{len(fails)} FAILED: {fails}")
    sys.exit(1)
print("all diff self-checks passed")
