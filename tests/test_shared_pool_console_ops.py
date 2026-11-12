from pathlib import Path
import shutil
import subprocess
import unittest

from render_bridge.shared_pool_console import CONSOLE_HTML


ROOT = Path(__file__).resolve().parents[1]


class SharedPoolConsoleOpsTests(unittest.TestCase):
    def test_operator_fleet_refresh_is_single_flight_and_excludes_history(self):
        self.assertIn("const FLEET_REFRESH_MS=5000", CONSOLE_HTML)
        self.assertIn("setTimeout(", CONSOLE_HTML)
        self.assertNotIn("setInterval(", CONSOLE_HTML)
        self.assertIn("if(operatorRefresh)return operatorRefresh", CONSOLE_HTML)
        self.assertIn("document.hidden", CONSOLE_HTML)

        refresh = CONSOLE_HTML.split("async function refreshOperator()", 1)[1].split(
            "async function refreshWorkspace()", 1
        )[0]
        self.assertIn("/bridge/control/v2/summary", refresh)
        self.assertIn("/bridge/control/v2/pools", refresh)
        self.assertIn("/bridge/control/v2/nodes", refresh)
        self.assertNotIn("/bridge/control/v2/tasks", refresh)

    def test_windows_first_run_bootstraps_before_enrollment_and_uses_pool_agent(self):
        launcher = (ROOT / "scripts" / "start_shared_pool_node.ps1").read_text(encoding="utf-8")
        batch = (ROOT / "scripts" / "start_shared_pool_node.bat").read_text(encoding="utf-8")

        self.assertIn("AlchemyRenderBridge\\SharedPool", launcher)
        self.assertIn("Join-Path $runtimeRoot 'venv'", launcher)
        self.assertIn("'agent-state'", launcher)
        self.assertIn("shared-node.json", launcher)
        self.assertIn("render_bridge.agent.pool_node enroll", launcher)
        self.assertIn("render_bridge.agent.pool_node run", launcher)
        self.assertIn("--url $Url", launcher)
        self.assertIn("--profiles $profileList", launcher)
        self.assertIn("importlib.util.find_spec", launcher)
        self.assertIn("if (-not (Test-SharedPoolRuntime))", launcher)
        self.assertIn("must use Python 3.12", launcher)
        self.assertIn("ensurepip --upgrade", launcher)
        self.assertNotIn("hatchet_sidecar", launcher)
        self.assertLess(launcher.index("pip install"), launcher.index("pool_node enroll"))
        self.assertLess(launcher.index("pool_node enroll"), launcher.index("pool_node run"))
        self.assertIn("start_shared_pool_node.ps1", batch)
        self.assertNotIn("%*", batch)
        self.assertIn("-ExecutionPolicy Bypass", batch)
        self.assertNotIn("Set-ExecutionPolicy", batch)
        self.assertIn("start_shared_pool_node.ps1", CONSOLE_HTML)
        self.assertIn("powershell.exe", CONSOLE_HTML)
        self.assertIn("ExecutionPolicy Bypass", CONSOLE_HTML)
        self.assertIn("-Profiles", CONSOLE_HTML)

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed for the browser lifecycle harness")
    def test_operator_fleet_refresh_runtime_lifecycle(self):
        result = subprocess.run(
            [shutil.which("node"), str(ROOT / "tests" / "shared_pool_console_runtime.mjs")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("lifecycle checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
