using System;
using System.Collections.Generic;

namespace AiTrainHK
{
    // The Godhome boss registry and the boss query resolver, extracted from AiDataExporter.cs
    // into a file with no game types: pure data and pure string logic. That is what the C#
    // self-check (tests/csharp/RegistryTests) compiles - CI has no game, so this file must
    // not reference UnityEngine, Modding or anything else from the install.
    //
    // Python mirrors this registry in bosses.py; tests/test_registry_parity.py compares the
    // two, so change this file and bosses.py together (see AGENTS.md, "Two sides, one commit").

    public struct BossEntry
    {
        public string Scene;
        public string Label;

        public BossEntry(string scene, string label)
        {
            Scene = scene;
            Label = label;
        }
    }

    public static class BossRegistry
    {
        // Godhome arenas enter the scene through the door_dreamEnter gate - it is the only
        // TransitionPoint in GG_* boss scenes (verified live in GG_False_Knight and
        // GG_Hornet_1). The mod still matches it against the scene's real TransitionPoints,
        // and an explicitly set set_gate takes priority.
        public const string DefaultBossScene = "GG_False_Knight";
        public const string DefaultEntryGate = "door_dreamEnter";

        // Scenes are taken from the game's build settings (hollow_knight_Data/globalgamemanagers).
        // Entries with the _V suffix are the harder fight versions (Ascended/Radiant),
        // GG_Mantis_Lords_V = Sisters of Battle, GG_Nosk_Hornet = Winged Nosk.
        public static readonly BossEntry[] Entries = new BossEntry[]
        {
            // --- Pantheon of the Master (early bosses) ---
            new BossEntry("GG_Vengefly", "Vengefly King"),
            new BossEntry("GG_Gruz_Mother", "Gruz Mother"),
            new BossEntry("GG_False_Knight", "False Knight"),
            new BossEntry("GG_Mega_Moss_Charger", "Massive Moss Charger"),
            new BossEntry("GG_Hornet_1", "Hornet Protector"),
            new BossEntry("GG_Brooding_Mawlek", "Brooding Mawlek"),
            // --- Pantheon of the Artist (early-to-mid game) ---
            new BossEntry("GG_Soul_Master", "Soul Master"),
            new BossEntry("GG_Crystal_Guardian", "Crystal Guardian"),
            new BossEntry("GG_Crystal_Guardian_2", "Enraged Guardian"),
            new BossEntry("GG_Grimm", "Troupe Master Grimm"),
            new BossEntry("GG_Collector", "The Collector"),
            new BossEntry("GG_Soul_Tyrant", "Soul Tyrant"),
            new BossEntry("GG_Dung_Defender", "Dung Defender"),
            new BossEntry("GG_Mage_Knight", "Soul Warrior"),
            new BossEntry("GG_Watcher_Knights", "Watcher Knights"),
            new BossEntry("GG_Oblobbles", "Oblobbles"),
            new BossEntry("GG_Hive_Knight", "Hive Knight"),
            new BossEntry("GG_Nosk", "Nosk"),
            new BossEntry("GG_Mantis_Lords", "Mantis Lords"),
            new BossEntry("GG_Broken_Vessel", "Broken Vessel"),
            // --- Pantheon of the Sage (mid-to-late game) ---
            new BossEntry("GG_Lost_Kin", "Lost Kin"),
            new BossEntry("GG_Failed_Champion", "Failed Champion"),
            new BossEntry("GG_Traitor_Lord", "Traitor Lord"),
            new BossEntry("GG_Uumuu", "Uumuu"),
            new BossEntry("GG_Flukemarm", "Flukemarm"),
            new BossEntry("GG_God_Tamer", "God Tamer"),
            new BossEntry("GG_Ghost_Xero", "Xero"),
            new BossEntry("GG_Ghost_Gorb", "Gorb"),
            new BossEntry("GG_Ghost_Marmu", "Marmu"),
            new BossEntry("GG_Ghost_No_Eyes", "No Eyes"),
            new BossEntry("GG_Ghost_Markoth", "Markoth"),
            new BossEntry("GG_Ghost_Galien", "Galien"),
            new BossEntry("GG_Ghost_Hu", "Elder Hu"),
            // --- Pantheon of the Knight (late bosses) ---
            new BossEntry("GG_Hornet_2", "Hornet Sentinel"),
            new BossEntry("GG_Grey_Prince_Zote", "Grey Prince Zote"),
            new BossEntry("GG_White_Defender", "White Defender"),
            new BossEntry("GG_Grimm_Nightmare", "Nightmare King Grimm"),
            new BossEntry("GG_Hollow_Knight", "Pure Vessel"),
            // --- Pantheon of Hallownest (finale) ---
            new BossEntry("GG_Radiance", "The Radiance"),
            // --- Nailmasters (finales of pantheons 1-3) ---
            new BossEntry("GG_Nailmasters", "Brothers Oro & Mato"),
            new BossEntry("GG_Painter", "Paintmaster Sheo"),
            new BossEntry("GG_Sly", "Great Nailsage Sly"),
            new BossEntry("GG_Lurker", "Pale Lurker"),
            // --- Harder fight variants (Ascended/Radiant) ---
            new BossEntry("GG_Mantis_Lords_V", "Sisters of Battle"),
            new BossEntry("GG_Nosk_Hornet", "Winged Nosk"),
            new BossEntry("GG_Vengefly_V", "Vengefly King (Variant)"),
            new BossEntry("GG_Gruz_Mother_V", "Gruz Mother (Variant)"),
            new BossEntry("GG_Brooding_Mawlek_V", "Brooding Mawlek (Variant)"),
            new BossEntry("GG_Collector_V", "The Collector (Variant)"),
            new BossEntry("GG_Mage_Knight_V", "Soul Warrior (Variant)"),
            new BossEntry("GG_Nosk_V", "Nosk (Variant)"),
            new BossEntry("GG_Uumuu_V", "Uumuu (Variant)"),
            new BossEntry("GG_Ghost_Gorb_V", "Gorb (Variant)"),
            new BossEntry("GG_Ghost_Marmu_V", "Marmu (Variant)"),
            new BossEntry("GG_Ghost_Markoth_V", "Markoth (Variant)"),
            new BossEntry("GG_Ghost_No_Eyes_V", "No Eyes (Variant)"),
            new BossEntry("GG_Ghost_Xero_V", "Xero (Variant)"),
            // --- Godhome hub (not bosses, but handy to teleport to) ---
            new BossEntry("GG_Atrium", "Godhome Atrium (hub)"),
            new BossEntry("GG_Workshop", "Godhome Workshop (workbench)"),
            new BossEntry("GG_Boss_Door_Entrance", "Pantheon Doors"),
        };

        // All GG_ scenes from the game's build settings - for canonicalizing names
        // entered by the user in any case (gg_hornet_1 -> GG_Hornet_1).
        public static readonly string[] KnownScenes = new string[]
        {
            "GG_Atrium", "GG_Atrium_Roof", "GG_Blue_Room", "GG_Boss_Door_Entrance",
            "GG_Broken_Vessel", "GG_Brooding_Mawlek", "GG_Brooding_Mawlek_V",
            "GG_Collector", "GG_Collector_V", "GG_Crystal_Guardian", "GG_Crystal_Guardian_2",
            "GG_Door_5_Finale", "GG_Dung_Defender", "GG_End_Sequence", "GG_Engine",
            "GG_Engine_Prime", "GG_Engine_Root", "GG_Entrance_Cutscene", "GG_Failed_Champion",
            "GG_False_Knight", "GG_Flukemarm", "GG_Ghost_Galien", "GG_Ghost_Gorb",
            "GG_Ghost_Gorb_V", "GG_Ghost_Hu", "GG_Ghost_Markoth", "GG_Ghost_Markoth_V",
            "GG_Ghost_Marmu", "GG_Ghost_Marmu_V", "GG_Ghost_No_Eyes", "GG_Ghost_No_Eyes_V",
            "GG_Ghost_Xero", "GG_Ghost_Xero_V", "GG_God_Tamer", "GG_Grey_Prince_Zote",
            "GG_Grimm", "GG_Grimm_Nightmare", "GG_Gruz_Mother", "GG_Gruz_Mother_V",
            "GG_Hive_Knight", "GG_Hollow_Knight", "GG_Hornet_1", "GG_Hornet_2",
            "GG_Land_of_Storms", "GG_Lost_Kin", "GG_Lurker", "GG_Mage_Knight",
            "GG_Mage_Knight_V", "GG_Mantis_Lords", "GG_Mantis_Lords_V", "GG_Mega_Moss_Charger",
            "GG_Mighty_Zote", "GG_Nailmasters", "GG_Nosk", "GG_Nosk_Hornet",
            "GG_Nosk_V", "GG_Oblobbles", "GG_Painter", "GG_Pipeway",
            "GG_Radiance", "GG_Shortcut", "GG_Sly", "GG_Soul_Master",
            "GG_Soul_Tyrant", "GG_Spa", "GG_Traitor_Lord", "GG_Unlock",
            "GG_Unlock_Wastes", "GG_Unn", "GG_Uumuu", "GG_Uumuu_V",
            "GG_Vengefly", "GG_Vengefly_V", "GG_Watcher_Knights", "GG_Waterways",
            "GG_White_Defender", "GG_Workshop", "GG_Wyrm"
        };

        // Popular short aliases that are not part of the scene names themselves.
        public static readonly Dictionary<string, string> ExtraAliases = new Dictionary<string, string>
        {
            { "hornet", "GG_Hornet_1" },
            { "hornet2", "GG_Hornet_2" },
            { "hornet_sentinel", "GG_Hornet_2" },
            { "sentinel", "GG_Hornet_2" },
            { "false", "GG_False_Knight" },
            { "falseknight", "GG_False_Knight" },
            { "gruz", "GG_Gruz_Mother" },
            { "gruzmother", "GG_Gruz_Mother" },
            { "vengefly_king", "GG_Vengefly" },
            { "moss_charger", "GG_Mega_Moss_Charger" },
            { "mawlek", "GG_Brooding_Mawlek" },
            { "vessel", "GG_Hollow_Knight" },
            { "pure_vessel", "GG_Hollow_Knight" },
            { "hollow_knight", "GG_Hollow_Knight" },
            { "thk", "GG_Hollow_Knight" },
            { "radiance", "GG_Radiance" },
            { "nkg", "GG_Grimm_Nightmare" },
            { "nightmare_king", "GG_Grimm_Nightmare" },
            { "zote", "GG_Grey_Prince_Zote" },
            { "gpz", "GG_Grey_Prince_Zote" },
            { "sisters", "GG_Mantis_Lords_V" },
            { "sisters_of_battle", "GG_Mantis_Lords_V" },
            { "winged_nosk", "GG_Nosk_Hornet" },
            { "oro", "GG_Nailmasters" },
            { "mato", "GG_Nailmasters" },
            { "oro_mato", "GG_Nailmasters" },
            { "nailmasters", "GG_Nailmasters" },
            { "sheo", "GG_Painter" },
            { "paintmaster", "GG_Painter" },
            { "nailsage", "GG_Sly" },
            { "soul_warrior", "GG_Mage_Knight" },
            { "watcher", "GG_Watcher_Knights" },
            { "umuu", "GG_Uumuu" },
            { "traitor", "GG_Traitor_Lord" },
            { "abs_rad", "GG_Radiance" },
        };

        public static string NormalizeQuery(string query)
        {
            if (query == null) return "";
            string norm = query.Trim().ToLowerInvariant();
            norm = norm.Replace(' ', '_').Replace('-', '_');
            while (norm.Contains("__")) norm = norm.Replace("__", "_");
            return norm.Trim('_');
        }

        // Scene name canonicalization: the user may type "gg_hornet_1"
        // in any case - we substitute the exact name from build settings.
        public static bool TryCanonicalizeScene(string normalized, out string scene)
        {
            foreach (string known in KnownScenes)
            {
                if (known.ToLowerInvariant() == normalized)
                {
                    scene = known;
                    return true;
                }
            }
            scene = null;
            return false;
        }

        // Parses a boss query: index in the list, scene name (any case),
        // short alias, exact or partial boss name.
        public static bool TryResolveBoss(string query, out string scene, out string label)
        {
            scene = null;
            label = null;
            if (string.IsNullOrWhiteSpace(query)) return false;

            string norm = NormalizeQuery(query);

            // 1. Index in the registry (1-based)
            int index;
            if (int.TryParse(norm, out index) && index >= 1 && index <= Entries.Length)
            {
                scene = Entries[index - 1].Scene;
                label = Entries[index - 1].Label;
                return true;
            }

            // 2. Exact scene name from the registry (case-insensitive)
            foreach (BossEntry e in Entries)
            {
                if (e.Scene.ToLowerInvariant() == norm)
                {
                    scene = e.Scene;
                    label = e.Label;
                    return true;
                }
            }

            // 3. Short alias (hornet, nkg, sisters, ...)
            string aliasScene;
            if (ExtraAliases.TryGetValue(norm, out aliasScene))
            {
                foreach (BossEntry e in Entries)
                {
                    if (e.Scene == aliasScene)
                    {
                        scene = e.Scene;
                        label = e.Label;
                        return true;
                    }
                }
            }

            // 4. Exact boss name
            foreach (BossEntry e in Entries)
            {
                if (NormalizeQuery(e.Label) == norm)
                {
                    scene = e.Scene;
                    label = e.Label;
                    return true;
                }
            }

            // 5. Scene name from build settings that did not make it into the registry
            // (GG_Spa, GG_Wyrm, GG_Engine and the like)
            string canonical;
            if (TryCanonicalizeScene(norm, out canonical))
            {
                scene = canonical;
                label = canonical + " (scene not in registry)";
                return true;
            }

            // 6. Partial match on scene/label; on ambiguity we
            // prefer the base version of the boss (not (Variant) and not *_V)
            string matched = null;
            string matchedLabel = null;
            foreach (BossEntry e in Entries)
            {
                string sceneNorm = NormalizeQuery(e.Scene);
                if (!sceneNorm.Contains(norm) && !NormalizeQuery(e.Label).Contains(norm))
                    continue;
                if (matched != null)
                {
                    // Second candidate: narrow down to the base version if possible
                    bool curIsVariant = matched.EndsWith("_V") || matchedLabel.Contains("(Variant)");
                    bool newIsVariant = e.Scene.EndsWith("_V") || e.Label.Contains("(Variant)");
                    if (newIsVariant) continue;          // a variant is worse than the base version
                    if (curIsVariant) { matched = e.Scene; matchedLabel = e.Label; continue; }
                    return false;                        // two base versions - ambiguous
                }
                matched = e.Scene;
                matchedLabel = e.Label;
            }
            if (matched != null)
            {
                scene = matched;
                label = matchedLabel;
                return true;
            }

            // 7. Unfamiliar gg_* name - pass it through as is, maybe the scene exists
            if (norm.StartsWith("gg_"))
            {
                scene = query.Trim();
                label = scene + " (unknown scene, attempting to load)";
                return true;
            }

            return false;
        }
    }
}
