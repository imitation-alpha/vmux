"""The installed project metadata is vmux's single version source."""

from importlib.metadata import version

import pytest

import vmux
from vmux import __main__ as cli
from vmux.config import Config
from vmux.server import create_app


def test_runtime_and_package_metadata_versions_agree():
    assert vmux.__version__ == version("vmux-agent")


def test_cli_version_agrees_with_package_metadata(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--version"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == "vmux " + version("vmux-agent")


def test_fastapi_version_agrees_with_package_metadata():
    app = create_app(Config())

    assert app.version == version("vmux-agent")
