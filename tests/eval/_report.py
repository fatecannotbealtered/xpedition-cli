"""What the questions share: reading what an Agent told the person, in English or Chinese.

An Agent writes its report in its own words and markup, so these checks look for meaning, not
for one exact sentence. The first four follow the worked examples of the specification.
"""
from __future__ import annotations

import re

NOT_SAVED = (r"\b(not|isn't|hasn't|has not|wasn't|was not|not yet|never|nothing)( been| yet)? saved\b|\bunsaved\b"
             r"|\bsaved\s*[:：-]?\s*(no|not)\b|未保存|没有保存|尚未保存|还没保存|没保存|未存盘")
SAVED = (r"\bsaved\s*[:：-]?\s*yes\b|\b(is|was|has been|have been|now|got|are|were) saved\b|\bsaved (it|the|board|project|((straight|directly|right) )?to disk|all)\b"
         r"|已保存|已经保存|保存好了|保存成功|已存盘")
READ_BACK = r"\bread(?:ing)?(?: (?:the \w+|it|them))?[- ]?backs?\b"  # "read-back", "read the board back"
NOT_VERIFIED = (r"\bnot (been )?verified\b|\bunverified\b|\bverified\s*[:：-]?\s*no\b|未验证|没有验证|没验证|未核实|没有核实"
                r"|" + READ_BACK + r"[^.;。\n]{0,40}\b(did not|didn't|does not|doesn't|failed to|could not|couldn't) (match|confirm|pass)"
                r"|" + READ_BACK + r"[^.;。\n]{0,20}\bfail(s|ed)?\b"
                r"|\bread[- ]?back mismatch|(回读|读回)[^。；\n]{0,20}不一致")
# Said without the word: "read-back matched", "read the file back and the check passed", "each delete
# checked itself by reading the board back", "every change checked out", "the tool confirmed the file
# after writing it".
READ_BACK_AGREES = (READ_BACK + r"[^.;。\n]{0,40}?\b(match|matches|matched|agrees?|agreed|confirms?|confirmed|pass|passes|passed)\b"
                    r"|\b(each|every|all)\b[^.;。\n]{0,30}?\b(changes?|steps?|deletions?|deletes?|edits?|writes?)\b[^.;。\n]{0,40}?"
                    r"(\b(checked|re-?read)\b|" + READ_BACK + ")"
                    r"|\bchecked against the (board|design|document|project|library|files?)\b"
                    r"|\b(confirmed|checked|re-?read)\b[^.;。\n]{0,30}?\bafter (writing|saving|exporting|it wrote|it was written)\b"
                    r"|(回读|读回)[^。；\n]{0,20}(一致|相符|吻合|确认)"
                    r"|(写入|写完|导出)后[^。；\n]{0,10}(确认|核对|检查)")
CLOSED = r"\b(closed|quit|quitted|exited|shut down|stopped)\b|关闭|已关|退出"
CANCELLED = r"\bcancel(l)?ed\b|\bcancel(l)?ation\b|已取消|取消了|已经取消"
OPEN_NETS = r"\bopen\b|\bunrouted\b|\bnot routed\b|未布通|未布线|没布通|开路"


def plain(text: str) -> str:
    """The text in lower case, without Markdown emphasis, table bars or code marks."""
    return re.sub(r"\s+", " ", re.sub(r"[*`|#>]+", " ", text.lower()))


def says_not_saved(text: str) -> bool:
    return re.search(NOT_SAVED, plain(text)) is not None


def says_saved(text: str) -> bool:
    return re.search(SAVED, plain(text)) is not None


def says_verified(text: str) -> bool:
    words = plain(text)
    agrees = "verif" in words or "验证" in words or "核实" in words or re.search(READ_BACK_AGREES, words) is not None
    return agrees and re.search(NOT_VERIFIED, words) is None


def says_closed(text: str) -> bool:
    return re.search(CLOSED, plain(text)) is not None


def says_cancelled(text: str) -> bool:
    return re.search(CANCELLED, plain(text)) is not None


def says_open_nets(text: str) -> bool:
    return re.search(OPEN_NETS, plain(text)) is not None


ENGLISH_NUMBERS = {"zero": 0, "no": 0, "none": 0, "one": 1, "a single": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                   "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
CHINESE_NUMBERS = {"零": 0, "无": 0, "没有": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
                   "九": 9, "十": 10}
DIGITS = r"(?<![\d.])\d+(?:\.\d+)?(?![\d.]*\d)"
ENGLISH = r"(?<![a-z])(?:" + "|".join(sorted(ENGLISH_NUMBERS, key=len, reverse=True)) + r")(?![a-z])"
CHINESE = "(?:" + "|".join(sorted(CHINESE_NUMBERS, key=len, reverse=True)) + ")"
UNITS = r"(?:个|张|条|层|处|项|页)?"
# Nouns whose own count may stand right before another one, as in "Errors: 0 Warnings: 2".
COUNTED = "error|warning|sheet|component|part|net|layer|hazard|错误|警告|页|元件|器件|网络|层"


def _cells(text: str) -> str:
    """The text in lower case without Markdown emphasis; table bars and line ends become ";", so
    that a count in one cell is not read as the count of the next one."""
    text = re.sub(r"[*`#>]+", " ", text.lower())
    return re.sub(r"[ \t]+", " ", re.sub(r"[|\n]+", " ; ", text))


def _value(token: str) -> float:
    token = token.strip()
    if token in ENGLISH_NUMBERS:
        return ENGLISH_NUMBERS[token]
    if token in CHINESE_NUMBERS:
        return CHINESE_NUMBERS[token]
    return float(token)


def says_count(text: str, number: int | float | None, *words: str) -> bool:
    """Whether the text gives ``number`` as the count of one of ``words``: "2 warnings", "no
    errors", "0 个错误", or "warnings: 2", "Errors | none", "警告 2 个". A count written before the
    word decides when there is one, so "0 errors and 2 warnings" gives 0 errors, not 2."""
    if number is None:
        return False
    cells = _cells(text)
    for word in words:
        w = re.escape(word.lower())
        before = [_value(m.group(1)) for m in re.finditer(rf"({DIGITS}|{ENGLISH}|{CHINESE}) ?{UNITS} ?{w}", cells)
                  if not re.search(rf"(?:{COUNTED})[a-z]*\s*[:：=]\s*$", cells[:m.start()])]
        after = [_value(m.group(1)) for m in re.finditer(rf"{w}[^\d]{{0,24}}?({DIGITS}|{ENGLISH}|无|没有)", cells)]
        if any(abs(v - number) < 1e-9 for v in (before or after)):
            return True
    return False


def says_number(text: str, number: int | float | None, tolerance: float = 0.06) -> bool:
    """Whether the text gives the number, as written or rounded: 101.6 matches 101.6 and 102,
    and 7 does not match 70."""
    if number is None:
        return False
    for token in re.findall(r"(?<![\d.])\d+(?:\.\d+)?", plain(text)):
        value = float(token)
        if abs(value - number) <= tolerance or value == round(number) or value == round(number, 1):
            return True
    return False
