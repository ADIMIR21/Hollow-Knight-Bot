# Диагностика Mono: ищем заглушки в BCL игры

Инструмент отвечает на вопрос «а точно ли этот API есть в Mono, на которой
работает Hollow Knight?» — без запуска игры.

Он читает IL указанной сборки через `System.Reflection.Metadata` и печатает для
каждого метода, есть ли в его теле `newobj` исключения-заглушки
(`NotImplementedException`, `PlatformNotSupportedException`, `NotSupportedException`)
и куда метод делегирует.

## Зачем понадобился

Сервер пайпа в моде не запускался, в `ModLog.txt` было бесконечно:

```
[ИИ] Ошибка пайп-сервера: The method or operation is not implemented.
```

Разбор `System.Core.dll` игры показал, что в этой Mono **все** публичные
конструкторы `System.IO.Pipes.NamedPipeServerStream` сходятся в две заглушки:

```
.ctor(String)                                             -> ..ctor(String, PipeDirection)
.ctor(String, PipeDirection)                              -> ..ctor(String, PipeDirection, Int32)
.ctor(String, PipeDirection, Int32)                       -> ..ctor(..., PipeTransmissionMode)
.ctor(..., PipeTransmissionMode)                          -> ..ctor(..., PipeOptions)
.ctor(..., PipeOptions)                                   -> ..ctor(..., Int32, Int32)   <- 7 аргументов
.ctor(String, PipeDirection, Int32, PipeTransmissionMode, PipeOptions, Int32, Int32)
                                                          -> ЗАГЛУШКА: NotImplementedException
.ctor(..., PipeSecurity)                                  -> ..ctor(..., HandleInheritability)
.ctor(..., HandleInheritability)                          -> ..ctor(..., PipeAccessRights)  <- 10 аргументов
.ctor(..., PipeAccessRights)                              -> ЗАГЛУШКА: NotImplementedException
.ctor(PipeDirection, Boolean, Boolean, SafePipeHandle)    -> ЗАГЛУШКА: NotImplementedException
```

То есть тип невозможно даже создать — дело не в выборе `PipeOptions`. Поэтому
сервер пайпа в моде поднимается через `kernel32` (`Mod/HK_AI_Mod/Win32Pipe.cs`),
а `PipeStream.Read/Write/BeginRead/EndRead` в Mono, кстати, реализованы — но до
них нельзя добраться без рабочего конструктора.

## Запуск

```powershell
# все типы System.IO.Pipes
dotnet run --project tests/mono_il/mono_il.csproj -- `
  "D:\SteamLibrary\steamapps\common\Hollow Knight\hollow_knight_Data\Managed\System.Core.dll"

# или конкретные типы
dotnet run --project tests/mono_il/mono_il.csproj -- `
  "...\Managed\System.Core.dll" NamedPipeServerStream
```

Если понадобится другой API (сокеты, `MemoryMappedFile`, `Stopwatch`) — подставьте
нужную сборку и имя типа. Важно помнить: ограничение здесь на стороне Mono, а не
мода, и обойти его можно только P/Invoke или другим классом API.
