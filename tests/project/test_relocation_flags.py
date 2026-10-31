import json

from meow.project.command_detection import relocation_flags


def web(tmp_path, scripts, deps=None, lock=None):
    package = {"scripts": scripts, "devDependencies": deps or {}}
    (tmp_path / "package.json").write_text(json.dumps(package), encoding="utf-8")
    if lock:
        (tmp_path / lock).write_text("", encoding="utf-8")
    return tmp_path


def test_a_vite_script_gets_the_port_flag(tmp_path):
    web(tmp_path, {"dev": "vite"}, lock="bun.lock")
    assert relocation_flags(tmp_path, "bun run dev", ()) == ("--port", "{port}")


def test_npm_needs_the_argument_separator(tmp_path):
    web(tmp_path, {"dev": "vite --host"}, lock="package-lock.json")
    assert relocation_flags(tmp_path, "npm run dev", ()) == ("--", "--port", "{port}")


def test_next_uses_its_short_flag_and_a_direct_command_is_recognized(tmp_path):
    web(tmp_path, {"dev": "next dev"}, lock="pnpm-lock.yaml")
    assert relocation_flags(tmp_path, "pnpm dev", ()) == ("-p", "{port}")
    assert relocation_flags(tmp_path, "npx vite", ()) == ("--port", "{port}")


def test_framework_from_dependencies_when_the_script_hides_it(tmp_path):
    web(tmp_path, {"dev": "node dev.js"}, deps={"vite": "^5"}, lock="bun.lock")
    assert relocation_flags(tmp_path, "bun run dev", ()) == ("--port", "{port}")


def test_unknown_servers_and_existing_flags_get_nothing(tmp_path):
    web(tmp_path, {"dev": "node server.js"}, lock="bun.lock")
    assert relocation_flags(tmp_path, "bun run dev", ()) == ()
    assert relocation_flags(tmp_path, "python -m http.server", ()) == ()
    web(tmp_path, {"dev": "vite"})
    assert relocation_flags(tmp_path, "vite --port 3000", ()) == ()
