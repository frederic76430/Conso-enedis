"""Fixtures communes : intégrations personnalisées et base d'enregistrement (recorder)."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """Le recorder doit démarrer avant Home Assistant : l'intégration en dépend (statistiques)."""
    yield
