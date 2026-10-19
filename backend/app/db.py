"""Neo4j connection. It fails fast: a short connection timeout, and after a connection failure the database is skipped
for a cool-down period, so a paused instance (Aura Free pauses idle databases) costs one slow call, not one per request.
DATA_SOURCE=snapshot (or real) turns Neo4j off entirely; callers then use their local fallbacks."""
import os
import time

from dotenv import load_dotenv
from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, DriverError, ServiceUnavailable

load_dotenv()

TIMEOUT = 5      # seconds to connect before giving up
COOLDOWN = 300   # seconds to skip Neo4j after a connection failure
_driver = None
_down_until = 0.0


def enabled() -> bool:
    return bool(os.getenv("NEO4J_URI")) and os.getenv("DATA_SOURCE", "") not in ("snapshot", "real")


def get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            os.environ["NEO4J_URI"],
            auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
            connection_timeout=TIMEOUT, connection_acquisition_timeout=TIMEOUT, max_transaction_retry_time=TIMEOUT,
        )
    return _driver


def run(query: str, **params):
    global _down_until
    if not enabled():
        raise ServiceUnavailable("Neo4j is turned off (DATA_SOURCE or no NEO4J_URI)")
    if time.monotonic() < _down_until:
        raise ServiceUnavailable("Neo4j was unreachable recently; skipping it during the cool-down")
    try:
        with get_driver().session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as s:
            return [r.data() for r in s.run(query, **params)]
    except (DriverError, AuthError, OSError):
        _down_until = time.monotonic() + COOLDOWN
        raise
