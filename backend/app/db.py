import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

_driver = None


def get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            os.environ["NEO4J_URI"],
            auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
        )
    return _driver


def run(query: str, **params):
    with get_driver().session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as s:
        return [r.data() for r in s.run(query, **params)]
