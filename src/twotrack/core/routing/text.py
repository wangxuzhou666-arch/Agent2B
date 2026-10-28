"""Text normalization shared by the rule arm and the semantic fallback (see ``core.text``)."""

from ..text import STOPWORDS, fold, has_cjk, normalize, phrase_in, phrase_spans, tokens

__all__ = ["STOPWORDS", "fold", "has_cjk", "normalize", "phrase_in", "phrase_spans", "tokens"]
