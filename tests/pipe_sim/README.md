# Стенд проверки пайп-протокола без запущенной игры

`hkpipesim` — маленький C#-сервер, повторяющий транспорт мода
(`Mod/HK_AI_Mod/AiDataExporter.cs`): hello, поток телеметрии, одноразовые
события (`boss_list` / `boss_selected` / `command_error`) и приём команд
(`restart`, `teleport`, `set_boss`, `set_gate`, `boss`, `bosses`, `warp`).

Он позволяет прогнать `hk_pipe.py`, `bosses.py` и `teleport.py` целиком на
реальном именованном пайпе Windows — без Hollow Knight.

Стенд окупился: на нём нашлись два бага, которые в игре выглядели бы как
«мод замолчал после первого кадра» и «команды не доходят»:

1. **`PipeOptions.None` (синхронный хэндл пайпа).** Пока висит блокирующий
   `ReadAsync`, следующий `WriteLine` виснет навсегда. Лечится
   `PipeOptions.Asynchronous` (overlapped I/O).
2. **`open(path, "r+b")` на клиенте.** Даёт `BufferedRandom`, который отдаёт
   первый считанный чанк и навсегда виснет на следующем `readline()`.
   Лечится сырыми `os.read` / `os.write`.

## Запуск

Нужен .NET SDK 8+ и Python 3.10+.

```powershell
# 1. Реестр боссов из bosses.py в формат события boss_list
python tests/pipe_sim/gen_boss_list.py

# 2. Mock-сервер мода (в отдельном терминале или в фоне)
dotnet run --project tests/pipe_sim/pipe_sim.csproj -- tests/pipe_sim/bosses.json

# 3. Интеграционный тест
python tests/pipe_sim/test_pipe.py
```

Успех — `ВСЕ ПРОВЕРКИ ПРОШЛИ` и код возврата 0.

Тест проверяет: hello и версию протокола, поток телеметрии и монотонность
`seq`, `wait_for_fresh`, команду `bosses` и сверку реестров
(`teleport.py --verify`), `boss <сцена>` с ожиданием начала боя,
`command_error` на нераспознанного босса, рестарт, `set_boss`/`set_gate`,
`teleport_to` по алиасу и `warp`.

**Важно:** событие не должно подменять последнюю телеметрию (иначе шаг RL
получит JSON без `hp`/`x`/`y`). Мод помечает события полем `"event": 1`,
клиент такие сообщения в `_latest` не пишет — тест это проверяет.

Симулятор — не мод: он не проверяет игровую логику (переходы сцен, watchdog,
телепорт героя). Это по-прежнему нужно прогонять в игре.
