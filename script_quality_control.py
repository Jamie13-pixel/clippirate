import re


# ============================================================
# SCRIPT QUALITY CONTROL
# ============================================================

MIN_WORDS = {
    30: 45,
    45: 65,
    60: 85,
}

MAX_WORDS = {
    30: 85,
    45: 115,
    60: 150,
}


def _word_count(text: str) -> int:
    return len(
        re.findall(
            r"\b[\w'-]+\b",
            text
        )
    )


def _clean_script(text: str) -> str:

    text = str(
        text or ""
    ).strip()

    # Remove accidental Markdown code fences.
    text = re.sub(
        r"```(?:text|json)?",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = text.replace(
        "```",
        ""
    )

    # Remove production directions.
    text = re.sub(
        r"\[(?:camera|scene|visual|music|sound effect|sfx)[^\]]*\]",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"\((?:camera|scene|visual|music|sound effect|sfx)[^)]*\)",
        "",
        text,
        flags=re.IGNORECASE
    )

    # Normalize whitespace.
    text = re.sub(
        r"\s+",
        " ",
        text
    ).strip()

    return text


def _split_sentences(text: str):

    return [
        part.strip()
        for part in re.split(
            r"(?<=[.!?])\s+",
            text
        )
        if part.strip()
    ]


def _check_issues(
    topic: str,
    script: str,
    duration: int
):

    issues = []

    word_count = _word_count(
        script
    )

    minimum = MIN_WORDS.get(
        duration,
        max(30, int(duration * 1.5))
    )

    maximum = MAX_WORDS.get(
        duration,
        int(duration * 2.5)
    )

    # --------------------------------------------------------
    # EMPTY SCRIPT
    # --------------------------------------------------------

    if not script:

        issues.append(
            "Script is empty."
        )

        return issues

    # --------------------------------------------------------
    # WORD COUNT
    # --------------------------------------------------------

    if word_count < minimum:

        issues.append(
            f"Script is too short: {word_count} words "
            f"(minimum expected: {minimum})."
        )

    if word_count > maximum:

        issues.append(
            f"Script is too long: {word_count} words "
            f"(maximum expected: {maximum})."
        )

    # --------------------------------------------------------
    # PRODUCTION INSTRUCTIONS
    # --------------------------------------------------------

    production_patterns = [
        r"\bcamera\b",
        r"\bscene\b",
        r"\bcut to\b",
        r"\bzoom in\b",
        r"\bzoom out\b",
        r"\bb-roll\b",
        r"\bvisual\b",
        r"\bon screen\b",
        r"\bsound effect\b",
        r"\bsfx\b",
        r"\bmusic fades\b",
        r"\bbackground music\b",
        r"\bvoiceover\b",
        r"\bvoice-over\b",
    ]

    for pattern in production_patterns:

        if re.search(
            pattern,
            script,
            flags=re.IGNORECASE
        ):

            issues.append(
                f"Contains production instruction: {pattern}"
            )

    # --------------------------------------------------------
    # AI REFERENCES
    # --------------------------------------------------------

    ai_patterns = [
        r"\bchatgpt\b",
        r"\bartificial intelligence\b",
        r"\bai-generated\b",
        r"\bai generated\b",
        r"\blanguage model\b",
    ]

    for pattern in ai_patterns:

        if re.search(
            pattern,
            script,
            flags=re.IGNORECASE
        ):

            issues.append(
                "Script contains an unnecessary AI reference."
            )

            break

    # --------------------------------------------------------
    # EXCESSIVE REPETITION
    # --------------------------------------------------------

    words = re.findall(
        r"\b[a-zA-Z']+\b",
        script.lower()
    )

    counts = {}

    for word in words:

        counts[word] = (
            counts.get(word, 0) + 1
        )

    repeated_words = {
        word: count
        for word, count in counts.items()
        if len(word) >= 5 and count >= 6
    }

    if repeated_words:

        issues.append(
            "Script contains excessive word repetition."
        )

    # --------------------------------------------------------
    # SENTENCE COUNT
    # --------------------------------------------------------

    sentences = _split_sentences(
        script
    )

    if len(sentences) < 2:

        issues.append(
            "Script should contain multiple spoken sentences."
        )

    # --------------------------------------------------------
    # EXTREMELY LONG SENTENCES
    # --------------------------------------------------------

    for sentence in sentences:

        if _word_count(sentence) > 45:

            issues.append(
                "Script contains an excessively long sentence."
            )

            break

    return issues


# ============================================================
# PUBLIC QUALITY CONTROL FUNCTION
# ============================================================

def quality_check_script(
    topic: str,
    script_text: str,
    duration: int = 30
):

    """
    Validate a generated narration before TTS
    and video generation.
    """

    script = _clean_script(
        script_text
    )

    issues = _check_issues(
        topic,
        script,
        duration
    )

    return {
        "approved": len(issues) == 0,
        "script": script,
        "issues": issues,
        "word_count": _word_count(script),
    }