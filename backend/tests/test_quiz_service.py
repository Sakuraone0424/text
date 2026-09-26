from app.quiz_service import normalize_answer, score_questions


def test_normalize_answer():
    assert normalize_answer(" a ") == "A"
    assert normalize_answer("E") is None
    assert normalize_answer(None) is None


def test_score_questions():
    rows = [
        {"id": 1, "correct_answer": "B", "explanation": "x", "subject": "biochemistry", "topic": "酶学"},
        {"id": 2, "correct_answer": "C", "explanation": "y", "subject": "cell_biology", "topic": "细胞核"},
    ]
    score, details = score_questions(rows, {1: "b", 2: "A"})
    assert score == 1
    assert details[0]["correct"] is True
    assert details[1]["correct"] is False
