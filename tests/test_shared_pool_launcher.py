import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "start_shared_pool_node.ps1"
)


class TestSharedPoolLauncher(unittest.TestCase):
    def test_python_checks_use_encoded_transport(self):
        with open(SCRIPT, encoding="utf-8") as handle:
            source = handle.read()

        helper = re.search(
            r"(?ms)^function Invoke-PythonCode\([^\n]*\) \{\n.*?^\}", source
        )
        self.assertIsNotNone(helper)
        self.assertIn("base64.b64decode('$encodedCode')", source)
        without_helper = source[: helper.start()] + source[helper.end() :]
        self.assertIsNone(re.search(r"&\s+\$[A-Za-z_][A-Za-z0-9_]*\s+-c\s", without_helper))
        self.assertGreaterEqual(source.count("Invoke-PythonCode -Interpreter"), 4)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell 5.1 argument transport")
    def test_python_transport_runs_under_windows_powershell_51(self):
        with open(SCRIPT, encoding="utf-8") as handle:
            source = handle.read()
        match = re.search(
            r"(?ms)^function Invoke-PythonCode\([^\n]*\) \{\n.*?^\}", source
        )
        self.assertIsNotNone(
            match, "The launcher must keep its tested Python argument transport helper."
        )

        powershell = shutil.which("powershell.exe")
        self.assertTrue(
            powershell, "Windows PowerShell 5.1 must be available for this regression."
        )

        interpreter = sys.executable.replace("'", "''")
        smoke_script = "\n".join(
            [
                match.group(0),
                f"$python = '{interpreter}'",
                "$result = Invoke-PythonCode -Interpreter $python -Code 'print(\"transport-ok\")'",
                "if ($LASTEXITCODE -ne 0 -or $result.Trim() -ne 'transport-ok') { exit 1 }",
                "$null = Invoke-PythonCode -Interpreter $python -Code 'import sys; sys.exit(23)'",
                "if ($LASTEXITCODE -ne 23) { exit 2 }",
                "Write-Output 'windows-powershell-python-transport=ok'",
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            smoke_path = os.path.join(temp_dir, "python-transport-smoke.ps1")
            with open(smoke_path, "w", encoding="utf-8-sig") as handle:
                handle.write(smoke_script)
            completed = subprocess.run(
                [powershell, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", smoke_path],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertIn("windows-powershell-python-transport=ok", completed.stdout)
