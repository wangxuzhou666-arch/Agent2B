"""Reply language and the fixed sentences the system itself writes.

Only two reply languages exist: English and Chinese. The language of a request is decided once
from the query text (``detect_language``) and travels in the request facts
(``ToolContext.facts["language"]``), so every deterministic sentence of that request (template
narration, fallbacks, prompts for confirmation) speaks the same language. Model output is told
the language but still passes the numeric lock like any other model text.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

Language = Literal["en", "zh"]
LANGUAGES: tuple[Language, ...] = ("en", "zh")
DEFAULT_LANGUAGE: Language = "en"

# CJK Unified Ideographs, extension A and the compatibility block
_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_LATIN_WORD = re.compile(r"[A-Za-z]+")


def detect_language(text: str) -> Language:
    """``zh`` when CJK characters are at least as many as Latin words, else ``en``.

    Counting Latin *words* rather than letters keeps identifiers fair: in "SKU-10042 库存多少"
    the code "SKU" is one word against four characters, so the request reads as Chinese.
    """
    cjk = len(_CJK.findall(text or ""))
    return "zh" if cjk and cjk >= len(_LATIN_WORD.findall(text or "")) else "en"


def as_language(value: Any) -> Language | None:
    return value if value in LANGUAGES else None


def language_of(facts: Mapping[str, Any]) -> Language:
    """The request language from request facts: the recorded one, else detected from the query."""
    return as_language(facts.get("language")) or detect_language(str(facts.get("query") or ""))


MESSAGES: dict[str, dict[Language, str]] = {
    # graph
    "query_empty": {"en": "The query is empty.", "zh": "问题为空。"},
    "query_too_long": {"en": "The query is longer than {limit} characters.",
                       "zh": "问题超过了 {limit} 个字符。"},
    "confirm_one": {"en": "Please confirm before this runs.", "zh": "运行前请先确认。"},
    "confirm_many": {"en": "Several reports could answer this. Which one did you mean?",
                     "zh": "有几个报表都能回答这个问题，你指的是哪一个？"},
    "needs_confirmation": {"en": "Confirmation needed: {summary}", "zh": "需要确认：{summary}"},
    "run_failed": {"en": "The request failed while running.", "zh": "请求在运行时失败了。"},
    "could_not_run": {"en": "The request could not run ({status}).",
                      "zh": "请求未能运行（{status}）。"},
    "chat_unavailable": {
        "en": "The assistant is unavailable right now. Ask for one of the listed reports.",
        "zh": "助手暂时不可用。请直接查询某个已有的报表。"},
    "draft_stripped": {
        "en": "The answer was withheld because it cited figures no tool computed.",
        "zh": "回答引用了没有任何工具计算过的数字，已被拦下。"},
    # zero-key provider
    "offline_chat": {
        "en": "No language model is configured, so I can only run the reports this console "
              "knows about. Try asking for one of them directly.",
        "zh": "当前没有配置语言模型，只能运行控制台已有的报表。请直接查询其中一个。"},
    "offline_agent": {
        "en": "No language model is configured, so open-ended tasks cannot be planned. "
              "Ask for a specific report instead.",
        "zh": "当前没有配置语言模型，无法规划开放式任务。请改为查询某个具体报表。"},
    # the mock's fixed script for the documented exploration case (providers.mock_script)
    "mock_script_end": {
        "en": "No language model is configured. The mock model follows a fixed script for this "
              "one request, and the script ended without an order proposal that could wait for "
              "approval.",
        "zh": "当前没有配置语言模型。模拟模型对这一个请求按固定脚本执行，"
              "脚本结束时没有可以等待审批的下单建议。"},
    "invented_figure": {"en": "Next period should come in around {figure}.",
                        "zh": "预计下一期约为 {figure}。"},
    # open-task loop
    "no_answer": {"en": "The task ended without an answer.", "zh": "任务结束，但没有得到回答。"},
    # a run that stopped short says so first (agent_loop.stopped_text), then shows the last
    # result it had, if any
    "explore_stopped_budget": {
        "en": "Exploration stopped early: it reached its {budget} limit before finishing.",
        "zh": "探索已提前停止：在完成前达到了{budget}上限。"},
    "budget_max_turns": {"en": "turn", "zh": "轮次"},
    "budget_max_tool_calls": {"en": "tool-call", "zh": "工具调用次数"},
    "model_down": {"en": "Exploration stopped: the model is unavailable, so the task could not "
                         "be completed.",
                   "zh": "探索已停止：模型不可用，任务未能完成。"},
    "explorer_timeout": {"en": "Exploration stopped: it ran out of time before finishing.",
                         "zh": "探索已停止：在完成前超出了时间限制。"},
    "explorer_failed": {"en": "Exploration stopped: the exploration runtime failed.",
                        "zh": "探索已停止：探索运行时出错。"},
    "explore_stopped_audit": {
        "en": "Exploration stopped: a step could not be written to the audit log.",
        "zh": "探索已停止：有一步无法写入审计日志。"},
    "explore_partial": {"en": "Last result before it stopped: {text}",
                        "zh": "停止前的最后结果：{text}"},
    # the explore node's guard (core.explore.handover): exploration never started
    "explore_refused_explicit_target": {
        "en": "Exploration was refused: this request names a specific report, so it runs only "
              "on its fixed path.",
        "zh": "已拒绝探索：该请求指定了具体报表，只能按其固定流程运行。"},
    "explore_refused_earlier_outcome": {
        "en": "Exploration was refused: this request already ended on its fixed path.",
        "zh": "已拒绝探索：该请求已在固定流程中结束。"},
    "explore_refused_no_handover": {
        "en": "Exploration was refused: this request was never handed over to it.",
        "zh": "已拒绝探索：该请求未被移交给探索。"},
    # workflows
    "workflow_needs_details": {
        "en": "Could not work out the details '{workflow}' needs ({step}). Please restate the "
              "request with those details.",
        "zh": "无法确定“{workflow}”需要的信息（{step}）。请补充这些信息后重新提问。"},
    "workflow_bad_input": {"en": "'{workflow}' got invalid input: {count} problem(s).",
                           "zh": "“{workflow}”的输入无效：{count} 处问题。"},
    # template narration
    "rows_none": {"en": "No rows matched{where}.", "zh": "没有匹配的记录{where}。"},
    "rows_one": {"en": "1 row matched{where}.", "zh": "共 1 条记录{where}。"},
    "rows_many": {"en": "{count} rows matched{where}.", "zh": "共 {count} 条记录{where}。"},
    "figure": {"en": "{label}: {value}.", "zh": "{label}：{value}。"},
    "no_figures": {"en": "The request completed with no figures to report.",
                   "zh": "请求已完成，没有需要报告的数字。"},
    # scope guard (core.routing.scope_guard): constraints a question names. Plain text: the
    # console shows these sentences as they are. {target} is the target's display title.
    "scope_kind_category": {"en": "category", "zh": "类别"},
    "scope_constraint": {"en": "{kind} {label}", "zh": "{kind}“{label}”"},
    "scope_constraint_not": {"en": "{kind} other than {label}", "zh": "{kind}非“{label}”"},
    "scope_or": {"en": " or ", "zh": "或"},
    "scope_and": {"en": " and ", "zh": "和"},
    "scope_gap_limit": {"en": "“{target}” can't be limited to {limit}.",
                        "zh": "“{target}”无法按{limit}筛选。"},
    "scope_gap_exclude": {"en": "“{target}” can't leave out {exclude}.",
                          "zh": "“{target}”无法排除{exclude}。"},
    "scope_gap_both": {"en": "“{target}” can't be limited to {limit}, and can't leave out "
                             "{exclude}.",
                       "zh": "“{target}”无法按{limit}筛选，也无法排除{exclude}。"},
    "scope_gap_choose": {
        "en": "“{choice}” shows its result for all {kinds} instead; the other choices can "
              "apply the filter.",
        "zh": "选择“{choice}”会显示全部{kinds}的结果；其他选项可以按此筛选。"},
    "scope_gap_only": {"en": "“{choice}” shows its result for all {kinds} instead.",
                       "zh": "选择“{choice}”会显示全部{kinds}的结果。"},
    "scope_show_all": {"en": "Show all {kinds}", "zh": "显示全部{kinds}"},
    "scope_conflict": {
        "en": "The question names more than one {kind} ({labels}), but “{target}” filters by "
              "one at a time. Pick one.",
        "zh": "问题提到了多个{kind}（{labels}），但“{target}”一次只能按一个{kind}筛选，"
              "请选择一个。"},
    "scope_unfiltered": {"en": "not filtered by {constraints}; all {kinds} shown",
                         "zh": "未按{constraints}筛选，显示全部{kinds}"},
    "scope_unknown_value": {"en": "'{value}' is not a known {kind}. Known: {known}.",
                            "zh": "“{value}”不是已知的{kind}。可选：{known}。"},
    "none": {"en": "none", "zh": "无"},
    # typed values (core.entities.values): what is wrong with one cell, never the value itself
    "value_number": {"en": "must be a number", "zh": "必须是数字"},
    "value_finite": {"en": "must be a finite number", "zh": "必须是有限的数字"},
    "value_whole": {"en": "must be a whole number", "zh": "必须是整数"},
    "value_bool": {"en": "must be true or false", "zh": "必须是 true 或 false"},
    "value_date": {"en": "must be a date (YYYY-MM-DD)", "zh": "必须是日期（YYYY-MM-DD）"},
    "value_one_of": {"en": "must be one of {values}", "zh": "必须是 {values} 之一"},
    "value_required": {"en": "required", "zh": "必填"},
    "value_duplicate_key": {"en": "duplicate key; first seen in row {row}",
                            "zh": "主键重复；首次出现在第 {row} 行"},
    "value_category_change": {
        "en": "“{pk}” is stored as {stored}, but this import is for {entity}; an import "
              "cannot change the category of an existing row",
        "zh": "“{pk}”已存储为“{stored}”，而本次导入的类别是“{entity}”；导入不能更改已有记录的类别"},
    "value_field": {"en": "{label}: {problem}", "zh": "{label}：{problem}"},
    "list_or": {"en": "{head} or {last}", "zh": "{head} 或 {last}"},
    "list_and": {"en": "{head} and {last}", "zh": "{head} 和 {last}"},
    # file-level upload problems (core.guards.upload, core.entities.importer): the console and
    # the API show these sentences as they are, so they read as one phrase, lower-case start
    "upload_empty": {"en": "the file is empty", "zh": "文件是空的"},
    "upload_too_large": {"en": "the file exceeds {limit} bytes", "zh": "文件超过了 {limit} 字节"},
    "upload_legacy_xls": {"en": "legacy .xls files are not supported; save as .xlsx",
                          "zh": "不支持旧版 .xls 文件；请另存为 .xlsx"},
    "upload_looks_like": {"en": "this looks like {kind}, not a CSV or XLSX",
                          "zh": "这看起来是“{kind}”，不是 CSV 或 XLSX"},
    "file_kind_pdf": {"en": "a PDF", "zh": "PDF 文件"},
    "file_kind_png": {"en": "a PNG image", "zh": "PNG 图片"},
    "file_kind_gif": {"en": "a GIF image", "zh": "GIF 图片"},
    "file_kind_jpeg": {"en": "a JPEG image", "zh": "JPEG 图片"},
    "file_kind_gzip": {"en": "a gzip archive", "zh": "gzip 压缩包"},
    "file_kind_executable": {"en": "an executable", "zh": "可执行文件"},
    "file_kind_rar": {"en": "a RAR archive", "zh": "RAR 压缩包"},
    "file_kind_7z": {"en": "a 7z archive", "zh": "7z 压缩包"},
    "file_kind_empty_zip": {"en": "an empty zip archive", "zh": "空的 zip 压缩包"},
    "upload_binary_text": {"en": "binary or UTF-16 content; save the CSV as UTF-8",
                           "zh": "内容是二进制或 UTF-16；请把 CSV 另存为 UTF-8"},
    "upload_xlsx_name": {
        "en": "the file is named .xlsx but its content is not an Excel workbook; save it as a "
              "real .xlsx or as a CSV",
        "zh": "文件名是 .xlsx，但内容不是 Excel 工作簿；请另存为真正的 .xlsx 或 CSV"},
    "upload_xlsx_unreadable": {"en": "the file is not a readable XLSX workbook",
                               "zh": "该文件不是可读取的 XLSX 工作簿"},
    "upload_xlsx_parts": {"en": "the workbook has too many internal parts",
                          "zh": "工作簿的内部部件过多"},
    "upload_xlsx_not_workbook": {"en": "the archive is not an XLSX workbook",
                                 "zh": "该压缩包不是 XLSX 工作簿"},
    "upload_xlsx_encrypted": {"en": "encrypted workbooks are not supported",
                              "zh": "不支持加密的工作簿"},
    "upload_xlsx_expands": {"en": "the workbook expands to more data than allowed",
                            "zh": "工作簿解压后的数据量超出了上限"},
    "upload_xlsx_ratio": {"en": "the workbook has an abnormal compression ratio",
                          "zh": "工作簿的压缩比异常"},
    "upload_xlsx_method": {"en": "the workbook uses a compression method that XLSX does not allow",
                           "zh": "工作簿使用了 XLSX 不允许的压缩方式"},
    "upload_too_many_rows": {"en": "more than {limit} data rows", "zh": "数据行超过了 {limit} 行"},
    "upload_rows_read": {
        "en": "more than {limit} rows, empty rows included; delete the empty rows and upload "
              "again",
        "zh": "含空行在内超过了 {limit} 行；请删除空行后重新上传"},
    "upload_too_many_columns": {"en": "more than {limit} columns in the header row",
                                "zh": "表头行超过了 {limit} 列"},
    "upload_no_header": {"en": "the file has no header row", "zh": "文件没有表头行"},
    "upload_parse_error": {"en": "the file could not be parsed ({error})",
                           "zh": "无法解析该文件（{error}）"},
    "upload_collision_item": {"en": "{headers} map to {field} ({id})",
                              "zh": "{headers}都对应到“{field}”（{id}）"},
    "upload_header_collision": {
        "en": "several columns map to the same field: {clashes}. Rename or remove the extra "
              "columns.",
        "zh": "多个列对应到了同一个字段：{clashes}。请重命名或删除多余的列。"},
    "upload_missing_columns": {"en": "required columns not found: {columns}",
                               "zh": "缺少必需的列：{columns}"},
    "upload_restricted_columns": {
        "en": "your role cannot write the column(s) {columns}; remove them from the file and "
              "upload again",
        "zh": "你的角色无权写入这些列：{columns}；请从文件中删除它们后重新上传"},
    "upload_forbidden": {"en": "your role may not import data", "zh": "你的角色无权导入数据"},
    "upload_digest_mismatch": {"en": "the file or its validation changed since the preview",
                               "zh": "文件或其校验结果在预览之后发生了变化"},
    "upload_grant_invalid": {"en": "the confirmation is invalid, expired or used",
                             "zh": "确认无效、已过期或已被使用"},
    # imports as approvals: the summary a confirmation shows, its title, and the outcome
    "import_summary": {"en": "Import {count} row(s) into {entity} from {filename}",
                       "zh": "将 {count} 行导入“{entity}”，来源文件：{filename}"},
    "import_label": {"en": "Import {entity}", "zh": "导入{entity}"},
    "import_done": {"en": "Imported {written} row(s); {skipped} skipped.",
                    "zh": "已导入 {written} 行，跳过 {skipped} 行。"},
    "approval_used": {"en": "This confirmation was already used; ask again to run it again.",
                      "zh": "此确认已经使用过；如需再次运行，请重新提问。"},
    # confirmation summaries
    "yes": {"en": "yes", "zh": "是"},
    "no": {"en": "no", "zh": "否"},
}

_WHERE = {"en": " ({scope})", "zh": "（{scope}）"}


def say(key: str, language: str | None = None, **values: Any) -> str:
    """One fixed sentence in ``language`` (English when unknown), with ``values`` filled in."""
    texts = MESSAGES[key]
    text = texts.get(as_language(language) or DEFAULT_LANGUAGE, texts[DEFAULT_LANGUAGE])
    return text.format(**values) if values else text


def _join(key: str, items: Sequence[str], language: str | None) -> str:
    items = [str(i) for i in items]
    if len(items) <= 1:
        return "".join(items)
    head = ("、" if as_language(language) == "zh" else ", ").join(items[:-1])
    return say(key, language, head=head, last=items[-1])


def join_or(items: Sequence[str], language: str | None = None) -> str:
    """"A, B or C" / "A、B 或 C": a list a sentence can hold (never a Python list repr)."""
    return _join("list_or", items, language)


def join_and(items: Sequence[str], language: str | None = None) -> str:
    """"A, B and C" / "A、B 和 C": like ``join_or``, for things that all hold."""
    return _join("list_and", items, language)


def localized(table: Any, language: str | None) -> str | None:
    """``table[language]``, else the English entry; ``None`` when neither is a usable string.

    The one rule for every per-language text a plugin declares (a tool's ``title``, an entity's
    or field's ``label`` and ``description``, a param's ``label``)."""
    if not isinstance(table, Mapping):
        return None
    for lang in (as_language(language) or DEFAULT_LANGUAGE, DEFAULT_LANGUAGE):
        found = table.get(lang)
        if isinstance(found, str) and found.strip():
            return found.strip()
    return None


def language_from_header(accept_language: str | None) -> Language | None:
    """The reply language an ``Accept-Language`` header asks for first (``zh-CN`` -> zh),
    or None when it names neither supported language before the other."""
    for part in (accept_language or "").split(","):
        tag = part.split(";")[0].strip().casefold()
        if tag.startswith("zh"):
            return "zh"
        if tag.startswith("en"):
            return "en"
    return None


def language_of_request(accept_language: str | None) -> Language:
    """The reply language of a REST request: ``zh`` when ``Accept-Language`` asks for Chinese
    first (``zh``, ``zh-CN``, ...), English for anything else, a missing header included.
    The query endpoint does not use this: it follows the language of the question."""
    return language_from_header(accept_language) or DEFAULT_LANGUAGE


def scope_clause(scope: str, language: str | None) -> str:
    """The parenthesised data scope appended to the row-count sentence ("" when empty)."""
    scope = scope.strip()
    if not scope:
        return ""
    return _WHERE[as_language(language) or DEFAULT_LANGUAGE].format(scope=scope)
