"""Whether a request excludes a value it names, read from its grammar, not from what it is about.

"给 SKU-10042 下单 50 件，不要用 SUP-003" names a supplier only to rule it out: SUP-003 is no param
of the order, and the approval card, which shows what will be ordered, cannot say "not SUP-003".
An exclusion marker rules out what follows it in its clause:

* Chinese: 不要用, 别用, 不用, 除了, 排除, 不选, 不包括 and 不从, and every negator
  (``negation.NEGATORS``; 别 wherever it is the negator, ``negation.bie_negates``: not in 别的,
  "other");
* English: "not" as it is written ("not", "cannot", "don't", "won't", "dont" ...), "except",
  "other than", "excluding", "instead of", "rather than", "without" and "avoid", and the
  negators "never" and "no need". Not "but": it joins two clauses far more often than it rules
  out a value ("anyone but SUP-001").

A clause runs to the next punctuation mark (the list comma 、 ends none). The markers are the
languages' closed classes of exclusion and negation words, read in two ways, one for each use:

* ``excluded_at``: a value is excluded when a marker stands right before it, with nothing in
  between but connecting words: articles and prepositions ("not from SUP-003", 不要从 ...), a verb
  of using or choosing ("don't use", 别选), the words that name the value's kind, which the
  caller gives ("excluding supplier SUP-006", 不要用供应商 SUP-006), and the values before it in a
  list ("except SUP-003 and SUP-004", 除了 SUP-002、SUP-004). An extractor never takes such a value
  as a param (``plugins.supply_chain.extract``). A marker further away negates something else:
  "late shipments that haven't arrived from SUP-006" still asks about SUP-006. A contraction of
  "not" ("don't", "didn't", "isn't" ..., and "dont") excludes a value only through one of those
  verbs ("don't use SUP-003"). With none in between it belongs to a question or negates its own
  verb, and the value is what the request asks about: "didn't SUP-003 deliver late?", "doesn't
  SKU-10042 have stock?". A contraction that does rule out what follows it without such a verb
  ("a supplier that isn't SUP-002") leaves the value a param, a known miss; the request still
  excludes it (below).
* ``excludes``: a request excludes something when an identifier (letters and digits, hyphens
  between them, with a digit: SUP-003, 50; or capitals joined by a hyphen: WH-EAST) follows a
  marker anywhere in its clause, a contraction included. The rule arm never lets a write named
  in such a request go straight to its approval (``rule_arm``): the user confirms it first.

The second is the wider on purpose. "don't order from SUP-002" keeps SUP-002 a param (ordering
is no verb of using or choosing), and "不用担心 SUP-003 的交期" ("never mind SUP-003's lead time")
excludes nothing, but both are confirmed before their approval, which costs a click; the other
mistake would put the approval of an order from SUP-002 in front of someone who said not to
order from it.
"""

import re
from collections.abc import Iterable

from .negation import NEGATORS, bie_negates
from .text import has_cjk

_ZH_MARKERS = ("不要用", "别用", "不用", "除了", "排除", "不选", "不包括", "不从",
               *(negator for negator in NEGATORS if has_cjk(negator)))
_EN_MARKERS = ("not", "cannot", "dont", "never", "no need", "except", "other than", "excluding",
               "instead of", "rather than", "without", "avoid")
# whole words, and "not" contracted ("don't", "won't"; ’ is the typographic apostrophe)
_EN_MARKER = (r"(?<![\w'’])(?:" + "|".join(r"\s+".join(map(re.escape, marker.split()))
                                           for marker in _EN_MARKERS)
              + r"|\w+n['’]t)(?![\w'’])")
_EN_MARKER_RE = re.compile(_EN_MARKER, re.IGNORECASE)
_EN_MARKER_AT_END = re.compile(_EN_MARKER + "$", re.IGNORECASE)
# "not" contracted, also "dont" as typed without its apostrophe: it excludes a value only through
# a verb of using or choosing (excluded_at; module docstring)
_CONTRACTION_AT_END = re.compile(r"(?<![\w'’])(?:\w+n['’]t|dont)$", re.IGNORECASE)

_BREAK = re.compile(r"[,，;；。!！?？:：\n]|\.(?=\s|$)")
# connecting words: articles, prepositions and verbs of using or choosing
_VERBS = ("use", "using", "choose", "pick", "select")
_GLUE = ("a", "an", "the", "any", "our", "their", "this", "that", "from", "with", "by", "via",
         "for", "of", "to", "in", "at", *_VERBS, "用", "选", "找", "从", "由", "向")
_LIST = frozenset({"and", "or", "nor"})
_ZH_LIST = ("和", "或", "及", "与", "跟", "、")
_LAST_WORD = re.compile(r"(?<![\w'’])([A-Za-z]+)$")
_TOKEN = r"(?<![A-Za-z0-9])[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*"
_TOKEN_RE = re.compile(_TOKEN)
_LAST_TOKEN = re.compile(_TOKEN + "$")


def _identifier(token: str) -> bool:
    return any(c.isdigit() for c in token) or ("-" in token and token.isupper())


def _bie_ok(text: str, end: int, marker: str) -> bool:
    """A marker ending at ``end`` of ``text`` holds 别 only where 别 is the negator."""
    at = marker.find("别")
    return at < 0 or bie_negates(text, end - len(marker) + at)


def _ends_with_marker(text: str) -> bool:
    return bool(_EN_MARKER_AT_END.search(text)) or any(
        text.endswith(marker) and _bie_ok(text, len(text), marker) for marker in _ZH_MARKERS)


def _first_marker_end(clause: str) -> int | None:
    ends = [found.end() for found in _EN_MARKER_RE.finditer(clause)]
    for marker in _ZH_MARKERS:
        at = clause.find(marker)
        while at >= 0:
            if _bie_ok(clause, at + len(marker), marker):
                ends.append(at + len(marker))
                break
            at = clause.find(marker, at + 1)
    return min(ends, default=None)


def _strip_word(text: str, words: Iterable[str], *, plural: bool = False) -> str | None:
    """``text`` without a last word that is one of ``words`` (a Latin word whole, also with a
    plural "s" when ``plural``; a Chinese one as written), else None."""
    for word in words:
        if has_cjk(word):
            if text.endswith(word):
                return text[:-len(word)]
            continue
        pattern = (r"(?<![\w'’])" + r"\s+".join(map(re.escape, word.split()))
                   + (r"s?$" if plural else "$"))
        found = re.search(pattern, text, re.IGNORECASE)
        if found:
            return text[:found.start()]
    return None


def _clause_before(text: str, index: int) -> str:
    start = 0
    for found in _BREAK.finditer(text, 0, index):
        start = found.end()
    return text[start:index]


def excluded_at(text: str, start: int, kinds: Iterable[str] = ()) -> bool:
    """Whether the value at ``start`` of ``text`` is excluded: an exclusion marker stands right
    before it, with only connecting words, its ``kinds`` ("supplier", 供应商) and the values of
    a list in between; a contraction of "not" only with a verb of using or choosing among them
    (module docstring)."""
    rest, kinds, after_list, verb = _clause_before(text, start), tuple(kinds), False, False
    while rest := rest.rstrip():
        if _ends_with_marker(rest):
            return verb or not _CONTRACTION_AT_END.search(rest)
        last = _LAST_WORD.search(rest)
        if rest.endswith(_ZH_LIST) or (last and last.group(1).casefold() in _LIST):
            # a list's connective: the value before it is excluded with the one after it
            rest, after_list = (rest[:-1] if rest.endswith(_ZH_LIST) else rest[:last.start()],
                                True)
            continue
        value = _LAST_TOKEN.search(rest) if after_list else None
        if value and _identifier(value.group()):
            rest, after_list = rest[:value.start()], False
            continue
        cut = _strip_word(rest, kinds, plural=True)
        if cut is None:
            cut = _strip_word(rest, _GLUE)
            verb = verb or (cut is not None and rest[len(cut):].strip().casefold() in _VERBS)
        if cut is None:
            return False
        rest = cut
    return False


def excludes(text: str) -> bool:
    """Whether ``text`` excludes something: an identifier follows an exclusion marker in its
    clause (module docstring)."""
    for clause in _BREAK.split(text):
        end = _first_marker_end(clause)
        if end is not None and any(_identifier(t) for t in _TOKEN_RE.findall(clause[end:])):
            return True
    return False
