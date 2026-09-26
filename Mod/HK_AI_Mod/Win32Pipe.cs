// Сервер именованных пайпов Windows напрямую через kernel32.
//
// ПОЧЕМУ НЕ NamedPipeServerStream:
// Hollow Knight работает на Mono (mscorlib 4.6.57, Unity). В этой Mono
// ВСЕ публичные конструкторы System.IO.Pipes.NamedPipeServerStream сходятся
// в две заглушки:
//   ..ctor(string, PipeDirection, int, PipeTransmissionMode, PipeOptions, int, int)
//   ..ctor(string, PipeDirection, int, PipeTransmissionMode, PipeOptions, int, int,
//           PipeSecurity, HandleInheritability, PipeAccessRights)
// обе бросают NotImplementedException, а одноаргументный и все остальные
// конструкторы делегируют в них. То есть тип невозможно даже создать:
// в игре это выглядело как бесконечное
//   "[ИИ] Ошибка пайп-сервера: The method or operation is not implemented."
// Проверено разбором IL System.Core.dll самой игры (NamedPipeClientStream.Connect
// в Mono, наоборот, реализован — но клиент у нас Python, он ходит в Win32 сам).
//
// Поэтому сервер поднимается через P/Invoke. Хэндл — обычный синхронный, без
// overlapped I/O. Каждый инстанс обслуживает один поток, который СТРОГО
// ПОСЛЕДОВАТЕЛЬНО пишет, затем опрашивает и читает: на хэндле никогда не висит
// незавершённая операция, поэтому дедлок «висящее чтение блокирует запись»
// (он воспроизводится на синхронном хэндле, если держать ReadAsync)
// структурно невозможен.
//
// Файл намеренно самодостаточный: тот же код использует стенд tests/pipe_sim,
// поэтому протокол проверяется ровно на том коде, который работает в игре.

using System;
using System.Runtime.InteropServices;

namespace HKPipeInterop
{
    public static class Win32Pipe
    {
        public const int ERROR_BROKEN_PIPE = 109;
        public const int ERROR_NO_DATA = 232;
        public const int ERROR_PIPE_BUSY = 231;
        public const int ERROR_PIPE_CONNECTED = 535;
        public const int ERROR_PIPE_LISTENING = 536;
        public const int ERROR_SEM_TIMEOUT = 121;

        // Открываем пайп как duplex: Python ходит в него одним os.open(O_RDWR).
        private const uint PIPE_ACCESS_DUPLEX = 0x00000003;
        // Байтовый тип + байтовый режим чтения + блокирующий режим: все нули.
        private const uint PIPE_TYPE_BYTE = 0x00000000;
        private const uint PIPE_READMODE_BYTE = 0x00000000;
        private const uint PIPE_WAIT = 0x00000000;

        private static readonly IntPtr INVALID_HANDLE_VALUE = new IntPtr(-1);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateNamedPipeW(
            string lpName,
            uint dwOpenMode,
            uint dwPipeMode,
            uint nMaxInstances,
            uint nOutBufferSize,
            uint nInBufferSize,
            uint nDefaultTimeOut,
            IntPtr lpSecurityAttributes);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool ConnectNamedPipe(IntPtr hNamedPipe, IntPtr lpOverlapped);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool PeekNamedPipe(
            IntPtr hNamedPipe,
            IntPtr lpBuffer,
            uint nBufferSize,
            IntPtr lpBytesRead,
            out uint lpTotalBytesAvail,
            IntPtr lpBytesLeftThisMessage);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool ReadFile(
            IntPtr hFile,
            byte[] lpBuffer,
            uint nNumberOfBytesToRead,
            out uint lpNumberOfBytesRead,
            IntPtr lpOverlapped);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool WriteFile(
            IntPtr hFile,
            byte[] lpBuffer,
            uint nNumberOfBytesToWrite,
            out uint lpNumberOfBytesWritten,
            IntPtr lpOverlapped);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool DisconnectNamedPipe(IntPtr hNamedPipe);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr hObject);

        /// <summary>Создаёт инстанс пайпа. IntPtr.Zero — не удалось.</summary>
        public static IntPtr Create(string fullPipeName, int maxInstances, int outBuffer, int inBuffer)
        {
            IntPtr handle = CreateNamedPipeW(
                fullPipeName,
                PIPE_ACCESS_DUPLEX,
                PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
                (uint)maxInstances,
                (uint)outBuffer,
                (uint)inBuffer,
                0,
                IntPtr.Zero);
            return handle == INVALID_HANDLE_VALUE ? IntPtr.Zero : handle;
        }

        /// <summary>
        /// Ждёт подключения клиента (блокирует поток). true — соединение есть.
        /// ERROR_PIPE_CONNECTED тоже успех: клиент успел подключиться до вызова.
        /// </summary>
        public static bool Connect(IntPtr handle)
        {
            if (ConnectNamedPipe(handle, IntPtr.Zero))
                return true;
            return Marshal.GetLastWin32Error() == ERROR_PIPE_CONNECTED;
        }

        /// <summary>Сколько байт готово к чтению. false — пайп разорван.</summary>
        public static bool Peek(IntPtr handle, out uint available)
        {
            return PeekNamedPipe(handle, IntPtr.Zero, 0, IntPtr.Zero, out available, IntPtr.Zero);
        }

        /// <summary>Читает до count байт (вызывать только когда Peek подтвердил данные). -1 — ошибка.</summary>
        public static int Read(IntPtr handle, byte[] buffer, int count)
        {
            uint read;
            if (!ReadFile(handle, buffer, (uint)count, out read, IntPtr.Zero))
                return -1;
            return (int)read;
        }

        /// <summary>Пишет все count байт, добивая частичные записи. false — пайп разорван.</summary>
        public static bool Write(IntPtr handle, byte[] data, int count)
        {
            int offset = 0;
            while (offset < count)
            {
                byte[] chunk;
                if (offset == 0 && count == data.Length)
                {
                    chunk = data;
                }
                else
                {
                    chunk = new byte[count - offset];
                    Buffer.BlockCopy(data, offset, chunk, 0, chunk.Length);
                }

                uint written;
                if (!WriteFile(handle, chunk, (uint)chunk.Length, out written, IntPtr.Zero))
                    return false;
                if (written == 0)
                    return false;
                offset += (int)written;
            }
            return true;
        }

        /// <summary>Отключает и закрывает инстанс.</summary>
        public static void Close(IntPtr handle)
        {
            if (handle == IntPtr.Zero || handle == INVALID_HANDLE_VALUE)
                return;
            try { DisconnectNamedPipe(handle); } catch (Exception) { }
            try { CloseHandle(handle); } catch (Exception) { }
        }

        public static int LastError()
        {
            return Marshal.GetLastWin32Error();
        }
    }
}
