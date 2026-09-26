// Mock-сервер пайпа мода HK_AI_Mod для проверки Python-клиента без игры.
// Повторяет транспорт объединённого AiDataExporter.cs: hello, поток телеметрии,
// одноразовые события (_outbox), приём команд. Реестр боссов читается из JSON.
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.IO.Pipes;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace hkpipesim
{
    public static class Program
    {
        private const string PIPE_NAME = "hk_ai_mod";
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
        private static string _targetGate = "door1";
        private static string _curScene = "GG_False_Knight";
        private static bool _restartPending = false;
        private static int _restartFrames = 0;

        public static void Main(string[] args)
        {
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
                Console.WriteLine($"[mock] реестр загружен: {Registry.Count} записей, сцена {_curScene}");
            }
            else
            {
                Console.WriteLine("[mock] ВНИМАНИЕ: реестр не передан, boss_list пустой");
            }

            new Thread(TelemetryLoop) { IsBackground = true, Name = "Telemetry" }.Start();
            new Thread(CommandLoop) { IsBackground = true, Name = "Commands" }.Start();

            while (!_shuttingDown)
            {
                NamedPipeServerStream server = null;
                try
                {
                    server = new NamedPipeServerStream(
                        PIPE_NAME, PipeDirection.InOut, MAX_CLIENTS,
                        PipeTransmissionMode.Byte, PipeOptions.Asynchronous,
                        inBufferSize: 8192, outBufferSize: 65536);
                    server.WaitForConnection();
                    Console.WriteLine("[mock] клиент подключился");
                    Thread pump = new Thread(ClientPump) { IsBackground = true, Name = "Pump" };
                    pump.Start(server);
                    server = null;
                }
                catch (Exception e)
                {
                    if (!_shuttingDown)
                    {
                        Console.WriteLine("[mock] ошибка сервера: " + e.Message);
                        Thread.Sleep(500);
                    }
                }
                finally
                {
                    if (server != null) { try { server.Dispose(); } catch (Exception) { } }
                }
            }
        }

        private static void Publish(string json)
        {
            lock (Sync) { _latestJson = json; _seq++; Monitor.PulseAll(Sync); }
        }

        private static void PublishEvent(string json)
        {
            // Та же метка, что в моде: событие не подменяет последнюю телеметрию.
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
                        + ", \"x\": 10.00, \"y\": 5.00, \"boss_x\": 15.00, \"boss_y\": 5.00"
                        + ", \"vel_x\": 0.00, \"vel_y\": 0.00, \"boss_vel_x\": 0.00, \"boss_vel_y\": 0.00"
                        + ", \"grounded\": 1, \"facing_right\": 1, \"boss_facing_right\": 1"
                        + ", \"is_attacking\": 0, \"is_dashing\": 0, \"is_jumping\": 0, \"is_falling\": 0"
                        + ", \"is_recoiling\": 0, \"is_dead\": 0, \"was_hit\": 0"
                        + ", \"hit_counter\": 0, \"boss_damage_total\": 0"
                        + ", \"boss_is_attacking\": 0, \"near_hazard\": 0"
                        + ", \"boss_state\": \"idle\"}";
                    Publish(json);
                    published++;
                    if (published % 100 == 0)
                        Console.WriteLine($"[mock] телеметрия опубликована {published} раз, seq={_seq}");
                }
                catch (Exception e)
                {
                    Console.WriteLine("[mock] TelemetryLoop ИСКЛЮЧЕНИЕ: " + e);
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
            Console.WriteLine($"[mock] команда: '{line}'");

            switch (cmd)
            {
                case "set_boss":
                    if (parts.Length >= 2) { _targetScene = parts[1]; Console.WriteLine($"[mock] цель: {_targetScene}"); }
                    break;
                case "set_gate":
                    if (parts.Length >= 2) { _targetGate = parts[1]; Console.WriteLine($"[mock] гейт: {_targetGate}"); }
                    break;
                case "boss":
                    SelectBoss(rest);
                    break;
                case "bosses":
                    PublishEvent(ReplaceTargetScene(_registryJson));
                    break;
                case "warp":
                    Console.WriteLine($"[mock] warp к гейту {_targetGate}");
                    break;
                case "restart":
                case "teleport":
                    if (parts.Length >= 2) _targetScene = parts[1];
                    if (parts.Length >= 3) _targetGate = parts[2];
                    _curScene = _targetScene;
                    _restartFrames = 4;
                    Console.WriteLine($"[mock] рестарт: {_targetScene} через {_targetGate}");
                    break;
                default:
                    PublishEvent("{\"status\": \"command_error\", \"command\": \"" + cmd + "\", \"reason\": \"неизвестная команда\"}");
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
            PublishEvent("{\"status\": \"command_error\", \"command\": \"boss " + query + "\", \"reason\": \"босс не распознан\"}");
        }

        private static string ReplaceTargetScene(string json)
        {
            int i = json.IndexOf("\"target_scene\"", StringComparison.Ordinal);
            if (i < 0) return json;
            int start = json.IndexOf('"', json.IndexOf(':', i) + 1);
            int end = json.IndexOf('"', start + 1);
            return json.Substring(0, start + 1) + _targetScene + json.Substring(end);
        }

        private static void ClientPump(object state)
        {
            NamedPipeServerStream server = (NamedPipeServerStream)state;
            int writes = 0;
            try
            {
                using (server)
                using (StreamWriter writer = new StreamWriter(server, new UTF8Encoding(false)) { AutoFlush = true, NewLine = "\n" })
                {
                    writer.WriteLine("{\"status\": \"pipe_hello\", \"protocol\": 3, \"mod_version\": \"1.3\"}");
                    Console.WriteLine("[mock] hello отправлен");

                    long lastSeq = -1;
                    long lastOutboxId;
                    lock (Sync) { lastOutboxId = _outboxSeq; }

                    List<string> events = new List<string>();
                    byte[] readBuf = new byte[4096];
                    StringBuilder lineBuf = new StringBuilder();
                    Task<int> readTask = BeginPipeRead(server, readBuf);
                    Console.WriteLine($"[mock] начальное чтение: task={(readTask == null ? "null" : "ok")}");

                    while (server.IsConnected && !_shuttingDown)
                    {
                        string toSend = null;
                        events.Clear();

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

                        for (int i = 0; i < events.Count; i++) writer.WriteLine(events[i]);
                        if (toSend != null)
                        {
                            writer.WriteLine(toSend);
                            writes++;
                            if (writes <= 5 || writes % 200 == 0)
                                Console.WriteLine($"[mock] write #{writes}: {toSend.Length} байт, isConnected={server.IsConnected}");
                        }

                        readTask = DrainIncoming(server, readTask, readBuf, lineBuf);
                        if (writes <= 3 && readTask != null)
                            Console.WriteLine($"[mock] после DrainIncoming: readTask completed={readTask.IsCompleted}");
                    }
                    Console.WriteLine("[mock] цикл pump завершён штатно");
                }
            }
            catch (Exception e)
            {
                Console.WriteLine($"[mock] pump ИСКЛЮЧЕНИЕ после {writes} записей: {e.GetType().Name}: {e.Message}");
            }
            finally
            {
                try { server.Dispose(); } catch (Exception) { }
            }
        }

        private static Task<int> BeginPipeRead(NamedPipeServerStream server, byte[] buf)
        {
            try { return server.ReadAsync(buf, 0, buf.Length); }
            catch (Exception) { return null; }
        }

        private static Task<int> DrainIncoming(NamedPipeServerStream server, Task<int> readTask, byte[] readBuf, StringBuilder lineBuf)
        {
            if (readTask == null || !readTask.IsCompleted) return readTask;

            int n;
            try { n = readTask.Result; }
            catch (Exception) { throw new IOException("пайп: ошибка чтения"); }

            if (n <= 0) throw new IOException("пайп: клиент закрыл соединение");

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

            return BeginPipeRead(server, readBuf);
        }
    }
}
