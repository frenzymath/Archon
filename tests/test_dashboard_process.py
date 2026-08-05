from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import typer

from archon.commands.dashboard.server import ServerProcess
from archon.commands.loop.services import DashboardProcess


def test_dashboard_timeout_keeps_url_and_registers_cleanup(tmp_path: Path):
    process = Mock()
    process.poll.return_value = None
    process.pid = 1234
    registered = []

    with (
        patch("archon.commands.loop.services.shutil.which", return_value="/usr/bin/node"),
        patch(
            "archon.commands.loop.services.PortProbe.find_free",
            return_value=8123,
        ),
        patch(
            "archon.commands.loop.services.subprocess.Popen",
            return_value=process,
        ) as popen,
        patch("archon.commands.loop.services.atexit.register", side_effect=registered.append),
        patch(
            "archon.commands.loop.services.PortProbe.is_free",
            return_value=True,
        ),
        patch(
            "archon.commands.loop.services.time.monotonic",
            side_effect=[0.0, 9.0],
        ),
        patch("archon.commands.loop.services.time.sleep"),
        patch("archon.commands.loop.services.log.panel"),
        patch("archon.commands.loop.services.log.warn"),
    ):
        dashboard = DashboardProcess(tmp_path)
        url = dashboard.start()

    assert url == "http://127.0.0.1:8123"
    assert dashboard.url == url
    assert popen.call_args.args[0][-1] == "--strict-port"
    assert len(registered) == 1
    assert registered[0].__self__ is dashboard
    assert registered[0].__func__ is DashboardProcess._cleanup


def test_dashboard_process_exit_before_bind_returns_no_url(tmp_path: Path):
    process = Mock()
    process.poll.return_value = 1
    process.pid = 1234
    registered = []

    with (
        patch("archon.commands.loop.services.shutil.which", return_value="/usr/bin/node"),
        patch(
            "archon.commands.loop.services.PortProbe.find_free",
            return_value=8123,
        ),
        patch("archon.commands.loop.services.subprocess.Popen", return_value=process),
        patch("archon.commands.loop.services.atexit.register", side_effect=registered.append),
        patch("archon.commands.loop.services.log.warn"),
    ):
        dashboard = DashboardProcess(tmp_path)
        url = dashboard.start()

    assert url is None
    assert dashboard.url is None
    assert len(registered) == 1


def _server(tmp_path: Path) -> ServerProcess:
    registry = Mock()
    registry.pidfile_for.side_effect = lambda port: tmp_path / f"{port}.pid"
    return ServerProcess(
        server_dir=tmp_path,
        project_path=tmp_path,
        archon_dir=tmp_path / ".archon",
        registry=registry,
    )


def test_strict_port_never_falls_back(tmp_path: Path):
    server = _server(tmp_path)
    process = Mock(pid=1234)
    process.poll.return_value = 1

    with (
        patch.object(server, "spawn", return_value=process) as spawn,
        patch("archon.commands.dashboard.server.port_in_use", return_value=False),
        patch("archon.commands.dashboard.server.wait_for_http", return_value=False),
        patch("archon.commands.dashboard.server.find_free_port") as find_free,
    ):
        with pytest.raises(typer.Exit):
            server.start_with_retry(8123, strict_port=True)

    spawn.assert_called_once_with(8123)
    find_free.assert_not_called()
    assert not (tmp_path / "8123.pid").exists()


def test_strict_port_rejects_initial_bind_race(tmp_path: Path):
    server = _server(tmp_path)

    with (
        patch.object(server, "spawn") as spawn,
        patch("archon.commands.dashboard.server.port_in_use", return_value=True),
        patch("archon.commands.dashboard.server.find_free_port") as find_free,
    ):
        with pytest.raises(typer.Exit):
            server.start_with_retry(8123, strict_port=True)

    spawn.assert_not_called()
    find_free.assert_not_called()


def test_standalone_dashboard_still_falls_back(tmp_path: Path):
    server = _server(tmp_path)
    first = Mock(pid=1234)
    first.poll.return_value = 1
    second = Mock(pid=1235)

    with (
        patch.object(server, "spawn", side_effect=[first, second]) as spawn,
        patch("archon.commands.dashboard.server.port_in_use", return_value=False),
        patch(
            "archon.commands.dashboard.server.wait_for_http",
            side_effect=[False, True],
        ),
        patch("archon.commands.dashboard.server.find_free_port", return_value=8124),
    ):
        port = server.start_with_retry(8123)

    assert port == 8124
    assert [call.args[0] for call in spawn.call_args_list] == [8123, 8124]
