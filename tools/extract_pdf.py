# -*- coding: utf-8 -*-
"""KPSS çıkmış soru PDF'lerinden questions.json üretir.

BU ARAÇ İÇERİK SAĞLAMAZ. Yalnızca, SİZİN sağladığınız bir PDF dosyasını ayrıştırır.
Kullandığınız PDF'in dağıtım/çoğaltma hakkına sahip olduğunuzdan emin olun; telif
sorumluluğu dosyayı sağlayan kullanıcıya aittir.

Kullanım:
    pip install pdfplumber
    python3 tools/extract_pdf.py SORULAR.pdf --answers "1A 2C 3D ..." -o questions.json
    # veya cevap anahtarını dosyadan:
    python3 tools/extract_pdf.py SORULAR.pdf --answers-file cevaplar.txt
    # cevap anahtarı PDF'in kendi içindeyse (deneysel):
    python3 tools/extract_pdf.py SORULAR.pdf --answers-in-pdf

Notlar:
- PDF düzeni çok değişken olduğundan çıktı her zaman gözden geçirilmelidir.
- Sadece A–E şıklı, numaralandırılmış (1., 2., ...) sorular yakalanır.
- --topic ile tüm sorulara konu etiketi verebilirsiniz (vars: "Türkçe").
"""
import argparse
import json
import re
import sys

OPTION_LETTERS = "ABCDE"


def extract_text(pdf_path, pages=None):
    """PDF'ten sayfa sayfa metin çıkarır (pdfplumber, yoksa pypdf)."""
    try:
        import pdfplumber
        out = []
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                if pages and i not in pages:
                    continue
                out.append(page.extract_text() or "")
        return "\n".join(out)
    except ImportError:
        pass
    try:
        import pypdf
        reader = pypdf.PdfReader(pdf_path)
        out = []
        for i, page in enumerate(reader.pages):
            if pages and i not in pages:
                continue
            out.append(page.extract_text() or "")
        return "\n".join(out)
    except ImportError:
        sys.exit("PDF kütüphanesi yok. Kurun: pip install pdfplumber")


def normalize(text):
    # Tireyle bölünmüş satır sonlarını birleştir, fazla boşlukları sadeleştir
    text = text.replace("\r", "\n")
    text = re.sub(r"-\n(?=\w)", "", text)        # kelime-\n bölünmesi
    text = re.sub(r"[ \t]+", " ", text)
    return text


# "1." veya "1)" ile satır başında başlayan soru numaraları
Q_SPLIT = re.compile(r"(?m)^\s*(\d{1,3})[\.\)]\s+")
# Şık başlangıçları: A) A. A]  (satır içi de olabilir)
OPT_SPLIT = re.compile(r"(?<![A-Za-zÇĞİÖŞÜçğıöşü])([A-E])[\)\.\]]\s")


def parse_questions(text):
    text = normalize(text)
    parts = Q_SPLIT.split(text)
    # parts: [önyazı, '1', blok1, '2', blok2, ...]
    questions = []
    for idx in range(1, len(parts), 2):
        num = int(parts[idx])
        block = parts[idx + 1] if idx + 1 < len(parts) else ""
        q = parse_block(num, block)
        if q:
            questions.append(q)
    return questions


def parse_block(num, block):
    """Bir soru bloğundan kök + A–E şıklarını ayıklar."""
    # Şıkları konumlarıyla bul
    matches = list(OPT_SPLIT.finditer(block))
    # A,B,C,D,E sırasını bul (ilk A, sonra B...)
    spans = {}
    for m in matches:
        letter = m.group(1)
        # her harfin İLK geçtiği yeri al
        if letter not in spans:
            spans[letter] = m.start()
    needed = list(OPTION_LETTERS)
    if not all(l in spans for l in needed):
        return None
    # sıralı pozisyonlar artan olmalı
    positions = [spans[l] for l in needed]
    if positions != sorted(positions):
        return None

    stem = block[: positions[0]].strip()
    options = []
    for i, letter in enumerate(needed):
        start = spans[letter]
        end = spans[needed[i + 1]] if i + 1 < len(needed) else len(block)
        seg = block[start:end]
        seg = OPT_SPLIT.sub("", seg, count=1).strip()
        # sonraki sorunun kök başlangıcı sızmışsa kırp
        seg = seg.split("\n\n")[0].strip()
        options.append(re.sub(r"\s+", " ", seg))
    stem = re.sub(r"\s+", " ", stem)
    if len(stem) < 5 or any(len(o) == 0 for o in options):
        return None
    return {"number": num, "text": stem, "options": options}


def parse_answer_key(s):
    """'1A 2C 3-D 4) E ...' gibi bir dizgeden {num: 'A'} üretir."""
    key = {}
    for m in re.finditer(r"(\d{1,3})\s*[\-\.\):]?\s*([A-Ea-e])", s):
        key[int(m.group(1))] = m.group(2).upper()
    return key


def main():
    ap = argparse.ArgumentParser(description="KPSS PDF -> questions.json")
    ap.add_argument("pdf", help="Girdi PDF dosyası (kullanıcı sağlar)")
    ap.add_argument("-o", "--out", default="questions.json")
    ap.add_argument("--answers", help="Cevap anahtarı dizgesi, ör. '1A 2C 3D'")
    ap.add_argument("--answers-file", help="Cevap anahtarını içeren metin dosyası")
    ap.add_argument("--answers-in-pdf", action="store_true",
                    help="Cevap anahtarını PDF metninden bulmayı dene (deneysel)")
    ap.add_argument("--topic", default="Türkçe", help="Tüm sorulara konu etiketi")
    ap.add_argument("--start-id", type=int, default=1)
    args = ap.parse_args()

    text = extract_text(args.pdf)
    parsed = parse_questions(text)

    key = {}
    if args.answers:
        key = parse_answer_key(args.answers)
    elif args.answers_file:
        with open(args.answers_file, encoding="utf-8") as f:
            key = parse_answer_key(f.read())
    elif args.answers_in_pdf:
        # "CEVAP ANAHTARI" sonrası bölümü tara
        m = re.search(r"CEVAP\s*ANAHTAR[^\n]*(.+)$", text, re.IGNORECASE | re.DOTALL)
        if m:
            key = parse_answer_key(m.group(1))

    out = []
    cid = args.start_id
    skipped_no_answer = 0
    for q in parsed:
        ans = key.get(q["number"])
        if ans is None:
            skipped_no_answer += 1
            continue
        out.append({
            "id": cid,
            "topic": args.topic,
            "text": q["text"],
            "options": q["options"],
            "answerIndex": OPTION_LETTERS.index(ans),
            "explanation": "",
        })
        cid += 1

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(f"Ayrıştırılan soru: {len(parsed)}")
    print(f"Cevap anahtarı eşleşen: {len(out)} -> {args.out}")
    if skipped_no_answer:
        print(f"Cevabı bulunamadığı için atlanan: {skipped_no_answer}")
    if not out:
        print("UYARI: Hiç soru yazılmadı. PDF düzeni farklı olabilir; "
              "--answers ile cevap anahtarı verdiğinizden emin olun.")


if __name__ == "__main__":
    main()
