#!/usr/bin/env python3
"""Probe guessed company job-board tokens on Greenhouse / Lever / Ashby public feeds."""
import json, sys, urllib.request, concurrent.futures as cf

CANDIDATES = sys.argv[1:]  # guessed tokens, e.g. ./probe_boards.py acme acmerobotics acme-robotics

def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "internship-agent/0.1"}), timeout=15) as r:
            return json.load(r)
    except Exception:
        return None

def probe(tok):
    hits = []
    gh = get(f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs")
    if gh and gh.get("jobs"): hits.append(("greenhouse", tok, len(gh["jobs"])))
    lv = get(f"https://api.lever.co/v0/postings/{tok}?mode=json")
    if isinstance(lv, list) and lv: hits.append(("lever", tok, len(lv)))
    ab = get(f"https://api.ashbyhq.com/posting-api/job-board/{tok}")
    if ab and ab.get("jobs"): hits.append(("ashby", tok, len(ab["jobs"])))
    return hits

with cf.ThreadPoolExecutor(16) as ex:
    for hits in ex.map(probe, CANDIDATES):
        for h in hits: print(*h)
