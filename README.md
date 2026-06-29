# KPSS Türkçe — Çıkmış Soru Pratiği (Web Sitesi)

KPSS **Lisans / Türkçe** için basit, hızlı bir soru pratiği sitesi. Saf HTML/CSS/JS;
sunucu gerektirmez, **GitHub Pages**'te statik olarak yayınlanır.

Site açılır açılmaz havuzdan **rastgele bir soru** gösterir. Soldaki **zara** her
basışta yeni bir rastgele soru gelir; sorunun **cevabı sağ alt köşedeki** kutudan
görünür (doğru şık + kısa açıklama). Şıklara tıklayıp kendini de sınayabilirsin.

> **1059 soruluk havuz** — yalnızca KPSS Lisans Türkçe konuları.

## Özellikler

- 🎲 **Soldaki zar** — dönen animasyonla yeni rastgele soru getirir (aynı soruyu üst üste vermez). Klavyede **Boşluk** tuşu da zar atar.
- ✅ **Sağ alt köşede cevap** — "Cevabı gör" ile doğru şık ve kısa açıklama açılır. Klavyede **C** tuşu da çalışır.
- 🖱️ Şıklara tıklayınca doğru/yanlış anında renklenir, çözülen soru sayacı artar.
- 📚 Konular: sözcükte anlam (eş/zıt anlam), **deyimler**, **atasözleri**, ses bilgisi
  (yumuşama, ünlü düşmesi, daralma, benzeşme), yazım, noktalama, sözcük türleri,
  fiil/fiilimsi/çatı, cümlenin ögeleri, cümle türleri, anlatım bozukluğu, paragraf.
- İnternetsiz çalışır; tüm sorular `questions.json` içinde yereldir.

## Sorular nereden geliyor?

Telifli sınav içeriğini toplu kopyalamak yerine, KPSS Türkçe konularını kapsayan
**özgün, doğruluğu denetlenmiş** sorular üretildi:

- `data/curated.json` — elle yazılmış örnek sorular (paragraf, anlatım bozukluğu, ögeler vb.).
- `tools/lexicon.py` — eş/zıt anlamlı sözcük çiftleri.
- `tools/idioms.py` — deyimler ve anlamları.
- `tools/proverbs.py` — atasözleri ve anlamları.
- `tools/phonetics.py` — ses olayları için sözcük listeleri.

`tools/generate.py` bu kaynakları çoktan seçmeli sorulara dönüştürür, doğru cevabın
**tek** olmasını (çeldiricilerin gerçekten yanlış olmasını) garanti eder ve kök dizine
`questions.json` yazar.

### Havuzu yeniden üretmek / genişletmek

```bash
# ilgili sözlüğe yeni satır ekle (ör. tools/idioms.py)
python3 tools/generate.py     # questions.json'u yeniden üretir
```

`tools/generate.py` çıktının sonunda toplam soru sayısını ve konu dağılımını yazar.

## Yerelde çalıştırma

`fetch` kullanıldığından dosyayı `file://` ile değil küçük bir sunucuyla aç:

```bash
python3 -m http.server 8000
# tarayıcıda: http://localhost:8000
```

## GitHub Pages'te yayınlama

İki yol var:

**A) Otomatik (önerilen).** Depoda `.github/workflows/pages.yml` hazır.
`Settings → Pages → Build and deployment → Source` kısmından **GitHub Actions**'ı seç.
Bu dala (veya `main`) her push'ta site otomatik yayımlanır.

**B) Klasik.** `Settings → Pages → Source: Deploy from a branch` seç, dal olarak bu dalı
ve klasör olarak `/ (root)` seç.

Yayımlanınca adres şu biçimde olur: `https://oznens.github.io/precam/`

## Dosya yapısı

```
index.html        # arayüz (zar + soru kartı + cevap köşesi)
style.css         # stiller
app.js            # rastgele seçim, cevap gösterme, sayaç
questions.json    # üretilmiş soru havuzu (1059 soru)
data/curated.json # elle yazılmış sorular
tools/            # soru üretici ve kürasyonlu sözlükler
```
