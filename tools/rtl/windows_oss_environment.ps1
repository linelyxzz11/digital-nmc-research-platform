param([Parameter(Mandatory=$true)][string]$SuiteRoot)
$SuiteRoot = (Resolve-Path -LiteralPath $SuiteRoot).Path
. (Join-Path $SuiteRoot 'environment.ps1')
# Keep portable tool path fixes outside RTL and the Python test assertions.
$shimRoot = Join-Path $PSScriptRoot '../../dump/rtl_validation_20261031/tool-shims'
New-Item -ItemType Directory -Force -Path $shimRoot | Out-Null
$compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'Windows .NET Framework C# compiler is required for native launch adapters' }
$adapterSource=Join-Path $PSScriptRoot 'windows_tool_launcher.cs'
$sourceHash=(Get-FileHash -LiteralPath $adapterSource -Algorithm SHA256).Hash
$marker=Join-Path $shimRoot 'source.sha256'
if (-not (Test-Path $marker) -or (Get-Content $marker -Raw).Trim() -ne $sourceHash) {
    & $compiler /nologo /target:exe "/out:$(Join-Path $shimRoot 'launcher.exe')" $adapterSource
    if ($LASTEXITCODE -ne 0) { throw 'Tool adapter compilation failed' }
    foreach ($tool in @('iverilog','vvp','verilator')) {
        Copy-Item -LiteralPath (Join-Path $shimRoot 'launcher.exe') -Destination (Join-Path $shimRoot "$tool.exe") -Force -ErrorAction Stop
    }
    Set-Content -LiteralPath $marker -Value $sourceHash
}
$env:VERILATOR_ROOT = Join-Path $SuiteRoot 'share/verilator'
$env:PATH = "$( (Resolve-Path $shimRoot).Path );$env:PATH"
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
