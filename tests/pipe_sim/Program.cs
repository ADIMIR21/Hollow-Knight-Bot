// Mock named-pipe server for the AiTrainHK mod: tests the Python client without the game.
// Replicates the transport of the merged AiDataExporter.cs: hello, telemetry stream,
// one-shot events (_outbox), command intake. The boss registry is read from JSON.
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Threading;
using HKPipeInterop;

namespace hkpipesim
{
    public static class Program
    {
        // The harness pipe name differs from the production one: the mock must not be
        // able to take over the pipe of a running game and hijack its clients (nor may
        // the test start driving the game instead of the mock). HK_PIPE_NAME overrides
        // this.
        private static readonly string PIPE_NAME =
            Environment.GetEnvironmentVariable("HK_PIPE_NAME") ?? "hk_ai_mod_sim";
        private const int MAX_CLIENTS = 4;
        private const int POLL_MS = 25;

        private static readonly object Sync = new object();
        private static string _latestJson = "{\"status\": \"booting\", \"restart_pending\": 0}";
        private static long _seq = 0;
        private static readonly List<KeyValuePair<long, string>> Outbox = new List<KeyValuePair<long, string>>();
        private static long _outboxSeq = 0;
        private static volatile bool _shuttingDown = false;
        private static readonly ConcurrentQueue<string> Incoming = new ConcurrentQueue<string>();

        private static readonly List<KeyValuePair<string, string>> Registry = new List<KeyValuePair<string, string>>();
        private static string _registryJson = "{\"status\":\"boss_list\",\"target_scene\":\"GG_False_Knight\",\"count\":0,\"bosses\":[]}";
        private static string _targetScene = "GG_False_Knight";
        private static string _targetGate = "door_dreamEnter";
        private static string _curScene = "GG_False_Knight";
        private static bool _restartPending = false;
        private static int _restartFrames = 0;

        public static void Main(string[] args)
        {
            if (Array.IndexOf(args, "--selftest-stuck") >= 0)
            {
                Environment.Exit(StuckClientSelfTest());
                return;
            }

            if (args.Length > 0 && File.Exists(args[0]))
            {
                _registryJson = File.ReadAllText(args[0]);
                using (JsonDocument doc = JsonDocument.Parse(_registryJson))
                {
                    foreach (JsonElement b in doc.RootElement.GetProperty("bosses").EnumerateArray())
                        Registry.Add(new KeyValuePair<string, string>(
                            b.GetProperty("scene").GetString(), b.GetProperty("label").GetString()));
                    if (doc.RootElement.TryGetProperty("target_scene", out JsonElement ts))
                        _targetScene = ts.GetString();
                }
                _curScene = _targetScene;
                Console.WriteLine($"[mock] registry loaded: {Registry.Count} entries, scene {_curScene}");
            }
            else
            {
                Console.WriteLine("[mock] WARNING: no registry passed, boss_list is empty");
            }

            new Thread(TelemetryLoop) { IsBackground = true, Name = "Telemetry" }.Start();
            new Thread(CommandLoop) { IsBackground = true, Name = "Commands" }.Start();

            // One thread per client slot — exactly as in the mod: the thread blocks in
            // ConnectNamedPipe, then serves that client itself.
            for (int slot = 0; slot < MAX_CLIENTS; slot++)
            {
                int slotId = slot;
                new Thread(() => PipeSlotLoop(slotId)) { IsBackground = true, Name = "Slot" + slotId }.Start();
            }

            while (!_shuttingDown) Thread.Sleep(200);
        }

        private static void Publish(string json)
        {
            lock (Sync) { _latestJson = json; _seq++; Monitor.PulseAll(Sync); }
        }

        private static void PublishEvent(string json)
        {
            // Same marker as in the mod: an event does not replace the latest telemetry.
            string marked = (json != null && json.StartsWith("{\"status\""))
                ? "{\"event\": 1, " + json.Substring(1)
                : json;
            lock (Sync)
            {
                _outboxSeq++;
                Outbox.Add(new KeyValuePair<long, string>(_outboxSeq, marked));
                while (Outbox.Count > 256) Outbox.RemoveAt(0);
                Monitor.PulseAll(Sync);
            }
        }

        // Update 9: the mock mirrors the mod's pause flag. There is no game to freeze
        // here, so it only has to answer and report the flag the way the mod does.
        private static bool _paused = false;

        private static string PauseStateJson(string status)
        {
            return "{\"status\": \"" + status + "\", \"paused\": " + (_paused ? 1 : 0)
                + ", \"time_scale\": " + (_paused ? "0.00" : "1.00") + "}";
        }

        private static void TelemetryLoop()
        {
            int published = 0;
            while (!_shuttingDown)
            {
                try
                {
                    if (_restartFrames > 0)
                    {
                        _restartFrames--;
                        _restartPending = true;
                    }
                    else
                    {
                        _restartPending = false;
                    }

                    string json = "{\"status\": \"fight\", \"restart_pending\": " + (_restartPending ? 1 : 0)
                        + ", \"scene\": \"" + _curScene + "\""
                        + ", \"hp\": 9, \"max_hp\": 9, \"mana\": 33"
                        + ", \"boss_hp\": 40, \"boss_dead\": 0"
                        + ", \"arena_bosses\": 1, \"arena_alive\": 1, \"arena_hp\": 40"
                        + ", \"arena_detail\": \"MockBoss:40:0\""
                        + ", \"x\": 10.00, \"y\": 5.00, \"boss_x\": 15.00, \"boss_y\": 5.00"
                        + ", \"vel_x\": 0.00, \"vel_y\": 0.00, \"boss_vel_x\": 0.00, \"boss_vel_y\": 0.00"
                        + ", \"grounded\": 1, \"facing_right\": 1, \"boss_facing_right\": 1"
                        + ", \"is_attacking\": 0, \"is_dashing\": 0, \"is_jumping\": 0, \"is_falling\": 0"
                        + ", \"is_recoiling\": 0, \"is_dead\": 0, \"was_hit\": 0"
                        + ", \"hit_counter\": 0, \"boss_damage_total\": 0"
                        + ", \"boss_is_attacking\": 0, \"near_hazard\": 0"
                        + ", \"boss_state\": \"idle\", \"paused\": " + (_paused ? 1 : 0) + "}";
                    Publish(json);
                    published++;
                    if (published % 100 == 0)
                        Console.WriteLine($"[mock] telemetry published {published} times, seq={_seq}");
                }
                catch (Exception e)
                {
                    Console.WriteLine("[mock] TelemetryLoop EXCEPTION: " + e);
                }
                Thread.Sleep(50);
            }
        }

        private static void CommandLoop()
        {
            while (!_shuttingDown)
            {
                string line;
                while (Incoming.TryDequeue(out line)) HandleCommand(line);
                Thread.Sleep(20);
            }
        }

        private static void HandleCommand(string raw)
        {
            string line = (raw ?? "").Trim();
            if (line.Length == 0) return;
            string[] parts = line.Split(new[] { ' ', '\t' }, StringSplitOptions.RemoveEmptyEntries);
            string cmd = parts[0].ToLowerInvariant();
            string rest = line.Substring(parts[0].Length).Trim();
            Console.WriteLine($"[mock] command: '{line}'");

            switch (cmd)
            {
                case "set_boss":
                    if (parts.Length >= 2) { _targetScene = parts[1]; Console.WriteLine($"[mock] target: {_targetScene}"); }
                    break;
                case "set_gate":
                    if (parts.Length >= 2) { _targetGate = parts[1]; Console.WriteLine($"[mock] gate: {_targetGate}"); }
                    break;
                case "boss":
                    SelectBoss(rest);
                    break;
                case "bosses":
                    PublishEvent(ReplaceTargetScene(_registryJson));
                    break;
                case "warp":
                    Console.WriteLine($"[mock] warp to gate {_targetGate}");
                    break;
                case "pause":
                    _paused = true;
                    Console.WriteLine("[mock] paused");
                    PublishEvent(PauseStateJson("paused"));
                    break;
                case "resume":
                    _paused = false;
                    Console.WriteLine("[mock] resumed");
                    PublishEvent(PauseStateJson("resumed"));
                    break;
                case "restart":
                case "teleport":
                    if (parts.Length >= 2) _targetScene = parts[1];
                    if (parts.Length >= 3) _targetGate = parts[2];
                    _curScene = _targetScene;
                    _restartFrames = 4;
                    Console.WriteLine($"[mock] restart: {_targetScene} via {_targetGate}");
                    break;
                default:
                    PublishEvent("{\"status\": \"command_error\", \"command\": \"" + cmd + "\", \"reason\": \"unknown command\"}");
                    break;
            }
        }

        private static void SelectBoss(string query)
        {
            string norm = (query ?? "").Trim().ToLowerInvariant();
            int index;
            if (int.TryParse(norm, out index) && index >= 1 && index <= Registry.Count)
            {
                string scene = Registry[index - 1].Key;
                string label = Registry[index - 1].Value;
                _targetScene = scene; _curScene = scene; _restartFrames = 4;
                PublishEvent("{\"status\": \"boss_selected\", \"scene\": \"" + scene + "\", \"label\": \"" + label + "\"}");
                return;
            }
            foreach (KeyValuePair<string, string> e in Registry)
            {
                if (e.Key.ToLowerInvariant() == norm
                    || e.Value.ToLowerInvariant() == norm
                    || e.Key.ToLowerInvariant().Contains(norm) && norm.Length >= 4)
                {
                    _targetScene = e.Key; _curScene = e.Key; _restartFrames = 4;
                    PublishEvent("{\"status\": \"boss_selected\", \"scene\": \"" + e.Key + "\", \"label\": \"" + e.Value + "\"}");
                    return;
                }
            }
            // The mod passes an unfamiliar gg_* name through as a scene ("maybe the scene
            // exists", rule 7 in TryResolveBoss). The mock must replicate this exactly:
            // it used to answer command_error, the test "confirmed" that, while in the game
            // the mod silently loaded a non-existent scene for GG_No_Such_Boss_Scene.
            if (norm.StartsWith("gg_"))
            {
                _targetScene = query.Trim(); _curScene = _targetScene; _restartFrames = 4;
                PublishEvent("{\"status\": \"boss_selected\", \"scene\": \"" + _targetScene
                    + "\", \"label\": \"" + _targetScene + " (unknown scene, load attempt)\"}");
                return;
            }
            PublishEvent("{\"status\": \"command_error\", \"command\": \"boss " + query + "\", \"reason\": \"boss not recognized\"}");
        }

        private static string ReplaceTargetScene(string json)
        {
            int i = json.IndexOf("\"target_scene\"", StringComparison.Ordinal);
            if (i < 0) return json;
            int start = json.IndexOf('"', json.IndexOf(':', i) + 1);
            int end = json.IndexOf('"', start + 1);
            return json.Substring(0, start + 1) + _targetScene + json.Substring(end);
        }

        private static void PipeSlotLoop(int slot)
        {
            byte[] readBuf = new byte[4096];
            var lineBuf = new StringBuilder();
            var events = new List<string>();
            var payload = new StringBuilder();

            while (!_shuttingDown)
            {
                IntPtr pipe = Win32Pipe.Create(@"\\.\pipe\" + PIPE_NAME, MAX_CLIENTS, 65536, 8192);
                if (pipe == IntPtr.Zero)
                {
                    Console.WriteLine($"[mock] slot {slot}: CreateNamedPipe failed, error {Win32Pipe.LastError()}");
                    Thread.Sleep(1000);
                    continue;
                }

                try
                {
                    if (!Win32Pipe.Connect(pipe))
                    {
                        Console.WriteLine($"[mock] slot {slot}: ConnectNamedPipe failed, error {Win32Pipe.LastError()}");
                        Thread.Sleep(1000);
                        continue;
                    }

                    Console.WriteLine($"[mock] slot {slot}: client connected");
                    // The "server" field is the harness marker: the test must make sure it
                    // is talking to the mock and not to the mod of a running game (their
                    // hellos are otherwise indistinguishable, and the test would start
                    // driving the game).
                    byte[] hello = Utf8("{\"status\": \"pipe_hello\", \"protocol\": 3, \"mod_version\": \"1\", \"server\": \"hkpipesim\"}\n");
                    if (Win32Pipe.Write(pipe, hello, hello.Length))
                        PumpClient(pipe, readBuf, lineBuf, events, payload);
                }
                catch (Exception e)
                {
                    Console.WriteLine($"[mock] slot {slot}: EXCEPTION {e.GetType().Name}: {e.Message}");
                }
                finally
                {
                    Win32Pipe.Close(pipe);
                    Console.WriteLine($"[mock] slot {slot}: client disconnected");
                }
            }
        }

        private static void PumpClient(IntPtr pipe, byte[] readBuf, StringBuilder lineBuf,
            List<string> events, StringBuilder payload)
        {
            long lastSeq = -1;
            long lastOutboxId;
            lock (Sync) { lastOutboxId = _outboxSeq; }
            int writes = 0;

            while (!_shuttingDown)
            {
                events.Clear();
                string toSend = null;

                lock (Sync)
                {
                    if (_outboxSeq != lastOutboxId)
                    {
                        foreach (KeyValuePair<long, string> ev in Outbox)
                            if (ev.Key > lastOutboxId) events.Add(ev.Value);
                        lastOutboxId = _outboxSeq;
                    }
                    if (_seq != lastSeq)
                    {
                        lastSeq = _seq;
                        toSend = _latestJson;
                    }
                    if (events.Count == 0 && toSend == null)
                        Monitor.Wait(Sync, POLL_MS);
                }

                if (events.Count > 0 || toSend != null)
                {
                    payload.Length = 0;
                    for (int i = 0; i < events.Count; i++) payload.Append(events[i]).Append('\n');
                    if (toSend != null) payload.Append(toSend).Append('\n');

                    byte[] bytes = Utf8(payload.ToString());
                    if (!Win32Pipe.Write(pipe, bytes, bytes.Length)) return;
                    writes++;
                    if (writes <= 3 || writes % 500 == 0)
                        Console.WriteLine($"[mock] write #{writes}: {bytes.Length} bytes");
                }

                if (!DrainCommands(pipe, readBuf, lineBuf)) return;
            }
        }

        private static bool DrainCommands(IntPtr pipe, byte[] readBuf, StringBuilder lineBuf)
        {
            while (true)
            {
                uint available;
                if (!Win32Pipe.Peek(pipe, out available)) return false;
                if (available == 0) return true;

                int toRead = (int)Math.Min(available, (uint)readBuf.Length);
                int n = Win32Pipe.Read(pipe, readBuf, toRead);
                if (n <= 0) return false;

                lineBuf.Append(Encoding.UTF8.GetString(readBuf, 0, n));
                while (true)
                {
                    string text = lineBuf.ToString();
                    int idx = text.IndexOf('\n');
                    if (idx < 0) break;
                    string line = text.Substring(0, idx).TrimEnd('\r').Trim();
                    lineBuf.Remove(0, idx + 1);
                    if (line.Length > 0) Incoming.Enqueue(line);
                }
                if (lineBuf.Length > 65536) lineBuf.Remove(0, lineBuf.Length - 1024);
            }
        }

        private static byte[] Utf8(string text)
        {
            return Encoding.UTF8.GetBytes(text);
        }

        // Regression test for the slot watchdog in the mod (PipeSlotWatchdogLoop): a client that
        // connects and then never reads makes WriteFile block as soon as the pipe's out buffer is
        // full. Without a way out the slot thread would stay blocked forever and the slot would be
        // lost for good. The mod cancels such a write with CancelSynchronousIo through
        // Win32Pipe.CancelBlockingWrite — that is exactly what this test proves, on this machine.
        //
        // Run: dotnet run --project tests/pipe_sim/pipe_sim.csproj -- --selftest-stuck
        private static int StuckClientSelfTest()
        {
            const string serverName = @"\\.\pipe\hk_ai_mod_stuck_test";
            const string clientName = "hk_ai_mod_stuck_test";

            IntPtr server = Win32Pipe.Create(serverName, 1, 4096, 4096);
            if (server == IntPtr.Zero)
            {
                Console.WriteLine($"FAIL: CreateNamedPipe failed (error {Win32Pipe.LastError()})");
                return 1;
            }

            // The client is .NET's own NamedPipeClientStream: the test runs on the desktop runtime,
            // where (unlike the game's Mono) it is implemented. It deliberately never reads.
            var clientThread = new Thread(() =>
            {
                var client = new System.IO.Pipes.NamedPipeClientStream(".", clientName,
                    System.IO.Pipes.PipeDirection.InOut);
                try
                {
                    client.Connect(5000);
                    Thread.Sleep(60000); // connected, never reading — this is the whole point
                }
                catch (Exception e)
                {
                    Console.WriteLine("client: " + e.Message);
                }
            });
            clientThread.IsBackground = true;
            clientThread.Start();

            if (!Win32Pipe.Connect(server))
            {
                Console.WriteLine($"FAIL: ConnectNamedPipe failed (error {Win32Pipe.LastError()})");
                Win32Pipe.Close(server);
                return 1;
            }

            byte[] chunk = new byte[64 * 1024];
            var writerDone = new ManualResetEventSlim(false);
            bool writeOk = true;
            uint writerThreadId = 0;

            var writer = new Thread(() =>
            {
                writerThreadId = Win32Pipe.CurrentThreadId();
                writeOk = Win32Pipe.Write(server, chunk, chunk.Length); // blocks once the buffer fills
                writerDone.Set();
            });
            writer.IsBackground = true;
            writer.Start();

            Thread.Sleep(1000); // the buffer is 4 KB, the write is 64 KB: by now it must be blocked
            bool stuck = !writerDone.IsSet;
            Console.WriteLine(stuck
                ? "the write is blocked as expected (the client is not reading)"
                : "WARNING: the write did not block — check the pipe buffer sizes");

            bool cancelIssued = Win32Pipe.CancelBlockingWrite(writerThreadId);
            bool unblocked = writerDone.Wait(5000);

            Console.WriteLine($"CancelBlockingWrite issued: {cancelIssued}, "
                + $"the write returned: {unblocked}, Write() result: {writeOk}");

            Win32Pipe.Close(server);

            bool pass = stuck && cancelIssued && unblocked && !writeOk;
            Console.WriteLine(pass
                ? "STUCK-CLIENT SELFTEST PASSED: the stuck write is cancellable, the slot can be freed"
                : "STUCK-CLIENT SELFTEST FAILED");
            return pass ? 0 : 1;
        }
    }
}
