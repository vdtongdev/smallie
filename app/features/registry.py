"""Every feature mounted by app/api/main.py."""

from app.features.base import Feature
from app.features.documents.feature import FEATURE as DOCUMENTS
from app.features.market.feature import FEATURE as MARKET

FEATURES: tuple[Feature, ...] = (DOCUMENTS, MARKET)
