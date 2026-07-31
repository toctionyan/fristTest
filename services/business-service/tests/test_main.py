from __future__ import annotations

from pathlib import Path


def test_main_is_thin_composition_facade_and_preserves_public_exports() -> None:
    from business_service import main

    path = Path(main.__file__)
    source = path.read_text(encoding="utf-8")
    assert len(source.splitlines()) <= 80
    assert "from .api import create_app" in source
    assert "from .application.service import BusinessService" in source
    assert "@app." not in source
    assert "class BusinessService" not in source
    assert main.app is not None
    assert main.BusinessService.__module__ == "business_service.application.service"
