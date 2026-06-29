# -*- coding: utf-8 -*-
"""KPSS Lisans Türkçe soru havuzunu üretir.

Kürasyonlu sözlüklerden (eş/zıt anlam, deyim, atasözü, ses bilgisi) doğruluğu
garanti çoktan seçmeli sorular üretir, elle yazılmış sorularla birleştirir ve
proje kökündeki questions.json dosyasına yazar.

Çalıştırma:  python3 tools/generate.py
"""
import json
import os
import random

import lexicon
import idioms
import proverbs
import phonetics

random.seed(20260629)  # tekrar üretilebilir çıktı

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

LETTERS = "ABCDE"


def assert_unique_tokens(pairs, name):
    """Çiftlerdeki her sözcüğün listede yalnızca bir kez geçtiğini doğrular."""
    seen = {}
    for a, b in pairs:
        for w in (a, b):
            key = w.strip().lower()
            if key in seen:
                raise SystemExit(
                    f"[{name}] '{w}' birden çok kez geçiyor -> belirsiz cevap riski"
                )
            seen[key] = True


def make_mc(topic, text, correct, distractor_pool, explanation, exclude=None):
    """Bir çoktan seçmeli soru sözlüğü kurar.

    correct: doğru şıkkın metni
    distractor_pool: çeldiriciler için aday metinler listesi
    exclude: çeldirici olarak ASLA kullanılmaması gereken metinler kümesi
    """
    exclude = set(exclude or ())
    exclude.add(correct)
    candidates = [d for d in distractor_pool if d not in exclude]
    distractors = random.sample(candidates, 4)
    options = distractors + [correct]
    random.shuffle(options)
    answer_index = options.index(correct)
    return {
        "topic": topic,
        "text": text,
        "options": options,
        "answerIndex": answer_index,
        "explanation": explanation,
    }


def build_synonyms():
    assert_unique_tokens(lexicon.SYNONYMS, "SYNONYMS")
    qs = []
    all_words = [b for _, b in lexicon.SYNONYMS] + [a for a, _ in lexicon.SYNONYMS]
    # bir sözcüğün tüm eş anlamlılarını dışla (iki yönlü)
    syn_of = {}
    for a, b in lexicon.SYNONYMS:
        syn_of.setdefault(a, set()).add(b)
        syn_of.setdefault(b, set()).add(a)
    for a, b in lexicon.SYNONYMS:
        ex = syn_of[a] | syn_of[b] | {a, b}
        qs.append(make_mc(
            "Sözcükte Anlam (Eş Anlam)",
            f"\"{a.capitalize()}\" sözcüğünün eş anlamlısı (anlamdaşı) aşağıdakilerden hangisidir?",
            b, all_words,
            f"\"{a.capitalize()}\" ile \"{b}\" eş anlamlı sözcüklerdir.",
            exclude=ex))
        qs.append(make_mc(
            "Sözcükte Anlam (Eş Anlam)",
            f"\"{b.capitalize()}\" sözcüğünün eş anlamlısı (anlamdaşı) aşağıdakilerden hangisidir?",
            a, all_words,
            f"\"{b.capitalize()}\" ile \"{a}\" eş anlamlı sözcüklerdir.",
            exclude=ex))
    return qs


def build_antonyms():
    assert_unique_tokens(lexicon.ANTONYMS, "ANTONYMS")
    qs = []
    all_words = [b for _, b in lexicon.ANTONYMS] + [a for a, _ in lexicon.ANTONYMS]
    ant_of = {}
    for a, b in lexicon.ANTONYMS:
        ant_of.setdefault(a, set()).add(b)
        ant_of.setdefault(b, set()).add(a)
    for a, b in lexicon.ANTONYMS:
        ex = ant_of[a] | ant_of[b] | {a, b}
        qs.append(make_mc(
            "Sözcükte Anlam (Zıt Anlam)",
            f"\"{a.capitalize()}\" sözcüğünün zıt (karşıt) anlamlısı aşağıdakilerden hangisidir?",
            b, all_words,
            f"\"{a.capitalize()}\" ile \"{b}\" zıt anlamlı sözcüklerdir.",
            exclude=ex))
        qs.append(make_mc(
            "Sözcükte Anlam (Zıt Anlam)",
            f"\"{b.capitalize()}\" sözcüğünün zıt (karşıt) anlamlısı aşağıdakilerden hangisidir?",
            a, all_words,
            f"\"{b.capitalize()}\" ile \"{a}\" zıt anlamlı sözcüklerdir.",
            exclude=ex))
    return qs


def build_idioms():
    qs = []
    deyimler = [d for d, _ in idioms.IDIOMS]
    anlamlar = [m for _, m in idioms.IDIOMS]
    for d, m in idioms.IDIOMS:
        qs.append(make_mc(
            "Deyimler",
            f"\"{d.capitalize()}\" deyiminin anlamı aşağıdakilerden hangisidir?",
            m, anlamlar,
            f"\"{d.capitalize()}\" deyimi \"{m}\" anlamına gelir.",
            exclude={m}))
        qs.append(make_mc(
            "Deyimler",
            f"Anlamı \"{m}\" olan deyim aşağıdakilerden hangisidir?",
            d, deyimler,
            f"\"{m}\" anlamı \"{d}\" deyimine aittir.",
            exclude={d}))
    return qs


def build_proverbs():
    qs = []
    sozler = [s for s, _ in proverbs.PROVERBS]
    anlamlar = [m for _, m in proverbs.PROVERBS]
    for s, m in proverbs.PROVERBS:
        qs.append(make_mc(
            "Atasözleri",
            f"\"{s}\" atasözüyle anlatılmak istenen aşağıdakilerden hangisidir?",
            m, anlamlar,
            f"\"{s}\" atasözü \"{m}\" anlamını taşır.",
            exclude={m}))
        qs.append(make_mc(
            "Atasözleri",
            f"\"{m}\" yargısını anlatan atasözü aşağıdakilerden hangisidir?",
            s, sozler,
            f"Bu anlam \"{s}\" atasözüne aittir.",
            exclude={s}))
    return qs


def build_phonetics():
    qs = []
    # Ünsüz yumuşaması
    for w in phonetics.SOFTEN_YES:
        qs.append(make_mc(
            "Ses Bilgisi (Ünsüz Yumuşaması)",
            "Aşağıdaki sözcüklerden hangisine ünlüyle başlayan bir ek getirildiğinde "
            "ünsüz yumuşaması (yumuşama) olur?",
            w, phonetics.SOFTEN_NO,
            f"\"{w}\" sözcüğü ünlüyle başlayan ek alınca sonundaki sert ünsüz yumuşar.",
            exclude=set(phonetics.SOFTEN_YES)))
    # Ünlü düşmesi
    for w in phonetics.DROP_YES:
        qs.append(make_mc(
            "Ses Bilgisi (Ünlü Düşmesi)",
            "Aşağıdaki sözcüklerden hangisine ünlüyle başlayan bir ek getirildiğinde "
            "ünlü düşmesi olur?",
            w, phonetics.DROP_NO,
            f"\"{w}\" sözcüğü ek alınca ikinci hecedeki dar ünlü düşer.",
            exclude=set(phonetics.DROP_YES)))
    # Ünlü daralması
    for w in phonetics.NARROW_YES:
        disp = w + "-"
        qs.append(make_mc(
            "Ses Bilgisi (Ünlü Daralması)",
            "Aşağıdaki fiillerden hangisine \"-yor\" eki getirildiğinde ünlü daralması olur?",
            disp, [x + "-" for x in phonetics.NARROW_NO],
            f"\"{w}-\" fiilinin geniş ünlüsü \"-yor\" ekiyle daralır.",
            exclude=set(x + "-" for x in phonetics.NARROW_YES)))
    # Ünsüz benzeşmesi
    for w in phonetics.HARDEN_YES:
        qs.append(make_mc(
            "Ses Bilgisi (Ünsüz Benzeşmesi)",
            "Aşağıdaki sözcüklerden hangisinde ünsüz benzeşmesi (sertleşmesi) vardır?",
            w, phonetics.HARDEN_NO,
            f"\"{w}\" sözcüğünde sert ünsüzden sonra gelen ek sertleşmiştir.",
            exclude=set(phonetics.HARDEN_YES)))
    return qs


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
    pool = []
    pool += load_curated()
    pool += build_synonyms()
    pool += build_antonyms()
    pool += build_idioms()
    pool += build_proverbs()
    pool += build_phonetics()

    for q in pool:
        validate(q)

    random.shuffle(pool)
    for i, q in enumerate(pool, start=1):
        q_with_id = {"id": i}
        q_with_id.update(q)
        pool[i - 1] = q_with_id

    out_path = os.path.join(ROOT, "questions.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=1)

    # konu dağılımı
    from collections import Counter
    by_topic = Counter(q["topic"] for q in pool)
    print(f"TOPLAM: {len(pool)} soru -> {out_path}")
    for t, c in sorted(by_topic.items(), key=lambda x: -x[1]):
        print(f"  {c:4d}  {t}")


if __name__ == "__main__":
    main()
