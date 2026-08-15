"""PEP 517 wrapper that generates IntentRail's embedded installation bundle."""

import sys
from pathlib import Path

from setuptools import build_meta as _setuptools


ROOT = Path(__file__).resolve().parent


def _prepare_runtime_bundle():
    tools = ROOT / "tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    from build_distributions import write_runtime_bundle

    write_runtime_bundle()


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    _prepare_runtime_bundle()
    return _setuptools.build_wheel(wheel_directory, config_settings, metadata_directory)


def build_sdist(sdist_directory, config_settings=None):
    _prepare_runtime_bundle()
    return _setuptools.build_sdist(sdist_directory, config_settings)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    _prepare_runtime_bundle()
    return _setuptools.build_editable(wheel_directory, config_settings, metadata_directory)


get_requires_for_build_wheel = _setuptools.get_requires_for_build_wheel
get_requires_for_build_sdist = _setuptools.get_requires_for_build_sdist
get_requires_for_build_editable = _setuptools.get_requires_for_build_editable
prepare_metadata_for_build_wheel = _setuptools.prepare_metadata_for_build_wheel
prepare_metadata_for_build_editable = _setuptools.prepare_metadata_for_build_editable
