"""Explicit research concepts and related search expressions.

Expressions in one group are OR alternatives for retrieval/matching; groups
are AND requirements for a core recommendation. Related expressions are not
claims that their scientific definitions are identical.
"""
from __future__ import annotations

from .storage import normalized


# Small, authored expansion table, never a generated scientific equivalence.
RELATED_EXPRESSIONS = (
    ("high entropy alloy", "high-entropy alloy", "high entropy alloys", "HEA", "HEAs"),
    ("dislocation mobility", "dislocation velocity", "dislocation kinetics",
     "mobility of dislocations", "velocity of dislocations"),
    ("Peierls Nabarro", "Peierls-Nabarro", "Peierls Nabarro model"),
)


def _unique(terms):
    if isinstance(terms, str):
        terms = [terms]
    if not isinstance(terms, (list, tuple)):
        return []
    values, seen = [], set()
    for term in terms:
        if not isinstance(term, str):
            continue
        term = term.strip()
        key = normalized(term)
        if key and key not in seen:
            values.append(term)
            seen.add(key)
    return values


def expand_group(terms):
    """Return authored retrieval alternatives, preserving the user's label."""
    values = _unique(terms)
    keys = {normalized(term) for term in values}
    for family in RELATED_EXPRESSIONS:
        if keys.intersection(normalized(term) for term in family):
            values = _unique(values + list(family))
    return values


def normalize_concepts(profile):
    """Normalize authored groups, or derive a conservative legacy migration.

    Explicit groups preserve order: put the research object first. Legacy
    mobility+dislocation becomes a single dislocation-mobility concept. Terms
    already contained in a longer phrase do not create extra requirements.
    """
    explicit = profile.get("core_concepts")
    if explicit:
        groups = [expand_group(group) for group in explicit if isinstance(group, (list, tuple))]
        return [group for group in groups if group]
    terms = _unique(profile.get("keywords") or profile.get("include_terms", []))
    keys = {normalized(term) for term in terms}
    if {"dislocation", "mobility"}.issubset(keys) or {"dislocations", "mobility"}.issubset(keys):
        terms = [term for term in terms if normalized(term) not in {"dislocation", "dislocations", "mobility"}]
        terms.append("dislocation mobility")
    remaining = []
    for term in terms:
        key = " " + normalized(term) + " "
        if any(key in " " + normalized(other) + " " and normalized(term) != normalized(other) for other in terms):
            continue
        remaining.append(term)
    groups, seen = [], set()
    for term in remaining:
        group = expand_group([term])
        keys = {normalized(value) for value in group}
        if keys.intersection(seen):
            continue
        groups.append(group)
        seen.update(keys)
    # Known alloy object comes before mobility even if legacy keyword order
    # has been reversed for retrieval priority. Other objects retain order.
    alloy = {normalized(value) for value in RELATED_EXPRESSIONS[0]}
    groups.sort(key=lambda group: 0 if alloy.intersection(normalized(value) for value in group) else 1)
    return groups


core_concepts = normalize_concepts
