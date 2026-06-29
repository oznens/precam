# -*- coding: utf-8 -*-
"""Ses bilgisi (ses olayları) için sözcük listeleri.

Doğru/yanlış ayrımı bilerek seçilmiştir; generate.py bu listelerden
çoktan seçmeli sorular üretir.
"""

# Ünlüyle başlayan ek alınca SONUNDAKİ sert ünsüz YUMUŞAYAN sözcükler
SOFTEN_YES = [
    "kitap", "kebap", "dolap", "hesap", "cevap", "kâğıt", "kanat", "geçit",
    "ümit", "ağaç", "kazanç", "amaç", "ihtiyaç", "renk", "ahenk", "ekmek",
    "çocuk", "sokak", "yatak", "durak", "kayık", "balık", "ayak", "börek",
    "dilek", "bilek", "tarak", "bıçak", "yaprak", "kapak", "konak", "yamaç",
]
# Ek alınca yumuşama OLMAYAN (genellikle tek heceli) sözcükler
SOFTEN_NO = [
    "top", "ip", "sap", "saç", "suç", "koç", "at", "et", "süt", "kat",
    "ot", "ok", "göç", "halk", "sırt", "üst", "park", "kurs",
]

# Ek alınca ORTA HECE ünlüsü DÜŞEN (ünlü düşmesi olan) sözcükler
DROP_YES = [
    "burun", "ağız", "alın", "boyun", "beyin", "göğüs", "karın", "omuz",
    "gönül", "akıl", "fikir", "şehir", "sabır", "oğul", "resim", "vakit",
    "kayıp", "nakit", "metin", "şükür", "koyun",
]
# Ünlü düşmesi OLMAYAN sözcükler
DROP_NO = [
    "kalem", "kitap", "defter", "masa", "okul", "ev", "araba", "pencere",
    "öğretmen", "kapı", "duvar", "bardak", "çiçek", "orman",
]

# "-yor" eki gelince ünlü DARALMASI olan fiiller (geniş ünlü -> dar)
NARROW_YES = [
    "bekle", "başla", "ağla", "kapa", "söyle", "izle", "gözle", "dinle",
    "anla", "oyna", "uğra", "topla", "kolla", "yokla", "atla", "haşla",
    "de", "ye",
]
# Ünlü daralması OLMAYAN fiiller
NARROW_NO = [
    "gel", "gör", "gül", "yaz", "koş", "sor", "bul", "gir", "sev", "vur",
    "kır", "in", "çök", "düş", "as", "kes",
]

# Ünsüz BENZEŞMESİ (sertleşme) OLAN sözcükler (sert ünsüzden sonra c/d/g -> ç/t/k)
HARDEN_YES = [
    "ağaçtan", "kitapçı", "simitçi", "seçkin", "çiçekten", "sokakta",
    "dolapta", "ağaçta", "balıkçı", "sütçü", "topçu", "aşçı", "taştan",
    "uçtan", "yutkun", "kapçık", "ipçi", "saçtan",
]
# Benzeşme OLMAYAN sözcükler (yumuşak ünsüzden sonra ek aynı kalır)
HARDEN_NO = [
    "yoldan", "kalemden", "gölde", "elden", "günden", "evden", "köyde",
    "denizden", "yıldan", "gözde", "aydan", "okuldan",
]
