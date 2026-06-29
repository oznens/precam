"use strict";

const LETTERS = ["A", "B", "C", "D", "E"];

const els = {
  topic: document.getElementById("topic"),
  question: document.getElementById("questionText"),
  options: document.getElementById("options"),
  diceBtn: document.getElementById("diceBtn"),
  answerBtn: document.getElementById("answerBtn"),
  answerBox: document.getElementById("answerBox"),
  answerLetter: document.getElementById("answerLetter"),
  answerExplain: document.getElementById("answerExplain"),
  counter: document.getElementById("counter"),
  poolInfo: document.getElementById("poolInfo"),
};

let questions = [];
let current = null;
let lastIndex = -1;
let revealed = false;
let solved = 0;

/* Bir öncekinden farklı, rastgele bir soru seç */
function pickRandom() {
  if (questions.length === 0) return null;
  if (questions.length === 1) return questions[0];
  let i;
  do {
    i = Math.floor(Math.random() * questions.length);
  } while (i === lastIndex);
  lastIndex = i;
  return questions[i];
}

function renderQuestion(q) {
  current = q;
  revealed = false;
  els.topic.textContent = q.topic || "Türkçe";
  els.question.textContent = q.text;

  els.options.innerHTML = "";
  q.options.forEach((opt, idx) => {
    const li = document.createElement("li");
    li.dataset.idx = String(idx);

    const letter = document.createElement("span");
    letter.className = "letter";
    letter.textContent = LETTERS[idx];

    const span = document.createElement("span");
    span.textContent = opt;

    li.append(letter, span);
    li.addEventListener("click", () => onPick(li, idx));
    els.options.appendChild(li);
  });

  // cevap köşesini sıfırla
  els.answerBox.hidden = true;
  els.answerBtn.textContent = "Cevabı gör";
  els.answerBtn.classList.remove("shown");
}

/* Şıkka tıklayınca doğru/yanlış işaretle */
function onPick(li, idx) {
  if (!current) return;
  const correct = current.answerIndex;
  if (idx === correct) {
    li.classList.add("correct");
  } else {
    li.classList.add("wrong");
    const right = els.options.querySelector(`li[data-idx="${correct}"]`);
    if (right) right.classList.add("correct");
  }
  solved += 1;
  updateCounter();
  showAnswer(true);
}

function showAnswer(force) {
  if (!current) return;
  revealed = force === true ? true : !revealed;
  if (revealed) {
    const c = current.answerIndex;
    els.answerLetter.textContent = `${LETTERS[c]}) ${current.options[c]}`;
    els.answerExplain.textContent = current.explanation || "";
    els.answerExplain.style.display = current.explanation ? "block" : "none";
    els.answerBox.hidden = false;
    els.answerBtn.textContent = "Gizle";
    els.answerBtn.classList.add("shown");
  } else {
    els.answerBox.hidden = true;
    els.answerBtn.textContent = "Cevabı gör";
    els.answerBtn.classList.remove("shown");
  }
}

function updateCounter() {
  els.counter.textContent = `${solved} / ${questions.length}`;
}

function newQuestion() {
  const q = pickRandom();
  if (q) renderQuestion(q);
  // zar dönme animasyonu
  els.diceBtn.classList.remove("rolling");
  void els.diceBtn.offsetWidth; // reflow -> animasyonu yeniden tetikle
  els.diceBtn.classList.add("rolling");
}

/* Olaylar */
els.diceBtn.addEventListener("click", newQuestion);
els.answerBtn.addEventListener("click", () => showAnswer(false));
document.addEventListener("keydown", (e) => {
  if (e.code === "Space") { e.preventDefault(); newQuestion(); }
  if (e.key.toLowerCase() === "c") showAnswer(false);
});

/* Başlat */
fetch("questions.json")
  .then((r) => r.json())
  .then((data) => {
    questions = data;
    els.poolInfo.textContent = `Çıkmış soru tarzı · ${questions.length} soruluk havuz`;
    updateCounter();
    newQuestion();
  })
  .catch((err) => {
    els.question.textContent = "Sorular yüklenemedi: " + err.message;
  });
