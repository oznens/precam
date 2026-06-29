# -*- coding: utf-8 -*-
"""KPSS Lisans Türkçe soru havuzunu üretir.

Havuz, KPSS sınav formatında ELLE YAZILMIŞ özgün sorulardan oluşur
(data/curated.json). Bu betik soruları doğrular, karıştırır, numara verir ve
proje kökündeki questions.json dosyasına yazar.

Çalıştırma:  python3 tools/generate.py
"""
import json
import os
import random
from collections import Counter

random.seed(20260629)  # tekrar üretilebilir sıra

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load_curated():
    path = os.path.join(ROOT, "data", "curated.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    out = []
    for q in data:
        out.append({
            "topic": q.get("topic", "Türkçe"),
            "text": q["text"],
            "options": q["options"],
            "answerIndex": q["answerIndex"],
            "explanation": q.get("explanation", ""),
        })
    return out


def validate(q):
    assert len(q["options"]) == 5, q
    assert 0 <= q["answerIndex"] < 5, q
    assert len(set(q["options"])) == 5, ("yinelenen şık", q["text"])
    assert q["text"].strip(), q


def main():
    pool = load_curated()

    seen = set()
    for q in pool:
        validate(q)
        if q["text"] in seen:
            raise SystemExit("Yinelenen soru: " + q["text"][:60])
        seen.add(q["text"])

    random.shuffle(pool)
    for i, q in enumerate(pool, start=1):
        q_with_id = {"id": i}
        q_with_id.update(q)
        pool[i - 1] = q_with_id

    out_path = os.path.join(ROOT, "questions.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=1)

    by_topic = Counter(q["topic"] for q in pool)
    print(f"TOPLAM: {len(pool)} soru -> {out_path}")
    for t, c in sorted(by_topic.items(), key=lambda x: -x[1]):
        print(f"  {c:4d}  {t}")


if __name__ == "__main__":
    main()
