from pathlib import Path
from unittest.mock import Mock, patch

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
        patch("archon.commands.loop.services.subprocess.Popen", return_value=process),
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

    assert url == "http://localhost:8123"
    assert dashboard.url == url
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
