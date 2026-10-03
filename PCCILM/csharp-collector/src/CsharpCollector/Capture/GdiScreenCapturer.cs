using System.ComponentModel;
using System.Runtime.InteropServices;
using CsharpCollector.Native;

namespace CsharpCollector.Capture;

/// <summary>
/// Primary-monitor capture via GDI BitBlt into a cached DIB, copied into a reused managed buffer.
/// Not thread-safe: use it from one thread at a time. See Native/NativeMethods.cs for the
/// permission and antivirus notes.
/// </summary>
/// <remarks>
/// A GDI StretchBlt straight to 64x36 was measured to be no cheaper than a full BitBlt: both are
/// dominated by DWM's GPU-to-CPU readback. So the probe grabs the full frame once, downscales it
/// in managed code, and a trigger keeps that same frame instead of capturing a second time.
/// </remarks>
internal sealed class GdiScreenCapturer : IScreenCapturer, IDisposable
{
    private DibSurface? _surface;
    private byte[] _pixels = [];

    public static (int Width, int Height) PrimaryScreenSize() =>
        (NativeMethods.GetSystemMetrics(NativeMethods.SM_CXSCREEN), NativeMethods.GetSystemMetrics(NativeMethods.SM_CYSCREEN));

    public Frame? Capture()
    {
        var (width, height) = PrimaryScreenSize();
        var screenDc = NativeMethods.GetDC(IntPtr.Zero);
        if (screenDc == IntPtr.Zero)
            return null;

        try
        {
            if (_surface is null || _surface.Width != width || _surface.Height != height)
            {
                _surface?.Dispose();
                _surface = DibSurface.Create(screenDc, width, height);
                _pixels = new byte[width * height * 4];
            }

            if (!NativeMethods.BitBlt(_surface.Dc, 0, 0, width, height, screenDc, 0, 0, NativeMethods.SRCCOPY))
                return null;

            NativeMethods.GdiFlush();
            Marshal.Copy(_surface.Bits, _pixels, 0, _pixels.Length);
            return new Frame(width, height, _pixels, DateTimeOffset.UtcNow);
        }
        finally
        {
            NativeMethods.ReleaseDC(IntPtr.Zero, screenDc);
        }
    }

    public void Dispose()
    {
        _surface?.Dispose();
        _surface = null;
    }

    private sealed class DibSurface : IDisposable
    {
        private readonly IntPtr _bitmap;
        private readonly IntPtr _previousObject;

        private DibSurface(IntPtr dc, IntPtr bitmap, IntPtr bits, IntPtr previousObject, int width, int height)
        {
            Dc = dc;
            _bitmap = bitmap;
            Bits = bits;
            _previousObject = previousObject;
            Width = width;
            Height = height;
        }

        public IntPtr Dc { get; }
        public IntPtr Bits { get; }
        public int Width { get; }
        public int Height { get; }

        public static DibSurface Create(IntPtr screenDc, int width, int height)
        {
            var dc = NativeMethods.CreateCompatibleDC(screenDc);
            if (dc == IntPtr.Zero)
                throw new Win32Exception(Marshal.GetLastWin32Error(), "CreateCompatibleDC failed.");

            var header = new NativeMethods.BITMAPINFOHEADER
            {
                biSize = (uint)Marshal.SizeOf<NativeMethods.BITMAPINFOHEADER>(),
                biWidth = width,
                biHeight = -height, // a negative height gives top-down rows, matching Frame
                biPlanes = 1,
                biBitCount = 32,
                biCompression = NativeMethods.BI_RGB,
            };
            var bitmap = NativeMethods.CreateDIBSection(screenDc, ref header, NativeMethods.DIB_RGB_COLORS, out var bits, IntPtr.Zero, 0);
            if (bitmap == IntPtr.Zero)
            {
                var error = Marshal.GetLastWin32Error();
                NativeMethods.DeleteDC(dc);
                throw new Win32Exception(error, "CreateDIBSection failed.");
            }

            var previous = NativeMethods.SelectObject(dc, bitmap);
            return new DibSurface(dc, bitmap, bits, previous, width, height);
        }

        public void Dispose()
        {
            NativeMethods.SelectObject(Dc, _previousObject);
            NativeMethods.DeleteObject(_bitmap);
            NativeMethods.DeleteDC(Dc);
        }
    }
}
