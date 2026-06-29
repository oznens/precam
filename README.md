# KPSS Türkçe — Çıkmış Soru Uygulaması

KPSS **Lisans / Türkçe** için basit ve hızlı bir soru uygulaması (Android, Kotlin + Jetpack Compose).

Uygulama açılır açılmaz havuzdan **rastgele bir soru** gösterir. Soldaki **zar** butonuna
her dokunuşta yeni bir rastgele soru gelir; sorunun **cevabını sağ alt köşedeki** butona
basarak görebilirsin (cevap şıkkı + kısa çözüm).

## Özellikler

- 🎲 **Sol taraftaki zar butonu** — dönen animasyonla yeni rastgele soru getirir (aynı soruyu üst üste vermez).
- ✅ **Sağ alt köşede cevap** — "Cevabı gör" düğmesiyle doğru şık ve kısa açıklama açılır/kapanır.
- 📚 **Geniş soru havuzu** — yalnızca KPSS Lisans Türkçe konuları:
  sözcükte/cümlede anlam, paragraf, ses bilgisi, yazım kuralları, noktalama,
  sözcükte yapı, sözcük türleri, fiil, fiilimsi, fiilde çatı, cümlenin ögeleri,
  cümle türleri ve anlatım bozukluğu.
- Tüm sorular cihazda yereldir (`app/src/main/assets/questions.json`), internet gerektirmez.

## Proje yapısı

```
app/src/main/
├── java/com/precam/kpssturkce/
│   ├── MainActivity.kt          # Compose arayüzü (zar + soru kartı + cevap köşesi)
│   ├── Question.kt              # Soru veri modeli
│   ├── QuestionRepository.kt    # JSON'dan yükleme + rastgele seçim
│   └── ui/theme/Theme.kt        # Renkler / Material 3 teması
├── assets/questions.json        # Soru bankası
└── res/                         # ikon, strings, tema
```

## Derleme ve çalıştırma

Android Studio (Hedgehog veya üzeri) ile:

1. Bu klasörü **Android Studio'da aç** (`File > Open`).
2. Gradle senkronizasyonunu bekle (AGP 8.5, Gradle 8.7, JDK 17).
3. Bir emülatör veya cihaz seçip **Run** (▶) ile çalıştır.

Komut satırından (Android SDK kuruluysa):

```bash
./gradlew assembleDebug
# çıktı: app/build/outputs/apk/debug/app-debug.apk
```

- minSdk 24 (Android 7.0), targetSdk 34.

## Yeni soru ekleme

`app/src/main/assets/questions.json` dosyasına şu biçimde nesne ekle:

```json
{
  "id": 57,
  "topic": "Paragraf",
  "text": "Soru kökü...",
  "options": ["A şıkkı", "B şıkkı", "C şıkkı", "D şıkkı", "E şıkkı"],
  "answerIndex": 2,
  "explanation": "Kısa çözüm (opsiyonel)."
}
```

- `answerIndex` 0 tabanlıdır (0 = A, 1 = B, ... 4 = E).
- `options` 5 şık içermelidir.

> Not: Sorular KPSS Türkçe çıkmış-soru tarzında hazırlanmış örneklerdir; havuzu
> dilediğin kadar genişletebilirsin.
