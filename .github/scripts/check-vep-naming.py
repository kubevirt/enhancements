#!/usr/bin/env python3

import os
import re
import subprocess
import sys

DIR_PATTERN = re.compile(r"^[0-9]+-[a-z0-9]+(-[a-z0-9]+)*$")


def changed_files(vep_dir, base_sha, head_sha):
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACR", base_sha, head_sha, "--", f"{vep_dir}/"],
        capture_output=True, text=True, check=True,
    )
    owners = f"{vep_dir}/OWNERS"
    return [f for f in result.stdout.splitlines() if f and f != owners]


def check(vep_dirs, base_sha, head_sha):
    errors = []

    for vep_dir in vep_dirs:
        for file in changed_files(vep_dir, base_sha, head_sha):
            rel = file.removeprefix(f"{vep_dir}/")

            if "/" not in rel:
                errors.append(f"{file}: VEP files must be inside a <number>-<name>/ directory, not directly under {vep_dir}/")
                continue

            dir_name = rel.split("/", 1)[0]

            if not DIR_PATTERN.match(dir_name):
                errors.append(f"{file}: directory '{dir_name}' does not match the required <number>-<name> pattern (lowercase, hyphen-separated)")

            basename = rel.split("/", 1)[1]
            if basename.endswith(".md") and basename != "vep.md":
                dir_path = os.path.join(vep_dir, dir_name)
                if not os.path.isfile(os.path.join(dir_path, "vep.md")):
                    errors.append(f"{file}: main VEP document must be named 'vep.md', not '{basename}'")

    return errors


def main():
    vep_dirs = os.environ["VEP_DIRS"].split()
    base_sha = os.environ["BASE_SHA"]
    head_sha = os.environ["HEAD_SHA"]

    errors = check(vep_dirs, base_sha, head_sha)

    if errors:
        print("::error::VEP naming convention violations found:")
        for err in errors:
            print(f"  - {err}")
        print()
        print("Expected structure: <vep-dir>/<number>-<name>/vep.md")
        print("  - Directory name: digits, hyphen, then lowercase alphanumeric segments separated by hyphens")
        print("  - Main VEP file: must be named vep.md")
        sys.exit(1)

    print("All changed VEP files follow the naming convention.")


if __name__ == "__main__":
    main()
