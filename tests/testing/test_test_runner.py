import asyncio
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

from meow.infrastructure.test_runner import TesterSetupError, _argv, prepared_test_stage
from meow.project.config import DevServerCommand, TestCommand


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class TestRunnerTests(unittest.IsolatedAsyncioTestCase):  # ruff: ignore[too-many-public-methods]
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "apps" / "web").mkdir(parents=True)
        (self.root / "services" / "api").mkdir(parents=True)

    def test_quoted_executable_path_is_parsed(self):
        if os.name != "nt":
            self.skipTest("Windows command quoting only")
        argv = _argv(f'"{sys.executable}" -c "pass"', ())
        self.assertEqual(Path(argv[0]), Path(sys.executable))
        self.assertEqual(argv[1:], ["-c", "pass"])

    async def test_test_commands_run_in_component_directories_with_env(self):
        marker = self.root / "runs.txt"
        script = self.root / "record.py"
        script.write_text(
            "import os\n"
            + (
                f"open(r'{marker}', 'a').write("
                "os.getcwd()+'|'+os.getenv('PART','')+'\\n')\n"
            ),
            encoding="utf-8",
        )
        config = {
            "tester": {
                "tests": [
                    TestCommand(
                        Path("apps/web"),
                        f"{sys.executable} {script}",
                        env={"PART": "web"},
                    ),
                    TestCommand(
                        Path("services/api"),
                        f"{sys.executable} {script}",
                        env={"PART": "api"},
                    ),
                ],
                "dev_server": [],
                "base_url": None,
            }
        }
        async with prepared_test_stage(self.root, config) as evidence:
            self.assertEqual(len(evidence.commands), 2)
            self.assertFalse(evidence.blocking_failed)
        self.assertEqual(len(marker.read_text(encoding="utf-8").splitlines()), 2)
        self.assertNotIn("web", repr(evidence.commands[0].output))

    async def test_empty_tests_and_non_gate_failure_are_valid_and_advisory(self):
        empty = {"tester": {"tests": [], "dev_server": [], "base_url": None}}
        async with prepared_test_stage(self.root, empty) as evidence:
            self.assertFalse(evidence.blocking_failed)
            self.assertEqual(evidence.commands, ())
        script = self.root / "fail.py"
        script.write_text("print('visible')\nraise SystemExit(2)\n", encoding="utf-8")
        config = {
            "tester": {
                "tests": [
                    TestCommand(Path("."), f"{sys.executable} {script}", gate=False)
                ],
                "dev_server": [],
                "base_url": None,
            }
        }
        async with prepared_test_stage(self.root, config) as evidence:
            self.assertFalse(evidence.blocking_failed)
            self.assertEqual(evidence.commands[0].exit_code, 2)
            self.assertIn("visible", evidence.commands[0].output)

    async def test_gate_failure_and_timeout_are_blocking(self):
        fail = self.root / "fail.py"
        fail.write_text("raise SystemExit(3)\n", encoding="utf-8")
        config = {
            "tester": {
                "tests": [TestCommand(Path("."), f"{sys.executable} {fail}")],
                "dev_server": [],
                "base_url": None,
            }
        }
        async with prepared_test_stage(self.root, config) as evidence:
            self.assertTrue(evidence.blocking_failed)
        hang = self.root / "hang.py"
        hang.write_text("import time\ntime.sleep(20)\n", encoding="utf-8")
        config["tester"]["tests"] = [
            TestCommand(Path("."), f"{sys.executable} {hang}", timeout=0.1)
        ]
        async with prepared_test_stage(self.root, config) as evidence:
            self.assertTrue(evidence.commands[0].timed_out)
            self.assertTrue(evidence.blocking_failed)

    async def test_output_decodes_invalid_utf8_and_is_bounded(self):
        script = self.root / "output.py"
        script.write_text(
            "import sys\nsys.stdout.buffer.write(b'\\xff' + b'x' * 12000)\n",
            encoding="utf-8",
        )
        config = {
            "tester": {
                "tests": [TestCommand(Path("."), f"{sys.executable} {script}")],
                "dev_server": [],
                "base_url": None,
            }
        }
        async with prepared_test_stage(self.root, config) as evidence:
            output = evidence.commands[0].output
            self.assertIn("�", output)
            self.assertIn("output truncated", output)
            self.assertLess(len(output), 8200)

    async def test_server_startup_failure_and_port_collision_are_setup_errors(  # ruff: ignore[too-many-statements]
        self,
    ):
        port = _port()
        ready = f"http://127.0.0.1:{port}/"
        collision = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "http.server",
            str(port),
            "--bind",
            "127.0.0.1",
            cwd=self.root,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            for _ in range(30):
                if await asyncio.to_thread(_reachable, ready):
                    break
                await asyncio.sleep(0.05)
            server = DevServerCommand(
                Path("."), "unused", ready_url=ready, startup_timeout=1
            )
            config = {"tester": {"tests": [], "dev_server": [server], "base_url": None}}
            with self.assertRaisesRegex(TesterSetupError, "already serving"):
                async with prepared_test_stage(self.root, config):
                    self.fail("collision should fail before entering the stage")
        finally:
            collision.kill()
            await collision.wait()

        failed_script = self.root / "server-fail.py"
        failed_script.write_text("raise SystemExit(4)\n", encoding="utf-8")
        failed = DevServerCommand(
            Path("."),
            f"{sys.executable} {failed_script}",
            ready_url=f"http://127.0.0.1:{_port()}/",
            startup_timeout=2,
        )
        config["tester"]["dev_server"] = [failed]
        with self.assertRaisesRegex(TesterSetupError, "exited with code 4"):
            async with prepared_test_stage(self.root, config):
                self.fail("failed startup should not enter the stage")

    async def test_test_setup_failure_cleans_already_started_server(self):
        port = _port()
        ready = f"http://127.0.0.1:{port}/"
        server = DevServerCommand(
            Path("."),
            f"{sys.executable} -m http.server {port} --bind 127.0.0.1",
            ready_url=ready,
            startup_timeout=5,
        )
        config = {
            "tester": {
                "tests": [TestCommand(Path("."), "missing-test-program-zz")],
                "dev_server": [server],
                "base_url": None,
            }
        }
        with self.assertRaises(TesterSetupError):
            async with prepared_test_stage(self.root, config):
                self.fail("missing test executable should be a setup error")
        self.assertFalse(await asyncio.to_thread(_reachable, ready))

    @unittest.skipIf(os.name == "nt", "POSIX process groups")
    async def test_posix_server_wrapper_exit_still_stops_child_process(self):
        port = _port()
        ready = f"http://127.0.0.1:{port}/"
        child = self.root / "server-child.py"
        child.write_text(
            "import subprocess, sys\n"
            f"subprocess.Popen([sys.executable, '-m', 'http.server', '{port}', "
            "'--bind', '127.0.0.1'])\n",
            encoding="utf-8",
        )
        server = DevServerCommand(
            Path("."),
            f"{sys.executable} {child}",
            ready_url=ready,
            startup_timeout=2,
        )
        config = {"tester": {"tests": [], "dev_server": [server], "base_url": None}}
        with self.assertRaises(TesterSetupError):
            async with prepared_test_stage(self.root, config):
                pass
        self.assertFalse(await asyncio.to_thread(_reachable, ready))

    async def test_missing_executable_is_setup_error_without_env_values(self):
        secret = "do-not-print-this"
        config = {
            "tester": {
                "tests": [
                    TestCommand(
                        Path("."), "missing-test-program-zz", env={"TOKEN": secret}
                    )
                ],
                "dev_server": [],
                "base_url": None,
            }
        }
        with self.assertRaises(TesterSetupError) as caught:
            async with prepared_test_stage(self.root, config):
                self.fail("setup should not complete")
        self.assertIn("missing-test-program-zz", str(caught.exception))
        self.assertNotIn(secret, str(caught.exception))

    async def test_server_lives_inside_stage_and_is_stopped_after_exit(self):
        port = _port()
        ready = f"http://127.0.0.1:{port}/"
        server = DevServerCommand(
            Path("."),
            f"{sys.executable} -m http.server {port} --bind 127.0.0.1",
            ready_url=ready,
            startup_timeout=5,
        )
        config = {"tester": {"tests": [], "dev_server": [server], "base_url": None}}
        async with prepared_test_stage(self.root, config) as evidence:
            self.assertEqual(evidence.server_urls, (ready,))
            self.assertTrue(await asyncio.to_thread(_reachable, ready))
        self.assertFalse(await asyncio.to_thread(_reachable, ready))

    @unittest.skipUnless(os.name == "nt", "Windows process-tree ownership")
    async def test_server_wrapper_exit_still_stops_child_process(self):
        port = _port()
        ready = f"http://127.0.0.1:{port}/"
        child = (
            "import subprocess,sys; "
            f"subprocess.Popen([sys.executable,'-m','http.server','{port}',"
            "'--bind','127.0.0.1'])"
        )
        server = DevServerCommand(
            Path("."),
            f'{sys.executable} -c "{child}"',
            ready_url=ready,
            startup_timeout=5,
        )
        config = {"tester": {"tests": [], "dev_server": [server], "base_url": None}}
        try:
            async with prepared_test_stage(self.root, config):
                self.assertTrue(await asyncio.to_thread(_reachable, ready))
        except TesterSetupError:
            pass  # The wrapper exited; cleanup still owns its child server.
        self.assertFalse(await asyncio.to_thread(_reachable, ready))

    async def test_cancellation_cleans_owned_server(self):
        port = _port()
        ready = f"http://127.0.0.1:{port}/"
        server = DevServerCommand(
            Path("."),
            f"{sys.executable} -m http.server {port} --bind 127.0.0.1",
            ready_url=ready,
            startup_timeout=5,
        )
        config = {"tester": {"tests": [], "dev_server": [server], "base_url": None}}
        with self.assertRaises(asyncio.CancelledError):
            async with prepared_test_stage(self.root, config):
                self.assertTrue(await asyncio.to_thread(_reachable, ready))
                raise asyncio.CancelledError
        self.assertFalse(await asyncio.to_thread(_reachable, ready))


def _reachable(url: str) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=0.2):
            return True
    except Exception:
        return False


if __name__ == "__main__":
    unittest.main()
