param(
    [Parameter(Mandatory = $true)]
    [string]$ImagePath,
    [string]$Lang = "auto",
    [int]$TimeoutSec = 6
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::UTF8
$effectiveTimeout = [Math]::Max(2, [Math]::Min(20, [int]$TimeoutSec))

function Emit-Json([hashtable]$payload) {
    $payload | ConvertTo-Json -Compress -Depth 4
}

function Await-Async([object]$op, [string]$stepName, [int]$timeoutSec, [type]$resultType) {
    if ($null -eq $script:AsTaskMethodDef) {
        $script:AsTaskMethodDef = [System.WindowsRuntimeSystemExtensions].GetMethods() `
            | Where-Object {
                $_.Name -eq "AsTask" `
                -and $_.IsGenericMethodDefinition `
                -and $_.GetParameters().Count -eq 1 `
                -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
            } `
            | Select-Object -First 1
    }
    if ($null -eq $script:AsTaskMethodDef) {
        throw "$stepName-astask-method-missing"
    }
    $task = $null
    try {
        $task = $script:AsTaskMethodDef.MakeGenericMethod($resultType).Invoke($null, @($op))
    } catch {
        $task = $null
    }
    if ($null -eq $task) {
        throw "$stepName-astask-unavailable"
    }
    if (-not $task.Wait([TimeSpan]::FromSeconds($timeoutSec))) {
        throw "$stepName-timeout(${timeoutSec}s)"
    }
    return $task.GetAwaiter().GetResult()
}

try {
    if (-not (Test-Path -LiteralPath $ImagePath)) {
        Emit-Json @{ ok = $false; error = "image-not-found" }
        exit 0
    }

    Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null

    $null = [Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType = WindowsRuntime]
    $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
    $null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
    $null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]

    $file = Await-Async ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) "get-file" $effectiveTimeout ([Windows.Storage.StorageFile])
    $stream = Await-Async ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) "open-stream" $effectiveTimeout ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await-Async ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) "create-decoder" $effectiveTimeout ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await-Async ($decoder.GetSoftwareBitmapAsync()) "decode-bitmap" $effectiveTimeout ([Windows.Graphics.Imaging.SoftwareBitmap])

    if ([string]::IsNullOrWhiteSpace($Lang) -or $Lang -eq "auto") {
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    } else {
        $language = [Windows.Globalization.Language]::new($Lang)
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
    }

    if ($null -eq $engine) {
        Emit-Json @{ ok = $false; error = "Language package not installed" }
        exit 0
    }

    $result = Await-Async ($engine.RecognizeAsync($bitmap)) "recognize" $effectiveTimeout ([Windows.Media.Ocr.OcrResult])
    $text = [string]$result.Text
    Emit-Json @{ ok = $true; text = ($text.Trim()) }
}
catch {
    $msg = [string]$_.Exception.Message
    if ([string]::IsNullOrWhiteSpace($msg)) {
        $msg = "windows-ocr-failed"
    }
    Emit-Json @{ ok = $false; error = $msg }
}
