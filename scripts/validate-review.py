#!/usr/bin/env python3
"""
validate-review.py — Validate review agent output quality.

Usage:
    python3 validate-review.py <session_dir> [attempts_raw.jsonl]

Checks:
1. milestones.jsonl exists and has valid JSON lines
2. Each milestone has required fields (target.file, target.theorem, status)
3. Each non-blocked milestone has at least 1 attempt with code/strategy
4. Cross-checks attempt counts against raw log data
5. summary.md exists and is non-trivial

Exit code: 0 = pass, 1 = warnings, 2 = fail
"""

import json
import sys
from pathlib import Path
from collections import Counter


def load_jsonl(path: str) -> list:
    items = []
    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    items.append({'_parse_error': line[:200]})
    return items


def validate(session_dir: str, attempts_path: str = None):
    session = Path(session_dir)
    issues = []
    warnings = []

    # 1. Check milestones.jsonl exists
    milestones_path = session / 'milestones.jsonl'
    if not milestones_path.exists():
        issues.append("FAIL: milestones.jsonl not found")
        return issues, warnings

    milestones = load_jsonl(str(milestones_path))
    if not milestones:
        issues.append("FAIL: milestones.jsonl is empty")
        return issues, warnings

    # 2. Check required fields
    for i, m in enumerate(milestones):
        if '_parse_error' in m:
            issues.append(f"FAIL: milestone {i} has JSON parse error: {m['_parse_error'][:100]}")
            continue

        target = m.get('target', {})
        if not isinstance(target, dict):
            issues.append(f"FAIL: milestone {i} target is not a dict (got {type(target).__name__})")
        elif not target.get('file'):
            issues.append(f"FAIL: milestone {i} missing target.file")
        elif not target.get('theorem'):
            warnings.append(f"WARN: milestone {i} missing target.theorem")

        status = m.get('status', '')
        valid_statuses = {'solved', 'partial', 'blocked', 'not_started'}
        if status not in valid_statuses:
            warnings.append(f"WARN: milestone {i} has non-standard status '{status}' (expected: {valid_statuses})")

        if status not in ('blocked', 'not_started'):
            attempts = m.get('attempts', [])
            if not attempts:
                warnings.append(f"WARN: milestone {i} ({target.get('theorem', '?')}) status={status} but no attempts recorded")
            else:
                for j, att in enumerate(attempts):
                    if not att.get('strategy'):
                        warnings.append(f"WARN: milestone {i} attempt {j} has no strategy")

    # 3. Cross-check with attempts_raw.jsonl if available
    if attempts_path and Path(attempts_path).exists():
        raw = load_jsonl(attempts_path)

        edit_counts = Counter()
        goal_counts = Counter()
        for ev in raw:
            if ev.get('type') == 'code_change':
                edit_counts[ev.get('file', '')] += 1
            elif ev.get('type') == 'goal_state':
                goal_counts[ev.get('file', '')] += 1

        for m in milestones:
            if '_parse_error' in m:
                continue
            target = m.get('target', {})
            if not isinstance(target, dict):
                continue
            tfile = target.get('file', '')

            matching_edits = 0
            matching_goals = 0
            for f, c in edit_counts.items():
                if f.endswith(tfile) or tfile.endswith(f.split('/')[-1]):
                    matching_edits += c
            for f, c in goal_counts.items():
                if f.endswith(tfile) or tfile.endswith(f.split('/')[-1]):
                    matching_goals += c

            attempts = m.get('attempts', [])
            if matching_edits >= 5 and len(attempts) <= 1:
                warnings.append(
                    f"WARN: {tfile} had {matching_edits} edits in raw log but only {len(attempts)} attempt(s) recorded"
                )

            if matching_goals >= 3 and not any('goal' in str(a).lower() for a in attempts):
                warnings.append(
                    f"WARN: {tfile} had {matching_goals} goal checks in raw log but attempts don't reference goal states"
                )

        summary_ev = next((ev for ev in raw if ev.get('type') == 'summary'), {})
        if summary_ev:
            total_edits = summary_ev.get('edits', 0)
            if total_edits > 10 and len(milestones) <= 1:
                warnings.append(
                    f"WARN: raw log has {total_edits} edits but only {len(milestones)} milestone(s)"
                )

    # 4. Check summary.md exists and is non-trivial
    summary_path = session / 'summary.md'
    if not summary_path.exists():
        issues.append("FAIL: summary.md not found")
    else:
        content = summary_path.read_text()
        if len(content) < 200:
            warnings.append(f"WARN: summary.md is very short ({len(content)} chars)")

        # Check it has at least 2 markdown H2 headings
        h2_count = content.count('\n## ')
        if content.startswith('## '):
            h2_count += 1
        if h2_count < 2:
            warnings.append("WARN: summary.md has fewer than 2 sections (## headings)")

    return issues, warnings


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <session_dir> [attempts_raw.jsonl]", file=sys.stderr)
        sys.exit(1)

    session_dir = sys.argv[1]
    attempts_path = sys.argv[2] if len(sys.argv) > 2 else None

    issues, warnings = validate(session_dir, attempts_path)

    for i in issues:
        print(i)
    for w in warnings:
        print(w)

    if not issues and not warnings:
        print("PASS: Review output validated successfully")

    print(f"\n--- {len(issues)} failures, {len(warnings)} warnings ---")

    if issues:
        sys.exit(2)
    elif warnings:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == '__main__':
    main()
