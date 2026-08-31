"""Fact patterns: one SPARQL query + one set of phrasings + one similarity axis.

Every query returns the same column contract so the rest of the pipeline never
has to know which pattern it is looking at:

    ?subject       the entity URI  (identity, used for dedupe)
    ?subjectLabel  what goes into the question text
    ?objectLabel   the correct answer
    ?links         wikibase:sitelinks of the subject (drives difficulty)
    ?sim*          optional similarity columns for the distractor picker

Queries must carry their own ORDER BY: paging without one is not stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Properties whose value is true today and false in a year. A question built on
# one of these rots silently inside a shipped app, so the loader refuses them.
TIME_VARYING_PROPERTIES = {
    "P6",     # head of government
    "P26",    # spouse
    "P35",    # head of state
    "P39",    # position held
    "P54",    # member of sports team
    "P108",   # employer
    "P169",   # chief executive officer
    "P488",   # chairperson
    "P1082",  # population
    "P1128",  # employees
    "P2196",  # students count
    "P2295",  # net profit
}

# One occupation per shard. Each is small enough to finish inside the WDQS
# timeout, and together they cover the people a general audience recognises.
OCCUPATION_SHARDS = tuple(
    f"wdt:P106 wd:{qid}" for qid in (
        "Q116",        # monarch
        "Q36834",      # composer
        "Q169470",     # physicist
        "Q4964182",    # philosopher
        "Q170790",     # mathematician
        "Q11631",      # astronaut
        "Q36180",      # writer
        "Q1028181",    # painter
        "Q11900058",   # explorer
        "Q593644",     # chemist
        "Q864503",     # biologist
        "Q205375",     # inventor
        "Q2526255",    # film director
    )
)

LABEL_SERVICE = 'SERVICE wikibase:label { bd:serviceParam wikibase:language "en,mul". '


@dataclass(frozen=True)
class Pattern:
    id: str
    category: str
    prop: str                        # the Wikidata property, for cross-pattern dedupe
    query: str
    phrasings: tuple[str, ...]       # each contains {subject}
    explanation: str                 # contains {subject}, {answer}
    sim_fields: tuple[str, ...] = ()
    numeric_object: bool = False     # rank distractors by numeric distance
    min_sitelinks: int = 25
    page_size: int = 5000
    max_rows: int | None = None
    # Appending LIMIT/OFFSET changes the WDQS query plan and can turn a query
    # that finishes in 40s into one that times out. Patterns already bounded by
    # a sitelink subquery are fetched in a single unpaged request instead.
    paged: bool = True
    # WDQS enforces a hard 60s wall. A pattern anchored on a large class cannot
    # finish as one request, so it is split into shards: each entry is a SPARQL
    # fragment substituted for {SHARD}, run separately, and unioned. A shard that
    # still times out is skipped with a warning rather than failing the run.
    shards: tuple[str, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        if self.prop in TIME_VARYING_PROPERTIES:
            raise ValueError(
                f"pattern {self.id!r} uses time-varying property {self.prop}; "
                "these are correct today and wrong after you ship"
            )
        if "order by" not in self.query.lower():
            raise ValueError(f"pattern {self.id!r} has no ORDER BY; paging would be unstable")
        for phrasing in self.phrasings:
            if "{subject}" not in phrasing:
                raise ValueError(f"pattern {self.id!r} phrasing missing {{subject}}: {phrasing}")


def _country_pattern(pid: str, category: str, prop: str, phrasings, explanation, floor=25):
    """Sovereign states are a small, clean, high-recognition entity set."""
    query = f"""
SELECT ?subject ?subjectLabel ?objectLabel ?links ?simContinent WHERE {{
  ?subject wdt:P31 wd:Q3624078 ;
           wdt:{prop} ?object ;
           wikibase:sitelinks ?links .
  FILTER NOT EXISTS {{ ?subject wdt:P576 ?dissolved }}
  FILTER(?links >= {floor})
  OPTIONAL {{ ?subject wdt:P30 ?continent . }}
  {LABEL_SERVICE}
    ?subject rdfs:label ?subjectLabel .
    ?object rdfs:label ?objectLabel .
    ?continent rdfs:label ?simContinent .
  }}
}}
ORDER BY ?subject
""".strip()
    return Pattern(
        id=pid,
        category=category,
        prop=prop,
        query=query,
        phrasings=phrasings,
        explanation=explanation,
        sim_fields=("simContinent",),
        min_sitelinks=floor,
    )


PATTERNS: list[Pattern] = [
    # ---------------------------------------------------------------- geography
    _country_pattern(
        "capital-of", "geography", "P36",
        ("What is the capital of {subject}?",
         "{subject} has which city as its capital?",
         "Which city is the seat of government of {subject}?"),
        "{answer} is the capital of {subject}.",
    ),
    _country_pattern(
        "currency-of", "geography", "P38",
        ("What is the official currency of {subject}?",
         "Which currency do you spend in {subject}?"),
        "The official currency of {subject} is the {answer}.",
    ),
    _country_pattern(
        "language-of", "geography", "P37",
        ("What is an official language of {subject}?",
         "Which language holds official status in {subject}?"),
        "{answer} is an official language of {subject}.",
    ),
    _country_pattern(
        "continent-of", "geography", "P30",
        ("On which continent is {subject}?",
         "{subject} lies on which continent?"),
        "{subject} is located in {answer}.",
        floor=40,
    ),
    Pattern(
        id="city-country",
        category="geography",
        prop="P17",
        query=f"""
SELECT ?subject ?subjectLabel ?objectLabel ?links ?simContinent WHERE {{
  ?subject wdt:P31 wd:Q515 ;
           wdt:P17 ?object ;
           wikibase:sitelinks ?links .
  FILTER(?links >= 40)
  OPTIONAL {{ ?object wdt:P30 ?continent . }}
  {LABEL_SERVICE}
    ?subject rdfs:label ?subjectLabel .
    ?object rdfs:label ?objectLabel .
    ?continent rdfs:label ?simContinent .
  }}
}}
ORDER BY ?subject
""".strip(),
        phrasings=("In which country is the city of {subject}?",
                   "{subject} is a city in which country?"),
        explanation="{subject} is a city in {answer}.",
        sim_fields=("simContinent",),
        min_sitelinks=40,
        max_rows=20000,
    ),

    # ------------------------------------------------------------------ science
    Pattern(
        id="element-symbol",
        category="science",
        prop="P246",
        query=f"""
SELECT ?subject ?subjectLabel ?objectLabel ?links ?simPeriod WHERE {{
  ?subject wdt:P31 wd:Q11344 ;
           wdt:P246 ?symbol ;
           wikibase:sitelinks ?links .
  OPTIONAL {{ ?subject wdt:P1086 ?atomicNumber . }}
  BIND(STR(?symbol) AS ?objectLabel)
  BIND(STR(FLOOR(?atomicNumber / 18)) AS ?simPeriod)
  {LABEL_SERVICE}
    ?subject rdfs:label ?subjectLabel .
  }}
}}
ORDER BY ?subject
""".strip(),
        phrasings=("What is the chemical symbol for {subject}?",
                   "On the periodic table, {subject} is written as which symbol?"),
        explanation="The chemical symbol for {subject} is {answer}.",
        sim_fields=("simPeriod",),
        min_sitelinks=20,
    ),
    Pattern(
        id="element-atomic-number",
        category="science",
        prop="P1086",
        query=f"""
SELECT ?subject ?subjectLabel ?objectLabel ?links WHERE {{
  ?subject wdt:P31 wd:Q11344 ;
           wdt:P1086 ?atomicNumber ;
           wikibase:sitelinks ?links .
  BIND(STR(?atomicNumber) AS ?objectLabel)
  {LABEL_SERVICE}
    ?subject rdfs:label ?subjectLabel .
  }}
}}
ORDER BY ?subject
""".strip(),
        phrasings=("What is the atomic number of {subject}?",
                   "How many protons does an atom of {subject} have?"),
        explanation="{subject} has atomic number {answer}.",
        numeric_object=True,
        min_sitelinks=20,
    ),

    # --------------------------------------------------------------------- film
    Pattern(
        id="film-director",
        category="film",
        prop="P57",
        query=f"""
SELECT ?subject ?subjectLabel ?objectLabel ?links ?simDecade WHERE {{
  {{ SELECT ?subject ?links WHERE {{
      ?subject wdt:P31 wd:Q11424 ;
               wikibase:sitelinks ?links .
      FILTER(?links >= 60)
  }} }}
  ?subject wdt:P57 ?object .
  OPTIONAL {{ ?subject wdt:P577 ?published . }}
  BIND(STR(FLOOR(YEAR(?published) / 10) * 10) AS ?simDecade)
  {LABEL_SERVICE}
    ?subject rdfs:label ?subjectLabel .
    ?object rdfs:label ?objectLabel .
  }}
}}
ORDER BY ?subject
""".strip(),
        phrasings=("Who directed the film {subject}?",
                   "{subject} was directed by whom?"),
        explanation="{subject} was directed by {answer}.",
        sim_fields=("simDecade",),
        min_sitelinks=60,
        max_rows=8000,
        paged=False,
    ),

    # -------------------------------------------------------------- literature
    Pattern(
        id="book-author",
        category="literature",
        prop="P50",
        query=f"""
SELECT ?subject ?subjectLabel ?objectLabel ?links ?simCentury WHERE {{
  {{ SELECT ?subject ?links WHERE {{
      ?subject wdt:P31 wd:Q7725634 ;
               wikibase:sitelinks ?links .
      FILTER(?links >= 45)
  }} }}
  ?subject wdt:P50 ?object .
  OPTIONAL {{ ?subject wdt:P577 ?published . }}
  BIND(STR(FLOOR(YEAR(?published) / 100)) AS ?simCentury)
  {LABEL_SERVICE}
    ?subject rdfs:label ?subjectLabel .
    ?object rdfs:label ?objectLabel .
  }}
}}
ORDER BY ?subject
""".strip(),
        phrasings=("Who wrote {subject}?",
                   "{subject} is a work by which author?"),
        explanation="{subject} was written by {answer}.",
        sim_fields=("simCentury",),
        min_sitelinks=45,
        max_rows=8000,
        paged=False,
    ),

    # --------------------------------------------------------------------- art
    Pattern(
        id="painting-creator",
        category="art",
        prop="P170",
        query=f"""
SELECT ?subject ?subjectLabel ?objectLabel ?links ?simMovement WHERE {{
  {{ SELECT ?subject ?links WHERE {{
      ?subject wdt:P31 wd:Q3305213 ;
               wikibase:sitelinks ?links .
      FILTER(?links >= 30)
  }} }}
  ?subject wdt:P170 ?object .
  OPTIONAL {{ ?subject wdt:P135 ?movement . }}
  {LABEL_SERVICE}
    ?subject rdfs:label ?subjectLabel .
    ?object rdfs:label ?objectLabel .
    ?movement rdfs:label ?simMovement .
  }}
}}
ORDER BY ?subject
""".strip(),
        phrasings=("Who painted {subject}?",
                   "{subject} is the work of which artist?"),
        explanation="{subject} was painted by {answer}.",
        sim_fields=("simMovement",),
        min_sitelinks=30,
        max_rows=8000,
        paged=False,
    ),

    # -------------------------------------------------------------------- music
    Pattern(
        id="album-artist",
        category="music",
        prop="P175",
        query=f"""
SELECT ?subject ?subjectLabel ?objectLabel ?links ?simDecade WHERE {{
  {{ SELECT ?subject ?links WHERE {{
      ?subject wdt:P31 wd:Q482994 ;
               wikibase:sitelinks ?links .
      FILTER(?links >= 40)
  }} }}
  ?subject wdt:P175 ?object .
  OPTIONAL {{ ?subject wdt:P577 ?published . }}
  BIND(STR(FLOOR(YEAR(?published) / 10) * 10) AS ?simDecade)
  {LABEL_SERVICE}
    ?subject rdfs:label ?subjectLabel .
    ?object rdfs:label ?objectLabel .
  }}
}}
ORDER BY ?subject
""".strip(),
        phrasings=("Which artist released the album {subject}?",
                   "The album {subject} is by whom?"),
        explanation="{subject} is an album by {answer}.",
        sim_fields=("simDecade",),
        min_sitelinks=40,
        max_rows=8000,
        paged=False,
    ),

    # ------------------------------------------------------------------ history
    Pattern(
        id="person-birth-year",
        category="history",
        prop="P569",
        query=f"""
SELECT ?subject ?subjectLabel ?objectLabel ?links ?simCountry WHERE {{
  ?subject wdt:P31 wd:Q5 ;
           {{SHARD}} ;
           wikibase:sitelinks ?links .
  FILTER(?links >= 60)
  ?subject wdt:P569 ?born .
  FILTER(YEAR(?born) > 1000)
  BIND(STR(YEAR(?born)) AS ?objectLabel)
  OPTIONAL {{ ?subject wdt:P27 ?citizenship . }}
  {LABEL_SERVICE}
    ?subject rdfs:label ?subjectLabel .
    ?citizenship rdfs:label ?simCountry .
  }}
}}
ORDER BY ?subject
""".strip(),
        phrasings=("In which year was {subject} born?",
                   "{subject} was born in which year?"),
        explanation="{subject} was born in {answer}.",
        sim_fields=("simCountry",),
        numeric_object=True,
        min_sitelinks=60,
        paged=False,
        shards=OCCUPATION_SHARDS,
    ),
    Pattern(
        id="person-nationality",
        category="history",
        prop="P27",
        query=f"""
SELECT ?subject ?subjectLabel ?objectLabel ?links ?simContinent WHERE {{
  ?subject wdt:P31 wd:Q5 ;
           {{SHARD}} ;
           wikibase:sitelinks ?links .
  FILTER(?links >= 60)
  ?subject wdt:P27 ?object .
  OPTIONAL {{ ?object wdt:P30 ?continent . }}
  {LABEL_SERVICE}
    ?subject rdfs:label ?subjectLabel .
    ?object rdfs:label ?objectLabel .
    ?continent rdfs:label ?simContinent .
  }}
}}
ORDER BY ?subject
""".strip(),
        phrasings=("Of which country was {subject} a citizen?",
                   "{subject} held citizenship of which country?"),
        explanation="{subject} was a citizen of {answer}.",
        sim_fields=("simContinent",),
        min_sitelinks=60,
        paged=False,
        shards=OCCUPATION_SHARDS,
    ),
]

# Patterns that do not reliably complete on the public WDQS endpoint. They are
# addressable by name but excluded from a bare `generate` run. See README.
EXPERIMENTAL = {"painting-creator", "album-artist"}

BY_ID = {p.id: p for p in PATTERNS}


def resolve(names: list[str] | None) -> list[Pattern]:
    if not names:
        return [p for p in PATTERNS if p.id not in EXPERIMENTAL]
    chosen: list[Pattern] = []
    for name in names:
        if name in BY_ID:
            chosen.append(BY_ID[name])
            continue
        matches = [p for p in PATTERNS if p.category == name]
        if not matches:
            raise SystemExit(f"unknown pattern or category: {name!r}")
        chosen.extend(matches)
    return chosen
