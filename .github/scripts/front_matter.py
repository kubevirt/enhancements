"""Parsing helpers for VEP YAML front matter."""
import yaml


def extract_front_matter(text):
    """Returns the parsed front matter dict, None if absent, or "invalid-yaml"."""
    if text is None:
        return None
    lines = text.split("\n")
    if lines[0].rstrip() != "---":
        return None
    end_idx = next(
        (i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None
    )
    if end_idx is None:
        return None
    block = "\n".join(lines[1:end_idx])
    try:
        return yaml.safe_load(block) or {}
    except yaml.YAMLError:
        return "invalid-yaml"


def is_meta_vep(path):
    return path.startswith("veps/meta-VEPs/")
