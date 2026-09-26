# Стенд проверки пайп-протокола без запущенной игры

`hkpipesim` — маленький C#-сервер, повторяющий транспорт мода
(`Mod/HK_AI_Mod/AiDataExporter.cs`): hello, поток телеметрии, одноразовые
события (`boss_list` / `boss_selected` / `command_error`) и приём команд
(`restart`, `teleport`, `set_boss`, `set_gate`, `boss`, `bosses`, `warp`).

Он позволяет прогнать `hk_pipe.py`, `bosses.py` и `teleport.py` целиком на
реальном именованном пайпе Windows — без Hollow Knight.

Проект **подключает `Mod/HK_AI_Mod/Win32Pipe.cs` тем же файлом**, а не копией:
стенд проверяет ровно тот код, который работает в игре.

## Три бага, которые нашлись по дороге

Первые два нашёл этот стенд, третий — запущенная игра с новым модом. Все три
выглядели одинаково: «мод молчит, команды не доходят».

1. **`PipeOptions.None` (синхронный хэндл).** Пока висит блокирующий `ReadAsync`,
   следующий `WriteLine` виснет навсегда. В .NET лечится `PipeOptions.Asynchronous`.
2. **`open(path, "r+b")` на клиенте.** Даёт `BufferedRandom`, который отдаёт
   первый считанный чанк и навсегда виснет на следующем `readline()`.
   Лечится сырыми `os.read` / `os.write`.
3. **Mono не умеет `NamedPipeServerStream` вообще.** В `System.Core.dll` игры
   (mscorlib 4.6.57) все публичные конструкторы типа сходятся в две заглушки:
   `..ctor(string, PipeDirection, int, PipeTransmissionMode, PipeOptions, int, int)`
   и `..ctor(..., PipeSecurity, HandleInheritability, PipeAccessRights)` — обе
   бросают `NotImplementedException`. В игре это дало бесконечное
   `[ИИ] Ошибка пайп-сервера: The method or operation is not implemented.`
   Лечится тем, что сервер поднимается через `kernel32` напрямую (`Win32Pipe.cs`).
   Диагноз получен разбором IL, без запуска игры: `System.Reflection.Metadata`
   по `System.Core.dll` показывает, какие методы — заглушки.

Из-за третьего пункта `PipeOptions` больше не используется, но первый баг
остаётся причиной, по которой `Win32Pipe` работает **без** overlapped I/O и без
висящих операций: один поток на клиента строго по очереди пишет, опрашивает
(`PeekNamedPipe`) и читает. Незавершённых операций на хэндле не бывает.

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
