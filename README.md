# github-dork-monitor

Runs a list of GitHub dorks through the GitHub Code Search and Repository
Search APIs, snapshots the hits, and on every run after the first tells you
only what is **new** since last time.

The diff is the point. A dork like `"yourbrand" api_key` has thousands of
standing results you have already triaged; what matters is the one that
appeared yesterday.

> Only point this at assets you are authorised to test. The dork list in this
> repo is placeholders — put your own scope in it.

## Install

Python 3.9+ and `requests`.

```bash
pip install requests
```

## Usage

```bash
python3 github_dorks.py -f dorks.txt -t "$GITHUB_TOKEN"
```

```bash
python3 github_dorks.py -f dorks.txt -t "$GITHUB_TOKEN" --mode code
```

```bash
python3 github_dorks.py -f dorks.txt -t "$GITHUB_TOKEN" --no-diff
```

Resume an interrupted run, skipping the first 12 dorks:

```bash
python3 github_dorks.py -f dorks.txt -t "$GITHUB_TOKEN" --resume 12
```

| Flag | Meaning |
|---|---|
| `-f`, `--file` | Dork list. One per line, `#` for comments. |
| `-t`, `--token` | GitHub PAT. Code Search needs an authenticated token. |
| `-n`, `--num` | Results per dork (max 30 for code search, 100 for repos). |
| `--mode` | `code`, `repos` or `both` (default). |
| `--resume N` | Skip the first N dorks. |
| `--no-diff` | Report every hit, not just new ones. |

Pass the token through an environment variable as above rather than typing it
inline — an argument lands in your shell history.

Output goes to `Dorks Outputs/<dorkfile>_output_<timestamp>.json`, and the next
run diffs against the most recent one in that folder automatically.

## Dork syntax

GitHub search, **not** Google. `site:` and `inurl:` do not exist here and come
back as HTTP 422.

```
"yourbrand"
"yourbrand" DB_PASSWORD
filename:.env "yourbrand"
extension:json "yourbrand" api_key
org:yourorg
user:yourorg
"api.example.com"
```

## Rate limits

Authenticated search is 30 requests/minute. The script sleeps 2.5s between
requests and, on a 403 or 429, honours `Retry-After` or `X-RateLimit-Reset`
before retrying. `--mode both` costs two requests per dork.

## Self-check

```bash
python3 test_github_dorks.py
```

Six offline assertions on the diff, because a broken diff does not crash — it
quietly reports everything as new for ever. They cover the three ways that
happens:

- **Repository hits.** These carry a top-level `html_url`, so keying them by URL
  never matched the seen-set of `full_name`s and every repo came back new on
  every run. Fixed, and pinned by the first assertion.
- **Blob SHAs.** A code hit's `html_url` embeds the commit SHA, which changes
  whenever anything in that repo is committed.
- **Percent-encoding.** GitHub returns the same path's spaces as a literal space
  or as `%20`, inconsistently, within a single run.

Each assertion has been shown to fail when its fix is reverted. A test that has
only ever passed is not evidence.
