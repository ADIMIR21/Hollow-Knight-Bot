"""The fingerprint a checkpoint is only valid under.

A model and its VecNormalize statistics belong to the reward scale, the discount factor and the
observation/action sizes they were learned with. Resuming after one of those changed keeps a
value function and running normalization statistics that describe a different problem: the loss
curve looks plausible, the policy behaves as if it had learned nothing, and nothing in the logs
says why. So the environment's configuration is written next to the model and compared before a
resume; on a mismatch the run starts from scratch and says which part changed.

The one way past it is deliberate: `HK_RESUME_CONFIG_CHANGED=1` resumes anyway and prints what is
being accepted. That is a maintainer's decision about a warm start, never a default - the policy
weights still transfer, but the value function and the running statistics describe the old reward
and have to re-fit.

Imports nothing but the standard library, so the unit tier checks it directly (see AGENTS.md).
"""

import json
import os
import subprocess
import time

CONFIG_NAME = "run_config.json"
MANIFEST_NAME = "run_manifest.json"
RESUME_OVERRIDE_ENV = "HK_RESUME_CONFIG_CHANGED"
REWARD_OVERRIDES_ENV = "HK_REWARD_OVERRIDES"


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


def override_requested(environ=None):
    """Whether the maintainer explicitly asked to resume across a changed configuration.

    Only an affirmative value counts: an empty variable, a "0" or anything unrecognized leaves the
    refusal in place, because the failure this guards against is silent.
    """
    environ = os.environ if environ is None else environ
    return str(environ.get(RESUME_OVERRIDE_ENV, "")).strip().lower() in ("1", "true", "yes", "on")


def matches(path, values):
    """True only when the stored fingerprint exists and describes exactly these values."""
    stored = read_config(path)
    return stored is not None and stored == build_config(values)


def write_config(path, values):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(build_config(values), handle, indent=2, sort_keys=True)
        handle.write("\n")


def git_commit(repo_root=None):
    """The short hash of the working tree's HEAD, or None without a repo/git.

    Used only for the manifest: which code produced a checkpoint is provenance,
    not validity - a different commit does not make the value function wrong the
    way a changed reward scale does, so it must never leak into the fingerprint.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=repo_root,
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    commit = result.stdout.strip()
    return commit or None


def write_manifest(path, info):
    """Writes the run's provenance record (versions, hyperparameters, scene).

    Deliberately NOT the fingerprint: everything here describes *where a
    checkpoint came from*, not *what problem it describes*. Nothing in the
    manifest is compared on a resume.
    """
    record = dict(info)
    record.setdefault("written", time.strftime("%Y-%m-%d %H:%M:%S"))
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True, default=str)
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


def apply_reward_overrides(module_globals, environ=None, path=None):
    """Applies the sweep bench's reward overrides to a module's globals.

    The environment variable ``HK_REWARD_OVERRIDES`` names a JSON file whose keys are
    the reward constants (``DAMAGE_REWARD_PER_HP`` and friends) and whose values
    replace them at import time - this is how ``tools/sweep`` runs A/B configurations
    without editing the tree. The applied values land in the checkpoint fingerprint
    (``run_config_values()`` reads the same module attributes afterwards), so a
    checkpoint trained under an override is never resumed by a run without it.

    Anything malformed - a missing file, a non-object, a lowercase key, a non-number -
    is refused loudly with SystemExit instead of silently training the default.
    Returns the list of overridden names (empty when the variable is unset).
    """
    environ = os.environ if environ is None else environ
    if path is None:
        path = environ.get(REWARD_OVERRIDES_ENV, "")
    if not path:
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            overrides = json.load(handle)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"{REWARD_OVERRIDES_ENV}={path}: cannot read the overrides: {exc}")
    if not isinstance(overrides, dict):
        raise SystemExit(f"{REWARD_OVERRIDES_ENV}={path}: the file must hold a JSON object")
    applied = []
    for name, value in overrides.items():
        if not (isinstance(name, str) and name.isidentifier() and name.isupper()):
            raise SystemExit(f"{REWARD_OVERRIDES_ENV}={path}: not a constant name: {name!r}")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SystemExit(f"{REWARD_OVERRIDES_ENV}={path}: {name}: not a number: {value!r}")
        module_globals[name] = float(value)
        applied.append(name)
    return applied
