package com.precam.kpssturkce

import android.content.Context
import org.json.JSONArray
import kotlin.random.Random

/**
 * Soru bankasını assets/questions.json dosyasından yükler ve
 * rastgele soru seçimini yönetir.
 *
 * Aynı soruyu üst üste vermemek için son verilen sorunun indeksini saklar.
 */
class QuestionRepository(context: Context) {

    val questions: List<Question> = loadFromAssets(context)
    private var lastIndex: Int = -1

    val size: Int get() = questions.size

    /** Bankadan, mümkünse bir öncekinden farklı, rastgele bir soru döndürür. */
    fun random(): Question {
        if (questions.isEmpty()) error("Soru bankası boş.")
        if (questions.size == 1) return questions[0]
        var idx: Int
        do {
            idx = Random.nextInt(questions.size)
        } while (idx == lastIndex)
        lastIndex = idx
        return questions[idx]
    }

    private fun loadFromAssets(context: Context): List<Question> {
        val raw = context.assets.open("questions.json")
            .bufferedReader(Charsets.UTF_8)
            .use { it.readText() }
        val arr = JSONArray(raw)
        val result = ArrayList<Question>(arr.length())
        for (i in 0 until arr.length()) {
            val o = arr.getJSONObject(i)
            val optsArr = o.getJSONArray("options")
            val opts = ArrayList<String>(optsArr.length())
            for (j in 0 until optsArr.length()) opts.add(optsArr.getString(j))
            result.add(
                Question(
                    id = o.getInt("id"),
                    topic = o.optString("topic", "Türkçe"),
                    text = o.getString("text"),
                    options = opts,
                    answerIndex = o.getInt("answerIndex"),
                    explanation = o.optString("explanation", "")
                )
            )
        }
        return result
    }
}
