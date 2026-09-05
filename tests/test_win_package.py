"""验证打包依赖准备，不下载软件或执行实际安装。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "win32", "Windows PowerShell packaging")
class WindowsPackageTests(unittest.TestCase):
    def run_language_setup(self, scenario: str) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = {
                "script": str(PROJECT_ROOT / "tools" / "package_win.ps1"),
                "scenario": scenario,
            }
            (root / "fixture.json").write_text(json.dumps(fixture), encoding="utf-8")
            harness = root / "harness.ps1"
            harness.write_text(
                r"""
$ErrorActionPreference = "Stop"
$fixture = Get-Content -LiteralPath (Join-Path $PSScriptRoot "fixture.json") -Raw | ConvertFrom-Json
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $fixture.script, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
$function = $ast.Find({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq "Install-InnoChineseLanguage"
}, $true)
. ([scriptblock]::Create($function.Extent.Text))
$script:downloadCount = 0
$languagePath = Join-Path $PSScriptRoot "Languages\ChineseSimplified.isl"
New-Item -ItemType Directory -Path (Split-Path -Parent $languagePath) | Out-Null
if ($fixture.scenario -ne "missing") {
    Set-Content -LiteralPath $languagePath -Value "existing"
}
function Invoke-WebRequest {
    param($Uri, $OutFile, [switch]$UseBasicParsing)
    $script:downloadCount += 1
    Set-Content -LiteralPath $OutFile -Value "downloaded"
}
function Get-FileHash {
    param($LiteralPath, $Algorithm)
    if ($fixture.scenario -eq "corrupt") { return @{ Hash = "invalid" } }
    return @{ Hash = "E0B0B350E2245F3C5E65586DFE43D574F6E7F06F2261149ABA284954B3FC9A8D" }
}
$failed = $false
try { Install-InnoChineseLanguage -CompilerDirectory $PSScriptRoot }
catch { $failed = $true }
@{
    failed = $failed
    downloads = $script:downloadCount
    content = (Get-Content -LiteralPath $languagePath -Raw).Trim()
    temporary_exists = (Test-Path -LiteralPath "$languagePath.download")
} | ConvertTo-Json -Compress
""",
                encoding="utf-8-sig",
            )
            result = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(harness),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout.strip())

    def test_missing_translation_is_installed(self) -> None:
        result = self.run_language_setup("missing")
        self.assertEqual(result, {
            "failed": False, "downloads": 1, "content": "downloaded",
            "temporary_exists": False,
        })

    def test_verified_cached_translation_needs_no_network(self) -> None:
        result = self.run_language_setup("cached")
        self.assertEqual(result["downloads"], 0)
        self.assertFalse(result["failed"])
        self.assertEqual(result["content"], "existing")

    def test_corrupt_download_cannot_replace_existing_translation(self) -> None:
        result = self.run_language_setup("corrupt")
        self.assertTrue(result["failed"])
        self.assertEqual(result["downloads"], 1)
        self.assertEqual(result["content"], "existing")
        self.assertFalse(result["temporary_exists"])


if __name__ == "__main__":
    unittest.main()
