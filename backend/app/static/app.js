const state = { paper: null, answers: {} };
const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  let body = null;
  try { body = await response.json(); } catch (_) {}
  if (response.status === 401) throw new Error('AUTH');
  if (!response.ok) throw new Error(body?.detail || ('HTTP ' + response.status));
  return body;
}

function showLogin() { $('loginView').hidden = false; $('appView').hidden = true; }
function showApp() { $('loginView').hidden = true; $('appView').hidden = false; }

async function boot() {
  try {
    await api('/auth/me');
    showApp();
    await loadQuiz();
  } catch (_) { showLogin(); }
}

$('loginForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('loginError').textContent = '';
  try {
    await api('/auth/login', { method:'POST', body: JSON.stringify({ password:$('password').value }) });
    showApp();
    await loadQuiz();
  } catch (e) { $('loginError').textContent = e.message === 'AUTH' ? '密码错误' : e.message; }
});

$('logoutBtn').addEventListener('click', async () => {
  await api('/auth/logout', { method:'POST' });
  location.reload();
});

async function loadQuiz() {
  state.paper = await api('/quiz/today');
  $('paperMeta').textContent = state.paper.date + ' · 生化30题 + 细胞30题';
  renderQuestions();
  if (state.paper.submitted) {
    $('submitBtn').disabled = true;
    $('submitBtn').textContent = '今日已交卷 · ' + state.paper.score + '/60';
  }
}

function renderQuestions() {
  const wrap = $('questions');
  wrap.innerHTML = '';
  state.paper.questions.forEach((q, idx) => {
    const card = document.createElement('article');
    card.className = 'question';
    const subject = q.subject === 'biochemistry' ? '生物化学' : '细胞生物学';
    const options = Object.entries(q.options).map(([key, value]) =>
      '<label class="option"><input type="radio" name="q' + q.id + '" value="' + key + '"><b>' + key + '.</b> ' + escapeHtml(value) + '</label>'
    ).join('');
    card.innerHTML = '<div class="meta">第 ' + (idx+1) + ' 题 · ' + subject + ' · ' + escapeHtml(q.topic) + '</div>' +
      '<h3>' + escapeHtml(q.stem) + '</h3>' + options;
    card.querySelectorAll('input').forEach(input => input.addEventListener('change', () => {
      state.answers[q.id] = input.value;
      updateProgress();
    }));
    wrap.appendChild(card);
  });
  updateProgress();
}

function updateProgress() {
  const count = Object.keys(state.answers).length;
  $('progressText').textContent = count + ' / 60';
  $('progressBar').style.width = (count / 60 * 100) + '%';
  $('submitBtn').disabled = count !== 60 || state.paper?.submitted;
  if (count === 0) $('subjectText').textContent = '未开始';
  else if (count <= 30) $('subjectText').textContent = '生物化学';
  else $('subjectText').textContent = '细胞生物学';
}

$('submitBtn').addEventListener('click', async () => {
  if (!confirm('确认提交60道题吗？交卷后不能重复提交。')) return;
  $('submitBtn').disabled = true;
  $('submitBtn').textContent = '正在批改…';
  try {
    const result = await api('/quiz/submit', {
      method:'POST',
      body: JSON.stringify({ paper_id: state.paper.paper_id, answers: state.answers }),
    });
    renderResult(result);
    $('submitBtn').textContent = '已交卷 · ' + result.score + '/60';
  } catch (e) {
    alert(e.message);
    $('submitBtn').textContent = '统一交卷';
    updateProgress();
  }
});

function renderResult(result) {
  const box = $('result');
  const wrong = result.details.filter(x => !x.correct);
  box.hidden = false;
  box.innerHTML = '<div class="score">' + result.score + ' / ' + result.total + '</div>' +
    '<p>正确率 ' + result.percent + '% · 错题 ' + wrong.length + ' 道</p>' +
    result.details.map((d, idx) => '<div class="result-item">' +
      '<b>第 ' + (idx+1) + ' 题：</b>' +
      '<span class="' + (d.correct ? 'correct' : 'wrong') + '">' + (d.correct ? '正确' : '错误') + '</span>' +
      ' · 你的答案 ' + (d.selected_answer || '未答') + ' · 正确答案 ' + d.correct_answer +
      '<br><small>' + escapeHtml(d.topic) + '</small><br>' + escapeHtml(d.explanation) + '</div>').join('');
  box.scrollIntoView({ behavior:'smooth' });
}

async function loadStats() {
  const data = await api('/stats');
  $('statsBox').innerHTML = '<h2>学习概览</h2><p>累计作答 ' + data.answered + ' 题 · 正确率 ' + (data.accuracy ?? '—') + '%</p>' +
    '<h3>当前薄弱知识点</h3>' + (data.weak_topics.length ? data.weak_topics.map(x =>
      '<div class="list-row"><span class="badge">' + (x.subject === 'biochemistry' ? '生化' : '细胞') + '</span> ' +
      escapeHtml(x.topic) + ' · ' + x.correct + '/' + x.answered + ' · ' + x.accuracy + '%</div>').join('') : '<p>完成第一套试卷后生成。</p>');
}

async function loadHistory() {
  const data = await api('/history');
  $('historyBox').innerHTML = '<h2>历史成绩</h2>' + (data.items.length ? data.items.map(x =>
    '<div class="list-row"><b>' + x.date + '</b> · ' + x.score + '/60 · ' + x.percent + '%</div>').join('') : '<p>暂无已提交试卷。</p>');
}

async function loadReview() {
  const data = await api('/review/due');
  $('reviewBox').innerHTML = '<h2>到期复习</h2><p>当前到期 ' + data.count + ' 项</p>' + (data.items.length ? data.items.map(x =>
    '<div class="list-row"><span class="badge">第' + x.review_stage + '天</span> ' + escapeHtml(x.topic) + '<br>' + escapeHtml(x.stem) + '</div>').join('') : '<p>今天没有额外到期复习。</p>');
}

document.querySelectorAll('.tabs button').forEach(btn => btn.addEventListener('click', async () => {
  document.querySelectorAll('.tabs button').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(x => x.classList.remove('active'));
  btn.classList.add('active');
  const tab = btn.dataset.tab;
  $(tab + 'Tab').classList.add('active');
  if (tab === 'stats') await loadStats();
  if (tab === 'history') await loadHistory();
  if (tab === 'review') await loadReview();
}));

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch]));
}

boot();
