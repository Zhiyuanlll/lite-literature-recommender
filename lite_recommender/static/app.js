'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const token = $('meta[name="lite-token"]').content;
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const safeURL = value => {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : ''; }
  catch { return ''; }
};
const lines = value => String(value || '').split(/[\n,，;；]+/).map(s => s.trim()).filter(Boolean);
const multiline = value => Array.isArray(value) ? value.join('\n') : String(value || '');
const conceptLines = value => String(value || '').split(/\r?\n/).map(line => [...new Set(line.split('|').map(term => term.trim()).filter(Boolean))]).filter(group => group.length);
const multilineConcepts = value => Array.isArray(value) ? value.map(group => Array.isArray(group) ? group.join(' | ') : String(group)).join('\n') : '';
const paperKey = paper => JSON.stringify([paper.topic_id, paper.paper_id]);
const stages = {survey: '综述入门', gap: '候选 gap', focus: '聚焦跟踪'};
const reasons = {
  relevant: '主题相关', method_useful: '方法有用', mechanism_relevant: '机制相关',
  reproducible: '可复现', question_relevant: '符合当前问题',
  topic_mismatch: '主题不合', scale_mismatch: '尺度不合', method_mismatch: '方法不合',
  duplicate: '内容重复', stage_mismatch: '阶段不合', too_difficult: '目前太难',
  no_time: '暂时没时间', no_fulltext: '难以获取全文', evidence_insufficient: '证据不足', other: '其他'
};
const readStates = {unread: '待阅读', reading: '阅读中', skimmed: '已略读', read: '已读', cited: '已引用', ignore: '暂不阅读'};
const kinds = {review: '综述', research: '研究', method: '方法', representative: '代表研究', direct: '直接相关', explore: '探索', exploration: '探索', counterexample: '反例', classic: '经典'};
const runStatuses = {complete: '已完成', partial: '部分完成', error: '运行失败', running: '运行中'};
let state = {topics: [], runs: [], settings: {}, stats: {}};
let selectedTopics = new Set();
let selectionInitialized = false;
let currentRun = null;
let currentJob = null;
let currentCandidates = [];
let currentDiagnostics = {};
let currentFeedbackBasis = null;
let toastTimer;
const feedbackDrafts = new Map();
let feedbackSaving = false;

async function api(path, payload) {
  const response = await fetch(path, payload === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-Lite-Token': token}, body: JSON.stringify(payload)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
  return data;
}

function toast(message, error = false) {
  const dialog = $('dialog[open]');
  if (error && dialog) {
    const inlineError = $('.form-error', dialog);
    if (inlineError) { inlineError.textContent = message; inlineError.classList.remove('hidden'); return; }
  }
  const el = $('#toast');
  el.textContent = message;
  el.classList.remove('hidden');
  el.classList.toggle('error', error);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), error ? 8000 : 3500);
}

function topicTitle(id) { return state.topics.find(t => t.id === id)?.title || id || '未指定方向'; }
function dateText(value) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'});
}
function runID(run) { return run.run_id || run.id; }
function isDemo(run) { return run?.mode === 'demo' || run?.demo === true; }

function renderState() {
  if (!selectionInitialized) {
    selectedTopics = new Set(state.topics.filter(t => t.active !== false).map(t => t.id));
    if (!selectedTopics.size && state.topics.length) selectedTopics.add(state.topics[0].id);
    selectionInitialized = true;
  }
  const groups = new Map();
  for (const topic of state.topics) {
    const name = topic.group || '其他';
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(topic);
  }
  $('#topic-list').innerHTML = groups.size ? [...groups].map(([group, topics]) => `<div class="topic-group"><h3>${escapeHTML(group)}</h3>${topics.map(t => `<div class="topic-item ${t.active === false ? 'inactive' : ''}"><label><input type="checkbox" data-select-topic="${escapeHTML(t.id)}" ${selectedTopics.has(t.id) ? 'checked' : ''}><span>${escapeHTML(t.title || t.id)}<small>${escapeHTML(stages[t.stage] || t.stage || '综述入门')}${t.active === false ? ' · 未启用' : ''} · 本周 ${state.stats.used_by_topic?.[t.id] ?? 0} · 核心${state.settings.recommendation_counts?.core ?? 5}＋相关${state.settings.recommendation_counts?.related ?? 15}</small></span></label><button class="icon-button" data-edit-topic="${escapeHTML(t.id)}" aria-label="编辑 ${escapeHTML(t.title || t.id)}">⋯</button></div>`).join('')}</div>`).join('') : '<p class="no-topics">尚无方向，点击 ＋ 创建。</p>';
  $('#stat-topics').textContent = state.topics.filter(t => t.active !== false).length;
  $('#stat-budget').textContent = `${state.settings.recommendation_counts?.core ?? 5}＋${state.settings.recommendation_counts?.related ?? 15}`;
  $('#budget-used').textContent = `各方向本周合计 ${state.stats.used_this_week ?? 0} 篇`;
  $('#stat-runs').textContent = state.iteration_count ?? 0;
  $('#stat-feedback').textContent = state.stats.rated ?? state.stats.feedback_count ?? state.stats.ratings ?? state.stats.feedback ?? 0;
  const runs = [...state.runs].sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
  $('#run-select').innerHTML = runs.length ? runs.map(r => `<option value="${escapeHTML(runID(r))}">${escapeHTML(dateText(r.created_at) || runID(r))}${isDemo(r) ? ' · 离线演示' : r.mode === 'preview' ? ' · 下周预览（不可评分）' : r.current_feedback ? ' · 当前可评分清单' : r.action === 'accelerate' ? ' · 加速迭代（可评分）' : r.action === 'weekly' || r.action === 'reselect' ? ` · 本周推荐 ${r.week || ''}` : ' · 历史推荐'} · ${r.count ?? '?'} 篇${r.status === 'error' ? ' · 失败' : ''}</option>`).join('') : '<option value="">尚无推荐记录</option>';
  if (currentRun) $('#run-select').value = currentRun.run_id || currentRun.id;
  const busy=!!currentJob || feedbackSaving;
  const activeSelected=state.topics.some(t=>t.active!==false && selectedTopics.has(t.id));
  $('#preview-run').disabled=busy || !activeSelected;
  $('#reselect-run').disabled=busy || !activeSelected;
  const basis=feedbackBasis();
  const basisPapers=(basis?.papers || []).filter(p=>selectedTopics.has(p.topic_id));
  $('#real-run').disabled=busy || !activeSelected || !basis || (basis.papers && (!basisPapers.length || basisPapers.some(p=>!p.feedback?.rating) || state.topics.some(t=>t.active!==false && selectedTopics.has(t.id) && !basis.papers.some(p=>p.topic_id===t.id))));
  const missingTopics=state.topics.filter(t=>t.active!==false && selectedTopics.has(t.id) && basis?.papers && !basis.papers.some(p=>p.topic_id===t.id));
  const unrated=basisPapers.filter(p=>!p.feedback?.rating).length;
  $('#real-run').title=missingTopics.length ? `所选方向尚无当前清单：${missingTopics.map(t=>t.title).join('、')}；请先重选本周推荐，或只勾选已有清单的方向` : unrated ? `还有${unrated}篇未评分或未保存；请完成评分并保存全部反馈` : '使用全部已保存评分更新偏好，提供可评分的新文献';
  $('#demo-run').disabled=busy || selectedTopics.size===0;
  renderSemanticStatus();
  updateFeedbackButton();
}

function evidenceText(paper) {
  const evidence = paper.evidence;
  if (typeof evidence === 'string') return ({metadata: '题名与元数据', title: '题名与元数据', title_only: '仅标题，待核验', abstract: '摘要', fulltext: '全文', full_text: '全文'}[evidence] || evidence);
  if (evidence && typeof evidence === 'object') return Object.entries(evidence).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' · ');
  return paper.abstract ? '摘要与元数据' : '题名与元数据';
}
function authorText(authors) {
  if (Array.isArray(authors)) return authors.map(a => typeof a === 'string' ? a : a.name || [a.given, a.family].filter(Boolean).join(' ')).filter(Boolean).join(', ');
  return String(authors || '作者信息待核验');
}
function numeric(value) { return Number.isFinite(Number(value)) ? Number(value).toFixed(1) : '—'; }
const categoryLabels = {core: '核心推荐', exploration: '探索推荐', pending: '仅标题，待核验', rejected: '未入选'};
const rejectionLabels = {missing_abstract: '来源未提供摘要，保留待核验', missing_core_concept: '未覆盖全部核心概念', below_core_score: '匹配分低于核心推荐门槛', excluded_term: '命中排除词', future_date: '发表日期晚于本轮运行日期', already_recommended: '此方向此前已展示', not_adjacent: '缺少与当前研究问题相关的线索'};
function paperCategory(paper) {
  return paper.classification || paper.category || (paper.exploration ? 'exploration' : paper.eligible ? 'core' : paper.exploration_eligible ? 'exploration' : !paper.abstract ? 'pending' : 'rejected');
}
function renderPaper(paper, index) {
  const feedback = feedbackDrafts.get(draftKey(paper)) || paper.feedback || {};
  const rating = feedback.rating;
  const demo = isDemo(currentRun);
  const url = demo ? '' : safeURL(paper.url || (paper.doi ? `https://doi.org/${paper.doi}` : ''));
  const matched = Array.isArray(paper.matched_terms) ? paper.matched_terms.filter(t => typeof t === 'string') : [];
  const selectedTags = new Set((feedback.tags || []).map(t => typeof t === 'string' ? t : t.tag));
  const sources = paper.sources || paper.source || '';
  const sourceText = Array.isArray(sources) ? sources.map(s => typeof s === 'string' ? s : s.name || '').join(', ') : String(sources);
  const reasonOptions = '<option value="">请选择原因</option>' + Object.entries(reasons).map(([value, label]) => `<option value="${value}" ${feedback.reason === value ? 'selected' : ''}>${label}</option>`).join('');
  return `<article class="paper-card" data-paper-key="${escapeHTML(paperKey(paper))}">
    <div class="paper-topline"><span class="paper-index">${String(index + 1).padStart(2, '0')}</span><span class="topic-badge">${escapeHTML(topicTitle(paper.topic_id))}</span>${paper.classification || paper.category || paper.exploration ? `<span class="category-badge ${paperCategory(paper) === 'exploration' ? 'exploration' : ''}">${escapeHTML(paper.recommendation_group === 'core' ? '核心候选' : paper.recommendation_group === 'related' ? (paper.title_only ? '相关候选 · 仅标题' : '相关候选') : categoryLabels[paperCategory(paper)] || paperCategory(paper))}</span>` : ''}${paper.kind ? `<span class="kind-badge">${escapeHTML(kinds[paper.kind] || paper.kind)}</span>` : ''}<span class="paper-date">${escapeHTML(paper.published || '')}</span></div>
    <div class="paper-title-row"><div><h3>${url ? `<a href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(paper.title)} <span class="external" aria-hidden="true">↗</span></a>` : escapeHTML(paper.title)}</h3><p class="authors">${escapeHTML(authorText(paper.authors))}</p></div><div class="score"><strong>${numeric(paper.score)}</strong><span>推荐分</span></div></div>
    <p class="recommendation">${escapeHTML(paper.reason || '该论文符合当前方向的检索条件。')}</p>
    <div class="paper-provenance"><span>依据：${escapeHTML(evidenceText(paper))}</span>${sourceText ? `<span>来源：${escapeHTML(sourceText)}</span>` : ''}<span>基础 ${numeric(paper.base_score)} · 偏好 ${Number(paper.adjustment || 0) >= 0 ? '+' : ''}${numeric(paper.adjustment || 0)}</span>${paper.semantic?.cosine != null ? `<span>语义相似度 ${Number(paper.semantic.cosine).toFixed(3)}（非概率）· 排序修正 ${numeric(paper.semantic.adjustment)}</span>` : ''}${paper.doi ? `<span>DOI：${escapeHTML(paper.doi)}</span>` : ''}</div>
    ${paper.abstract ? `<details class="abstract"><summary>阅读摘要</summary><p>${escapeHTML(paper.abstract)}</p></details>` : '<p class="metadata-only">当前来源未返回摘要，推荐依据限于题名和元数据。</p>'}
    ${currentRun.mode === 'preview' ? '<p class="demo-note">下周预览：按最新已保存反馈推荐新文献。下周预览只供阅读，不提交评分；新文献也会纳入后续去重。</p>' : ''}<form ${currentRun.mode === 'preview' ? 'hidden' : ''} class="feedback-form" data-key="${escapeHTML(paperKey(paper))}">
      <div class="feedback-top"><div><span class="feedback-label">${demo ? '演示相关性评分' : '你的相关性评分'}</span><div class="rating-stars" role="group" aria-label="相关性评分">${[1, 2, 3, 4, 5].map(n => `<button type="button" class="star ${n <= rating ? 'selected' : ''}" data-rating="${n}" aria-label="${n} 分" aria-pressed="${n === rating}">★</button>`).join('')}<span class="rating-text">${rating ? `${rating} 分` : '待评分'}${feedbackDrafts.has(draftKey(paper)) ? ' · 待保存' : ''}</span><button type="button" class="clear-rating text-button" ${!rating ? 'disabled' : ''}>撤销</button></div><input name="rating" type="hidden" value="${rating ?? ''}"></div><label class="reading-state"><span>阅读状态</span><select name="read_status">${Object.entries(readStates).map(([value, label]) => `<option value="${value}" ${(feedback.read_status || feedback.status || 'unread') === value ? 'selected' : ''}>${label}</option>`).join('')}</select></label></div>
      ${demo ? '<p class="demo-note">合成样例，非真实论文。可体验评分闭环；演示反馈不影响真实推荐偏好。</p>' : ''}<div class="feedback-fields"><label>评分原因<select name="reason">${reasonOptions}</select></label><label>补充说明<input name="note" maxlength="2000" placeholder="例如：尺度不合，我需要位错层面的机制" value="${escapeHTML(feedback.note || '')}"></label></div>${matched.length ? `<fieldset class="matched-terms"><legend>与这次反馈有关的检索词（可选，帮助偏好迭代）</legend>${matched.map(t => `<label class="tag-check"><input type="checkbox" name="tags" value="${escapeHTML(t)}" ${selectedTags.has(t) ? 'checked' : ''}>${escapeHTML(t)}</label>`).join('')}</fieldset>` : ''}<div class="feedback-bottom"><small>未评分不会当作负反馈。已读、没时间和全文难获取不会直接改变兴趣。</small><button type="submit" class="secondary compact">保存${demo ? '演示' : ''}反馈</button></div>
    </form>
  </article>`;
}

function diagnosticEntries(diagnostics) {
  if (Array.isArray(diagnostics)) return diagnostics.map(item => [item.topic_id, item]);
  return Object.entries(diagnostics || {}).filter(([, item]) => item && typeof item === 'object');
}
function renderCandidateSummary() {
  const entries = diagnosticEntries(currentDiagnostics);
  const visible = !!currentRun && !isDemo(currentRun) && (entries.length > 0 || currentCandidates.length > 0);
  $('#candidate-panel').classList.toggle('hidden', !visible);
  if (!visible) return;
  $('#candidate-summary').innerHTML = entries.map(([id, item]) => `<div class="diagnostic-topic"><strong>${escapeHTML(topicTitle(id))}</strong><div class="diagnostic-grid">${[
    ['来源返回', item.raw_count], ['去重候选', item.deduped_count], ['已展示排除', item.seen_count],
    ['待核验', item.pending_count], ['核心候选', item.core_count], ['探索候选', item.exploration_count],
    ['被筛除', item.rejected_count], ['本轮推荐', item.selected_count], ['核心推送', item.selected_core_count], ['相关推送', item.selected_related_count], ['仅标题推送', item.selected_title_only_count], ['两组推送目标', item.budget_remaining]
  ].map(([label, value]) => `<span>${label}<b>${value == null ? '—' : escapeHTML(value)}</b></span>`).join('')}</div></div>`).join('');
  renderCandidates();
}
function renderCandidates() {
  const filter = $('#candidate-filter').value;
  const candidates = currentCandidates.filter(p => filter === 'all' || (filter === 'rejected' ? paperCategory(p) === 'rejected' || p.seen || p.already_seen : paperCategory(p) === filter && !p.seen));
  $('#candidate-count').textContent = `${candidates.length} / ${currentCandidates.length} 条`;
  $('#candidates').innerHTML = candidates.length ? candidates.map(p => {
    const category = paperCategory(p);
    const rejection = p.rejection_reasons || [];
    const url = safeURL(p.url || (p.doi ? `https://doi.org/${p.doi}` : ''));
    return `<article class="candidate-card"><div class="candidate-topline"><span class="topic-badge">${escapeHTML(topicTitle(p.topic_id))}</span><span class="category-badge ${category === 'exploration' ? 'exploration' : category === 'pending' ? 'pending' : ''}">${escapeHTML(categoryLabels[category] || category)}</span>${p.seen || p.already_seen || p.previously_recommended ? '<span class="kind-badge">此前已展示</span>' : ''}<span>匹配分 ${numeric(p.score)}</span></div><h4>${url ? `<a href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(p.title)} ↗</a>` : escapeHTML(p.title)}</h4><p class="candidate-reasons">${escapeHTML((Array.isArray(rejection) ? rejection : [rejection]).map(reason => rejectionLabels[reason] || reason).join('；') || p.reason || (category === 'pending' ? '当前来源及主页补取尚未返回摘要；标题相关时仍可进入相关推送。' : '通过对应候选分类，推送由核心、相关两组目标及排序决定。'))}</p>${p.missing_concepts?.length ? `<p class="candidate-reasons">待核验概念：${escapeHTML(p.missing_concepts.join('；'))}</p>` : ''}${p.excluded_terms?.length ? `<p class="candidate-reasons">排除词：${escapeHTML(p.excluded_terms.join('；'))}</p>` : ''}${p.abstract ? `<details class="abstract"><summary>查看候选摘要</summary><p>${escapeHTML(p.abstract)}</p></details>` : ''}</article>`;
  }).join('') : '<p class="candidate-empty">本轮没有此类候选。历史批次可能未保存新版筛选明细。</p>';
}

function renderPapers() {
  if (!currentRun) return;
  captureVisibleDrafts();
  const allPapers = currentRun.papers || [];
  const filter = $('#paper-filter').value;
  const papers = allPapers.filter(p => (!filter || p.topic_id === filter) && (!$('#unrated-only').checked || !p.feedback?.rating));
  $('#paper-count').textContent = `${allPapers.length} 篇`;
  const actionLabel={preview_next:'下周预览 · 新文献，不可评分',reselect:'重选本周推荐 · 可评分',accelerate:'评分后加速迭代 · 新文献，可评分',weekly:'每周推荐 · 可评分'}[currentRun.action] || (isDemo(currentRun)?'离线演示':currentRun.mode==='preview'?'历史预览 · 不可评分':'历史推荐 · 可评分');
  $('#run-meta').textContent=`${dateText(currentRun.created_at)} · ${actionLabel}${currentRun.target_week ? ` · ${currentRun.target_week}` : ''} · ${runStatuses[currentRun.status] || currentRun.status}`;
  $('#run-warnings').innerHTML = (currentRun.warnings || []).length ? `<div class="warnings"><strong>本轮说明</strong><ul>${currentRun.warnings.map(w => `<li>${escapeHTML(typeof w === 'string' ? w : JSON.stringify(w))}</li>`).join('')}</ul></div>` : '';
  $('#papers').innerHTML = papers.length ? papers.map((paper,index) => `${paper.recommendation_group && (index === 0 || papers[index-1].recommendation_group !== paper.recommendation_group) ? `<h3 class="recommendation-group-heading">${paper.recommendation_group === 'core' ? '本周核心候选' : '本周相关候选'} · ${papers.filter(p=>p.recommendation_group===paper.recommendation_group).length} 篇</h3>` : ''}${renderPaper(paper,index)}`).join('') : '<div class="empty-state small-empty"><h3>当前没有符合条件的文献</h3><p>检索结果不足、每周预算已用完，或筛选条件未匹配；请查看本轮说明。</p></div>';
  $('#filter-bar').classList.toggle('hidden', !allPapers.length);
  $('#export-toggle').disabled = false;
  updateFeedbackButton();
  for (const fmt of ['bib', 'json', 'markdown']) {
    $(`#export-${fmt}`).href = `/api/export?id=${encodeURIComponent(currentRun.run_id)}&format=${fmt}`;
  }
}

async function refreshState(loadLatest = false) {
  state = await api('/api/state');
  state.topics = state.topics.map(topic => ({...topic, id: topic.id || topic.topic_id}));
  if (state.current_feedback_run_id && !state.runs.some(r=>runID(r)===state.current_feedback_run_id)) {
    try {const current=await api(`/api/run?id=${encodeURIComponent(state.current_feedback_run_id)}`);state.runs.push({...current,current_feedback:true});} catch { /* Old data stays readable. */ }
  }
  renderState();
  if (loadLatest && state.runs.length) {
    const latest = [...state.runs].sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')))[0];
    await loadRun(runID(latest));
  }
}
async function loadRun(id) {
  if (!id) return;
  captureVisibleDrafts();
  $('#papers').innerHTML = '';
  currentRun = await api(`/api/run?id=${encodeURIComponent(id)}`);
  currentFeedbackBasis=null;
  if (currentRun.mode==='preview' && state.current_feedback_run_id) {
    try {currentFeedbackBasis=await api(`/api/run?id=${encodeURIComponent(state.current_feedback_run_id)}`);} catch { /* Server also validates the rating gate. */ }
  }
  currentCandidates = []; currentDiagnostics = currentRun.diagnostics || {};
  try { const candidates = await api(`/api/candidates?id=${encodeURIComponent(id)}`); currentCandidates = candidates.candidates || []; currentDiagnostics = candidates.diagnostics || currentDiagnostics; } catch { /* Earlier batches may have no candidate snapshot. */ }
  const ids = [...new Set((currentRun.papers || []).map(p => p.topic_id))];
  $('#paper-filter').innerHTML = '<option value="">全部方向</option>' + ids.map(id => `<option value="${escapeHTML(id)}">${escapeHTML(topicTitle(id))}</option>`).join('');
  $('#run-select').value = id;
  renderPapers();
  renderState();
  renderCandidateSummary();
}

function editTopic(id = '') {
  const topic = state.topics.find(t => t.id === id) || {active: true, stage: 'survey'};
  const form = $('#topic-form');
  form.reset();
  $('.form-error', form).classList.add('hidden');
  for (const field of ['id', 'title', 'group', 'questions', 'keywords', 'method_terms', 'exclude_terms', 'stage']) {
    form.elements[field].value = ['questions', 'keywords', 'method_terms', 'exclude_terms'].includes(field) ? multiline(topic[field]) : topic[field] || '';
  }
  form.elements.stage.value = topic.stage || 'survey';
  form.elements.core_concepts.value = multilineConcepts(topic.core_concepts);
  form.elements.active.checked = topic.active !== false;
  form.elements.id.readOnly = !!id;
  $('#topic-dialog-title').textContent = id ? '编辑研究方向' : '新增研究方向';
  $('#topic-dialog').showModal();
}

function feedbackBasis() {
  if (currentRun && !isDemo(currentRun) && currentRun.mode!=='preview') return currentRun;
  return currentFeedbackBasis || state.runs.find(r=>runID(r)===state.current_feedback_run_id) || null;
}
async function startRun(action) {
  captureVisibleDrafts();
  if ((currentRun?.papers || []).some(p=>feedbackDrafts.has(draftKey(p)))) {toast('还有未保存反馈，请先点击“保存全部反馈”。',true);return;}
  const demo=action==='demo';
  const topics=demo ? [...selectedTopics] : state.topics.filter(t=>t.active!==false && selectedTopics.has(t.id)).map(t=>t.id);
  if (!topics.length) {toast('请先勾选并启用研究方向。',true);return;}
  try {
    const run_id=`manual-${action}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,8)}`;
    const payload=demo ? {topics,demo:true,run_id} : {topics,action,run_id,...(action==='accelerate' ? {base_run_id:runID(feedbackBasis() || {})} : {})};
    const job=await api('/api/run',payload);currentJob=job.job_id;
    $('#job-status').classList.remove('hidden');
    $('#job-status').textContent=demo ? '正在生成合成演示样例…' : '正在自动扩展检索、去重与筛选新文献…';
    renderState();pollJob();
  } catch(error) {toast(error.message,true);renderState();}
}
async function pollJob() {
  if (!currentJob) return;
  try {
    const job = await api(`/api/job?id=${encodeURIComponent(currentJob)}`);
    if (job.status === 'running') {
      if (job.message) {
        $('#job-status').textContent = job.message;
      }
      setTimeout(pollJob, 1200); return;
    }
    currentJob = null;
    $('#job-status').classList.add('hidden');
    if (job.status === 'error') throw new Error(job.error);
    if (job.result.semantic_setup) {
      await refreshState(); renderSemanticStatus();
      toast(job.result.message || '本地语义模型已准备。');
      return;
    }
    await refreshState();
    await loadRun(job.result.run_id || job.result.id);
    toast('本轮推荐已保存。');
  } catch (error) {
    currentJob = null;
    $('#job-status').classList.add('hidden');
    renderState();
    toast(error.message, true);
  }
}

function draftKey(paper) { return JSON.stringify([currentRun?.run_id || currentRun?.id, paper.topic_id, paper.paper_id]); }
function savedPayload(paper) {
  const v = paper.feedback || {};
  return {run_id:runID(currentRun),topic_id:paper.topic_id,paper_id:paper.paper_id,profile_version:paper.profile_version,
    rating:v.rating ?? null,read_status:v.read_status || v.status || 'unread',reason:v.reason || '',
    note:(v.note || '').trim(),tags:[...(v.tags || [])].sort()};
}
function formPayload(form,paper) {
  return {...savedPayload(paper),rating:form.elements.rating.value ? Number(form.elements.rating.value) : null,
    read_status:form.elements.read_status.value,reason:form.elements.reason.value,
    note:form.elements.note.value.trim(),tags:[...form.querySelectorAll('input[name="tags"]:checked')].map(el=>el.value).sort()};
}
function captureForm(form) {
  const paper=currentRun?.papers?.find(p=>paperKey(p)===form.dataset.key);
  if (!paper || currentRun.mode==='preview') return;
  const value=formPayload(form,paper),key=draftKey(paper);
  if (JSON.stringify(value)===JSON.stringify(savedPayload(paper))) feedbackDrafts.delete(key);
  else feedbackDrafts.set(key,value);
}
function captureVisibleDrafts() {
  if (feedbackSaving) return;
  document.querySelectorAll('#papers .feedback-form').forEach(captureForm);
}
function updateFeedbackButton() {
  const button=$('#save-all-feedback');
  const count=(currentRun?.papers || []).filter(p=>feedbackDrafts.has(draftKey(p))).length;
  button.hidden=false;
  button.title=currentRun?.mode==='preview' ? '下周预览不可评分；请使用本周推荐或加速迭代清单' : '保存本轮所有修改，包含方向筛选后隐藏的条目';
  button.disabled=feedbackSaving || !!currentJob || !currentRun || currentRun.mode==='preview';
  button.textContent=feedbackSaving ? '正在保存…' : `保存全部反馈${count ? `（${count}）` : ''}`;
}
async function persistFeedback(papers) {
  if (feedbackSaving || !papers.length) return;
  const run=currentRun;
  feedbackSaving=true;updateFeedbackButton();renderState();
  document.querySelectorAll('#papers .feedback-form input,#papers .feedback-form select,#papers .feedback-form textarea,#papers .feedback-form button,#run-select,#paper-filter,#unrated-only').forEach(e=>e.disabled=true);
  toast('正在保存反馈…');
  let saved=0;const failures=[];
  for (const paper of papers) {
    const key=draftKey(paper),payload=feedbackDrafts.get(key);
    if (!payload) continue;
    try {
      const result=await api('/api/feedback',payload);
      paper.feedback=result.feedback || result;feedbackDrafts.delete(key);saved++;
    } catch(error) { failures.push(`${paper.title}: ${error.message}`); }
  }
  // DOM still contains submitted values; do not recapture stale values while rerendering.
  if (currentRun===run) renderPapers();
  feedbackSaving=false;
  $('#run-select').disabled=false;$('#paper-filter').disabled=false;$('#unrated-only').disabled=false;
  try {await refreshState();} catch { /* Feedback already saved; retain local results. */ }
  updateFeedbackButton();
  toast(failures.length ? `已保存${saved}篇，${failures.length}篇失败；未保存的修改仍保留，可重试。${failures[0]}` : `已保存${saved}篇反馈，可以进行下周预览或评分后加速迭代。`,!!failures.length);
}
async function saveFeedback(form,retract=false) {
  if (feedbackSaving) return;
  captureVisibleDrafts();
  const paper=currentRun.papers.find(p=>paperKey(p)===form.dataset.key);
  if (!paper) return;
  if (retract) feedbackDrafts.set(draftKey(paper),{...formPayload(form,paper),rating:null});
  if (!feedbackDrafts.has(draftKey(paper))) {toast('反馈未改变，无需重复保存。');return;}
  await persistFeedback([paper]);
}
$('#save-all-feedback').addEventListener('click',async()=>{
  if (feedbackSaving || currentJob || currentRun?.mode==='preview') return;
  captureVisibleDrafts();
  const changed=(currentRun?.papers || []).filter(p=>feedbackDrafts.has(draftKey(p)));
  if (!changed.length) {toast('没有待保存的修改。');return;}
  await persistFeedback(changed);
});
['input','change'].forEach(type=>$('#papers').addEventListener(type,event=>{
  const form=event.target.closest('.feedback-form');if (form) {captureForm(form);updateFeedbackButton();}
}));

$('#add-topic').addEventListener('click', () => editTopic());
$('#empty-add').addEventListener('click', () => editTopic());
$('#topic-list').addEventListener('click', event => {
  const button = event.target.closest('[data-edit-topic]');
  if (button) editTopic(button.dataset.editTopic);
});
$('#topic-list').addEventListener('change', event => {
  const id = event.target.dataset.selectTopic;
  if (!id) return;
  event.target.checked ? selectedTopics.add(id) : selectedTopics.delete(id);
  $('#real-run').disabled = !!currentJob || feedbackSaving || !state.topics.some(t => t.active !== false && selectedTopics.has(t.id));
  $('#demo-run').disabled = !!currentJob || feedbackSaving || selectedTopics.size === 0;
  $('#preview-run').disabled = !!currentJob || feedbackSaving || selectedTopics.size === 0;
});
document.querySelectorAll('.close-dialog').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
// Keep topic and settings forms open when the backdrop is clicked.
$('#topic-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  $('.form-error', form).classList.add('hidden');
  const payload = {};
  for (const key of ['id', 'title', 'group', 'stage']) payload[key] = form.elements[key].value.trim();
  payload.topic_id = payload.id;
  for (const key of ['questions', 'keywords', 'method_terms', 'exclude_terms']) payload[key] = lines(form.elements[key].value);
  payload.core_concepts = conceptLines(form.elements.core_concepts.value);
  payload.active = form.elements.active.checked;
  const button = $('button[type="submit"]', form); button.disabled = true;
  try {
    await api('/api/topic', payload);
    if (payload.active) selectedTopics.add(payload.id); else selectedTopics.delete(payload.id);
    $('#topic-dialog').close();
    await refreshState();
    if (currentRun) renderPapers();
    toast('方向已保存。');
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});
$('#settings-open').addEventListener('click', () => {
  const form = $('#settings-form');
  $('.form-error', form).classList.add('hidden');
  const settings = state.settings || {};
  const schedule = settings.schedule || {};
  form.elements.core_count.value = settings.recommendation_counts?.core ?? 5;
  form.elements.related_count.value = settings.recommendation_counts?.related ?? 15;
  form.elements.semantic_enabled.checked = !!settings.semantic?.enabled;
  form.elements.semantic_model_dir.value = settings.semantic?.model_dir || '';
  form.elements.semantic_weight.value = settings.semantic?.weight ?? 0.25;
  renderSemanticStatus();
  form.elements.schedule_enabled.checked = !!schedule.enabled;
  form.elements.day.value = String(schedule.day ?? 0);
  form.elements.time.value = schedule.time || '09:00';
  form.elements.timezone.value = schedule.timezone || 'Asia/Shanghai';
  form.elements.publisher_enabled.checked = !!settings.publisher_access?.enabled;
  $('#access-status').textContent = `官方摘要接口密钥：${state.access_status?.elsevier_key_configured ? '已配置' : '未配置；仍可使用公共来源及主页补摘要'}。`;
  $('#settings-dialog').showModal();
});
$('#settings-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  $('.form-error', form).classList.add('hidden');
  const payload = {recommendation_counts: {core:Number(form.elements.core_count.value),related:Number(form.elements.related_count.value)}, semantic: {enabled: form.elements.semantic_enabled.checked, model_dir: form.elements.semantic_model_dir.value.trim(), weight: Number(form.elements.semantic_weight.value)}, publisher_access: {enabled: form.elements.publisher_enabled.checked}, schedule: {
    enabled: form.elements.schedule_enabled.checked, day: Number(form.elements.day.value),
    time: form.elements.time.value, timezone: form.elements.timezone.value.trim()
  }};
  const button = $('button[type="submit"]', form); button.disabled = true;
  try { await api('/api/settings', payload); await refreshState(); $('#settings-dialog').close(); toast('设置已保存。'); }
  catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});
$('#real-run').addEventListener('click', () => startRun('accelerate'));
$('#reselect-run').addEventListener('click', () => startRun('reselect'));
$('#demo-run').addEventListener('click', () => {$('#settings-dialog').close();startRun('demo');});
$('#preview-run').addEventListener('click', () => startRun('preview_next'));

$('#candidate-filter').addEventListener('change', renderCandidates);
function renderSemanticStatus() {
  const status = state.semantic_status || {};
  $('#semantic-status').textContent = status.message || (status.ready ? '模型可用，启用后将在本机辅助排序。' : status.available ? '可选依赖已安装；请下载或指定完整模型目录。' : '普通模式可用；语义增强需先安装可选依赖，详见“本地语义模型”使用说明。');
  $('#semantic-setup').disabled = !!currentJob || feedbackSaving || !status.available;
}
$('#semantic-setup').addEventListener('click', async () => {
  if (currentJob) return;
  try {
    const job = await api('/api/semantic-setup', {model_dir: $('#settings-form').elements.semantic_model_dir.value.trim()});
    currentJob = job.job_id;
    $('#job-status').classList.remove('hidden');
    $('#job-status').textContent = '正在联网下载语义模型到本机。首次下载可能需要几分钟，请保留本地服务。';
    renderState(); renderSemanticStatus(); pollJob();
  } catch (error) { toast(error.message, true); }
});
$('#run-select').addEventListener('change', event => loadRun(event.target.value).catch(error => toast(error.message, true)));
$('#paper-filter').addEventListener('change', renderPapers);
$('#unrated-only').addEventListener('change', renderPapers);
$('#export-toggle').addEventListener('click', () => $('#export-options').classList.toggle('hidden'));
document.addEventListener('click', event => { if (!event.target.closest('.export-menu')) $('#export-options').classList.add('hidden'); });
$('#papers').addEventListener('click', event => {
  const star = event.target.closest('[data-rating]');
  const clear = event.target.closest('.clear-rating');
  const form = event.target.closest('.feedback-form');
  if (star && form) {
    const value = Number(star.dataset.rating);
    form.elements.rating.value = value;
    form.querySelectorAll('.star').forEach(button => { button.classList.toggle('selected', Number(button.dataset.rating) <= value); button.setAttribute('aria-pressed', Number(button.dataset.rating) === value); });
    $('.rating-text', form).textContent = `${value} 分 · 待保存`;
    $('.clear-rating', form).disabled = false;
    captureForm(form);updateFeedbackButton();
  }
  if (clear && form) saveFeedback(form, true);
});
$('#papers').addEventListener('submit', event => {
  event.preventDefault();
  if (event.target.classList.contains('feedback-form')) saveFeedback(event.target);
});
$('#backup').addEventListener('click', async () => {
  const button = $('#backup'); button.disabled = true;
  try {
    const response = await fetch('/api/backup', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Lite-Token': token}, body: '{}'});
    if (!response.ok) { const result = await response.json(); throw new Error(result.error); }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a'); link.href = url; link.download = `lite-recommender-backup-${new Date().toISOString().slice(0, 10)}.zip`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    toast('备份已下载，包含本地项目数据。分享前请使用发布打包命令移除私人数据。');
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});

refreshState(true).catch(error => { const notice = $('#notice'); notice.textContent = `无法读取项目：${error.message}`; notice.classList.remove('hidden'); });
// Pick up scheduler results without disturbing a form being edited.
setInterval(async () => {
  if (currentJob || feedbackSaving || $('dialog[open]') || document.activeElement?.closest('.feedback-form')) return;
  try {
    const latestState = await api('/api/state');
    latestState.topics = latestState.topics.map(topic => ({...topic, id: topic.id || topic.topic_id}));
    const changed = latestState.runs.map(runID).join() !== state.runs.map(runID).join();
    state = latestState; renderState();
    if (changed) toast('有新的推荐结果，可从历史清单中查看。');
  } catch { /* Keep the current reading list while the server restarts. */ }
}, 60000);
