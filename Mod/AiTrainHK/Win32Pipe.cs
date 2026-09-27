// Windows named pipe server driven directly through kernel32.
//
// WHY NOT NamedPipeServerStream:
// Hollow Knight runs on Mono (mscorlib 4.6.57, Unity). In this Mono,
// ALL public constructors of System.IO.Pipes.NamedPipeServerStream funnel
// into two stubs:
//   ..ctor(string, PipeDirection, int, PipeTransmissionMode, PipeOptions, int, int)
//   ..ctor(string, PipeDirection, int, PipeTransmissionMode, PipeOptions, int, int,
//           PipeSecurity, HandleInheritability, PipeAccessRights)
// both throw NotImplementedException, and the one-argument constructor and
// all the others delegate into them. That is, the type cannot even be created:
// in the game this looked like an endless
//   "[AI] Pipe server error: The method or operation is not implemented."
// Verified by disassembling the game's own System.Core.dll (NamedPipeClientStream.Connect
// in Mono, by contrast, is implemented — but our client is Python, it calls Win32 itself).
//
// That is why the server is brought up through P/Invoke. The handle is an ordinary
// synchronous one, without overlapped I/O. Each instance is served by one thread that
// STRICTLY SEQUENTIALLY writes, then polls and reads: an incomplete operation is never
// pending on the handle, so the deadlock "a hanging read blocks the write"
// (it reproduces on a synchronous handle if ReadAsync is kept pending)
// is structurally impossible.
//
// The file is deliberately self-contained: the tests/pipe_sim harness compiles the same code,
// so the protocol is verified against exactly the code that runs in the game.

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

        // Open the pipe as duplex: Python talks to it with a single os.open(O_RDWR).
        private const uint PIPE_ACCESS_DUPLEX = 0x00000003;
        // Byte type + byte read mode + blocking mode: all zeroes.
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

        [DllImport("kernel32.dll")]
        private static extern uint GetCurrentThreadId();

        // CancelSynchronousIo needs a thread handle opened with THREAD_TERMINATE.
        private const uint THREAD_TERMINATE = 0x0001;

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr OpenThread(uint desiredAccess, bool inheritHandle, uint threadId);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CancelSynchronousIo(IntPtr hThread);

        /// <summary>Creates a pipe instance. IntPtr.Zero means it failed.</summary>
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
        /// Waits for a client to connect (blocks the thread). true means connected.
        /// ERROR_PIPE_CONNECTED is also a success: the client connected before the call.
        /// </summary>
        public static bool Connect(IntPtr handle)
        {
            if (ConnectNamedPipe(handle, IntPtr.Zero))
                return true;
            return Marshal.GetLastWin32Error() == ERROR_PIPE_CONNECTED;
        }

        /// <summary>How many bytes are ready to read. false means the pipe is broken.</summary>
        public static bool Peek(IntPtr handle, out uint available)
        {
            return PeekNamedPipe(handle, IntPtr.Zero, 0, IntPtr.Zero, out available, IntPtr.Zero);
        }

        /// <summary>Reads up to count bytes (call only when Peek has confirmed data). -1 means error.</summary>
        public static int Read(IntPtr handle, byte[] buffer, int count)
        {
            uint read;
            if (!ReadFile(handle, buffer, (uint)count, out read, IntPtr.Zero))
                return -1;
            return (int)read;
        }

        /// <summary>Writes all count bytes, topping up partial writes. false means the pipe is broken.</summary>
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

        /// <summary>Disconnects and closes the instance.</summary>
        public static void Close(IntPtr handle)
        {
            if (handle == IntPtr.Zero || handle == INVALID_HANDLE_VALUE)
                return;
            try { DisconnectNamedPipe(handle); } catch (Exception) { }
            try { CloseHandle(handle); } catch (Exception) { }
        }

        /// <summary>Id of the calling thread. A slot thread stores it so that its stuck write can be cancelled later.</summary>
        public static uint CurrentThreadId()
        {
            return GetCurrentThreadId();
        }

        /// <summary>
        /// Cancels a blocking synchronous WriteFile that is running on another thread and reports whether the
        /// cancellation was issued. Needed because a client that stops reading makes WriteFile block forever once
        /// the pipe's out buffer is full - the slot would be lost for good, since it never gets back to
        /// ConnectNamedPipe. After the cancellation the write fails with ERROR_OPERATION_ABORTED and the slot
        /// rebuilds its pipe instance.
        /// </summary>
        public static bool CancelBlockingWrite(uint threadId)
        {
            IntPtr thread = OpenThread(THREAD_TERMINATE, false, threadId);
            if (thread == IntPtr.Zero)
                return false;
            try { return CancelSynchronousIo(thread); }
            finally { CloseHandle(thread); }
        }

        public static int LastError()
        {
            return Marshal.GetLastWin32Error();
        }
    }
}
