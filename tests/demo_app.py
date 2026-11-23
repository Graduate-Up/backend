from pathlib import Path

from app.api.requirements import get_rule_provider
from app.main import create_app
from app.services.graduation_engine.rule_loader import load_rules


def demo_rules():
    return load_rules(Path(__file__).parent / "fixtures", dataset="test")


def create_demo_app():
    app = create_app()
    app.dependency_overrides[get_rule_provider] = lambda: demo_rules
    return app
