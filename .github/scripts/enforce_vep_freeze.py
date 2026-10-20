#!/usr/bin/env python3
"""Enforce the VEP Freeze deadline, per veps/meta-VEPs/282-vep-proposal-governance/vep.md.

Usage: enforce_vep_freeze.py <changed-files-list>

For any changed veps/**/*.md file whose head front matter has status: implementable
and a milestone.alpha set, fetches the VEP Freeze date for that release from
kubevirt/sig-release and fails if today is on or after the freeze date. Skips
enforcement entirely if the PR carries the `freeze-exception` label.
"""
import datetime
import json
import os
import sys
import urllib.error
import urllib.request

import yaml

from front_matter import extract_front_matter

SCHEDULE_URL = (
    "https://raw.githubusercontent.com/kubevirt/sig-release/main/releases/{version}/schedule.yaml"
)


def has_freeze_exception():
    labels = json.loads(os.environ.get("PR_LABELS", "[]"))
    return any(label.get("name") == "freeze-exception" for label in labels)


def freeze_date(version):
    """version like 'v1.10'. Returns a datetime.date or None if not found or unparseable."""
    url = SCHEDULE_URL.format(version=version)
    try:
        with urllib.request.urlopen(url) as resp:
            schedule = yaml.safe_load(resp.read())
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        print(f"::warning::could not fetch schedule for {version}: {e}")
        return None

    if not isinstance(schedule, list):
        return None

    for entry in schedule:
        if not isinstance(entry, dict):
            continue
        what = entry.get("what", "")
        if "Virtualization Enhancement Proposal (VEP) Freeze" in what:
            when = entry.get("when")
            return datetime.datetime.strptime(when, "%Y/%m/%d").date() if when else None
    return None


def check_file(path, text, today):
    """Returns an error message if `path`'s VEP Freeze deadline has passed, else None.

    Not applicable (returns None) for files that aren't status: implementable, or
    that have no milestone entry for their current stage.
    """
    fm = extract_front_matter(text)
    if not isinstance(fm, dict) or fm.get("status") != "implementable":
        return None

    stage = fm.get("stage")
    milestone = fm.get("milestone") or {}
    target = milestone.get(stage)
    if not target:
        return None

    version = str(target) if str(target).startswith("v") else f"v{target}"
    deadline = freeze_date(version)
    if deadline is None:
        print(f"::warning file={path}::could not determine VEP Freeze date for {version}")
        return None

    if today >= deadline:
        return (
            f"VEP Freeze for {version} was {deadline.isoformat()}. "
            "A maintainer can add the `freeze-exception` label to bypass this check."
        )
    return None


def main():
    if has_freeze_exception():
        print("PR carries the `freeze-exception` label; skipping VEP Freeze enforcement.")
        return

    with open(sys.argv[1], encoding="utf-8") as f:
        files = [line.strip() for line in f if line.strip()]

    today = datetime.datetime.now(datetime.timezone.utc).date()
    had_error = False

    for path in files:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        message = check_file(path, text, today)
        if message:
            had_error = True
            print(f"::error file={path}::{message}")

    if had_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
