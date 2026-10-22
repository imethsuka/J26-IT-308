using System.IO.Compression;
using System.Runtime.InteropServices;
using System.Text;

namespace ContextDashboard.Data;

/// <summary>
/// Windows DPAPI, current-user scope: the same call the worker makes (<c>storage/raw_buffer.py</c>, <c>DpapiProtector</c>),
/// so the dashboard, running as the same Windows user, can open what the worker stored. Each store has its own entropy.
/// </summary>
internal static class Dpapi
{
    /// <summary><c>memory.db</c> pages (and the raw buffer): <c>_ENTROPY</c> in <c>storage/raw_buffer.py</c>.</summary>
    public static readonly byte[] TextEntropy = Encoding.ASCII.GetBytes("context-importance/raw-buffer/v1");

    /// <summary><c>screenshots.db</c>: <c>ENTROPY</c> in <c>storage/screenshot_store.py</c>.</summary>
    public static readonly byte[] ScreenshotEntropy = Encoding.ASCII.GetBytes("context-importance/screenshots/v1");

    public static byte[] Protect(byte[] data, byte[] entropy) => Call(data, entropy, protect: true);

    public static byte[] Unprotect(byte[] data, byte[] entropy) => Call(data, entropy, protect: false);

    /// <summary>A protected, zlib-compressed blob (how the worker stores page text) back to its bytes.</summary>
    public static byte[] UnprotectZlib(byte[] blob, byte[] entropy)
    {
        using var input = new MemoryStream(Unprotect(blob, entropy));
        using var zlib = new ZLibStream(input, CompressionMode.Decompress);
        using var output = new MemoryStream();
        zlib.CopyTo(output);
        return output.ToArray();
    }

    private static byte[] Call(byte[] data, byte[] entropy, bool protect)
    {
        var source = Pin(data);
        var extra = Pin(entropy);
        var result = new Blob();
        try
        {
            // Flags 0: current-user scope, no UI. The same arguments as the worker's ctypes call.
            var ok = protect
                ? CryptProtectData(ref source.Blob, null, ref extra.Blob, IntPtr.Zero, IntPtr.Zero, 0, ref result)
                : CryptUnprotectData(ref source.Blob, IntPtr.Zero, ref extra.Blob, IntPtr.Zero, IntPtr.Zero, 0, ref result);
            if (!ok) throw new IOException($"DPAPI {(protect ? "protect" : "unprotect")} failed (error {Marshal.GetLastWin32Error()})");
            var bytes = new byte[result.Size];
            Marshal.Copy(result.Data, bytes, 0, result.Size);
            return bytes;
        }
        finally
        {
            source.Handle.Free();
            extra.Handle.Free();
            if (result.Data != IntPtr.Zero) LocalFree(result.Data);
        }
    }

    private static (Blob Blob, GCHandle Handle) Pin(byte[] data)
    {
        var handle = GCHandle.Alloc(data, GCHandleType.Pinned);
        return (new Blob { Size = data.Length, Data = handle.AddrOfPinnedObject() }, handle);
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct Blob
    {
        public int Size;
        public IntPtr Data;
    }

    [DllImport("crypt32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool CryptProtectData(
        ref Blob dataIn, string? description, ref Blob entropy, IntPtr reserved, IntPtr prompt, int flags, ref Blob dataOut);

    [DllImport("crypt32.dll", SetLastError = true)]
    private static extern bool CryptUnprotectData(
        ref Blob dataIn, IntPtr description, ref Blob entropy, IntPtr reserved, IntPtr prompt, int flags, ref Blob dataOut);

    [DllImport("kernel32.dll")]
    private static extern IntPtr LocalFree(IntPtr handle);
}
