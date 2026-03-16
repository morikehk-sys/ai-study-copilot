# ===============================
# 1. Imports
# ===============================
import streamlit as st
import os, json, uuid, random, re, html
from datetime import datetime
from pypdf import PdfReader
import streamlit.components.v1 as components


# ===============================
# 2. File paths / folders
# ===============================
DATA_DIR = "data"
PDF_DIR = os.path.join(DATA_DIR, "pdfs")
NOTES_JSON = os.path.join(DATA_DIR, "notes.json")
STREAK_JSON = os.path.join(DATA_DIR, "streak_data.json")

os.makedirs(PDF_DIR, exist_ok=True)


# ===============================
# 3. Helper functions
# ===============================

def generate_flashcards_from_text(text: str, max_cards: int = 10):
    """
    FREE/local flashcards generator (no API).
    Tries to pull: definitions, key facts, and good study Q/A pairs from the note text.
    """
    if not text or not text.strip():
        return []

    # Clean up
    cleaned = re.sub(r"\s+", " ", text).strip()

    # Split into sentences (simple)
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)

    cards = []

    # 1) Definition-style cards (best quality)
    # Patterns like: "X is ...", "X are ...", "X means ..."
    def_patterns = [
        r"^(.{3,60}?)\s+is\s+(.{10,200})$",
        r"^(.{3,60}?)\s+are\s+(.{10,200})$",
        r"^(.{3,60}?)\s+means\s+(.{10,200})$",
        r"^(.{3,60}?)\s+refers to\s+(.{10,200})$",
    ]

    for s in sentences:
        s2 = s.strip().strip("•-— ")
        if len(s2) < 20 or len(s2) > 220:
            continue

        for pat in def_patterns:
            m = re.match(pat, s2, flags=re.IGNORECASE)
            if m:
                term = m.group(1).strip(" :;-")
                definition = m.group(2).strip()
                # Avoid weird "The" starts
                if len(term.split()) <= 10:
                    cards.append({
                        "q": f"What is {term}?",
                        "a": definition
                    })
                break

        if len(cards) >= max_cards:
            return cards

    # 2) Fallback: turn good sentences into Q/A (facts)
    # Example: "Culture affects how people judge others." -> Q: "What does culture affect?"
    for s in sentences:
        s2 = s.strip().strip("•-— ")
        if len(s2) < 25 or len(s2) > 180:
            continue

        # Skip if it's already used in definitions
        if any(s2 in c["a"] for c in cards):
            continue

        # Try to make a question from "X ... Y"
        words = s2.split()
        if len(words) < 6:
            continue

        # crude heuristic: question uses first ~5 words
        topic = " ".join(words[:5]).strip(",")
        cards.append({
            "q": f"Explain: {topic} ...?",
            "a": s2
        })

        if len(cards) >= max_cards:
            break

    return cards


def normalize_quiz_sentence(text: str) -> str:
    """Normalize common PDF extraction artifacts for cleaner quiz text."""
    if not text:
        return ""

    normalized = text.replace("\u00ad", "")  # soft hyphen
    normalized = normalized.replace("’", "'")
    normalized = re.sub(r"(\w)-\s+(\w)", r"\1\2", normalized)  # partic- ular -> particular
    normalized = re.sub(r"([A-Z]{2,})([a-z])", r"\1 \2", normalized)  # UKgovernment -> UK government
    normalized = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", normalized)  # theUK -> the UK
    normalized = re.sub(r"(['’]s)(?=[A-Za-z])", r"\1 ", normalized)  # government'sskill -> government's skill
    normalized = re.sub(r"[A-Za-z']{16,}", lambda m: split_glued_token(m.group(0)), normalized)
    normalized = re.sub(r"(?<=[,;:.!?])(?=\S)", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


NOISE_KEYWORDS = {
    "chapter", "learning", "lesson", "unit", "section", "page", "copyright", "isbn", "glossary",
    "vocabulary", "figure", "table", "photo", "illustration", "review", "objectives", "summary",
    "standards", "benchmark", "teacher", "student", "worksheet", "activity", "sidebar", "abstract",
    "journal", "volume", "publication", "author", "keywords"
}


COMMON_VERBS = {
    "is", "are", "was", "were", "be", "being", "been", "have", "has", "had", "do", "does", "did",
    "can", "could", "may", "might", "will", "would", "should", "suggests", "shows", "explains",
    "describes", "argues", "causes", "creates", "helps", "means", "becomes", "includes", "affects",
    "leads", "changes", "supports", "reveals"
}


def simplify_text_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def sentence_case(text: str) -> str:
    text = normalize_quiz_sentence(text).strip(" ,;:-")
    if not text:
        return ""
    return text[0].upper() + text[1:]


def looks_like_noise_line(line: str, repeated_keys=None) -> bool:
    """Filter headers, footers, chapter labels, page markers, and other PDF noise."""
    if not line:
        return True

    repeated_keys = repeated_keys or set()
    compact = normalize_quiz_sentence(line)
    if not compact:
        return True

    key = simplify_text_key(compact)
    words = compact.split()
    lower_words = [w.strip(".,;:!?()[]{}\"'").lower() for w in words]

    if key in repeated_keys:
        return True

    if re.fullmatch(r"(page\s*)?\d+(\s*of\s*\d+)?", compact.lower()):
        return True

    if re.fullmatch(r"chapter\s+\d+[a-z]?", compact.lower()):
        return True

    if any(token in {"www", "http", "https"} for token in lower_words):
        return True

    if any(keyword in compact.lower() for keyword in ["all rights reserved", "printed in", "published by"]):
        return True

    if len(words) <= 8:
        keyword_hits = sum(1 for w in lower_words if w in NOISE_KEYWORDS)
        upper_ratio = sum(1 for ch in compact if ch.isupper()) / max(sum(1 for ch in compact if ch.isalpha()), 1)
        title_like = sum(1 for w in words if w[:1].isupper()) / max(len(words), 1)
        if keyword_hits >= 1:
            return True
        if upper_ratio > 0.72:
            return True
        if title_like > 0.85 and not re.search(r"[.!?]$", compact):
            return True
        if len(words) <= 3 and not re.search(r"[.!?]$", compact):
            return True

    digit_ratio = sum(1 for ch in compact if ch.isdigit()) / max(len(compact), 1)
    if digit_ratio > 0.2:
        return True

    return False


def clean_extracted_text(text: str) -> str:
    """Keep article-style body text and remove common textbook/PDF noise."""
    if not text or not text.strip():
        return ""

    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    normalized_lines = [normalize_quiz_sentence(line) for line in raw_lines]

    line_counts = {}
    for line in normalized_lines:
        key = simplify_text_key(line)
        if not key:
            continue
        line_counts[key] = line_counts.get(key, 0) + 1

    repeated_keys = {
        key for key, count in line_counts.items()
        if count >= 2 and 1 <= len(key.split()) <= 10
    }

    paragraphs = []
    current = []

    for raw_line, normalized_line in zip(raw_lines, normalized_lines):
        if not raw_line.strip():
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue

        if looks_like_noise_line(normalized_line, repeated_keys):
            continue

        current.append(normalized_line)

    if current:
        paragraphs.append(" ".join(current))

    cleaned_paragraphs = []
    for paragraph in paragraphs:
        paragraph = normalize_quiz_sentence(paragraph)
        paragraph = re.sub(r"\s+", " ", paragraph).strip()
        if len(paragraph.split()) >= 8:
            cleaned_paragraphs.append(paragraph)

    return "\n\n".join(cleaned_paragraphs)


GLUED_WORD_HINTS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "by", "can", "did", "for", "from", "had", "has",
    "have", "in", "into", "is", "it", "its", "of", "on", "or", "that", "the", "their", "there", "these",
    "they", "this", "to", "was", "were", "what", "when", "which", "with", "would", "about", "between",
    "according", "census", "produced", "government", "skillset", "skills", "training", "body", "gender",
    "work", "segregation", "roles", "social", "review"
}


def split_glued_token(token: str) -> str:
    """Best-effort split for very long glued lowercase tokens."""
    core = token.strip(".,;:!?()[]{}\"")
    if len(core) < 16 or not re.fullmatch(r"[A-Za-z']+", core):
        return token

    lower = core.lower().replace("'", "")
    if len(lower) < 16:
        return token

    parts = []
    i = 0
    hints = sorted(GLUED_WORD_HINTS, key=len, reverse=True)
    n = len(lower)

    while i < n:
        match = next((w for w in hints if lower.startswith(w, i)), None)
        if match:
            parts.append(match)
            i += len(match)
            continue

        j = i + 1
        while j < n and not any(lower.startswith(w, j) for w in hints):
            j += 1
        parts.append(lower[i:j])
        i = j

    unknown_chunks = [p for p in parts if p not in GLUED_WORD_HINTS and len(p) > 2]
    if len(parts) < 2 or len(unknown_chunks) > 1:
        return token

    rebuilt = " ".join(parts)
    if core[0].isupper():
        rebuilt = rebuilt.capitalize()

    return token.replace(core, rebuilt, 1)


def is_readable_quiz_sentence(text: str) -> bool:
    """Reject sentences that still look heavily merged/garbled after cleanup."""
    if not text:
        return False

    words = text.split()
    if len(words) < 8:
        return False

    if any(len(w.strip(".,;:!?\"'()[]{}")) > 22 for w in words):
        return False

    space_ratio = text.count(" ") / max(len(text), 1)
    if space_ratio < 0.09:
        return False

    return True


def is_good_quiz_source_sentence(text: str) -> bool:
    """Accept body sentences with enough meaning for a comprehension question."""
    sentence = normalize_quiz_sentence(text)
    if not sentence:
        return False

    if not (45 <= len(sentence) <= 280):
        return False

    if not is_readable_quiz_sentence(sentence):
        return False

    if looks_like_noise_line(sentence):
        return False

    words = [w.strip(".,;:!?\"'()[]{}").lower() for w in sentence.split()]
    if len(words) < 9:
        return False

    verb_hits = sum(1 for w in words if w in COMMON_VERBS or w.endswith("ed") or w.endswith("ing"))
    if verb_hits < 1:
        return False

    keyword_hits = sum(1 for w in words if w in NOISE_KEYWORDS)
    if keyword_hits >= 2:
        return False

    if re.search(r"\b(chapter|lesson|unit|page|glossary|vocabulary)\b", sentence, flags=re.IGNORECASE):
        return False

    if re.search(r"\b(doi|abstract|journal|vol\.?|volume|publication|access|copyright)\b", sentence, flags=re.IGNORECASE):
        return False

    if re.search(r"\b(i|we|our|us|my)\b", sentence, flags=re.IGNORECASE):
        return False

    if sentence.count(",") > 3 or "(" in sentence or ")" in sentence:
        return False

    return True


QUIZ_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "into", "about", "between",
    "have", "has", "had", "were", "was", "are", "is", "will", "would", "could", "should",
    "their", "there", "which", "when", "what", "where", "whose", "while", "also", "than"
}


QUIZ_FILLER_WORDS = {
    "overall", "basically", "simply", "clearly", "generally", "usually", "often", "really",
    "very", "just", "kind", "sort"
}


OPTION_BAD_ENDINGS = {
    "a", "an", "and", "as", "at", "because", "by", "for", "from", "in", "into", "of", "on",
    "or", "so", "that", "the", "their", "there", "this", "to", "with", "while"
}


SUBORDINATE_STARTERS = {
    "although", "because", "before", "despite", "if", "since", "though", "unless", "until", "when", "while"
}


BAD_OPTION_STARTS = {
    "and", "because", "by", "for", "from", "if", "or", "provides", "shows", "describes",
    "explains", "suggests", "reveals", "includes", "with", "while", "whose"
}


def tokenize_for_similarity(text: str):
    return {
        w.lower().strip(".,;:!?\"'()[]{}")
        for w in text.split()
        if len(w.strip(".,;:!?\"'()[]{}")) > 2
    }


def jaccard_similarity(a: str, b: str) -> float:
    sa = tokenize_for_similarity(a)
    sb = tokenize_for_similarity(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(len(sa | sb), 1)


def clean_sentence_fragment(text: str) -> str:
    cleaned = normalize_quiz_sentence(text)
    cleaned = re.sub(r"\([^)]*\)", "", cleaned)
    cleaned = re.sub(r"https?://\S+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bdoi[:\s]*\S+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\(\s*\d{4}\s*\)", "", cleaned)
    cleaned = re.sub(r"\[\d+\]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,;:-")
    return cleaned


def trim_to_complete_phrase(text: str, max_words: int = 16) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text

    trimmed = words[:max_words]
    while trimmed and trimmed[-1].strip(".,;:!?\"'()[]{}").lower() in OPTION_BAD_ENDINGS:
        trimmed.pop()
    return " ".join(trimmed).strip(" ,;:-")


def strip_leading_subordinate_clause(text: str) -> str:
    words = text.split()
    if not words:
        return ""

    first = words[0].lower().strip(".,;:!?\"'()[]{}")
    if first not in SUBORDINATE_STARTERS:
        return text

    if "," in text:
        tail = text.split(",", 1)[1].strip()
        if tail:
            return tail

    return text


def has_meaningful_verb(text: str) -> bool:
    words = [w.strip(".,;:!?\"'()[]{}").lower() for w in text.split()]
    return any(
        w in COMMON_VERBS or w.endswith("ed") or w.endswith("ing")
        for w in words
    )


def polish_option_text(sentence: str, max_words: int = 16) -> str:
    """Rewrite extracted text into a concise, readable option sentence."""
    s = clean_sentence_fragment(sentence)
    if not s:
        return ""

    clauses = [c.strip(" ,;:-") for c in re.split(r"[;:]", s) if c.strip()]
    core = clauses[0] if clauses else s
    core = strip_leading_subordinate_clause(core)
    core = re.sub(
        r"^(however|therefore|instead|for example|for instance|in addition|on the other hand)\s*,?\s*",
        "",
        core,
        flags=re.IGNORECASE,
    )

    if "," in core:
        parts = [part.strip() for part in core.split(",") if part.strip()]
        core = max(parts, key=lambda item: len(item.split()))

    core = re.sub(r"^It\b", "The passage", core)
    core = re.sub(r"^They\b", "The passage", core)
    core = trim_to_complete_phrase(core, max_words=max_words)
    core = sentence_case(core).rstrip(".")
    if not core or not has_meaningful_verb(core):
        return ""

    return f"{core}."


def summarize_sentence_for_option(sentence: str, max_words: int = 16, as_choice: bool = True) -> str:
    polished = polish_option_text(sentence, max_words=max_words)
    if not polished:
        return ""
    return polished


def extract_focus_term(sentence: str) -> str:
    words = [w.strip(".,;:!?\"'()[]{}") for w in normalize_quiz_sentence(sentence).split()]
    for w in words[:10]:
        lw = w.lower()
        if len(w) >= 4 and lw not in QUIZ_STOPWORDS and not lw.isdigit():
            return w
    return words[0] if words else "concept"


def extract_focus_phrase(sentence: str, max_words: int = 3) -> str:
    words = [w.strip(".,;:!?\"'()[]{}") for w in normalize_quiz_sentence(sentence).split()]
    phrase = []
    for w in words[:12]:
        lw = w.lower()
        if len(w) >= 3 and lw not in QUIZ_STOPWORDS and not lw.isdigit():
            phrase.append(w)
            if len(phrase) >= max_words:
                break
    if phrase:
        return " ".join(phrase)
    return extract_focus_term(sentence)


def clean_topic_phrase(topic: str) -> str:
    topic = clean_sentence_fragment(topic)
    words = [w.strip(".,;:!?\"'()[]{}") for w in topic.split()]
    picked = []
    for word in words:
        lw = word.lower()
        if len(word) >= 3 and lw not in QUIZ_STOPWORDS and lw not in QUIZ_FILLER_WORDS and not lw.isdigit():
            picked.append(word)
        if len(picked) >= 3:
            break

    if not picked:
        return "this idea"
    return " ".join(picked)


def build_question_text(sentence: str, idx: int) -> str:
    sentence_lower = sentence.lower()

    if any(marker in sentence_lower for marker in ["because", "as a result", "therefore", "leads to", "resulted in"]):
        return "According to the passage, which outcome is described?"
    if any(marker in sentence_lower for marker in ["however", "although", "but", "while", "in contrast"]):
        return "Which statement best captures the contrast in the passage?"
    if any(marker in sentence_lower for marker in ["should", "must", "need to", "can help"]):
        return "Based on the passage, which idea is supported?"

    templates = [
        "According to the passage, which statement is most accurate?",
        "Which idea best reflects the main point of the passage?",
        "What does the passage emphasize most clearly?",
        "Which conclusion is best supported by the passage?"
    ]
    return templates[idx % len(templates)]


def build_base_explanation(sentence: str) -> str:
    snippet = summarize_sentence_for_option(sentence, max_words=18, as_choice=False).rstrip(".")
    if not snippet:
        return "This is correct because it best matches the main idea in the passage."
    snippet_body = snippet[0].lower() + snippet[1:]
    if snippet_body.startswith("the passage "):
        snippet_body = snippet_body[len("the passage "):]
    return f"This is correct because the passage supports the idea that {snippet_body}."


def is_valid_question_text(text: str) -> bool:
    normalized = clean_sentence_fragment(text)
    if not normalized or not normalized.endswith("?"):
        return False
    if len(normalized.split()) < 7 or len(normalized.split()) > 22:
        return False
    if re.search(r"\b(page|chapter|lesson|unit|figure|table)\b", normalized, flags=re.IGNORECASE):
        return False
    return True


def is_valid_option_text(text: str) -> bool:
    normalized = clean_sentence_fragment(text).rstrip(".")
    if not normalized:
        return False
    if "?" in normalized:
        return False
    words = normalized.split()
    if len(words) < 4 or len(words) > 18:
        return False
    if normalized[0].islower():
        return False
    first_word = words[0].strip(".,;:!?\"'()[]{}").lower()
    if first_word in BAD_OPTION_STARTS:
        return False
    if first_word in COMMON_VERBS and first_word not in {"is", "are"}:
        return False
    if first_word in {"are", "can", "could", "did", "do", "does", "had", "has", "have", "is", "should", "was", "were", "would"}:
        return False
    if any(len(word.strip(".,;:!?\"'()[]{}")) > 18 for word in words):
        return False
    if words[-1].strip(".,;:!?\"'()[]{}").lower() in OPTION_BAD_ENDINGS:
        return False
    if not has_meaningful_verb(normalized):
        return False
    return True


def choices_are_distinct(options) -> bool:
    option_values = list(options)
    for i in range(len(option_values)):
        for j in range(i + 1, len(option_values)):
            if jaccard_similarity(option_values[i], option_values[j]) > 0.74:
                return False
    return True


def is_valid_explanation_text(text: str) -> bool:
    normalized = clean_sentence_fragment(text)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", normalized) if s.strip()]
    return bool(normalized) and len(sentences) <= 4 and len(normalized.split()) <= 55


def build_quiz_question(sentence: str, distractor_pool: list, idx: int):
    correct_answer = polish_option_text(sentence, max_words=15)
    if not is_valid_option_text(correct_answer):
        return None

    distractors = []
    for other in distractor_pool:
        if other == sentence:
            continue
        option = polish_option_text(other, max_words=15)
        if not is_valid_option_text(option):
            continue
        if option == correct_answer:
            continue
        if jaccard_similarity(option, correct_answer) > 0.68:
            continue
        if any(jaccard_similarity(option, existing) > 0.68 for existing in distractors):
            continue
        distractors.append(option)
        if len(distractors) == 3:
            break

    if len(distractors) < 3:
        return None

    question_text = build_question_text(sentence, idx)
    if not is_valid_question_text(question_text):
        return None

    all_options = [correct_answer] + distractors
    if not choices_are_distinct(all_options):
        return None

    random.shuffle(all_options)
    correct_option = chr(65 + all_options.index(correct_answer))
    explanation = f"Correct answer: {correct_option}. {build_base_explanation(sentence)}"
    if not is_valid_explanation_text(explanation):
        return None

    return {
        "question": question_text,
        "topic": clean_topic_phrase(extract_focus_phrase(sentence)),
        "choices": {
            "A": all_options[0],
            "B": all_options[1],
            "C": all_options[2],
            "D": all_options[3]
        },
        "correct_answer": correct_option,
        "explanation": explanation,
        "source_sentence": sentence
    }


def build_feedback_explanation(question_data: dict, selected_option: str = None) -> str:
    base = normalize_quiz_sentence(question_data.get("explanation", "")).strip()
    correct_option = question_data.get("correct_answer")
    correct_text = normalize_quiz_sentence(question_data.get("choices", {}).get(correct_option, ""))

    if not selected_option or selected_option == correct_option:
        return base

    chosen_text = normalize_quiz_sentence(question_data.get("choices", {}).get(selected_option, ""))
    topic = question_data.get("topic") or clean_topic_phrase(extract_focus_phrase(correct_text))
    wrong_focus = clean_topic_phrase(extract_focus_phrase(chosen_text)) if chosen_text else "a different detail"

    comparison = (
        f" Your answer is less accurate because it focuses on {wrong_focus.lower()} instead of the passage's main point about {topic.lower()}."
    )
    combined = (base + comparison).strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", combined) if s.strip()]
    if len(sentences) > 4:
        combined = " ".join(sentences[:4])
    return combined


def generate_quiz_from_text(text: str, max_questions: int = 10):
    """
    FREE/local quiz generator (no API).
    Creates multiple-choice questions from the cleaned passage text.
    """
    if not text or not text.strip():
        return []

    cleaned = clean_extracted_text(text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)

    # Normalize + dedupe source sentences to reduce repeated options across questions.
    good_sentences = []
    seen_sentence_norm = set()
    for s in sentences:
        s = normalize_quiz_sentence(s.strip().strip("â€¢-â€” "))
        if not is_good_quiz_source_sentence(s):
            continue
        k = s.lower()
        if k not in seen_sentence_norm:
            good_sentences.append(s)
            seen_sentence_norm.add(k)

    if len(good_sentences) < 4:
        return []

    questions = []
    used_questions = set()

    for idx, sentence in enumerate(good_sentences):
        if len(questions) >= max_questions:
            break

        question = build_quiz_question(sentence, good_sentences, idx)
        if not question:
            continue

        question_key = simplify_text_key(question["question"])
        if question_key in used_questions:
            continue

        used_questions.add(question_key)
        questions.append(question)

    return questions


def render_quiz_feedback_card(title: str, body: str):
    st.markdown(
        f"""
        <div class="quiz-explanation-card">
            <div class="quiz-explanation-title">{html.escape(title)}</div>
            <div class="quiz-explanation-body">{html.escape(body)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_quiz_option_summary(question: dict, selected_option: str):
    correct_option = question["correct_answer"]
    for option_key in ["A", "B", "C", "D"]:
        state = "neutral"
        if option_key == correct_option:
            state = "correct"
        if option_key == selected_option and option_key != correct_option:
            state = "incorrect"

        st.markdown(
            f"""
            <div class="quiz-option-card quiz-option-{state}">
                <div class="quiz-option-badge">{option_key}</div>
                <div class="quiz-option-text">{html.escape(question['choices'][option_key])}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
def load_notes():
    """Load saved notes from notes.json"""
    if not os.path.exists(NOTES_JSON):
        return {}
    try:
        with open(NOTES_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}


def save_notes(notes: dict):
    """Save notes to notes.json"""
    with open(NOTES_JSON, "w", encoding="utf-8") as f:
        json.dump(notes, f, indent=2, ensure_ascii=False)


def load_streak():
    """Load streak data from streak_data.json"""
    if not os.path.exists(STREAK_JSON):
        return {"streak": 0, "last_study_date": None}
    try:
        with open(STREAK_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {"streak": 0, "last_study_date": None}


def save_streak(data: dict):
    """Save streak data to streak_data.json"""
    with open(STREAK_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def extract_text_from_pdf(uploaded_file):
    """Extract text from uploaded PDF"""
    try:
        reader = PdfReader(uploaded_file)
        text = ""
        for page in reader.pages:
            text += (page.extract_text() or "") + "\n"
        return clean_extracted_text(text)
    except Exception as e:
        st.error(f"Error extracting text from PDF: {str(e)}")
        return "Error: Could not extract text from this PDF. Please try a different file."


# ===============================
# 4. Load notes ONCE (VERY IMPORTANT)
# ===============================
if "notes" not in st.session_state:
    st.session_state.notes = load_notes()

if "streak_data" not in st.session_state:
    st.session_state.streak_data = load_streak()

# ===============================
# 5. Page config + styling
# ===============================
st.set_page_config(
    page_title="Study App",
    page_icon="📘",
    layout="wide"
)

st.markdown("""
<style>
.stApp {
    background-color: #0e1117;
    color: white;
}

/* Striped button styling */
.striped-button {
    background: linear-gradient(90deg, white 0%, white 50%, #4a90e2 50%, #4a90e2 100%);
    background-size: 20px 100%;
    background-position: 0 0;
    color: #0e1117;
    border: 2px solid #4a90e2;
    padding: 10px 24px;
    border-radius: 4px;
    font-weight: bold;
    cursor: pointer;
    transition: all 0.3s ease;
}

.striped-button:hover {
    background-position: -10px 0;
    transform: scale(1.02);
}

.quiz-shell {
    max-width: 760px;
    margin: 0 auto;
}

.quiz-question-card {
    background: linear-gradient(180deg, #161b22 0%, #11161d 100%);
    border: 1px solid #2c3340;
    border-radius: 20px;
    padding: 1.5rem;
    margin: 0.75rem 0 1rem 0;
    box-shadow: 0 14px 30px rgba(0, 0, 0, 0.18);
}

.quiz-progress-label {
    color: #94a3b8;
    font-size: 0.92rem;
    margin-bottom: 0.35rem;
}

.quiz-question-text {
    color: #f8fafc;
    font-size: 1.35rem;
    line-height: 1.45;
    font-weight: 700;
    margin: 0;
}

.quiz-section-label {
    color: #cbd5e1;
    font-size: 0.95rem;
    font-weight: 600;
    margin: 0.85rem 0 0.65rem 0;
}

.quiz-option-card {
    display: flex;
    gap: 0.9rem;
    align-items: flex-start;
    border-radius: 16px;
    border: 1px solid #2f3848;
    background: #141922;
    padding: 0.95rem 1rem;
    margin-bottom: 0.75rem;
}

.quiz-option-badge {
    width: 2rem;
    height: 2rem;
    border-radius: 999px;
    background: #243041;
    color: #f8fafc;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 700;
    flex-shrink: 0;
}

.quiz-option-text {
    color: #eef2ff;
    line-height: 1.55;
    font-size: 1rem;
}

.quiz-option-correct {
    border-color: #1f8f5f;
    background: rgba(23, 92, 61, 0.28);
}

.quiz-option-correct .quiz-option-badge {
    background: #1f8f5f;
}

.quiz-option-incorrect {
    border-color: #b54a4a;
    background: rgba(122, 37, 37, 0.26);
}

.quiz-option-incorrect .quiz-option-badge {
    background: #b54a4a;
}

.quiz-explanation-card {
    border: 1px solid #314056;
    background: #121a25;
    border-radius: 18px;
    padding: 1rem 1.1rem;
    margin-top: 1rem;
}

.quiz-explanation-title {
    color: #f8fafc;
    font-size: 1rem;
    font-weight: 700;
    margin-bottom: 0.45rem;
}

.quiz-explanation-body {
    color: #dbe4f0;
    line-height: 1.6;
}

div[data-testid="stRadio"] > label {
    display: none;
}

div[data-testid="stRadio"] div[role="radiogroup"] {
    gap: 0.8rem;
}

div[data-testid="stRadio"] div[role="radiogroup"] label {
    border: 1px solid #2f3848;
    border-radius: 16px;
    padding: 0.95rem 1rem;
    background: #141922;
    align-items: flex-start;
    gap: 0.85rem;
    transition: border-color 0.18s ease, background 0.18s ease, transform 0.18s ease;
}

div[data-testid="stRadio"] div[role="radiogroup"] label:hover {
    border-color: #5aa2ff;
    background: #172131;
    transform: translateY(-1px);
}

div[data-testid="stRadio"] div[role="radiogroup"] label p {
    color: #eef2ff;
    font-size: 1rem;
    line-height: 1.55;
}

div[data-testid="stRadio"] div[role="radiogroup"] label > div:first-child {
    margin-top: 0.2rem;
}

div[data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked) {
    border-color: #5aa2ff;
    background: #18283d;
    box-shadow: 0 0 0 1px rgba(90, 162, 255, 0.28);
}

div[data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked) p {
    color: #ffffff;
}

div[data-testid="stRadio"] div[role="radiogroup"] label > div:first-child [data-testid="stMarkdownContainer"] {
    display: none;
}
</style>
""", unsafe_allow_html=True)


# ===============================
# 6. Sidebar navigation
# ===============================
st.sidebar.title("📚 Study Menu")
option = st.sidebar.radio(
    "Choose a section:",
    ["Home", "Upload Notes", "Study Mode"]
)


# ===============================
# 7. Home Page
# ===============================
st.markdown('<div id="study-top"></div>', unsafe_allow_html=True)
st.title("Study App 🚀")
st.markdown('<div id="top"></div>', unsafe_allow_html=True)

# Display streak
streak = st.session_state.streak_data.get("streak", 0)
day_word = "day" if streak == 1 else "days"
if streak > 0:
    st.markdown(f"🔥 **Study Streak: {streak} {day_word}**")
elif streak == 0 and st.session_state.streak_data.get("last_study_date"):
    st.markdown("🔥 **Study Streak: 0 days** (Start studying to build a streak!)")
else:
    st.markdown("🔥 **Study Streak: 0 days**")

if option == "Home":
    st.write("Upload your notes and study smarter.")
    
    with st.expander("📖 Quick Start Guide"):
        st.write("""
        **Welcome to Study App!** Here's how to get started:
        
        1. **Upload Notes**: Go to 'Upload Notes' and select a PDF file. Give it a title and upload.
        2. **Generate Content**: In 'Study Mode', select your note and generate flashcards or quizzes.
        3. **Study**: Review flashcards or take quizzes to test your knowledge.
        
        Features:
        - Automatic text extraction from PDFs
        - AI-generated flashcards and quizzes
        - Progress tracking and feedback
        - Persistent storage (notes saved locally)
        """)


# ===============================
# 8. Upload Notes Page
# ===============================
elif option == "Upload Notes":
    st.subheader("Upload a PDF")

    title = st.text_input("Name this note", value="My Notes")
    uploaded_file = st.file_uploader("Choose a PDF file", type=["pdf"])

    if uploaded_file:
        note_id = uuid.uuid4().hex[:10]

        # Save PDF file
        pdf_path = os.path.join(PDF_DIR, f"{note_id}.pdf")
        with open(pdf_path, "wb") as f:
            f.write(uploaded_file.getbuffer())

        # Extract text
        extracted_text = extract_text_from_pdf(uploaded_file)

        # Save note data
        notes = st.session_state.notes
        notes[note_id] = {
            "title": title,
            "created_at": datetime.now().strftime("%Y-%m-%d %I:%M %p"),
            "pdf_path": pdf_path,
            "text": extracted_text,
            "flashcards": [],
            "quiz": [],
            "quiz_history": []
        }

        st.session_state.notes = notes
        save_notes(notes)

        st.success("Saved ✅ (This will still be here after refresh)")
        if st.button("📚 Go to Study Mode", use_container_width=True):
            st.session_state.page = "Study Mode"
            st.rerun()
        st.text_area(
            "Extracted Text (preview)",
            extracted_text[:4000],
            height=250
        )


# ===============================
# 9. Study Mode (THIS FIXES YOUR ISSUE)
# ===============================
elif option == "Study Mode":
    st.subheader("📚 Study Session")

    notes = st.session_state.notes

    if not notes:
        st.info("No saved notes yet. Upload one first!")
    else:
        # ===============================
        # Mini Stats Bar
        # ===============================
        # Calculate stats
        total_notes = len(notes)
        total_quizzes = sum(len(note.get("quiz_history", [])) for note in notes.values())
        
        # Average score
        all_percentages = []
        for note in notes.values():
            for attempt in note.get("quiz_history", []):
                all_percentages.append(attempt.get("percentage", 0))
        avg_score = round(sum(all_percentages) / len(all_percentages), 1) if all_percentages else 0
        
        streak = st.session_state.streak_data.get("streak", 0)
        
        # Display stats in columns
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("📝 Total Notes", total_notes)
        with col2:
            st.metric("⭐ Total Quizzes", total_quizzes)
        with col3:
            st.metric("📊 Avg Score", f"{avg_score}%")
        with col4:
            day_word = "day" if streak == 1 else "days"
            st.metric("🔥 Study Streak", f"{streak} {day_word}")
        
        st.divider()
        
        # ===============================
        # Note Selection Dropdown
        # ===============================
        note_list = list(reversed(list(notes.items())))  # Show newest first
        note_options = {note.get('title', 'Untitled'): note_id for note_id, note in note_list}
        
        selected_title = st.selectbox(
            "Select a note to study:",
            options=list(note_options.keys()),
            key="study_mode_note_select"
        )
        
        if selected_title:
            sid = note_options[selected_title]
            note = notes[sid]
            
            # ===============================
            # Note Header
            # ===============================
            st.divider()
            col1, col2 = st.columns([3, 1])
            with col1:
                st.markdown(f"### 📄 {note.get('title', 'Untitled')}")
            with col2:
                st.caption(f"📅 {note.get('created_at', 'unknown')}")
            
            # ===============================
            # Extracted Text Preview
            # ===============================
            with st.expander("👁️ Preview Extracted Text", expanded=False):
                st.text_area(
                    "Text",
                    note.get("text", ""),
                    height=300,
                    disabled=True
                )
            
            # ===============================
            # Quick Action Buttons
            # ===============================
            st.write("---")
            st.write("**Quick Actions:**")
            
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                if st.button("🎯 Generate Flashcards", key=f"gen_{sid}", use_container_width=True):
                    with st.spinner("Generating flashcards..."):
                        flashcards = generate_flashcards_from_text(note["text"], max_cards=10)
                    notes[sid]["flashcards"] = flashcards
                    st.session_state.notes = notes
                    save_notes(notes)
                    st.success(f"✅ Generated {len(flashcards)} flashcards!")
                    st.markdown('[📇 Go to Flashcards](#study-tabs)')
                    st.rerun()
            
            with col2:
                if st.button("📝 Generate Quiz", key=f"quiz_gen_{sid}", use_container_width=True):
                    with st.spinner("Generating quiz..."):
                        quiz_questions = generate_quiz_from_text(note["text"], max_questions=10)
                    notes[sid]["quiz"] = quiz_questions
                    st.session_state.notes = notes
                    save_notes(notes)
                    # Initialize quiz state
                    st.session_state[f"quiz_started_{sid}"] = True
                    st.session_state[f"quiz_current_{sid}"] = 0
                    st.session_state[f"quiz_score_{sid}"] = 0
                    st.session_state[f"quiz_answers_{sid}"] = {}
                    st.session_state[f"quiz_submitted_{sid}"] = False
                    st.session_state[f"quiz_results_saved_{sid}"] = False
                    st.success(f"✅ Generated {len(quiz_questions)} questions!")
                    st.markdown('[🎯 Go to Quiz](#study-tabs)')
                    st.rerun()
            
            with col3:
                if note.get("flashcards"):
                    st.metric("📇 Flashcards", len(note["flashcards"]))
                else:
                    st.write("")
            
            with col4:
                if note.get("quiz"):
                    st.metric("❓ Quiz", len(note["quiz"]))
                else:
                    st.write("")
            
            # ===============================
            # Study Tabs (FLASHCARDS | QUIZ)
            # ===============================
            if note.get("flashcards") or note.get("quiz"):
                st.divider()
                st.markdown('<div id="study-tabs"></div>', unsafe_allow_html=True)
                
                tabs_list = []
                if note.get("flashcards"):
                    tabs_list.append("📇 Flashcards")
                if note.get("quiz"):
                    tabs_list.append("🎯 Quiz")
                if note.get("quiz_history") and len(note["quiz_history"]) > 0:
                    tabs_list.append("📊 Progress History")
                
                if tabs_list:
                    tabs = st.tabs(tabs_list)
                    
                    tab_idx = 0
                    
                    # ===============================
                    # TAB: FLASHCARDS
                    # ===============================
                    if note.get("flashcards"):
                        with tabs[tab_idx]:
                            st.subheader("Review Your Flashcards")
                            st.write(f"**{len(note['flashcards'])} cards**")
                            st.write("---")
                            
                            for idx, card in enumerate(note["flashcards"]):
                                with st.expander(f"Card {idx+1}: {card['q'][:60]}..."):
                                    st.write(f"**Q:** {card['q']}")
                                    st.write(f"**A:** {card['a']}")
                        
                        tab_idx += 1
                    
                    # ===============================
                    # TAB: QUIZ
                    # ===============================
                    if note.get("quiz"):
                        with tabs[tab_idx]:
                            st.subheader("Take Your Quiz")
                            
                            # Initialize quiz state if not already done
                            if f"quiz_started_{sid}" not in st.session_state:
                                st.session_state[f"quiz_started_{sid}"] = False
                                st.session_state[f"quiz_current_{sid}"] = 0
                                st.session_state[f"quiz_score_{sid}"] = 0
                                st.session_state[f"quiz_answers_{sid}"] = {}
                                st.session_state[f"quiz_submitted_{sid}"] = False
                                st.session_state[f"quiz_results_saved_{sid}"] = False
                            
                            # Check if quiz is in progress
                            if st.session_state.get(f"quiz_started_{sid}"):
                                current_q_idx = st.session_state[f"quiz_current_{sid}"]
                                quiz_list = note["quiz"]
                                total_q = len(quiz_list)
                                
                                # Check if quiz is complete
                                if current_q_idx >= total_q:
                                    st.markdown('<div id="quiz-results"></div>', unsafe_allow_html=True)
                                    st.success(f"🎉 Quiz Complete!")
                                    final_score = st.session_state[f"quiz_score_{sid}"]
                                    percentage = round((final_score / total_q) * 100)
                                    
                                    # Feedback
                                    if percentage >= 90:
                                        feedback = "Excellent work"
                                    elif percentage >= 70:
                                        feedback = "Good progress"
                                    else:
                                        feedback = "Needs review"
                                    st.write(f"**Feedback:** {feedback}")
                                    
                                    # Metrics
                                    col1, col2 = st.columns(2)
                                    with col1:
                                        st.metric("Correct answers", final_score)
                                    with col2:
                                        st.metric("Incorrect answers", total_q - final_score)
                                    
                                    # Save to history once per completed run
                                    if not st.session_state.get(f"quiz_results_saved_{sid}", False):
                                        history_entry = {
                                            "date": datetime.now().isoformat(),
                                            "score": final_score,
                                            "percentage": percentage,
                                            "total": total_q
                                        }
                                        if "quiz_history" not in note:
                                            note["quiz_history"] = []
                                        note["quiz_history"].append(history_entry)
                                        notes = st.session_state.notes
                                        notes[sid] = note
                                        st.session_state.notes = notes
                                        save_notes(notes)
                                        st.session_state[f"quiz_results_saved_{sid}"] = True
                                    
                                    # Update streak
                                    current_date = datetime.now().date().isoformat()
                                    streak_data = st.session_state.streak_data
                                    last_date_str = streak_data.get("last_study_date")
                                    if last_date_str:
                                        last_date = datetime.fromisoformat(last_date_str).date()
                                        days_diff = (datetime.now().date() - last_date).days
                                        if days_diff == 1:
                                            streak_data["streak"] += 1
                                        elif days_diff > 1:
                                            streak_data["streak"] = 1
                                        # if 0, keep same
                                    else:
                                        streak_data["streak"] = 1
                                    streak_data["last_study_date"] = current_date
                                    st.session_state.streak_data = streak_data
                                    save_streak(streak_data)
                                    
                                    # Strengths vs Weak Areas
                                    answers = st.session_state[f"quiz_answers_{sid}"]
                                    correct_topics = []
                                    missed_concepts = []
                                    for idx, q in enumerate(quiz_list):
                                        user_answer = answers.get(idx)
                                        correct = q["correct_answer"]
                                        is_correct = user_answer == correct
                                        topic = q.get("topic", f"Question {idx+1}")
                                        if is_correct:
                                            correct_topics.append(topic)
                                        else:
                                            missed_concepts.append(topic)
                                    
                                    st.subheader("Strengths vs Weak Areas")
                                    col1, col2 = st.columns(2)
                                    with col1:
                                        st.write("**Correct topics:**")
                                        for t in correct_topics:
                                            st.write(f"- {t}")
                                    with col2:
                                        st.write("**Missed concepts:**")
                                        for t in missed_concepts:
                                            st.write(f"- {t}")
                                    
                                    # Show detailed results
                                    st.subheader("Results Summary")
                                    answers = st.session_state[f"quiz_answers_{sid}"]
                                    
                                    for idx, q in enumerate(quiz_list):
                                        user_answer = answers.get(idx)
                                        correct = q["correct_answer"]
                                        is_correct = user_answer == correct
                                        
                                        emoji = "✅" if is_correct else "❌"
                                        with st.expander(f"{emoji} Q{idx+1}: {q['question'][:50]}..."):
                                            st.write(f"**Question:** {q['question']}")
                                            st.write(f"**Your answer:** {user_answer} - {normalize_quiz_sentence(q['choices'].get(user_answer, 'N/A'))}")
                                            st.write(f"**Correct answer:** {correct} - {normalize_quiz_sentence(q['choices'][correct])}")
                                            st.write(f"**Explanation:** {build_feedback_explanation(q, user_answer)}")
                                    
                                    st.write("---")
                                    # Retake button
                                    if st.button("🔄 Retake Quiz", use_container_width=True):
                                        st.session_state[f"quiz_current_{sid}"] = 0
                                        st.session_state[f"quiz_score_{sid}"] = 0
                                        st.session_state[f"quiz_answers_{sid}"] = {}
                                        st.session_state[f"quiz_submitted_{sid}"] = False
                                        st.session_state[f"quiz_results_saved_{sid}"] = False
                                        st.rerun()
                                else:
                                    # Show current question
                                    q = quiz_list[current_q_idx]
                                    choice_key = f"quiz_choice_{sid}_{current_q_idx}"
                                    submitted = st.session_state.get(f"quiz_submitted_{sid}", False)
                                    center_left, center_mid, center_right = st.columns([1, 2.8, 1])

                                    with center_mid:
                                        progress = (current_q_idx) / total_q
                                        st.progress(progress)
                                        st.markdown(
                                            f"""
                                            <div class="quiz-shell">
                                                <div class="quiz-question-card">
                                                    <div class="quiz-progress-label">Question {current_q_idx + 1} of {total_q}</div>
                                                    <p class="quiz-question-text">{html.escape(q['question'])}</p>
                                                    <div class="quiz-section-label">Choose the best answer.</div>
                                                </div>
                                            </div>
                                            """,
                                            unsafe_allow_html=True,
                                        )

                                        if submitted:
                                            selected = st.session_state[f"quiz_answers_{sid}"].get(current_q_idx)
                                            render_quiz_option_summary(q, selected)
                                            is_correct = selected == q["correct_answer"]
                                            if is_correct:
                                                st.success("✅ Correct.")
                                            else:
                                                st.error(f"❌ Not quite. The correct answer is {q['correct_answer']}.")
                                            render_quiz_feedback_card("Why this is correct", build_feedback_explanation(q, selected))

                                            next_label = "See Results" if current_q_idx == total_q - 1 else "Next Question →"
                                            if st.button(next_label, key=f"quiz_next_{sid}_{current_q_idx}", use_container_width=True):
                                                st.session_state[f"quiz_current_{sid}"] += 1
                                                st.session_state[f"quiz_submitted_{sid}"] = False
                                                st.session_state.pop(choice_key, None)
                                                st.rerun()
                                        else:
                                            selected = st.radio(
                                                "Choose an answer:",
                                                options=["A", "B", "C", "D"],
                                                format_func=lambda x: f"{x}. {q['choices'][x]}",
                                                key=choice_key,
                                                label_visibility="collapsed"
                                            )

                                            if st.button("Submit Answer", key=f"quiz_submit_{sid}_{current_q_idx}", use_container_width=True):
                                                st.session_state[f"quiz_answers_{sid}"][current_q_idx] = selected
                                                if selected == q["correct_answer"]:
                                                    st.session_state[f"quiz_score_{sid}"] += 1
                                                st.session_state[f"quiz_submitted_{sid}"] = True
                                                st.rerun()
                            else:
                                st.write("---")
                                st.write(f"**{len(note['quiz'])} questions ready**")
                                st.write(f"Click the button below to start the quiz!")
                                
                                if st.button("▶️ Start Quiz Now", use_container_width=True):
                                    st.session_state[f"quiz_started_{sid}"] = True
                                    st.session_state[f"quiz_current_{sid}"] = 0
                                    st.session_state[f"quiz_score_{sid}"] = 0
                                    st.session_state[f"quiz_answers_{sid}"] = {}
                                    st.session_state[f"quiz_submitted_{sid}"] = False
                                    st.session_state[f"quiz_results_saved_{sid}"] = False
                                    st.rerun()
                        
                        tab_idx += 1
                    
                    # ===============================
                    # TAB: PROGRESS HISTORY
                    # ===============================
                    if note.get("quiz_history") and len(note["quiz_history"]) > 0:
                        with tabs[tab_idx]:
                            st.subheader("📊 Quiz Progress History")
                            st.write(f"**{len(note['quiz_history'])} attempts recorded**")
                            st.write("---")
                            
                            # Sort by date descending
                            history = sorted(note["quiz_history"], key=lambda x: x["date"], reverse=True)
                            
                            for attempt in history:
                                date_obj = datetime.fromisoformat(attempt["date"])
                                formatted_date = date_obj.strftime("%B %d, %Y at %I:%M %p")
                                
                                with st.expander(f"📅 {formatted_date} - {attempt['score']}/{attempt['total']} ({attempt['percentage']}%)"):
                                    col1, col2 = st.columns(2)
                                    with col1:
                                        st.metric("Score", f"{attempt['score']}/{attempt['total']}")
                                    with col2:
                                        st.metric("Percentage", f"{attempt['percentage']}%")
                        
                        tab_idx += 1
                st.info("👆 Generate Flashcards or Quiz to get started!")
        
        # Back to top button
        st.divider()
        col1, col2, col3 = st.columns([1, 1, 1])
        with col2:
            if "scroll_to_top" not in st.session_state:
                st.session_state.scroll_to_top = False
            if st.button("⬆️ Back to Top", use_container_width=True):
                st.session_state.scroll_to_top = True
            if st.session_state.scroll_to_top:
                components.html("""
                <script>
                setTimeout(() => {
                    const el = parent.document.querySelector('#study-top');
                    if (el) el.scrollIntoView({behavior: 'smooth', block: 'start'});
                }, 400);
                </script>
                """, height=0)
                st.session_state.scroll_to_top = False

