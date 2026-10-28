"""Whether a request negates the phrase it names, read from its grammar, not from what it is about.

The rule arm never lets a negated write go straight to its approval (``rule_arm``): "别给 SKU-10042
下单 200 件" names a write's phrase, a SKU and a quantity, but it says not to place the order. A
phrase is negated when a negator of its language stands anywhere before it in the request:

* English: the negative imperatives "don't" (also typed "dont") and "do not", "never", and "no
  need" ("no need to ...", "no need for ...");
* Chinese: the negative imperatives 别, 不要, 勿 and 甭, the prohibitions 禁止 ("forbid"), 不准
  and 不许 ("not allowed"), and the no-need negators 不用, 不必, 无需 (also written 无须) and 不需要
  (also 不需), "do not need".

The word lists are the languages' closed classes of prohibitive and no-need markers, not
phrasings collected from examples, and they match the way keywords do (``text.phrase_spans``):
whole words in English, as written in Chinese. A plain 不 or "not" is no negator: it negates a
statement as often as a command (库存不足, "not enough"), so 先不下单 ("hold off ordering") is not
read.

别 is the one negator that is also part of other words. It negates wherever it is written
(``bie_negates``: 这批货别下单, 记得别下单, 千万别下单), except inside a closed list of words in
which it is no imperative "don't" (``BIE_WORDS``):

* 别 second: 特别 ("especially"), 类别 ("category"), 级别, 区别, 分别, 个别, 识别, 差别, 告别, 性别,
  辨别, 鉴别, 派别, 判别, 离别, 送别, 道别, 惜别, 永别 and 临别;
* 别 first: 别人 ("others"), 别的 ("other"), 别处, 别名, 别称, 别墅, 别致, 别针 and 别扭.

The list is a blacklist on purpose: a word it misses reads as a negation, which costs a click,
never an approval nobody asked for. Known misses of that kind are other words and idioms that
hold 别 (别无选择, "no other choice"; 有别于, "unlike"; 分门别类; 天壤之别) and 别 that concedes
rather than forbids (别看量小, "small as it is"). 不准 has no such list: it also reads as a
negation inside 不准时 ("not on time") and 不准确 ("inaccurate").

It errs towards a negation. A negator anywhere before the phrase counts, in any clause and
whatever it negates ("don't forget to place an order ...", "不用担心预算，给 ... 下单 ...",
"不需要的旧料先放着，给 ... 下单 ..."): such a command is confirmed once before its approval, which
costs a click; a negated write read as a command would put the approval of an order in front of
someone who said not to place it. A negator after the phrase negates something else ("...，不用
加急", "no need to expedite") and leaves the write alone. The rule stands on its own: "don't"
and "do not" that open a clause also read as a question (``interrogative``), "please don't ...",
"I don't want ..." and every Chinese negator do not.
"""

from collections.abc import Iterable

from .text import normalize, phrase_spans

NEGATORS = ("don't", "dont", "do not", "never", "no need",
            "别", "不要", "勿", "甭",
            "禁止", "不准", "不许",
            "不用", "不必", "无需", "无须", "不需要", "不需")

# The words in which 别 is no imperative "don't" (bie_negates; module docstring)
BIE_WORDS = frozenset({
    "特别", "类别", "级别", "区别", "分别", "个别", "识别", "差别", "告别", "性别", "辨别", "鉴别",
    "派别", "判别", "离别", "送别", "道别", "惜别", "永别", "临别",
    "别人", "别的", "别处", "别名", "别称", "别墅", "别致", "别针", "别扭",
})


def bie_negates(text: str, at: int) -> bool:
    """Whether the 别 at ``at`` of ``text`` is the negator: it is, unless it is part of one of
    ``BIE_WORDS`` there (module docstring)."""
    return text[max(at - 1, 0):at + 1] not in BIE_WORDS and text[at:at + 2] not in BIE_WORDS


def negator_spans(text: str) -> list[tuple[int, int]]:
    """Where the negators stand in ``normalize(text)``, as (start, end) offsets."""
    norm = normalize(text)
    spans = [span for negator in NEGATORS if negator != "别"
             for span in phrase_spans(negator, text)]
    spans.extend((at, at + 1) for at, char in enumerate(norm)
                 if char == "别" and bie_negates(norm, at))
    return spans


def negates(text: str, spans: Iterable[tuple[int, int]]) -> bool:
    """Whether a negator stands before any of ``spans``: where a phrase of ``text`` matched, as
    offsets in its normalized form (``text.phrase_spans``; see the module docstring)."""
    last = max((start for start, _ in spans), default=None)
    if last is None:
        return False
    return any(start < last for start, _ in negator_spans(text))
