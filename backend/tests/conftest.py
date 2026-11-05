from pathlib import Path
import shutil
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))


def pytest_addoption(parser):
    parser.addoption("--without-rg", action="store_true",
                     help="Simulate missing ripgrep while testing literal-search fallback")


def pytest_configure(config):
    if config.getoption("--without-rg"):
        patch = pytest.MonkeyPatch()
        original_which = shutil.which

        def without_rg(command, *args, **kwargs):
            if str(command).replace("\\", "/").rsplit("/", 1)[-1].lower() in {"rg", "rg.exe"}:
                return None
            return original_which(command, *args, **kwargs)

        patch.setattr(shutil, "which", without_rg)
        patch.setenv("RESEARCH_RG_BINARY", "")
        config.add_cleanup(patch.undo)


def pytest_collection_modifyitems(config, items):
    if shutil.which("rg"):
        return
    skip = pytest.mark.skip(reason="requires ripgrep on PATH; install rg to run this integration test")
    for item in items:
        if item.get_closest_marker("requires_rg"):
            item.add_marker(skip)
