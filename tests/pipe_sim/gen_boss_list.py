# -*- coding: utf-8 -*-
"""Builds the boss registry from bosses.py in the mod's boss_list event format.

This way the mock server serves exactly the same registry as Python does, which
makes it possible to verify teleport.py --verify without a running game.
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from bosses import BOSS_LIST, DEFAULT_SCENE  # noqa: E402

data = {
    "status": "boss_list",
    "target_scene": DEFAULT_SCENE,
    "count": len(BOSS_LIST),
    "bosses": [
        {"index": i + 1, "scene": scene, "label": label}
        for i, (scene, label) in enumerate(BOSS_LIST)
    ],
}

out = os.path.join(os.path.dirname(__file__), "bosses.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False)

print(f"Wrote {out}: {data['count']} bosses")
