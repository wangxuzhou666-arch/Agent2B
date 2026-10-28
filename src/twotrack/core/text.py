"""Text normalization shared by routing (rule arm, BM25 fallback) and the scope vocabulary.

Lives at the top of ``core`` (not in ``core.routing``) so the registry can build its constraint
vocabulary without importing the routing package.
"""

import re
import unicodedata

_WORD_RE = re.compile(r"\w+")
_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")

STOPWORDS = frozenset(
    "a an and are as at be by can could do does for from give have how i in is it list me "
    "my of on or our please show tell than that the their there this to us we what when "
    "which who why will with would you your".split()
)
# Chinese has no word breaks, so its stopwords are characters: the particles 的 and 了 and the
# conjunctions 和, 与, 或 ("of", "and", "or"). They join content words ("延迟的采购单"), and a
# bigram across one ("的采") would let any sentence written with the same grammar look relevant.
# Only characters that are almost always such glue: a word built on one (目的, 总和) loses its
# bigram, which costs less than every "X的Y" matching every other.
CJK_STOP_CHARS = frozenset("的了和与或")
_CJK_STOP_RE = re.compile(f"[{''.join(sorted(CJK_STOP_CHARS))}]")


def normalize(text: str) -> str:
    """Width and case folded, punctuation dropped, single spaces."""
    return " ".join(_WORD_RE.findall(unicodedata.normalize("NFKC", text).casefold()))


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def phrase_spans(phrase: str, text: str) -> list[tuple[int, int]]:
    """Every (start, end) where ``phrase`` occurs in ``normalize(text)``: whole words for a
    Latin phrase, a substring for a CJK one. Occurrences may overlap."""
    phrase, text = normalize(phrase), normalize(text)
    if not phrase:
        return []
    # a Latin phrase is padded so it matches whole words only; a match at index i of the
    # padded text starts at index i of the text itself (the padding adds one leading space)
    haystack, needle = (text, phrase) if has_cjk(phrase) else (f" {text} ", f" {phrase} ")
    spans = []
    at = haystack.find(needle)
    while at >= 0:
        spans.append((at, at + len(phrase)))
        at = haystack.find(needle, at + 1)
    return spans


def phrase_in(phrase: str, text: str) -> bool:
    """Whole-word phrase match on normalized text; substring match for CJK phrases."""
    return bool(phrase_spans(phrase, text))


def fold(token: str) -> str:
    """Light plural folding so "orders" and "order" meet; good enough for short labels."""
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokens(text: str) -> list[str]:
    """Index terms: folded words minus stopwords; CJK runs, split at the Chinese stop
    characters, become character bigrams (a one-character piece is kept as it is)."""
    out: list[str] = []
    for word in normalize(text).split():
        if not has_cjk(word):
            if word not in STOPWORDS:
                out.append(fold(word))
            continue
        for piece in _CJK_STOP_RE.split(word):
            out.extend([piece] if len(piece) == 1 else
                       [piece[i:i + 2] for i in range(len(piece) - 1)])
    return out
