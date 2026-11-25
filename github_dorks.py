#!/usr/bin/env python3
"""
github_dorks.py — CTI GitHub Dorking via REST + Code Search API
----------------------------------------------------------------
Usage:
    python3 github_dorks.py -f dorks.txt -t YOUR_GITHUB_TOKEN
    python3 github_dorks.py -f dorks.txt -t YOUR_GITHUB_TOKEN -o results.json --mode code
    python3 github_dorks.py -f dorks.txt -t YOUR_GITHUB_TOKEN --mode both

Dork file format (one dork per line, # = comment):
    # Secrets exposure
    filename:.env DB_PASSWORD
    extension:json "api_key"
    org:targetorg password

Modes:
    code   — GitHub Code Search (searches file contents)
    repos  — GitHub Repository Search
    both   — Run both (default)

Rate limits (authenticated):
    Code Search:  30 req/min
    REST Search:  30 req/min
    → script auto-throttles with 2s+ buffer per request
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus, unquote

import requests


# ── Config ─────────────────────────────────────────────────────────────────────
GITHUB_CODE_SEARCH_URL = "https://api.github.com/search/code"
GITHUB_REPO_SEARCH_URL = "https://api.github.com/search/repositories"
GITHUB_CODE_BROWSER_URL = "https://github.com/search?type=code&q="
GITHUB_REPO_BROWSER_URL = "https://github.com/search?type=repositories&q="
DEFAULT_OUTPUT         = "output.json"  # base fallback; use -o to set your filename
DEFAULT_PER_PAGE       = 30      # max per request for code search
CODE_SEARCH_DELAY      = 2.5     # seconds between requests (stay under 30/min)
REPO_SEARCH_DELAY      = 2.5


# ── Helpers ────────────────────────────────────────────────────────────────────

def build_headers(token: str) -> dict:
    return {
        "Authorization":        f"token {token}",
        "Accept":               "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def check_token(token: str):
    """Validate token against /user endpoint."""
    r = requests.get("https://api.github.com/user", headers=build_headers(token), timeout=15)
    if r.status_code == 401:
        print("[ERROR] GitHub token invalid or expired.")
        sys.exit(1)
    if r.status_code == 200:
        login = r.json().get("login", "unknown")
        print(f"[+] Authenticated as: {login}")
    else:
        print(f"[WARN] Token check returned HTTP {r.status_code} — continuing anyway.")


def load_dorks(filepath: str) -> list[str]:
    path = Path(filepath)
    if not path.exists():
        print(f"[ERROR] Dork file not found: {filepath}")
        sys.exit(1)

    dorks = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                dorks.append(stripped)

    if not dorks:
        print("[ERROR] No dorks found in file.")
        sys.exit(1)

    print(f"[+] Loaded {len(dorks)} dorks from {filepath}")
    return dorks


def handle_rate_limit(response: requests.Response, delay: float):
    """
    Handle 403/429 rate-limit responses.
    Respects Retry-After or X-RateLimit-Reset headers.
    """
    if response.status_code in (403, 429):
        retry_after = response.headers.get("Retry-After")
        reset_epoch  = response.headers.get("X-RateLimit-Reset")

        if retry_after:
            wait = int(retry_after) + 2
        elif reset_epoch:
            wait = max(0, int(reset_epoch) - int(time.time())) + 2
        else:
            wait = 60  # fallback

        print(f"  [!] Rate limited — waiting {wait}s...")
        time.sleep(wait)
        return True
    return False


def search_code(query: str, token: str, per_page: int) -> tuple:
    """GitHub Code Search — searches file contents and paths."""
    headers  = build_headers(token)
    params   = {"q": query, "per_page": per_page}
    fallback = (0, GITHUB_CODE_BROWSER_URL + quote_plus(query), [])
    max_retries = 3

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(GITHUB_CODE_SEARCH_URL, headers=headers, params=params, timeout=30)
        except requests.exceptions.ConnectionError as e:
            wait = 5 * attempt  # 5s, 10s, 15s
            print(f"  [WARN] Connection error (attempt {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                print(f"  ↳ Retrying in {wait}s...")
                time.sleep(wait)
                continue
            print(f"  [ERR] Giving up on: {query}")
            return fallback
        except requests.exceptions.RequestException as e:
            print(f"  [WARN] Request failed: {e}")
            return fallback

        if handle_rate_limit(r, CODE_SEARCH_DELAY):
            continue  # retry after wait

        if r.status_code == 422:
            print(f"  [WARN] Invalid query (422): {query}")
            return fallback

        if not r.ok:
            print(f"  [WARN] Code search HTTP {r.status_code} for: {query}")
            return fallback

        data        = r.json()
        items       = data.get("items", [])
        total_count = data.get("total_count", 0)
        browser_url = GITHUB_CODE_BROWSER_URL + quote_plus(query)

        return total_count, browser_url, [
            {
                "name":       i.get("name"),
                "path":       i.get("path"),
                "sha":        i.get("sha"),
                "html_url":   i.get("html_url"),
                "repository": {
                    "full_name": i.get("repository", {}).get("full_name"),
                    "html_url":  i.get("repository", {}).get("html_url"),
                    "private":   i.get("repository", {}).get("private"),
                },
                "score": i.get("score"),
            }
            for i in items
        ]

    return fallback


def search_repos(query: str, token: str, per_page: int) -> tuple:
    """GitHub Repository Search."""
    headers     = build_headers(token)
    params      = {"q": query, "per_page": per_page, "sort": "updated"}
    fallback    = (0, GITHUB_REPO_BROWSER_URL + quote_plus(query), [])
    max_retries = 3

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(GITHUB_REPO_SEARCH_URL, headers=headers, params=params, timeout=30)
        except requests.exceptions.ConnectionError as e:
            wait = 5 * attempt  # 5s, 10s, 15s
            print(f"  [WARN] Connection error (attempt {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                print(f"  ↳ Retrying in {wait}s...")
                time.sleep(wait)
                continue
            print(f"  [ERR] Giving up on: {query}")
            return fallback
        except requests.exceptions.RequestException as e:
            print(f"  [WARN] Request failed: {e}")
            return fallback

        if handle_rate_limit(r, REPO_SEARCH_DELAY):
            continue

        if r.status_code == 422:
            print(f"  [WARN] Invalid query (422): {query}")
            return fallback

        if not r.ok:
            print(f"  [WARN] Repo search HTTP {r.status_code} for: {query}")
            return fallback

        data        = r.json()
        items       = data.get("items", [])
        total_count = data.get("total_count", 0)
        browser_url = GITHUB_REPO_BROWSER_URL + quote_plus(query)

        return total_count, browser_url, [
            {
                "full_name":   i.get("full_name"),
                "html_url":    i.get("html_url"),
                "description": i.get("description"),
                "private":     i.get("private"),
                "language":    i.get("language"),
                "stargazers":  i.get("stargazers_count"),
                "updated_at":  i.get("updated_at"),
                "pushed_at":   i.get("pushed_at"),
            }
            for i in items
        ]

    return fallback


def save_results(data: dict, output_path: str):
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Diff helpers ───────────────────────────────────────────────────────────────

def load_previous(base_output: str) -> dict:
    """
    Auto-detect the most recent previous output file in Dorks Outputs/ folder.
    Matches: <stem>_output_YYYYMMDD_HHMMSS.json
    Picks latest by filename sort (lexicographic == chronological for this format).
    """
    stem       = Path(base_output).stem.split("_output_")[0]
    pattern    = f"{stem}_output_*.txt"
    # Anchor to the script's own directory so it works regardless of where you run from
    script_dir = Path(__file__).parent.resolve()
    out_folder = script_dir / "Dorks Outputs"

    if not out_folder.exists():
        return {}

    candidates = sorted(out_folder.glob(pattern), reverse=True)

    for candidate in candidates:
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                data = json.load(f)
            print(f"[~] Loaded previous scan for diff: {candidate.name}")
            return data
        except (json.JSONDecodeError, OSError):
            continue

    return {}


_BLOB_SHA_RE = re.compile(r"/blob/[0-9a-f]{7,40}/")


def canon_code_url(url: str) -> str:
    """
    Identity key for a code-search hit, stable across re-commits of the same file
    AND across GitHub's inconsistent percent-encoding of the same URL.
    Two things drift independently:
      1. html_url embeds the blob commit SHA (.../blob/<sha>/path), which changes
         on every commit that touches the repo.
      2. GitHub's API sometimes returns path spaces as a literal space and
         sometimes as %20 -- observed for the SAME file across two dorks in the
         SAME run -- so raw string comparison alone re-flags it as "new" forever.
    Without normalizing both, a bot-maintained or oddly-named file gets
    re-flagged as brand-new indefinitely.
    """
    if not url:
        return url
    url = _BLOB_SHA_RE.sub("/blob/HEAD/", url)
    return unquote(url)


def build_seen_index(previous: dict) -> dict:
    """
    Build a lookup: dork -> {"code": set(html_urls), "repos": set(full_names)}
    from the previous run's JSON.
    """
    index = {}
    for entry in previous.get("results", []):
        dork = entry.get("dork", "")
        index[dork] = {
            "code":  {canon_code_url(r.get("html_url"))  for r in entry.get("code_results",  [])},
            "repos": {r.get("full_name")                  for r in entry.get("repo_results",  [])},
        }
    return index


def diff_results(hits: list, seen_urls: set) -> list:
    """Return only items whose identifying URL/name wasn't in the previous run."""
    new = []
    for h in hits:
        key = h.get("full_name") or canon_code_url(h.get("html_url", ""))
        if key not in seen_urls:
            new.append(h)
    return new


def timestamped_output_path(output_path: str) -> str:
    """
    Build output path with full timestamp inside 'Dorks Outputs/' folder.
    e.g. -o target.json  ->  Dorks Outputs/target_output_20260518_143022.json
    """
    path       = Path(output_path)
    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    script_dir = Path(__file__).parent.resolve()
    out_folder = script_dir / "Dorks Outputs"
    out_folder.mkdir(parents=True, exist_ok=True)
    return str(out_folder / f"{path.stem}_output_{timestamp}{path.suffix}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="CTI GitHub Dorking via REST + Code Search API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-f", "--file",     required=True,  help="Path to dork list file")
    parser.add_argument("-t", "--token",    required=True,  help="GitHub personal access token")
    parser.add_argument("-o", "--output",   default=DEFAULT_OUTPUT,
                        help=f"Output JSON base name (default: {DEFAULT_OUTPUT}). "
                             "Date is appended automatically: results_2026-05-18.json")
    parser.add_argument("-n", "--num",      type=int, default=DEFAULT_PER_PAGE,
                        help="Results per dork (max 30 for code search, 100 for repos)")
    parser.add_argument("--mode",           choices=["code", "repos", "both"], default="both",
                        help="Search mode: code | repos | both (default: both)")
    parser.add_argument("--resume",         type=int, default=0, metavar="N",
                        help="Skip first N dorks (resume interrupted run)")
    parser.add_argument("--no-diff",        action="store_true",
                        help="Disable diff — show all results regardless of previous run")
    args = parser.parse_args()

    # Dated output path for today
    # Auto-derive output name from dork file (-f abc.txt -> abc_output_timestamp.json)
    output_path = timestamped_output_path(args.file)

    check_token(args.token)
    dorks = load_dorks(args.file)

    if args.resume:
        print(f"[~] Resuming from dork #{args.resume + 1}")
        dorks = dorks[args.resume:]

    # Load previous run for diffing
    previous  = {} if args.no_diff else load_previous(args.file)
    seen_index = build_seen_index(previous)
    diff_mode  = bool(previous) and not args.no_diff
    if diff_mode:
        print("[~] Diff mode ON — only new results will be highlighted\n")
    else:
        print("[~] Diff mode OFF — showing all results\n")

    session_results = []
    total           = len(dorks)
    start_time      = datetime.now().isoformat()

    run_code  = args.mode in ("code",  "both")
    run_repos = args.mode in ("repos", "both")

    # hits_summary entries: (dork, kind, browser_url, total_count, new_count)
    hits_summary = []
    new_summary  = []   # only dorks with genuinely new results

    print(f"[+] Starting scan | {total} dorks | mode: {args.mode}\n")

    for idx, dork in enumerate(dorks, start=1):
        print(f"[{idx}/{total}] {dork}")
        entry    = {"dork": dork, "timestamp": datetime.now().isoformat()}
        has_hits = False
        seen     = seen_index.get(dork, {"code": set(), "repos": set()})

        if run_code:
            total_count, browser_url, code_hits = search_code(dork, args.token, args.num)
            new_hits   = diff_results(code_hits, seen["code"]) if diff_mode else code_hits
            new_count  = len(new_hits)
            print(f"  → code: {total_count} total | {new_count} new")
            print(f"     verify: {browser_url}")
            if total_count > 0:
                entry["code_results"]     = code_hits
                entry["code_total_count"] = total_count
                entry["code_browser_url"] = browser_url
                entry["code_new_count"]   = new_count
                hits_summary.append((dork, "code", browser_url, total_count, new_count))
                if new_count > 0:
                    new_summary.append((dork, "code", browser_url, total_count, new_count))
                has_hits = True
            time.sleep(CODE_SEARCH_DELAY)

        if run_repos:
            total_count, browser_url, repo_hits = search_repos(dork, args.token, args.num)
            new_hits   = diff_results(repo_hits, seen["repos"]) if diff_mode else repo_hits
            new_count  = len(new_hits)
            print(f"  → repos: {total_count} total | {new_count} new")
            print(f"     verify: {browser_url}")
            if total_count > 0:
                entry["repo_results"]     = repo_hits
                entry["repo_total_count"] = total_count
                entry["repo_browser_url"] = browser_url
                entry["repo_new_count"]   = new_count
                hits_summary.append((dork, "repos", browser_url, total_count, new_count))
                if new_count > 0:
                    new_summary.append((dork, "repos", browser_url, total_count, new_count))
                has_hits = True
            time.sleep(REPO_SEARCH_DELAY)

        if has_hits:
            session_results.append(entry)

        if session_results:
            save_results({
                "meta": {
                    "started":     start_time,
                    "source_file": args.file,
                    "total_dorks": total,
                    "mode":        args.mode,
                    "diff_mode":   diff_mode,
                },
                "results": session_results,
            }, output_path)

        print()

    # ── Final summary ──────────────────────────────────────────────────────────
    print("=" * 60)
    print(f"[✓] Scan complete — {total} dorks scanned")
    print(f"    Output: {output_path if session_results else 'N/A (no results)'}")
    print()

    if diff_mode:
        # Show NEW results prominently
        if new_summary:
            print(f"🆕  NEW results since yesterday ({len(new_summary)} dork(s)):")
            for dork, kind, url, total_count, new_count in new_summary:
                print(f"\n  [{kind}] {dork}")
                print(f"          {new_count} new  /  {total_count} total → {url}")
        else:
            print("  No new results since yesterday.")

        # Show unchanged hits under a separator
        unchanged = [(d, k, u, t, n) for d, k, u, t, n in hits_summary if n == 0]
        if unchanged:
            print(f"\n  Unchanged ({len(unchanged)} dork(s) — same as yesterday):")
            for dork, kind, url, total_count, _ in unchanged:
                print(f"    [{kind}] {dork}  ({total_count} total) → {url}")
    else:
        if hits_summary:
            print(f"Results found ({len(hits_summary)}):")
            for dork, kind, url, total_count, _ in hits_summary:
                print(f"  [{kind}] {dork}")
                print(f"          {total_count} result(s) → {url}")
        else:
            print("  No results found for any dork.")

    print("=" * 60)


if __name__ == "__main__":
    main()
