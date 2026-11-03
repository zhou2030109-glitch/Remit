"""用 PowerShell 进程替身验证停止器，绝不调用真实 taskkill。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "win32", "Windows PowerShell launcher")
class WindowsStopLauncherTests(unittest.TestCase):
    def run_stopper(
        self,
        *,
        identity: bool = False,
        stale: bool = False,
        listener: str = "none",
        generated: bool = False,
        owner: str = "",
        requested_owner: str = "",
    ) -> tuple[list[int], str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tools").mkdir()
            (root / "logs").mkdir()
            shutil.copy2(
                PROJECT_ROOT / "tools" / "stop_services.ps1",
                root / "tools" / "stop_services.ps1",
            )
            shutil.copy2(
                PROJECT_ROOT / "tools" / "start_services.ps1",
                root / "tools" / "start_services.ps1",
            )
            (root / "logs" / "frontend.pid").write_text("31001", encoding="ascii")
            (root / "fixture.json").write_text(
                json.dumps(
                    {
                        "identity": identity,
                        "stale": stale,
                        "listener": listener,
                        "generated": generated,
                        "owner": owner,
                        "requested_owner": requested_owner,
                    }
                ),
                encoding="utf-8",
            )
            harness = root / "harness.ps1"
            harness.write_text(
                r"""
$ErrorActionPreference = "Stop"
$fixture = Get-Content -LiteralPath (Join-Path $PSScriptRoot "fixture.json") -Raw | ConvertFrom-Json
$script:fixtureRoot = $PSScriptRoot
$script:killed = @()
$script:started = [datetime]::Parse("2026-01-01T00:00:00Z").ToUniversalTime()
if ($fixture.generated) {
    # 只加载记录函数的 AST，不能执行启动脚本顶层代码。
    $tokens = $null
    $parseErrors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $PSScriptRoot "tools\start_services.ps1"), [ref]$tokens, [ref]$parseErrors)
    $function = $ast.Find({ param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq "Save-ServicePid"
    }, $true)
    . ([scriptblock]::Create($function.Extent.Text))
    $Root = $script:fixtureRoot
    $LogDirectory = Join-Path $Root "logs"
    $script:fixtureProcess = Get-Process -Id $PID
    Save-ServicePid -Name "frontend" -Process $script:fixtureProcess
}
if ($fixture.identity) {
    $recordedStart = $script:started
    if ($fixture.stale) { $recordedStart = $recordedStart.AddSeconds(-10) }
    @{
        ProcessId = 31001
        StartedUtcTicks = $recordedStart.Ticks.ToString()
        ExecutablePath = "C:\Windows\System32\cmd.exe"
        ProjectRoot = $PSScriptRoot
        Service = "frontend"
        OwnerToken = $fixture.owner
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot "logs\frontend.pid.json")
}
function Get-Process {
    param([int]$Id)
    if ($fixture.generated) { return $script:fixtureProcess }
    return [pscustomobject]@{ Id = $Id; Path = "C:\Windows\System32\cmd.exe"; StartTime = $script:started }
}
function netstat.exe {
    if ($fixture.listener -ne "none") {
        "TCP 127.0.0.1:15173 0.0.0.0:0 LISTENING 32002"
    }
}
function Get-CimInstance {
    param($ClassName, $Filter)
    $directory = $script:fixtureRoot
    if ($fixture.listener -eq "sibling") { $directory += "-other" }
    $command = 'node "' + $directory + '\frontend\node_modules\vite\bin\vite.js" --port 15173'
    if ($fixture.listener -eq "foreign") { $command = 'node C:\unrelated\server.js' }
    return [pscustomobject]@{
        ExecutablePath = "C:\node\node.exe"
        CommandLine = $command
        ParentProcessId = 999
    }
}
function taskkill.exe {
    $script:killed += [int]$args[1]
    $global:LASTEXITCODE = 0
}
. (Join-Path $PSScriptRoot "tools\stop_services.ps1") -OwnerToken $fixture.requested_owner
Write-Output ("KILLED=" + (ConvertTo-Json -InputObject @($script:killed) -Compress))
""",
                encoding="utf-8-sig",
            )
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(harness),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=45,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            preserved = bool(requested_owner and requested_owner != owner)
            self.assertEqual((root / "logs" / "frontend.pid").exists(), preserved)
            self.assertEqual((root / "logs" / "frontend.pid.json").exists(), preserved and identity)
            line = next(
                line
                for line in result.stdout.splitlines()
                if line.startswith("KILLED=")
            )
            return json.loads(line.removeprefix("KILLED=")), result.stdout

    def test_reused_pid_is_not_terminated(self) -> None:
        killed, _ = self.run_stopper(identity=True, stale=True)
        self.assertEqual(killed, [])

    def test_desktop_leaves_preexisting_service_and_records_intact(self) -> None:
        killed, _ = self.run_stopper(identity=True, owner="browser", requested_owner="desktop")
        self.assertEqual(killed, [])

    def test_desktop_stops_its_own_service(self) -> None:
        killed, _ = self.run_stopper(identity=True, owner="desktop", requested_owner="desktop")
        self.assertEqual(killed, [31001])

    def test_desktop_does_not_claim_legacy_listener_on_exit(self) -> None:
        killed, _ = self.run_stopper(listener="owned", requested_owner="desktop")
        self.assertEqual(killed, [])

    def test_desktop_stale_record_does_not_claim_replacement_listener(self) -> None:
        killed, _ = self.run_stopper(
            identity=True, stale=True, listener="owned",
            owner="desktop", requested_owner="desktop",
        )
        self.assertEqual(killed, [])

    def test_manual_stop_can_find_listener_after_record_expires(self) -> None:
        killed, _ = self.run_stopper(identity=True, stale=True, listener="owned")
        self.assertEqual(killed, [32002])

    def test_matching_identity_terminates_recorded_process_tree(self) -> None:
        killed, _ = self.run_stopper(identity=True)
        self.assertEqual(killed, [31001])

    def test_start_writer_identity_is_accepted_by_stopper(self) -> None:
        killed, _ = self.run_stopper(generated=True)
        self.assertEqual(len(killed), 1)

    def test_unverified_pid_and_foreign_listener_are_not_terminated(self) -> None:
        killed, output = self.run_stopper(listener="foreign")
        self.assertEqual(killed, [])
        self.assertIn("leaving it running", output)

    def test_legacy_record_finds_only_the_owned_listener_not_its_parent(self) -> None:
        killed, _ = self.run_stopper(listener="owned")
        self.assertEqual(killed, [32002])

    def test_project_name_prefix_does_not_claim_sibling_install(self) -> None:
        killed, _ = self.run_stopper(listener="sibling")
        self.assertEqual(killed, [])


if __name__ == "__main__":
    unittest.main()
