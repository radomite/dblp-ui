"""Searchable short names for dblp's conference and journal labels."""

import re

STOP_WORDS = {"a", "an", "and", "at", "for", "in", "of", "on", "the", "to", "with"}
ORGANIZATIONS = {"ACM", "IEEE", "IFIP", "SIAM"}
CURATED = {
    "acm trans. database syst.": "TODS",
    "acm trans. comput. syst.": "TOCS",
    "acm trans. graph.": "TOG",
    "acm trans. inf. syst.": "TOIS",
    "acm trans. internet techn.": "TOIT",
    "acm trans. math. softw.": "TOMS",
    "acm trans. program. lang. syst.": "TOPLAS",
    "acm trans. softw. eng. methodol.": "TOSEM",
    "acm comput. surv.": "CSUR",
    "proc. vldb endow.": "PVLDB",
}


def aliases(venue):
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9]*", venue)
    words = [word for word in tokens if word.casefold() not in STOP_WORDS]
    if not words:
        return ""
    found = set()
    for word in words:
        if word.isupper() and 2 <= len(word) <= 12:
            found.add(word)
    for subset in (words, words[1:] if words[0].upper() in ORGANIZATIONS else []):
        if len(subset) < 2:
            continue
        initials = "".join(word[0].upper() for word in subset)
        compact = "".join(word.upper() if word.isupper() and len(word) <= 8 else word[0].upper() for word in subset)
        found.update((initials, compact))
    curated = CURATED.get(venue.casefold().strip())
    if curated:
        found.add(curated)
    return " ".join(sorted(found))
