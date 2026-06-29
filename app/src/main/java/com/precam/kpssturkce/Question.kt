package com.precam.kpssturkce

/**
 * Tek bir KPSS Lisans Türkçe sorusu.
 *
 * @param id          benzersiz numara
 * @param topic       konu (ör. "Sözcükte Anlam", "Paragraf", "Yazım Kuralları")
 * @param text        soru kökü
 * @param options     A–E şıkları (sırayla)
 * @param answerIndex doğru şıkkın 0 tabanlı indeksi
 * @param explanation kısa çözüm / açıklama (opsiyonel)
 */
data class Question(
    val id: Int,
    val topic: String,
    val text: String,
    val options: List<String>,
    val answerIndex: Int,
    val explanation: String = ""
) {
    /** Doğru şıkkın harfi: A, B, C, D, E */
    val answerLetter: String
        get() = ('A' + answerIndex).toString()

    /** Doğru şıkkın tam metni (harf + içerik) */
    val answerText: String
        get() = "$answerLetter) ${options.getOrElse(answerIndex) { "" }}"
}
