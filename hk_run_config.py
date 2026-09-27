"""The fingerprint a checkpoint is only valid under.

A model and its VecNormalize statistics belong to the reward scale, the discount factor and the
observation/action sizes they were learned with. Resuming after one of those changed keeps a
value function and running normalization statistics that describe a different problem: the loss
curve looks plausible, the policy behaves as if it had learned nothing, and nothing in the logs
says why. So the environment's configuration is written next to the model and compared before a
resume; on a mismatch the run starts from scratch and says which part changed.

Imports nothing but the standard library, so the unit tier checks it directly (see AGENTS.md).
"""

import json
import os

CONFIG_NAME = "run_config.json"


def build_config(values):
    """A comparable, JSON-safe fingerprint: the same numbers, order-independent."""
    return {str(key): round(float(value), 9) for key, value in values.items()}


def config_path(directory, name=CONFIG_NAME):
    return os.path.join(directory, name)


def read_config(path):
    """The stored fingerprint, or None when the file is missing or unusable."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            stored = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(stored, dict):
        return None
    try:
        return {str(key): float(value) for key, value in stored.items()}
    except (TypeError, ValueError):
        return None


def matches(path, values):
    """True only when the stored fingerprint exists and describes exactly these values."""
    stored = read_config(path)
    return stored is not None and stored == build_config(values)


def write_config(path, values):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(build_config(values), handle, indent=2, sort_keys=True)
        handle.write("\n")


def differences(path, values):
    """What changed since the stored fingerprint, for the message a human reads."""
    stored = read_config(path)
    expected = build_config(values)
    if stored is None:
        return ["no stored configuration"]
    changed = []
    for key in sorted(set(stored) | set(expected)):
        if key not in stored:
            changed.append(f"{key}: new ({expected[key]})")
        elif key not in expected:
            changed.append(f"{key}: dropped (was {stored[key]})")
        elif stored[key] != expected[key]:
            changed.append(f"{key}: {stored[key]} -> {expected[key]}")
    return changed
