"""Package metadata and release-install smoke contracts."""

from __future__ import annotations

import runpy
from pathlib import Path

import setuptools


ROOT = Path(__file__).resolve().parents[1]


def test_setup_declares_all_runtime_dependencies(monkeypatch) -> None:
    """A wheel install must provide every dependency imported by production."""
    captured = {}
    monkeypatch.setattr(setuptools, "setup", lambda **kwargs: captured.update(kwargs))

    runpy.run_path(str(ROOT / "setup.py"), run_name="__main__")

    requirements = set(captured["install_requires"])
    assert "ddgs>=9.0.0" in requirements
    assert "python-dotenv>=1.0.0" in requirements
    assert "lxml_html_clean>=0.4.0" in requirements
    assert "requests>=2.32.3" in requirements
    assert not any(item.startswith("duckduckgo_search") for item in requirements)
    assert captured["python_requires"] == ">=3.10"


def test_release_workflow_smoke_installs_built_wheel() -> None:
    """Release CI must import modules and exercise CLI help from its wheel."""
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    assert "pip install --force-reinstall dist/*.whl" in workflow
    assert "python -m sift --help" in workflow
    assert "import sift.cli" in workflow
    assert "import sift.crawler" in workflow
    assert "import sift.curation" in workflow
    assert "import sift.db" in workflow
    assert "import sift.feeds" in workflow
    assert "import sift.outbound" in workflow
    assert "import sift.pulse" in workflow
    assert "import sift.robots" in workflow
    assert "import sift.synthesize" in workflow
    assert "import sift.wiki" in workflow
