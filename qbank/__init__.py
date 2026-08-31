"""Engine A: build a trivia question bank from structured Wikidata facts.

No model is asked to recall a fact. Facts come from SPARQL, phrasing comes
from templates, and distractors come from sibling rows of the same query.
"""

__version__ = "0.1.0"
