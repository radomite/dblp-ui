"""Editable venue overrides and explicit links between dblp records."""

import json
from pathlib import Path


HERE = Path(__file__).parent


def load_overrides(path=HERE / "manual_overrides.json"):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"venues", "key_prefixes"}:
        raise ValueError("manual_overrides.json needs venues and key_prefixes objects")
    for section in ("venues", "key_prefixes"):
        entries = data[section]
        if not isinstance(entries, dict):
            raise ValueError(f"{section} must be an object")
        for name, override in entries.items():
            if not isinstance(name, str) or not name or not isinstance(override, dict):
                raise ValueError(f"Invalid {section} entry")
            if set(override) - {"label", "preprint"} or not override:
                raise ValueError(f"Invalid override for {name}")
            if "label" in override and (not isinstance(override["label"], str) or not override["label"]):
                raise ValueError(f"Invalid label for {name}")
            if "preprint" in override and not isinstance(override["preprint"], bool):
                raise ValueError(f"Invalid preprint flag for {name}")
    return data


def load_consolidations(path=HERE / "manual_consolidations.json"):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"groups"} or not isinstance(data["groups"], list):
        raise ValueError("manual_consolidations.json needs a groups array")
    by_key = {}
    for group in data["groups"]:
        if not isinstance(group, dict) or set(group) - {"keys", "note"} or not isinstance(group.get("keys"), list):
            raise ValueError("Each consolidation needs a keys array and optional note")
        keys = group["keys"]
        if len(keys) < 2 or any(not isinstance(key, str) or not key for key in keys):
            raise ValueError("Each consolidation needs at least two dblp keys")
        if "note" in group and not isinstance(group["note"], str):
            raise ValueError("Consolidation note must be a string")
        if any(key in by_key for key in keys) or len(set(keys)) != len(keys):
            raise ValueError("A dblp key can occur in only one consolidation group")
        members = frozenset(keys)
        by_key.update({key: members for key in keys})
    return by_key


OVERRIDES = load_overrides()
CONSOLIDATIONS_BY_KEY = load_consolidations()
