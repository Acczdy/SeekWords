# Windows GUI launcher. Requires Python 3.10+ with Tcl/Tk.
# Example: powershell -NoProfile -ExecutionPolicy Bypass -File .\seekwords.ps1 -Path 'D:\Data'
param([string[]]$Path = @(), [switch]$Administrator)
$ErrorActionPreference = 'Stop'
$executable = Join-Path $PSScriptRoot 'SeekWords.exe'
if (Test-Path -LiteralPath $executable) {
    $launchArgs = @{ FilePath = $executable; WorkingDirectory = $PSScriptRoot }
    if ($Path.Count) {
        $launchArgs.ArgumentList = ($Path | ForEach-Object { '"' + ($_ -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"' }) -join ' '
    }
    if ($Administrator) { $launchArgs.Verb = 'RunAs' }
    Start-Process @launchArgs
    exit 0
}
if ($Administrator) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        # Encode literal arguments so paths containing spaces, apostrophes or & stay data.
        $scriptLiteral = "'" + $PSCommandPath.Replace("'", "''") + "'"
        $expression = '& ' + $scriptLiteral
        if ($Path.Count) {
            $literals = @($Path | ForEach-Object { "'" + $_.Replace("'", "''") + "'" })
            $expression += ' -Path @(' + ($literals -join ',') + ')'
        }
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($expression))
        try {
            Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -Verb RunAs -WindowStyle Hidden -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded)
            exit 0
        } catch {
            Write-Host 'Administrator launch was cancelled or denied.'
            exit 1
        }
    }
}
# WSL and some terminal launchers omit PATHEXT; PowerShell otherwise treats .exe as a document.
if (($env:PATHEXT -split ';') -notcontains '.EXE') {
    $env:PATHEXT = '.EXE;.COM;.BAT;.CMD;' + $env:PATHEXT
}
$entry = Join-Path $PSScriptRoot 'seekwords.py'
$candidates = @(
    @{ Command = 'py'; Prefix = @('-3') },
    @{ Command = 'python'; Prefix = @() },
    @{ Command = 'python3'; Prefix = @() }
)
if ($env:WINDIR) {
    $candidates += @{ Command = (Join-Path $env:WINDIR 'py.exe'); Prefix = @('-3') }
}
foreach ($candidate in $candidates) {
    if (Get-Command $candidate.Command -ErrorAction SilentlyContinue) {
        $command = $candidate.Command
        $prefix = $candidate.Prefix
        & $command @prefix -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' 2>$null
        if ($LASTEXITCODE -eq 0) {
            & $command @prefix $entry @Path
            exit $LASTEXITCODE
        }
    }
}
Write-Host 'Python 3.10+ with Tcl/Tk is required. Install Python from https://www.python.org/downloads/ (enable Tcl/Tk and PATH).'
exit 1
