# Mono diagnostics: looking for stubs in the game's BCL

The tool answers the question "does this API really exist in the Mono that
Hollow Knight runs on?" — without launching the game.

It reads the IL of the given assembly through `System.Reflection.Metadata` and
prints, for every method, whether its body contains a `newobj` of a stub exception
(`NotImplementedException`, `PlatformNotSupportedException`, `NotSupportedException`)
and where the method delegates to.

## Why it was needed

The pipe server in the mod would not start; `ModLog.txt` was endless:

```
[AI] Pipe slot 0: error — The method or operation is not implemented.
```

(The log line was still in Russian when this was diagnosed; it is quoted here translated, the exception text itself verbatim.)

Analysis of the game's `System.Core.dll` showed that in this Mono **all** public
constructors of `System.IO.Pipes.NamedPipeServerStream` collapse into two stubs:

```
.ctor(String)                                             -> ..ctor(String, PipeDirection)
.ctor(String, PipeDirection)                              -> ..ctor(String, PipeDirection, Int32)
.ctor(String, PipeDirection, Int32)                       -> ..ctor(..., PipeTransmissionMode)
.ctor(..., PipeTransmissionMode)                          -> ..ctor(..., PipeOptions)
.ctor(..., PipeOptions)                                   -> ..ctor(..., Int32, Int32)   <- 7 arguments
.ctor(String, PipeDirection, Int32, PipeTransmissionMode, PipeOptions, Int32, Int32)
                                                          -> STUB: NotImplementedException
.ctor(..., PipeSecurity)                                  -> ..ctor(..., HandleInheritability)
.ctor(..., HandleInheritability)                          -> ..ctor(..., PipeAccessRights)  <- 10 arguments
.ctor(..., PipeAccessRights)                              -> STUB: NotImplementedException
.ctor(PipeDirection, Boolean, Boolean, SafePipeHandle)    -> STUB: NotImplementedException
```

That is, the type cannot even be instantiated — the problem is not the choice of
`PipeOptions`. That is why the pipe server in the mod comes up through `kernel32`
(`Mod/AiTrainHK/Win32Pipe.cs`), while `PipeStream.Read/Write/BeginRead/EndRead` in
Mono are, incidentally, implemented — but you cannot reach them without a working
constructor.

## Running

```powershell
# all System.IO.Pipes types
dotnet run --project tests/mono_il/mono_il.csproj -- `
  "D:\SteamLibrary\steamapps\common\Hollow Knight\hollow_knight_Data\Managed\System.Core.dll"

# or specific types
dotnet run --project tests/mono_il/mono_il.csproj -- `
  "...\Managed\System.Core.dll" NamedPipeServerStream
```

If you need a different API (sockets, `MemoryMappedFile`, `Stopwatch`) — plug in the
required assembly and type name. Remember: the limitation here is on the Mono side,
not the mod's, and the only ways around it are P/Invoke or a different API class.

## Listing types, fields and visibility

The probe only ever looks at types it is told by name, so finding an unknown class means listing
first:

```powershell
# every type whose full name contains the substring, case-insensitive
dotnet run --project tests/mono_il/mono_il.csproj -- "...\Assembly-CSharp.dll" --list Input

# one type: its fields, its methods, the visibility of each, and what the IL does
dotnet run --project tests/mono_il/mono_il.csproj -- "...\Assembly-CSharp.dll" InputHandler
```

A type filter widens the probe to every namespace; without one it stays on `System.IO.Pipes`,
which is what the tool was written for. Both were needed to find the game's input classes:
`InputHandler` and `HeroActions` are plain types in the global namespace, so the old hard-coded
namespace filter hid them no matter how they were named.

The visibility column is what decides whether the mod can call something directly - in
`OneAxisInputControl`, `CommitWithState` is `public` while `PrepareForUpdate` is `internal`.