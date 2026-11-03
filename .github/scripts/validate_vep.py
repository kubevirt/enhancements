#!/usr/bin/env python3
"""Validate VEP front matter, per veps/meta-VEPs/282-vep-proposal-governance/vep.md.

Usage: validate_vep.py <changed-files-list>

Reads the list of changed veps/**/*.md paths (one per line) from the given file,
compares each file's front matter at the PR base vs. head, and fails (non-zero
exit) if any check does not pass. Results are also written to $GITHUB_STEP_SUMMARY
when running in Actions.
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request

from front_matter import extract_front_matter, is_meta_vep

VALID_STATUSES = [
    "provisional",
    "implementable",
    "implemented",
    "deferred",
    "rejected",
    "withdrawn",
    "replaced",
]
# Allowed next statuses per current status, per the state diagram in
# veps/meta-VEPs/282-vep-proposal-governance/vep.md. Anything not listed here is
# rejected, including all transitions out of deferred/rejected/withdrawn/replaced,
# since the diagram defines no path out of them.
ALLOWED_TRANSITIONS = {
    "provisional": {"implementable", "deferred", "rejected", "withdrawn", "replaced"},
    "implementable": {"implemented", "deferred", "rejected", "withdrawn", "replaced"},
    "implemented": {"replaced"},
    "deferred": set(),
    "rejected": set(),
    "withdrawn": set(),
    "replaced": set(),
}
VALID_SIGS = {"sig-compute", "sig-network", "sig-storage"}
REQUIRED_FIELDS = [
    "title",
    "vep-number",
    "creation-date",
    "status",
    "authors",
    "owning-sig",
    "reviewers",
    "approvers",
]


def gh_api(path):
    repo = os.environ["REPO"]
    token = os.environ["GH_TOKEN"]
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise RuntimeError(f"GitHub API request to {path} failed: {e}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"GitHub API request to {path} failed: {e}") from e


def read_head(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_base(path):
    base_sha = os.environ["BASE_SHA"]
    content = gh_api(f"contents/{path}?ref={base_sha}")
    if content is None:
        return None  # file did not exist on base (new VEP)
    return base64.b64decode(content["content"]).decode("utf-8")


def validate_file(path, head_fm, base_fm):
    """Returns a list of errors for one file's head front matter."""
    errors = []

    if head_fm == "invalid-yaml":
        return ["front matter block is not valid YAML"]

    for field in REQUIRED_FIELDS:
        if field not in head_fm or head_fm[field] in (None, "", []):
            errors.append(f"missing required field `{field}`")

    status = head_fm.get("status")
    stage = head_fm.get("stage")
    milestone = head_fm.get("milestone") or {}

    if not is_meta_vep(path):
        if "feature-gate" not in head_fm or not head_fm["feature-gate"]:
            errors.append(
                "missing `feature-gate` (add it, or justify its absence in the VEP body)"
            )
        if status == "implementable" and not stage:
            errors.append("missing required field `stage` when status is implementable")
        if stage and not milestone.get(stage):
            errors.append(f"`milestone.{stage}` is required when `stage: {stage}`")

    if status is not None and status not in VALID_STATUSES:
        errors.append(f"`status: {status}` is not one of {VALID_STATUSES}")

    owning_sig = head_fm.get("owning-sig")
    if owning_sig is not None and owning_sig not in VALID_SIGS:
        errors.append(f"`owning-sig: {owning_sig}` is not one of {sorted(VALID_SIGS)}")

    vep_number = head_fm.get("vep-number")
    if isinstance(vep_number, int) and vep_number > 0:
        issue = gh_api(f"issues/{vep_number}")
        if issue is None or "pull_request" in issue:
            errors.append(
                f"`vep-number: {vep_number}` does not match an issue in this repo"
            )
    elif vep_number not in (None, "NNNN"):
        errors.append(f"`vep-number: {vep_number}` must be a positive integer")

    if status == "implementable":
        reviewers = head_fm.get("reviewers") or []
        approvers = head_fm.get("approvers") or []
        if any(str(r).upper() == "TBD" for r in reviewers):
            errors.append("`reviewers` may not contain TBD when status is implementable")
        if any(str(a).upper() == "TBD" for a in approvers):
            errors.append("`approvers` may not contain TBD when status is implementable")

    if status == "replaced" and not head_fm.get("superseded-by"):
        errors.append("`superseded-by` is required when status is replaced")

    # Status transition check: only when the base version already had valid front matter.
    if isinstance(base_fm, dict) and status is not None:
        old_status = base_fm.get("status")
        if old_status is not None and old_status != status:
            if status not in ALLOWED_TRANSITIONS.get(old_status, set()):
                errors.append(
                    f"invalid status transition: `{old_status}` -> `{status}`"
                )

    return errors


def evaluate_file(path, head_text, base_text):
    """Returns (status, messages) for one changed file, status in 'ok'/'warn'/'error'.

    Handles the migration-friendly cases (new file, pre-existing file not yet
    migrated to front matter, front matter removed) before delegating to
    validate_file() for the full rule set once front matter is present.
    """
    head_fm = extract_front_matter(head_text)
    base_fm = extract_front_matter(base_text)

    if head_fm is None:
        if isinstance(base_fm, dict):
            # Front matter existed before this PR and was removed: a regression.
            return "error", ["Front matter block was removed."]
        if base_text is None:
            # New file with no front matter at all: not allowed for new VEPs.
            return "error", [
                "New VEP files must include the front matter block. "
                "See docs/vep-governance.md."
            ]
        # Pre-existing VEP not yet migrated to front matter: warn only.
        return "warn", [
            "No front matter block found; skipping validation "
            "(pre-existing VEP, not yet migrated)."
        ]

    errors = validate_file(path, head_fm, base_fm)
    if errors:
        return "error", errors
    return "ok", []


def main():
    with open(sys.argv[1], encoding="utf-8") as f:
        files = [line.strip() for line in f if line.strip()]

    summary_lines = ["# VEP front matter validation", ""]
    had_error = False

    if not files:
        print("No veps/**/*.md files changed; nothing to validate.")
        return

    icons = {"ok": "✅", "warn": "⚠️", "error": "❌"}
    for path in files:
        head_text = read_head(path)
        base_text = read_base(path)
        status, messages = evaluate_file(path, head_text, base_text)

        summary_lines.append(f"## {icons[status]} {path}")
        for m in messages:
            summary_lines.append(f"- {m}")
            if status == "error":
                print(f"::error file={path}::{m}")
        if status == "error":
            had_error = True

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("\n".join(summary_lines) + "\n")

    if had_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
