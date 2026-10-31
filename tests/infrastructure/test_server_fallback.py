import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from meow.infrastructure import test_runner as runner
from meow.project.config_models import DevServerCommand


def _server():
    return DevServerCommand(
        cwd=Path("."),
        command="serve",
        args=("--port", "{port}"),
        ready_url="http://127.0.0.1:{port}/",
    )


def _taken(reason="port taken"):
    return runner.VerificationSetupError("serve", Path("."), reason)


async def _run(start, location=("127.0.0.1", 3000), server=None):
    with (
        patch.object(runner, "_start_server", start),
        patch.object(runner, "_url_port", return_value=location),
        patch.object(runner, "_free_port", return_value=4567),
    ):
        return await runner._start_server_with_fallback(Path("."), server or _server())


class ServerFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_port_race_retries_on_a_fresh_port(self):
        start = AsyncMock(side_effect=[_taken(), ("proc", "url")])
        result = await _run(start)
        self.assertEqual(result, ("proc", "url"))
        self.assertEqual(start.await_count, 2)
        self.assertIn("4567", start.await_args.args[1].ready_url)

    async def test_gives_up_after_three_attempts(self):
        start = AsyncMock(side_effect=_taken())
        with self.assertRaises(runner.VerificationSetupError):
            await _run(start)
        self.assertEqual(start.await_count, 3)

    async def test_non_loopback_url_is_not_retried(self):
        start = AsyncMock(side_effect=_taken())
        with self.assertRaises(runner.VerificationSetupError):
            await _run(
                start,
                location=None,
                server=DevServerCommand(
                    cwd=Path("."),
                    command="serve",
                    ready_url="http://example.test:3000/",
                ),
            )
        self.assertEqual(start.await_count, 1)


class TakenFixedPortTests(unittest.IsolatedAsyncioTestCase):
    async def test_taken_vite_port_is_relocated_with_the_framework_flag(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps({"scripts": {"dev": "vite"}}), encoding="utf-8"
            )
            (root / "bun.lock").write_text("", encoding="utf-8")
            server = DevServerCommand(
                cwd=Path("."),
                command="bun run dev",
                ready_url="http://127.0.0.1:5173/",
                startup_timeout=1,
            )
            seen = {}

            def fake_spawn(*argv, **_):
                seen["argv"] = argv
                raise OSError("stop after launch")

            with (
                patch.object(runner, "_port_unavailable", return_value="in use"),
                patch.object(runner, "_free_port", return_value=5999),
                patch.object(runner.asyncio, "create_subprocess_exec", fake_spawn),
                self.assertRaises(runner.VerificationSetupError),
            ):
                await runner._start_server(root, server)
        self.assertEqual(list(seen["argv"][-2:]), ["--port", "5999"])
