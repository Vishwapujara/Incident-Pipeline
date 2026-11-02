"""
Thin wrapper around spaCy's NER model - the fallback/grounding
check for affected_service when no Service::* ground-truth label
exists on a record.

Setup (one-time):
    pip install spacy
    python -m spacy download en_core_web_sm
"""

import spacy

_nlp = None  # loaded once, reused across calls (loading the model is slow)


def get_nlp():
    global _nlp
    if _nlp is None:
        _nlp = spacy.load("en_core_web_sm")
    return _nlp


# Entity types that could plausibly be a "service" name in this context
ENTITY_LABELS_OF_INTEREST = {"ORG", "PRODUCT", "FAC"}


def extract_entities(text: str, max_chars: int = 5000) -> list[str]:
    """
    Returns a deduplicated list of entity strings found in the text,
    restricted to types that could represent a service/system name.
    """
    if not text or not text.strip():
        return []
    nlp = get_nlp()
    doc = nlp(text[:max_chars])
    return list({
        ent.text.strip() for ent in doc.ents
        if ent.label_ in ENTITY_LABELS_OF_INTEREST and ent.text.strip()
    })