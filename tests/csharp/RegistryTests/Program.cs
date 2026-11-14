using System;
using System.Collections.Generic;
using AiTrainHK;

// A dependency-free self-check for the mod's boss registry and query resolver
// (Mod/AiTrainHK/BossRegistry.cs, compiled into this project as a link). The
// Python tier only regexes the source; this one executes it. Exit code 0 = all
// checks passed, 1 = at least one failed (CI runs it on the windows job).
namespace RegistryTests
{
    public static class Program
    {
        private static int _failures;

        private static void Check(string name, bool condition, string detail = null)
        {
            if (condition)
            {
                Console.WriteLine($"PASS {name}");
                return;
            }
            _failures++;
            Console.WriteLine($"FAIL {name}{(detail == null ? "" : ": " + detail)}");
        }

        public static int Main()
        {
            var entries = BossRegistry.Entries;

            Check("registry is not empty", entries.Length > 0);
            Check("registry holds the documented 60 entries", entries.Length == 60,
                  $"found {entries.Length}");

            var scenes = new HashSet<string>();
            foreach (var entry in entries) scenes.Add(entry.Scene);
            Check("no scene is listed twice", scenes.Count == entries.Length,
                  $"{scenes.Count} distinct scenes for {entries.Length} entries");

            var aliasTargets = new HashSet<string>();
            foreach (var target in BossRegistry.ExtraAliases.Values) aliasTargets.Add(target);
            Check("every alias targets a registry scene", aliasTargets.IsSubsetOf(scenes),
                  "ExtraAliases points at a scene the registry does not know");

            Check("index lookup is 1-based",
                  BossRegistry.TryResolveBoss("3", out var scene, out _) && scene == "GG_False_Knight",
                  scene);

            Check("exact scene name in any case",
                  BossRegistry.TryResolveBoss("gg_hornet_1", out scene, out _) && scene == "GG_Hornet_1",
                  scene);

            Check("short alias",
                  BossRegistry.TryResolveBoss("nkg", out scene, out _) && scene == "GG_Grimm_Nightmare",
                  scene);

            Check("exact boss label with spaces",
                  BossRegistry.TryResolveBoss("Nightmare King Grimm", out scene, out _)
                  && scene == "GG_Grimm_Nightmare",
                  scene);

            Check("a scene outside the registry is canonicalized",
                  BossRegistry.TryResolveBoss("gg_spa", out scene, out _)
                  && scene == "GG_Spa" && scene != null,
                  scene);

            Check("partial match prefers the base version over the variant",
                  BossRegistry.TryResolveBoss("mantis", out scene, out _)
                  && scene == "GG_Mantis_Lords",
                  scene);

            Check("an unknown gg_ name passes through",
                  BossRegistry.TryResolveBoss("gg_made_up_boss", out scene, out _)
                  && scene == "gg_made_up_boss",
                  scene);

            Check("an unknown non-gg_ query is refused",
                  !BossRegistry.TryResolveBoss("totally-not-a-boss", out scene, out _),
                  scene ?? "<null>");

            Check("an empty query is refused",
                  !BossRegistry.TryResolveBoss("   ", out scene, out _),
                  scene ?? "<null>");

            Check("normalize folds separators",
                  BossRegistry.NormalizeQuery("GG  Hornet--1 _") == "gg_hornet_1",
                  BossRegistry.NormalizeQuery("GG  Hornet--1 _"));

            Check("the default scene and gate are the documented ones",
                  BossRegistry.DefaultBossScene == "GG_False_Knight"
                  && BossRegistry.DefaultEntryGate == "door_dreamEnter");

            Console.WriteLine(_failures == 0
                ? $"ALL CHECKS PASSED ({entries.Length} registry entries)"
                : $"{_failures} CHECK(S) FAILED");
            return _failures == 0 ? 0 : 1;
        }
    }
}
