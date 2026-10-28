"""Whether a request is a question, read from its grammar, not from what it is about.

The rule arm never lifts a question to a complete write (``rule_arm``): "SKU-10042 上周下单 200
件，到货了吗" names a write's phrase, a SKU and a quantity, but it asks whether an order arrived,
not for a new one. A request reads as a question when it carries a question marker of its
language anywhere:

* in any language, a question mark (``?``, and the full-width ``？`` that folds to it);
* English: a clause that opens with an auxiliary or a modal verb, the inversion that makes a
  yes/no question ("did we ...", "is there ...", "should I ..."), or with a wh-word ("how long
  ...", "which vendor ..."), after any coordinator ("..., and did it ship"); or the
  subordinator "whether" anywhere ("tell me whether ...");
* Chinese: a question particle that ends a clause (吗, 呢, 么), an A-not-A form (any X不X or
  X没X, such as 有没有, 是不是, 能不能, and the literary 是否, 能否, 可否), or a question word
  (什么, 谁, 哪, 怎, 为何, 如何, 何时, 多少, 多久).

The word lists are the languages' closed classes of question markers, not phrasings collected
from examples. Left out, because in a command they mostly mean something else: 几 ("a few" in
十几 and 好几, and part of 几乎, "almost"), and 哪 in 哪怕 ("even if").

It errs towards a question. A command phrased as one ("can you create a PO for ...", "能帮我
...下单...吗") is confirmed once before its approval, which costs a click; a question read as a
command would put the approval of a new order in front of someone who asked about an old one.
"""

import re
import unicodedata

# subject-auxiliary inversion: what opens an English yes/no question
_AUXILIARIES = frozenset(
    "am is are was were do does did has have had can could will would shall should may might "
    "must isn't aren't wasn't weren't don't doesn't didn't hasn't haven't hadn't can't cannot "
    "couldn't won't wouldn't shan't shouldn't mightn't mustn't".split()
)
_WH_WORDS = frozenset("what which who whom whose when where why how".split())
# a clause opens after any punctuation (a hyphen and an apostrophe stay inside a word: SKU-10042,
# didn't), and a coordinator before its first word leaves its mood as it is
_CLAUSE_BREAK = re.compile(r"[^\w \t'-]+")
_OPENER = re.compile(r"(?:(?:and|but|or|so)\s+)*([a-z]+(?:'[a-z]+)?)")
_WHETHER = re.compile(r"\bwhether\b")

_ZH_PARTICLE = re.compile(r"[吗呢么](?!\w)")  # at the end of a clause
_ZH_A_NOT_A = re.compile(r"([一-鿿]{1,2})[不没]\1|[是能可]否")
_ZH_QUESTION_WORD = re.compile(r"什么|谁|哪(?!怕)|怎|为何|如何|何时|多少|多久")


def _opens_question(clause: str) -> bool:
    found = _OPENER.match(clause.strip())
    if found is None:
        return False
    word = found.group(1)
    return word in _AUXILIARIES or word.split("'")[0] in _WH_WORDS


def is_question(text: str) -> bool:
    """Whether ``text`` carries a question marker of English or Chinese (module docstring)."""
    folded = unicodedata.normalize("NFKC", text).casefold().replace("’", "'")
    if "?" in folded or _WHETHER.search(folded):
        return True
    if any(rx.search(folded) for rx in (_ZH_PARTICLE, _ZH_A_NOT_A, _ZH_QUESTION_WORD)):
        return True
    return any(_opens_question(clause) for clause in _CLAUSE_BREAK.split(folded))
