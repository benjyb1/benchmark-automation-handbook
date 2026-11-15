# The words in a picture, read by Windows itself (Windows.Media.Ocr: built in, offline, free).
#   powershell -NoProfile -ExecutionPolicy Bypass -File windows_ocr.ps1 PICTURE
# Prints one line of JSON in the shape vision_ocr.js gives on a Mac, so ocr.py joins the pieces the
# same way: {"passes": [[{"t", "l", "r", "top", "bot"}, ...]]} with pixel boxes, top-left origin,
# or {"error": "..."}. A big picture is cut into overlapping pieces, each no larger than the reader
# takes, and every line found is moved back to where it sits in the whole picture.
# PowerShell names ignore case ($w is $W), so every name here is distinct.
# Windows PowerShell 5.1 (powershell.exe), which every Windows 10 and 11 has: PowerShell 7 cannot
# load the Windows reader. Keep this file ASCII: 5.1 reads a file without a BOM in the local code page.
param([string]$Path)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false

function Say($obj) { $obj | ConvertTo-Json -Depth 6 -Compress | Write-Output }

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
    $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
    function Await($op, [Type]$type) {
        $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
        $null = $task.Wait(-1)
        $task.Result
    }

    $full = (Resolve-Path -LiteralPath $Path).ProviderPath
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($full)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if ($null -eq $engine) { Say @{ error = 'no-language' }; exit 0 }

    $imgW = [int]$decoder.PixelWidth
    $imgH = [int]$decoder.PixelHeight
    $tile = [Math]::Min([int][Windows.Media.Ocr.OcrEngine]::MaxImageDimension, 2400)
    $overlap = 200
    $pieces = New-Object System.Collections.ArrayList
    $y = 0
    while ($true) {
        $tileH = [Math]::Min($tile, $imgH - $y)
        $x = 0
        while ($true) {
            $tileW = [Math]::Min($tile, $imgW - $x)
            $bounds = New-Object Windows.Graphics.Imaging.BitmapBounds
            $bounds.X = [uint32]$x; $bounds.Y = [uint32]$y
            $bounds.Width = [uint32]$tileW; $bounds.Height = [uint32]$tileH
            $transform = New-Object Windows.Graphics.Imaging.BitmapTransform
            $transform.Bounds = $bounds
            $bitmap = Await ($decoder.GetSoftwareBitmapAsync(
                [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
                [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied, $transform,
                [Windows.Graphics.Imaging.ExifOrientationMode]::IgnoreExifOrientation,
                [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage)) ([Windows.Graphics.Imaging.SoftwareBitmap])
            $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
            foreach ($line in $result.Lines) {
                $l = [double]::MaxValue; $t = [double]::MaxValue; $r = 0.0; $b = 0.0
                foreach ($word in $line.Words) {
                    $box = $word.BoundingRect
                    $l = [Math]::Min($l, $box.X); $t = [Math]::Min($t, $box.Y)
                    $r = [Math]::Max($r, $box.X + $box.Width); $b = [Math]::Max($b, $box.Y + $box.Height)
                }
                if ($line.Words.Count -gt 0) {
                    $null = $pieces.Add([ordered]@{ t = $line.Text; l = $l + $x; r = $r + $x; top = $t + $y; bot = $b + $y })
                }
            }
            if ($x + $tileW -ge $imgW) { break }
            $x += $tile - $overlap
        }
        if ($y + $tileH -ge $imgH) { break }
        $y += $tile - $overlap
    }
    Say @{ passes = @(, $pieces.ToArray()); width = $imgW; height = $imgH }
} catch {
    Say @{ error = $_.Exception.Message }
}
