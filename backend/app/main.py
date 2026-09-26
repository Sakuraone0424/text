from fastapi import FastAPI
from pydantic import BaseModel
from typing import Dict, List

app = FastAPI(title="USTC Adaptive Quiz")

QUESTIONS = [
    {
        "id": 1,
        "subject": "biochemistry",
        "stem": "竞争性抑制剂对酶动力学参数的典型影响是？",
        "options": {
            "A": "Km降低，Vmax降低",
            "B": "Km升高，Vmax不变",
            "C": "Km不变，Vmax降低",
            "D": "Km和Vmax均不变",
        },
        "answer": "B",
        "explanation": "竞争性抑制剂与底物竞争酶活性中心，可通过增加底物浓度克服，因此Vmax不变，而表观Km升高。"
    },
    {
        "id": 2,
        "subject": "cell_biology",
        "stem": "真核细胞中蛋白质翻译主要发生在？",
        "options": {
            "A": "高尔基体",
            "B": "溶酶体",
            "C": "核糖体",
            "D": "过氧化物酶体",
        },
        "answer": "C",
        "explanation": "蛋白质翻译发生于核糖体，包括游离核糖体和附着于粗面内质网的核糖体。"
    },
]

class Submission(BaseModel):
    answers: Dict[int, str]

@app.get("/")
def root():
    return {"status": "ok", "service": "USTC Adaptive Quiz"}

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.get("/quiz/today")
def get_today_quiz():
    return {
        "count": len(QUESTIONS),
        "questions": [
            {
                "id": q["id"],
                "subject": q["subject"],
                "stem": q["stem"],
                "options": q["options"],
            }
            for q in QUESTIONS
        ]
    }

@app.post("/quiz/submit")
def submit_quiz(submission: Submission):
    details: List[dict] = []
    score = 0

    for q in QUESTIONS:
        user_answer = submission.answers.get(q["id"])
        correct = user_answer == q["answer"]

        if correct:
            score += 1

        details.append({
            "id": q["id"],
            "user_answer": user_answer,
            "correct_answer": q["answer"],
            "correct": correct,
            "explanation": q["explanation"],
        })

    return {
        "score": score,
        "total": len(QUESTIONS),
        "details": details,
    }
