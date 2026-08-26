"""Geração de baralhos .apkg (genanki) usando os MESMOS note types que já existem
no Anki do usuário ("Múltipla Escolha Universal" e "Verdadeiro ou Falso Universal"),
com os IDs/campos/templates/CSS reais copiados via AnkiConnect em 2026-08-24 - não
os do JSON de criar_modelos_anki.json, que ficou desatualizado (ordem de campos
diferente, e "Múltipla Escolha Universal" ganhou um campo novo "Imagem_Verso" que
não está documentado em lugar nenhum).

Usar os MESMOS model IDs é o que faz o Anki mesclar as notas geradas aqui dentro
dos note types existentes ao importar o .apkg, em vez de criar um duplicado.
"""
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional

import genanki

from config.settings import settings
from utils.logger import logger

# IDs reais dos note types, obtidos via AnkiConnect (modelNamesAndIds) - não gerar
# um ID novo aqui, isso criaria um note type duplicado no Anki do usuário.
MODEL_ID_MULTIPLA_ESCOLHA = 1787544307885
MODEL_ID_VERDADEIRO_FALSO = 1787544307892

# Ordem real dos campos, obtida via AnkiConnect (modelFieldNames) - diverge do
# criar_modelos_anki.json em ambos os note types (ordem diferente, e MC tem um
# campo extra "Imagem_Verso" que não está no JSON).
FIELDS_MULTIPLA_ESCOLHA = [
    "Enunciado", "Materia", "Imagem", "Imagem_Verso", "Resposta_Correta",
    "Opcao_2", "Opcao_3", "Opcao_4", "Opcao_5", "Opcao_6", "Opcao_7", "Opcao_8",
    "Pegadinha", "Explicação", "Fonte", "Video",
]
FIELDS_VERDADEIRO_FALSO = [
    "Assertiva", "Materia", "Contexto_Enunciado", "Imagem", "Gabarito",
    "Pegadinha", "Explicação", "Fonte", "Video",
]

# Templates/CSS copiados originalmente do Anki real (modelTemplates/modelStyling
# via AnkiConnect em 2026-08-24), pra importar sem o Anki achar que o note type
# mudou de forma incompatível. Não são mais byte a byte idênticos ao original:
# ".explicacao-box" (o quadro do "💡 GABARITO COMENTADO") não tinha "color"
# próprio - herdava do body, o que ficava ILEGÍVEL quando o Anki está em modo
# noturno (o modo escuro sobrescreve a cor herdada do body, mas não alcança uma
# cor declarada diretamente no elemento) - bug real reportado em produção.
# Corrigido aqui E via updateModelStyling direto no note type já existente no
# Anki da usuária (esse arquivo sozinho não afeta cards já sincronizados).
# ".context-text"/".fonte-footer" também tiveram o cinza claro (#9a9a9a, baixo
# contraste) escurecido para #6b6b6b.
_TEMPLATE_MC_FRONT = "<div class=\"duo-wrapper\">\n  <div class=\"header-stack\">\n    <div class=\"med-badge\">MEDICINA</div>\n    <div class=\"subject-badge\" data-materia=\"{{Materia}}\">{{Materia}}</div>\n  </div>\n  <div class=\"duo-card\">\n    <h1 class=\"question-text\">{{Enunciado}}</h1>\n    {{#Imagem}}<div class=\"card-image\">{{Imagem}}</div>{{/Imagem}}\n    <div id=\"quiz-options\" class=\"options-grid\">\n      <button class=\"duo-btn\" data-id=\"1\" onclick=\"handleInstantClick(this)\">{{Resposta_Correta}}</button>\n      <button class=\"duo-btn\" data-id=\"2\" onclick=\"handleInstantClick(this)\">{{Opcao_2}}</button>\n      <button class=\"duo-btn\" data-id=\"3\" onclick=\"handleInstantClick(this)\">{{Opcao_3}}</button>\n      {{#Opcao_4}}<button class=\"duo-btn\" data-id=\"4\" onclick=\"handleInstantClick(this)\">{{Opcao_4}}</button>{{/Opcao_4}}\n      {{#Opcao_5}}<button class=\"duo-btn\" data-id=\"5\" onclick=\"handleInstantClick(this)\">{{Opcao_5}}</button>{{/Opcao_5}}\n      {{#Opcao_6}}<button class=\"duo-btn\" data-id=\"6\" onclick=\"handleInstantClick(this)\">{{Opcao_6}}</button>{{/Opcao_6}}\n      {{#Opcao_7}}<button class=\"duo-btn\" data-id=\"7\" onclick=\"handleInstantClick(this)\">{{Opcao_7}}</button>{{/Opcao_7}}\n      {{#Opcao_8}}<button class=\"duo-btn\" data-id=\"8\" onclick=\"handleInstantClick(this)\">{{Opcao_8}}</button>{{/Opcao_8}}\n    </div>\n  </div>\n</div>\n\n<script>\n(function() {\n  window.__quizLocked = false;\n\n  function ucColor(materia) {\n    var m = (materia || '').toUpperCase();\n    var map = {\n      'UC04': ['#1cb0f6', '#1899d6'], 'UC05': ['#58cc02', '#46a302'],\n      'UC06': ['#ff9600', '#d97e00'], 'UC08': ['#ce82ff', '#a568cc'],\n      'UC09': ['#ff4b4b', '#d42c2c'], 'UC10': ['#2b70c9', '#1d569e'],\n      'UC11': ['#00c2a8', '#009985'], 'UC12': ['#ff6392', '#d94b77'],\n      'UC16': ['#8a2be2', '#6a1fb0'], 'UC17': ['#4caf50', '#3d8b40'],\n      'UC21': ['#e91e63', '#c2154f'], 'UC24': ['#795548', '#5d4037'],\n      'UC29': ['#607d8b', '#455a64'], 'MT':   ['#9c27b0', '#7b1fa2']\n    };\n    for (var key in map) { if (m.indexOf(key) !== -1) return map[key]; }\n    return ['#8a2be2', '#6a1fb0'];\n  }\n  var badge = document.querySelector('.subject-badge');\n  if (badge) {\n    var colors = ucColor(badge.getAttribute('data-materia'));\n    badge.style.background = colors[0];\n    badge.style.boxShadow = '0 4px 0 ' + colors[1];\n  }\n\n  var grid = document.getElementById('quiz-options');\n  if (grid && !grid.getAttribute('data-sorted')) {\n    var btns = Array.prototype.slice.call(grid.children);\n    var btnCorreto = null;\n    for (var i = 0; i < btns.length; i++) { if (btns[i].getAttribute('data-id') === '1') { btnCorreto = btns[i]; break; } }\n    var chaveTexto = btnCorreto ? btnCorreto.textContent.replace(/[^a-zA-Z]/g, '').substring(0, 15) : 'default';\n    var orderKey = 'Ordem_' + chaveTexto;\n    var savedOrder = sessionStorage.getItem(orderKey);\n    var orderArray;\n    if (!savedOrder) {\n      btns.sort(function() { return Math.random() - 0.5; });\n      orderArray = btns.map(function(b) { return b.getAttribute('data-id'); });\n      sessionStorage.setItem(orderKey, JSON.stringify(orderArray));\n    } else {\n      orderArray = JSON.parse(savedOrder);\n      btns.sort(function(a, b) { return orderArray.indexOf(a.getAttribute('data-id')) - orderArray.indexOf(b.getAttribute('data-id')); });\n    }\n    grid.innerHTML = '';\n    btns.forEach(function(b) { grid.appendChild(b); });\n    grid.setAttribute('data-sorted', '1');\n  }\n\n  window.handleInstantClick = function(btn) {\n    if (window.__quizLocked) return;\n    window.__quizLocked = true;\n    sessionStorage.setItem('userChoiceId', btn.getAttribute('data-id'));\n    var all = document.querySelectorAll('.duo-btn');\n    all.forEach(function(b) { b.style.pointerEvents = 'none'; });\n    btn.classList.add('clicked');\n    setTimeout(function() {\n      if (typeof pycmd !== 'undefined') { pycmd('ans'); }\n      else { var a = document.getElementById('answer'); if (a) a.click(); }\n    }, 150);\n  };\n})();\n</script>"
_TEMPLATE_MC_BACK = "﻿{{FrontSide}}\n\n<div id=\"duo-footer\" class=\"footer-feedback\">\n  <div class=\"feedback-content\">\n    <div id=\"feedback-icon\" class=\"fb-icon\"></div>\n    <div class=\"fb-text-area\">\n      <div id=\"feedback-title\" class=\"fb-title\"></div>\n      <div id=\"feedback-msg\" class=\"fb-msg\"></div>\n    </div>\n  </div>\n</div>\n\n<div class=\"answer-section\">\n  <div class=\"answer-box correct-box\">✅ Resposta correta: {{Resposta_Correta}}</div>\n  {{#Imagem_Verso}}<div class=\"card-image\">{{Imagem_Verso}}</div>{{/Imagem_Verso}}\n  {{#Pegadinha}}<div class=\"pegadinha-box\"><b>⚠️ Atenção à pegadinha</b>{{Pegadinha}}</div>{{/Pegadinha}}\n  {{#Explicação}}<div class=\"explicacao-box\">{{Explicação}}</div>{{/Explicação}}\n  {{#Fonte}}<div class=\"fonte-footer\">📄 Fonte: {{Fonte}}</div>{{/Fonte}}\n  {{#Video}}<div class=\"video-wrap\"><iframe src=\"https://www.youtube.com/embed/{{Video}}\" allowfullscreen></iframe></div>{{/Video}}\n</div>\n\n<script>\n(function() {\n  var userChoiceId = sessionStorage.getItem('userChoiceId');\n  var isWin = (userChoiceId === '1');\n  var allBtns = document.querySelectorAll('.duo-btn');\n  var footer = document.getElementById('duo-footer');\n  var fbTitle = document.getElementById('feedback-title');\n  var fbMsg = document.getElementById('feedback-msg');\n  var fbIcon = document.getElementById('feedback-icon');\n\n  function playSound(type) {\n    try {\n      var ctx = new (window.AudioContext || window.webkitAudioContext)();\n      var gain = ctx.createGain();\n      gain.connect(ctx.destination);\n      if (type === 'win') {\n        var osc1 = ctx.createOscillator(); osc1.connect(gain);\n        osc1.frequency.value = 523.25;\n        gain.gain.setValueAtTime(0.1, ctx.currentTime); gain.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.5);\n        osc1.start(); osc1.stop(ctx.currentTime + 0.5);\n      } else {\n        var osc = ctx.createOscillator(); osc.connect(gain);\n        osc.type = 'sawtooth'; osc.frequency.setValueAtTime(150, ctx.currentTime);\n        gain.gain.setValueAtTime(0.1, ctx.currentTime); gain.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.3);\n        osc.start(); osc.stop(ctx.currentTime + 0.3);\n      }\n    } catch (e) {}\n  }\n\n  function createConfetti() {\n    var colors = ['#58cc02', '#1cb0f6', '#ff4b4b', '#ffc800'];\n    var container = document.createElement('div');\n    container.className = 'confetti-container';\n    document.body.appendChild(container);\n    for (var i = 0; i < 40; i++) {\n      var p = document.createElement('div');\n      p.className = 'confetti-piece';\n      p.style.background = colors[Math.floor(Math.random() * colors.length)];\n      p.style.left = (Math.random() * 100) + 'vw';\n      var size = (6 + Math.random() * 6) + 'px';\n      p.style.width = size; p.style.height = size;\n      p.style.animationDuration = (1.5 + Math.random() * 1.5) + 's';\n      container.appendChild(p);\n    }\n    setTimeout(function() { container.remove(); }, 3200);\n  }\n\n  allBtns.forEach(function(btn) {\n    var btnId = btn.getAttribute('data-id');\n    btn.classList.remove('clicked');\n    btn.style.pointerEvents = 'none';\n    if (btnId === '1') btn.classList.add('correct');\n    if (btnId === userChoiceId && !isWin) btn.classList.add('wrong');\n  });\n\n  if (footer) {\n    footer.style.display = 'block';\n    if (isWin) {\n      footer.style.background = '#d7ffb8'; footer.style.color = '#2b6a00';\n      if (fbTitle) fbTitle.innerText = 'Mandou bem!';\n      if (fbMsg) fbMsg.innerText = 'Resposta correta!';\n      if (fbIcon) fbIcon.innerText = '🎉';\n      playSound('win'); createConfetti();\n    } else {\n      footer.style.background = '#ffdfe0'; footer.style.color = '#9c1c1c';\n      if (fbTitle) fbTitle.innerText = 'Errou...';\n      if (fbMsg) fbMsg.innerText = 'A resposta certa está em verde.';\n      if (fbIcon) fbIcon.innerText = '🤕';\n      playSound('lose');\n    }\n  }\n})();\n</script>\n"
_TEMPLATE_VF_FRONT = "<div class=\"duo-wrapper\">\n  <div class=\"header-stack\">\n    <div class=\"med-badge\">MEDICINA</div>\n    <div class=\"subject-badge\" data-materia=\"{{Materia}}\">{{Materia}}</div>\n  </div>\n  <div class=\"duo-card\">\n    {{#Contexto_Enunciado}}<div class=\"context-text\">{{Contexto_Enunciado}}</div>{{/Contexto_Enunciado}}\n    <h1 class=\"question-text\">{{Assertiva}}</h1>\n    {{#Imagem}}<div class=\"card-image\">{{Imagem}}</div>{{/Imagem}}\n    <div id=\"quiz-options\" class=\"vf-grid\">\n      <button class=\"vf-btn\" data-id=\"V\" onclick=\"handleInstantClick(this)\">✅ Verdadeiro</button>\n      <button class=\"vf-btn\" data-id=\"F\" onclick=\"handleInstantClick(this)\">❌ Falso</button>\n    </div>\n  </div>\n</div>\n\n<script>\n(function() {\n  window.__quizLocked = false;\n\n  function ucColor(materia) {\n    var m = (materia || '').toUpperCase();\n    var map = {\n      'UC04': ['#1cb0f6', '#1899d6'], 'UC05': ['#58cc02', '#46a302'],\n      'UC06': ['#ff9600', '#d97e00'], 'UC08': ['#ce82ff', '#a568cc'],\n      'UC09': ['#ff4b4b', '#d42c2c'], 'UC10': ['#2b70c9', '#1d569e'],\n      'UC11': ['#00c2a8', '#009985'], 'UC12': ['#ff6392', '#d94b77'],\n      'UC16': ['#8a2be2', '#6a1fb0'], 'UC17': ['#4caf50', '#3d8b40'],\n      'UC21': ['#e91e63', '#c2154f'], 'UC24': ['#795548', '#5d4037'],\n      'UC29': ['#607d8b', '#455a64'], 'MT':   ['#9c27b0', '#7b1fa2']\n    };\n    for (var key in map) { if (m.indexOf(key) !== -1) return map[key]; }\n    return ['#8a2be2', '#6a1fb0'];\n  }\n  var badge = document.querySelector('.subject-badge');\n  if (badge) {\n    var colors = ucColor(badge.getAttribute('data-materia'));\n    badge.style.background = colors[0];\n    badge.style.boxShadow = '0 4px 0 ' + colors[1];\n  }\n\n  window.handleInstantClick = function(btn) {\n    if (window.__quizLocked) return;\n    window.__quizLocked = true;\n    sessionStorage.setItem('userChoiceId', btn.getAttribute('data-id'));\n    var all = document.querySelectorAll('.vf-btn');\n    all.forEach(function(b) { b.style.pointerEvents = 'none'; });\n    btn.classList.add('clicked');\n    setTimeout(function() {\n      if (typeof pycmd !== 'undefined') { pycmd('ans'); }\n      else { var a = document.getElementById('answer'); if (a) a.click(); }\n    }, 150);\n  };\n})();\n</script>"
_TEMPLATE_VF_BACK = "{{FrontSide}}\n\n<div id=\"duo-footer\" class=\"footer-feedback\">\n  <div class=\"feedback-content\">\n    <div id=\"feedback-icon\" class=\"fb-icon\"></div>\n    <div class=\"fb-text-area\">\n      <div id=\"feedback-title\" class=\"fb-title\"></div>\n      <div id=\"feedback-msg\" class=\"fb-msg\"></div>\n    </div>\n  </div>\n</div>\n\n<div class=\"answer-section\">\n  <div class=\"answer-box correct-box\">✅ Gabarito: {{Gabarito}}</div>\n  {{#Pegadinha}}<div class=\"pegadinha-box\"><b>⚠️ Atenção à pegadinha</b>{{Pegadinha}}</div>{{/Pegadinha}}\n  {{#Explicação}}<div class=\"explicacao-box\">{{Explicação}}</div>{{/Explicação}}\n  {{#Fonte}}<div class=\"fonte-footer\">📄 Fonte: {{Fonte}}</div>{{/Fonte}}\n  {{#Video}}<div class=\"video-wrap\"><iframe src=\"https://www.youtube.com/embed/{{Video}}\" allowfullscreen></iframe></div>{{/Video}}\n</div>\n\n<script>\n(function() {\n  var gabaritoVerdadeiro = '{{Gabarito}}'.trim() === 'Verdadeiro';\n  var userChoiceId = sessionStorage.getItem('userChoiceId');\n  var acertou = (userChoiceId === 'V' && gabaritoVerdadeiro) || (userChoiceId === 'F' && !gabaritoVerdadeiro);\n  var allBtns = document.querySelectorAll('.vf-btn');\n  var footer = document.getElementById('duo-footer');\n  var fbTitle = document.getElementById('feedback-title');\n  var fbMsg = document.getElementById('feedback-msg');\n  var fbIcon = document.getElementById('feedback-icon');\n\n  function playSound(type) {\n    try {\n      var ctx = new (window.AudioContext || window.webkitAudioContext)();\n      var gain = ctx.createGain();\n      gain.connect(ctx.destination);\n      if (type === 'win') {\n        var osc1 = ctx.createOscillator(); osc1.connect(gain);\n        osc1.frequency.value = 523.25;\n        gain.gain.setValueAtTime(0.1, ctx.currentTime); gain.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.5);\n        osc1.start(); osc1.stop(ctx.currentTime + 0.5);\n      } else {\n        var osc = ctx.createOscillator(); osc.connect(gain);\n        osc.type = 'sawtooth'; osc.frequency.setValueAtTime(150, ctx.currentTime);\n        gain.gain.setValueAtTime(0.1, ctx.currentTime); gain.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.3);\n        osc.start(); osc.stop(ctx.currentTime + 0.3);\n      }\n    } catch (e) {}\n  }\n\n  function createConfetti() {\n    var colors = ['#58cc02', '#1cb0f6', '#ff4b4b', '#ffc800'];\n    var container = document.createElement('div');\n    container.className = 'confetti-container';\n    document.body.appendChild(container);\n    for (var i = 0; i < 40; i++) {\n      var p = document.createElement('div');\n      p.className = 'confetti-piece';\n      p.style.background = colors[Math.floor(Math.random() * colors.length)];\n      p.style.left = (Math.random() * 100) + 'vw';\n      var size = (6 + Math.random() * 6) + 'px';\n      p.style.width = size; p.style.height = size;\n      p.style.animationDuration = (1.5 + Math.random() * 1.5) + 's';\n      container.appendChild(p);\n    }\n    setTimeout(function() { container.remove(); }, 3200);\n  }\n\n  allBtns.forEach(function(btn) {\n    var btnId = btn.getAttribute('data-id');\n    btn.classList.remove('clicked');\n    btn.style.pointerEvents = 'none';\n    var btnIsCorrect = (btnId === 'V' && gabaritoVerdadeiro) || (btnId === 'F' && !gabaritoVerdadeiro);\n    if (btnIsCorrect) btn.classList.add('correct');\n    if (btnId === userChoiceId && !acertou) btn.classList.add('wrong');\n  });\n\n  if (footer) {\n    footer.style.display = 'block';\n    if (acertou) {\n      footer.style.background = '#d7ffb8'; footer.style.color = '#2b6a00';\n      if (fbTitle) fbTitle.innerText = 'Mandou bem!';\n      if (fbMsg) fbMsg.innerText = 'Resposta correta!';\n      if (fbIcon) fbIcon.innerText = '🎉';\n      playSound('win'); createConfetti();\n    } else {\n      footer.style.background = '#ffdfe0'; footer.style.color = '#9c1c1c';\n      if (fbTitle) fbTitle.innerText = 'Errou...';\n      if (fbMsg) fbMsg.innerText = 'O gabarito certo está em verde.';\n      if (fbIcon) fbIcon.innerText = '🤕';\n      playSound('lose');\n    }\n  }\n})();\n</script>"

_CSS = "@import url('https://fonts.googleapis.com/css2?family=Nunito:wght@400;700;800&display=swap');\n\n:root {\n  --duo-green: #58cc02; --duo-green-shadow: #46a302;\n  --duo-red: #ff4b4b; --duo-red-shadow: #d42c2c;\n  --duo-blue: #1cb0f6; --duo-blue-shadow: #1899d6;\n  --duo-yellow: #ffc800; --duo-yellow-shadow: #e0a800;\n}\n\nbody {\n  font-family: 'Nunito', sans-serif;\n  background-image: linear-gradient(135deg, #a18cd1 0%, #fbc2eb 100%);\n  background-attachment: fixed;\n  margin: 0; padding: 20px; min-height: 100vh; color: #4b4b4b;\n}\n\n.duo-wrapper { max-width: 520px; margin: 0 auto; }\n.header-stack { display: flex; flex-direction: column; align-items: center; gap: 8px; margin-bottom: 20px; }\n.med-badge {\n  font-weight: 900; font-size: 12px; color: rgba(255,255,255,0.95);\n  background: rgba(0,0,0,0.25); padding: 4px 12px; border-radius: 20px;\n  letter-spacing: 1px; text-transform: uppercase; backdrop-filter: blur(4px);\n}\n.subject-badge {\n  color: white; font-weight: 800; font-size: 15px;\n  padding: 8px 22px; border-radius: 15px; box-shadow: 0 4px 0 rgba(0,0,0,0.15);\n  text-transform: uppercase;\n}\n.duo-card { background: white; border-radius: 24px; padding: 24px; box-shadow: 0 8px 0 rgba(0,0,0,0.05), 0 20px 40px rgba(0,0,0,0.1); }\n.context-text { font-size: 13px; color: #6b6b6b; font-weight: 700; margin-bottom: 6px; }\n.question-text { font-size: 20px; color: #3c3c3c; margin: 6px 0 18px; font-weight: 800; text-align: left; line-height: 1.45; }\n.question-text img, .card-image img { width: 100%; border-radius: 16px; border: 2px solid #f0f0f0; margin: 10px 0; display: block; }\n.options-grid { display: grid; grid-template-columns: 1fr; gap: 10px; }\n.duo-btn {\n  background-color: white; border: 2px solid #e5e5e5; border-bottom: 4px solid #e5e5e5;\n  border-radius: 16px; padding: 14px 18px; color: #4b4b4b; font-family: 'Nunito', sans-serif;\n  font-size: 15px; font-weight: 700; text-align: left; cursor: pointer; transition: all .1s;\n  position: relative; top: 0; box-sizing: border-box; width: 100%;\n}\n.duo-btn:hover { background-color: #f7f7f7; }\n.duo-btn:active { transform: translateY(2px); }\n.duo-btn.clicked { background-color: var(--duo-blue) !important; border-color: var(--duo-blue-shadow) !important; color: white !important; border-bottom-width: 0px !important; top: 4px; box-shadow: inset 0 2px 0 rgba(0,0,0,0.1); pointer-events: none; }\n.duo-btn.correct { background-color: #d7ffb8; border-color: var(--duo-green); color: #2b6a00; }\n.duo-btn.wrong { background-color: #ffdfe0; border-color: var(--duo-red); color: #9c1c1c; }\n.vf-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 10px; }\n.vf-btn {\n  border-radius: 16px; padding: 18px; font-weight: 900; font-size: 17px; text-align: center;\n  border: 2px solid #e5e5e5; border-bottom: 4px solid #e5e5e5; background: white; color: #4b4b4b; cursor: pointer;\n  position: relative; top: 0; transition: all .1s;\n}\n.vf-btn:active { transform: translateY(2px); }\n.vf-btn.clicked { background-color: var(--duo-blue) !important; border-color: var(--duo-blue-shadow) !important; color: white !important; border-bottom-width: 0px !important; top: 4px; box-shadow: inset 0 2px 0 rgba(0,0,0,0.1); pointer-events: none; }\n.vf-btn.correct { background-color: #d7ffb8; border-color: var(--duo-green); color: #2b6a00; }\n.vf-btn.wrong { background-color: #ffdfe0; border-color: var(--duo-red); color: #9c1c1c; }\n\n/* ---- Verso / Answer side ---- */\n.footer-feedback {\n  margin: 18px auto 0 auto; padding: 15px; border-radius: 16px; max-width: 520px;\n  animation: slideUp 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);\n  display: none; box-sizing: border-box; text-align: left;\n}\n.feedback-content { display: flex; align-items: center; gap: 15px; }\n.fb-icon { font-size: 28px; background: white; width: 46px; height: 46px; border-radius: 50%; display: flex; align-items: center; justify-content: center; box-shadow: 0 4px 0 rgba(0,0,0,0.1); flex-shrink: 0; }\n.fb-title { font-weight: 800; font-size: 18px; }\n.fb-msg { font-size: 13px; opacity: 0.9; }\n\n.answer-section { margin-top: 18px; }\n.answer-box {\n  border-radius: 16px; padding: 16px 18px; margin-bottom: 14px; font-weight: 700; font-size: 15px;\n}\n.answer-box.correct-box { background: #d7ffb8; color: #2b6a00; border: 2px solid var(--duo-green); }\n.pegadinha-box { background: #fff3cd; border: 2px solid var(--duo-yellow); color: #6b5300; border-radius: 16px; padding: 14px 18px; margin-bottom: 14px; }\n.pegadinha-box b { display:block; margin-bottom: 4px; }\n.explicacao-box { background: #eef6ff; border: 2px solid var(--duo-blue); border-radius: 16px; padding: 16px 18px; margin-bottom: 14px; font-size: 14.5px; line-height: 1.55; text-align: left; color: #17395c; }\n.explicacao-box div:first-child { font-weight: 900; margin-bottom: 8px; }\n.fonte-footer { font-size: 12px; color: #6b6b6b; text-align: right; margin-top: 4px; margin-bottom: 12px; }\n.video-wrap { border-radius: 16px; overflow: hidden; margin-top: 6px; box-shadow: 0 4px 0 rgba(0,0,0,0.05); }\n.video-wrap iframe { width: 100%; aspect-ratio: 16/9; border: none; display: block; }\n\n@keyframes slideUp { from { transform: translateY(20px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }\n\n/* ---- Confete (acerto) ---- */\n.confetti-container {\n  position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;\n  pointer-events: none; overflow: hidden; z-index: 9999;\n}\n.confetti-piece {\n  position: absolute; top: -12px; opacity: 0.95; border-radius: 2px;\n  animation-name: confetti-fall; animation-timing-function: linear; animation-fill-mode: forwards;\n}\n@keyframes confetti-fall {\n  0%   { transform: translateY(0) rotate(0deg); opacity: 1; }\n  100% { transform: translateY(112vh) rotate(600deg); opacity: 0.85; }\n}"

MODEL_MULTIPLA_ESCOLHA = genanki.Model(
    MODEL_ID_MULTIPLA_ESCOLHA,
    "Múltipla Escolha Universal",
    fields=[{"name": f} for f in FIELDS_MULTIPLA_ESCOLHA],
    templates=[{"name": "Cartão 1", "qfmt": _TEMPLATE_MC_FRONT, "afmt": _TEMPLATE_MC_BACK}],
    css=_CSS,
)

MODEL_VERDADEIRO_FALSO = genanki.Model(
    MODEL_ID_VERDADEIRO_FALSO,
    "Verdadeiro ou Falso Universal",
    fields=[{"name": f} for f in FIELDS_VERDADEIRO_FALSO],
    templates=[{"name": "Cartão 1", "qfmt": _TEMPLATE_VF_FRONT, "afmt": _TEMPLATE_VF_BACK}],
    css=_CSS,
)

TAG_PADRAO = "gerado-claude"


def _stable_id(*parts: str) -> int:
    """Gera um ID inteiro determinístico (positivo, dentro do range aceito pelo
    genanki) a partir de strings - mesmo (unit_code, lesson_name) sempre produz o
    mesmo deck ID, pra reprocessar a mesma aula mesclar no mesmo deck em vez de
    criar um novo a cada tentativa."""
    joined = "||".join(parts).encode("utf-8")
    # CRC32 cabe em 32 bits e é mais que suficiente pra evitar colisão entre aulas.
    return 1000000000 + (zlib.crc32(joined) % 900000000)


def _register_media(image_path: str, media_files: Dict[str, str]) -> str:
    """Registra `image_path` em `media_files` (dict {basename: caminho_completo},
    dedup automático quando dois cards reaproveitam a mesma imagem) e devolve o
    basename - é isso que o Anki usa pra referenciar mídia dentro de um `<img>`."""
    basename = Path(image_path).name
    media_files[basename] = image_path
    return basename


def _image_field_html(card: Dict[str, Any], media_files: Dict[str, str]) -> str:
    """Retorna o HTML `<img>` pro campo "Imagem" (mostrado do lado da PERGUNTA)
    quando o card tem card["imagem_path"] - usado quando a própria pergunta
    depende de ver a imagem (ex.: "olhando esta radiografia..."), ou "" quando
    não há imagem nesse papel."""
    image_path = card.get("imagem_path")
    if not image_path:
        return ""
    return f'<img src="{_register_media(image_path, media_files)}">'


def _explicacao_com_imagem(card: Dict[str, Any], media_files: Dict[str, str]) -> str:
    """Monta o texto do campo "Explicação" (o quadro "💡 GABARITO COMENTADO"),
    prefixando com a imagem do slide quando card["imagem_gabarito_path"] estiver
    setado - usado quando a imagem ilustra/reforça a EXPLICAÇÃO (ex.: "veja
    como a TC mostra..."), papel diferente de "imagem_path" (que ilustra a
    PERGUNTA). Um mesmo card pode ter as duas, ou só uma, ou nenhuma.

    Funciona igual pra MC e VF (ao contrário do campo dedicado "Imagem_Verso",
    que só existe no note type de múltipla escolha) - embutir o <img> direto no
    texto da explicação evita depender de um campo que nem todo note type tem."""
    explicacao = card.get("explicacao", "")
    image_path = card.get("imagem_gabarito_path")
    if not image_path:
        return explicacao
    img_html = f'<img src="{_register_media(image_path, media_files)}" style="margin-bottom:10px;">'
    return img_html + explicacao


def _mc_note(card: Dict[str, Any], materia: str, media_files: Dict[str, str]) -> genanki.Note:
    opcoes_erradas = list(card.get("opcoes_erradas") or [])
    # Opcao_2..Opcao_8 (até 7 opções erradas); sobra fica em branco.
    opcoes_padded = (opcoes_erradas + [""] * 7)[:7]
    fields = [
        card.get("enunciado", ""),
        materia,
        _image_field_html(card, media_files),  # Imagem - ilustra a PERGUNTA
        "",  # Imagem_Verso - não usado (a imagem do gabarito vai embutida na própria Explicação, ver _explicacao_com_imagem)
        card.get("resposta_correta", ""),
        *opcoes_padded,
        card.get("pegadinha", ""),
        _explicacao_com_imagem(card, media_files),
        card.get("fonte", ""),
        card.get("video", ""),
    ]
    return genanki.Note(model=MODEL_MULTIPLA_ESCOLHA, fields=fields, tags=[TAG_PADRAO])


def _vf_note(card: Dict[str, Any], materia: str, media_files: Dict[str, str]) -> genanki.Note:
    fields = [
        card.get("assertiva", ""),
        materia,
        card.get("contexto_enunciado", ""),
        _image_field_html(card, media_files),  # Imagem
        card.get("gabarito", ""),
        card.get("pegadinha", ""),
        _explicacao_com_imagem(card, media_files),
        card.get("fonte", ""),
        card.get("video", ""),
    ]
    return genanki.Note(model=MODEL_VERDADEIRO_FALSO, fields=fields, tags=[TAG_PADRAO])


def build_flashcards_apkg(
    flashcards: List[Dict[str, Any]],
    unit_code: str,
    lesson_name: str,
    output_path: Path,
) -> Dict[str, Any]:
    """Gera um .apkg com as notas MC/VF fornecidas, usando os note types reais do
    Anki do usuário (mesmo model ID). `flashcards` é uma lista de dicts com "tipo"
    ("mc" ou "vf") + os campos correspondentes (em português, já mapeados pro
    formato interno - ver core/multimodal_processor.py).

    Retorna {"success", "path", "count_mc", "count_vf", "error"}. Nunca levanta -
    falhas viram success=False, pra quem chama decidir sem travar o pipeline.
    """
    try:
        # Hierarquia "Medicina::<UC>::<aula>" (reaproveitando app.anki_root_deck de
        # config/settings.py) - cada aula cai no seu próprio deck, nada misturado.
        deck_id = _stable_id(unit_code, lesson_name)
        root_deck = settings.app.anki_root_deck
        deck_name = f"{root_deck}::{unit_code}::{lesson_name}"
        deck = genanki.Deck(deck_id, deck_name)

        # {basename: caminho_completo} - dict em vez de lista pra dedup automático
        # quando duas cards referenciam a mesma página do slide.
        media_files: Dict[str, str] = {}

        count_mc = 0
        count_vf = 0
        for card in flashcards:
            tipo = (card.get("tipo") or "").strip().lower()
            if tipo == "mc":
                deck.add_note(_mc_note(card, unit_code, media_files))
                count_mc += 1
            elif tipo == "vf":
                deck.add_note(_vf_note(card, unit_code, media_files))
                count_vf += 1
            else:
                logger.warning(f"Flashcard com tipo desconhecido ignorado: {card.get('tipo')!r}")

        if count_mc + count_vf == 0:
            return {"success": False, "path": None, "count_mc": 0, "count_vf": 0, "error": "nenhum flashcard válido para incluir no .apkg"}

        output_path.parent.mkdir(parents=True, exist_ok=True)
        genanki.Package(deck, media_files=list(media_files.values())).write_to_file(str(output_path))
        image_note = f" + {len(media_files)} imagem(ns) de slide" if media_files else ""
        logger.info(f".apkg gerado com {count_mc} MC + {count_vf} VF{image_note} em: {output_path}")

        return {"success": True, "path": output_path, "count_mc": count_mc, "count_vf": count_vf, "error": None}

    except Exception as e:
        logger.error(f"Erro ao gerar .apkg de flashcards: {e}")
        return {"success": False, "path": None, "count_mc": 0, "count_vf": 0, "error": str(e)}
