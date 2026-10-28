"""Whether a request is a question (core/routing/interrogative.py): its language's question
markers, read from grammar. A question is never lifted to a complete write (rule_arm)."""

import pytest

from twotrack.core.routing import is_question


@pytest.mark.parametrize("text", [
    # a question mark, ASCII or full-width
    "ship it today?",
    "发货了？",
    # English: an auxiliary or a modal opens a clause (inversion), contracted forms included
    "did the shipment arrive",
    "has it shipped",
    "is there an open order for it",
    "are we out of stock",
    "can you check the stock",
    "could you raise it",
    "would you approve it",
    "should I reorder now",
    "didn't it arrive",
    "didn’t it arrive",  # a typographic apostrophe
    "isn't SKU-1 in stock",
    # English: a wh-word opens a clause
    "what is the lead time",
    "what's the status",
    "which vendor is cheapest",
    "how long does it take",
    "when will it arrive",
    # ... in any clause, after any coordinator
    "I ordered it last week, did it arrive",
    "we raised one. has it shipped",
    "ok, so can you check it",
    "SKU-1 下单 40 件, did it ship",
    # the subordinator of an indirect question
    "tell me whether it shipped",
    # Chinese: a particle that ends a clause
    "到货了吗",
    "能补货吗：明天",
    "那 SKU-1 呢",
    "发货了么",
    # Chinese: A-not-A, one or two characters, and the literary forms with 否
    "有没有到货",
    "是不是已经发货",
    "能不能今天发货",
    "可不可以加急",
    "到货没到货",
    "是否已经审批",
    "能否今天到",
    # Chinese: question words, wherever they stand
    "什么时候到货",
    "谁批准的",
    "哪家供应商最快",
    "怎么取消订单",
    "为何没有发货",
    "如何加急",
    "多少件已到货",
    "多久能到",
])
def test_a_question_marker_makes_a_question(text):
    assert is_question(text)


@pytest.mark.parametrize("text", [
    "",
    "place an order for 50 units",
    "please raise it today",
    "I'd like 40 units",
    "cancel the draft order",  # "can" only as a whole word
    "show what is late",  # a wh-word that opens no clause
    "ship it, whatever it costs",
    "给 SKU-1 下单 40 件",
    "下单 90 箱吧",  # 吧 urges, it does not ask
    "这么多件就下单",  # 么 that ends no clause
    "库存不足，补货",  # 不 between different characters
    "不得不下单",
    "哪怕贵一点也要下单",  # 哪怕: "even if"
    "几乎没货了，下单 300 件",  # 几乎: "almost"
    "十几件就够",
])
def test_a_command_or_statement_is_no_question(text):
    assert not is_question(text)


def test_it_errs_towards_a_question():
    """A relative clause opens with a wh-word as well; reading it as a question costs the
    command one confirmation, which is the cheaper mistake."""
    assert is_question("raise a PO for SKU-1, which we need by Friday")
