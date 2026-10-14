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
let toastTimer;

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
  $('#topic-list').innerHTML = groups.size ? [...groups].map(([group, topics]) => `<div class="topic-group"><h3>${escapeHTML(group)}</h3>${topics.map(t => `<div class="topic-item ${t.active === false ? 'inactive' : ''}"><label><input type="checkbox" data-select-topic="${escapeHTML(t.id)}" ${selectedTopics.has(t.id) ? 'checked' : ''}><span>${escapeHTML(t.title || t.id)}<small>${escapeHTML(stages[t.stage] || t.stage || '综述入门')}${t.active === false ? ' · 未启用' : ''} · 本周 ${state.stats.used_by_topic?.[t.id] ?? 0}/${state.settings.budget ?? 10}</small></span></label><button class="icon-button" data-edit-topic="${escapeHTML(t.id)}" aria-label="编辑 ${escapeHTML(t.title || t.id)}">⋯</button></div>`).join('')}</div>`).join('') : '<p class="no-topics">尚无方向，点击 ＋ 创建。</p>';
  $('#stat-topics').textContent = state.topics.filter(t => t.active !== false).length;
  $('#stat-budget').textContent = state.settings.budget ?? 10;
  $('#budget-used').textContent = `各方向本周合计 ${state.stats.used_this_week ?? 0} 篇`;
  $('#stat-runs').textContent = state.runs.length;
  $('#stat-feedback').textContent = state.stats.rated ?? state.stats.feedback_count ?? state.stats.ratings ?? state.stats.feedback ?? 0;
  const runs = [...state.runs].sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
  $('#run-select').innerHTML = runs.length ? runs.map(r => `<option value="${escapeHTML(runID(r))}">${escapeHTML(dateText(r.created_at) || runID(r))}${isDemo(r) ? ' · 离线演示' : r.mode === 'preview' ? ' · 检索预览' : ''} · ${r.count ?? '?'} 篇${r.status === 'error' ? ' · 失败' : ''}</option>`).join('') : '<option value="">尚无推荐记录</option>';
  if (currentRun) $('#run-select').value = currentRun.run_id || currentRun.id;
  $('#real-run').disabled = !!currentJob || !state.topics.some(t => t.active !== false && selectedTopics.has(t.id));
  $('#demo-run').disabled = !!currentJob || selectedTopics.size === 0;
  $('#preview-run').disabled = !!currentJob || selectedTopics.size === 0;
  $('#continue-search').disabled = !!currentJob || !currentRun || isDemo(currentRun);
  renderSemanticStatus();
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
const rejectionLabels = {missing_abstract: '来源未提供摘要，保留待核验', missing_core_concept: '未覆盖全部核心概念', below_core_score: '匹配分低于核心推荐门槛', excluded_term: '命中排除词', future_date: '发表日期晚于本轮运行日期', already_recommended: '此方向此前已正式推荐', not_adjacent: '缺少与当前研究问题相关的线索'};
function paperCategory(paper) {
  return paper.classification || paper.category || (paper.exploration ? 'exploration' : paper.eligible ? 'core' : paper.exploration_eligible ? 'exploration' : !paper.abstract ? 'pending' : 'rejected');
}
function renderAccess(paper) {
  return `<p class="paper-access"><button type="button" class="secondary compact" data-import-pdf="${escapeHTML(paper.paper_id)}" data-import-topic="${escapeHTML(paper.topic_id || '')}">导入本地 PDF</button></p>`;
}
function renderPaper(paper, index) {
  const feedback = paper.feedback || {};
  const rating = feedback.rating;
  const demo = isDemo(currentRun);
  const url = demo ? '' : safeURL(paper.url || (paper.doi ? `https://doi.org/${paper.doi}` : ''));
  const matched = Array.isArray(paper.matched_terms) ? paper.matched_terms.filter(t => typeof t === 'string') : [];
  const selectedTags = new Set((feedback.tags || []).map(t => typeof t === 'string' ? t : t.tag));
  const sources = paper.sources || paper.source || '';
  const sourceText = Array.isArray(sources) ? sources.map(s => typeof s === 'string' ? s : s.name || '').join(', ') : String(sources);
  const reasonOptions = '<option value="">请选择原因</option>' + Object.entries(reasons).map(([value, label]) => `<option value="${value}" ${feedback.reason === value ? 'selected' : ''}>${label}</option>`).join('');
  return `<article class="paper-card" data-paper-key="${escapeHTML(paperKey(paper))}">
    <div class="paper-topline"><span class="paper-index">${String(index + 1).padStart(2, '0')}</span><span class="topic-badge">${escapeHTML(topicTitle(paper.topic_id))}</span>${paper.classification || paper.category || paper.exploration ? `<span class="category-badge ${paperCategory(paper) === 'exploration' ? 'exploration' : ''}">${escapeHTML(categoryLabels[paperCategory(paper)] || paperCategory(paper))}</span>` : ''}${paper.kind ? `<span class="kind-badge">${escapeHTML(kinds[paper.kind] || paper.kind)}</span>` : ''}<span class="paper-date">${escapeHTML(paper.published || '')}</span></div>
    <div class="paper-title-row"><div><h3>${url ? `<a href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(paper.title)} <span class="external" aria-hidden="true">↗</span></a>` : escapeHTML(paper.title)}</h3><p class="authors">${escapeHTML(authorText(paper.authors))}</p></div><div class="score"><strong>${numeric(paper.score)}</strong><span>推荐分</span></div></div>
    ${!demo ? renderAccess(paper) : ''}
    <p class="recommendation">${escapeHTML(paper.reason || '该论文符合当前方向的检索条件。')}</p>
    <div class="paper-provenance"><span>依据：${escapeHTML(evidenceText(paper))}</span>${sourceText ? `<span>来源：${escapeHTML(sourceText)}</span>` : ''}<span>基础 ${numeric(paper.base_score)} · 偏好 ${Number(paper.adjustment || 0) >= 0 ? '+' : ''}${numeric(paper.adjustment || 0)}</span>${paper.semantic?.cosine != null ? `<span>语义相似度 ${Number(paper.semantic.cosine).toFixed(3)}（非概率）· 排序修正 ${numeric(paper.semantic.adjustment)}</span>` : ''}${paper.doi ? `<span>DOI：${escapeHTML(paper.doi)}</span>` : ''}</div>
    ${paper.abstract ? `<details class="abstract"><summary>阅读摘要</summary><p>${escapeHTML(paper.abstract)}</p></details>` : '<p class="metadata-only">当前来源未返回摘要，推荐依据限于题名和元数据。</p>'}
    ${currentRun.mode === 'preview' ? '<p class="demo-note">检索预览：不占周配额，可包含已推荐论文。调参后使用“检索并推荐”生成正式批次并评分。</p>' : ''}<form ${currentRun.mode === 'preview' ? 'hidden' : ''} class="feedback-form" data-key="${escapeHTML(paperKey(paper))}">
      <div class="feedback-top"><div><span class="feedback-label">${demo ? '演示相关性评分' : '你的相关性评分'}</span><div class="rating-stars" role="group" aria-label="相关性评分">${[1, 2, 3, 4, 5].map(n => `<button type="button" class="star ${n <= rating ? 'selected' : ''}" data-rating="${n}" aria-label="${n} 分" aria-pressed="${n === rating}">★</button>`).join('')}<span class="rating-text">${rating ? `${rating} 分` : '待评分'}</span><button type="button" class="clear-rating text-button" ${!rating ? 'disabled' : ''}>撤销</button></div><input name="rating" type="hidden" value="${rating ?? ''}"></div><label class="reading-state"><span>阅读状态</span><select name="read_status">${Object.entries(readStates).map(([value, label]) => `<option value="${value}" ${(feedback.read_status || feedback.status || 'unread') === value ? 'selected' : ''}>${label}</option>`).join('')}</select></label></div>
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
    ['来源返回', item.raw_count], ['去重候选', item.deduped_count], ['已推荐排除', item.seen_count],
    ['待核验', item.pending_count], ['核心候选', item.core_count], ['探索候选', item.exploration_count],
    ['被筛除', item.rejected_count], ['本轮推荐', item.selected_count], ['本轮可用配额', item.budget_remaining]
  ].map(([label, value]) => `<span>${label}<b>${value == null ? '—' : escapeHTML(value)}</b></span>`).join('')}</div></div>`).join('');
  $('#continue-search').textContent = `继续检索第 ${Number(currentRun.search_page || 0) + 2} 页（预览）`;
  $('#continue-search').disabled = !!currentJob;
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
    return `<article class="candidate-card"><div class="candidate-topline"><span class="topic-badge">${escapeHTML(topicTitle(p.topic_id))}</span><span class="category-badge ${category === 'exploration' ? 'exploration' : category === 'pending' ? 'pending' : ''}">${escapeHTML(categoryLabels[category] || category)}</span>${p.seen || p.already_seen || p.previously_recommended ? '<span class="kind-badge">此前已正式推荐</span>' : ''}<span>匹配分 ${numeric(p.score)}</span></div><h4>${url ? `<a href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(p.title)} ↗</a>` : escapeHTML(p.title)}</h4><p class="candidate-reasons">${escapeHTML((Array.isArray(rejection) ? rejection : [rejection]).map(reason => rejectionLabels[reason] || reason).join('；') || p.reason || (category === 'pending' ? '当前来源未返回摘要；可导入 PDF 并核对摘要后重新预览。' : '通过对应候选分类，是否推荐还受排序、去重及配额影响。'))}</p>${p.missing_concepts?.length ? `<p class="candidate-reasons">待核验概念：${escapeHTML(p.missing_concepts.join('；'))}</p>` : ''}${p.excluded_terms?.length ? `<p class="candidate-reasons">排除词：${escapeHTML(p.excluded_terms.join('；'))}</p>` : ''}${p.abstract ? `<details class="abstract"><summary>查看候选摘要</summary><p>${escapeHTML(p.abstract)}</p></details>` : ''}${renderAccess(p)}</article>`;
  }).join('') : '<p class="candidate-empty">本轮没有此类候选。历史批次可能未保存新版筛选明细。</p>';
}

function renderPapers() {
  if (!currentRun) return;
  const allPapers = currentRun.papers || [];
  const filter = $('#paper-filter').value;
  const papers = allPapers.filter(p => (!filter || p.topic_id === filter) && (!$('#unrated-only').checked || !p.feedback?.rating));
  $('#paper-count').textContent = `${allPapers.length} 篇`;
  $('#run-meta').textContent = `${dateText(currentRun.created_at) || currentRun.run_id}${isDemo(currentRun) ? ' · 离线演示：不占用真实周预算' : currentRun.mode === 'preview' ? ' · 真实检索预览：不占周配额' : ' · 真实检索'}${currentRun.status ? ` · ${runStatuses[currentRun.status] || currentRun.status}` : ''}`;
  $('#run-warnings').innerHTML = (currentRun.warnings || []).length ? `<div class="warnings"><strong>本轮说明</strong><ul>${currentRun.warnings.map(w => `<li>${escapeHTML(typeof w === 'string' ? w : JSON.stringify(w))}</li>`).join('')}</ul></div>` : '';
  $('#papers').innerHTML = papers.length ? papers.map(renderPaper).join('') : '<div class="empty-state small-empty"><h3>当前没有符合条件的文献</h3><p>检索结果不足、每周预算已用完，或筛选条件未匹配；请查看本轮说明。</p></div>';
  $('#filter-bar').classList.toggle('hidden', !allPapers.length);
  $('#export-toggle').disabled = false;
  for (const fmt of ['bib', 'json', 'markdown']) {
    $(`#export-${fmt}`).href = `/api/export?id=${encodeURIComponent(currentRun.run_id)}&format=${fmt}`;
  }
}

async function refreshState(loadLatest = false) {
  state = await api('/api/state');
  state.topics = state.topics.map(topic => ({...topic, id: topic.id || topic.topic_id}));
  renderState();
  if (loadLatest && state.runs.length) {
    const latest = [...state.runs].sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')))[0];
    await loadRun(runID(latest));
  }
}
async function loadRun(id) {
  if (!id) return;
  currentRun = await api(`/api/run?id=${encodeURIComponent(id)}`);
  currentCandidates = []; currentDiagnostics = currentRun.diagnostics || {};
  try { const candidates = await api(`/api/candidates?id=${encodeURIComponent(id)}`); currentCandidates = candidates.candidates || []; currentDiagnostics = candidates.diagnostics || currentDiagnostics; } catch { /* Earlier batches may have no candidate snapshot. */ }
  try { renderPDFReport(await api(`/api/pdf-report?id=${encodeURIComponent(id)}`)); } catch { renderPDFReport(null); }
  const ids = [...new Set((currentRun.papers || []).map(p => p.topic_id))];
  $('#paper-filter').innerHTML = '<option value="">全部方向</option>' + ids.map(id => `<option value="${escapeHTML(id)}">${escapeHTML(topicTitle(id))}</option>`).join('');
  $('#run-select').value = id;
  renderPapers();
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

async function startRun(demo, preview = false, searchPage = 0, fixedTopics = null, parentRunID = '') {
  const topics = fixedTopics || (demo || preview ? [...selectedTopics] : state.topics.filter(t => t.active !== false && selectedTopics.has(t.id)).map(t => t.id));
  if (!topics.length) { toast(demo ? '请先新增并勾选至少一个研究方向。' : '请先编辑方向，启用后再进行真实检索。', true); return; }
  try {
    $('#real-run').disabled = true; $('#demo-run').disabled = true; $('#preview-run').disabled = true;
    const run_id = `manual-${demo ? 'demo' : preview ? 'preview' : 'real'}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    const job = await api('/api/run', {topics, demo, preview, run_id, search_page: searchPage, ...(parentRunID ? {parent_run_id: parentRunID} : {})});
    currentJob = job.job_id;
    const banner = $('#job-status');
    banner.classList.remove('hidden');
    banner.textContent = demo ? '正在运行离线演示，检查检索、排序与报告流程…' : '正在检索与核验候选文献，请保持本地服务运行。你可以继续查看历史记录。';
    renderState();
    pollJob();
  } catch (error) { toast(error.message, true); renderState(); }
}
async function pollJob() {
  if (!currentJob) return;
  try {
    const job = await api(`/api/job?id=${encodeURIComponent(currentJob)}`);
    if (job.status === 'running') {
      if (job.message) {
        $('#job-status').innerHTML = `<span>${escapeHTML(job.message)}</span>${job.waiting ? ` <button type="button" class="secondary compact" data-pdf-control="continue">继续下载</button> <button type="button" class="secondary compact" data-pdf-control="skip">${job.stage === 'institution_browser' ? '机构无法获取，转查 Sci-Hub' : '跳过此论文'}</button>` : ''}`;
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
    if (job.result.pdf_export) {
      renderState();
      const result = job.result;
      renderPDFReport(result.report);
      if (!result.has_pdfs) {
        toast('没有获取到PDF，未下载空ZIP。请查看页面上的逐篇原因。', true);
        return;
      }
      const response = await fetch(`/api/pdf-archive?id=${encodeURIComponent(result.run_id)}`);
      if (!response.ok) throw new Error((await response.json()).error);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a'); link.href = url; link.download = 'literature-pdfs.zip'; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      toast(`PDF获取完成：新下载${result.downloaded}，已有${result.already_exists}，无直链${result.no_link}，失败${result.failed}。ZIP内含逐篇报告。`);
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

async function saveFeedback(form, retract = false) {
  const paper = currentRun.papers.find(p => paperKey(p) === form.dataset.key);
  if (!paper) return;
  const selectedRating = form.elements.rating.value;
  const payload = {
    topic_id: paper.topic_id, paper_id: paper.paper_id,
    profile_version: paper.profile_version,
    rating: retract || !selectedRating ? null : Number(selectedRating),
    read_status: form.elements.read_status.value, reason: form.elements.reason.value,
    note: form.elements.note.value.trim(), tags: [...form.querySelectorAll('input[name="tags"]:checked')].map(el => el.value)
  };
  const button = $('button[type="submit"]', form);
  button.disabled = true;
  try {
    const result = await api('/api/feedback', payload);
    paper.feedback = result.feedback || result;
    await refreshState();
    const article = form.closest('.paper-card');
    if ($('#unrated-only').checked && paper.feedback.rating) article.remove();
    else article.outerHTML = renderPaper(paper, [...$('#papers').children].indexOf(article));
    toast(retract ? '评分已撤销；阅读状态和笔记已保留。' : isDemo(currentRun) ? '演示反馈已保存，真实推荐偏好不受影响。' : '反馈已保存。原因和匹配词可帮助下一轮迭代。');
  } catch (error) { toast(error.message, true); button.disabled = false; }
}

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
  $('#real-run').disabled = !!currentJob || !state.topics.some(t => t.active !== false && selectedTopics.has(t.id));
  $('#demo-run').disabled = !!currentJob || selectedTopics.size === 0;
  $('#preview-run').disabled = !!currentJob || selectedTopics.size === 0;
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
  form.elements.budget.value = settings.budget || 10;
  form.elements.candidate_limit.value = settings.candidate_limit || 200;
  form.elements.semantic_enabled.checked = !!settings.semantic?.enabled;
  form.elements.semantic_model_dir.value = settings.semantic?.model_dir || '';
  form.elements.semantic_weight.value = settings.semantic?.weight ?? 0.25;
  renderSemanticStatus();
  form.elements.schedule_enabled.checked = !!schedule.enabled;
  form.elements.day.value = String(schedule.day ?? 0);
  form.elements.time.value = schedule.time || '09:00';
  form.elements.timezone.value = schedule.timezone || 'Asia/Shanghai';
  form.elements.publisher_enabled.checked = !!settings.publisher_access?.enabled;
  $('#access-status').textContent = `机构下载浏览器：${state.access_status?.browser_available ? '已安装' : '需安装浏览器组件，见 README'}；官方接口密钥：${state.access_status?.elsevier_key_configured ? '已配置' : '未配置（可直接使用机构浏览器下载）'}；PDF文本提取：${state.access_status?.pdf_text_available ? '可用' : '需安装 Poppler pdftotext'}。`;
  $('#settings-dialog').showModal();
});
$('#settings-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  $('.form-error', form).classList.add('hidden');
  const payload = {candidate_limit: Number(form.elements.candidate_limit.value), semantic: {enabled: form.elements.semantic_enabled.checked, model_dir: form.elements.semantic_model_dir.value.trim(), weight: Number(form.elements.semantic_weight.value)}, publisher_access: {enabled: form.elements.publisher_enabled.checked}, budget: Number(form.elements.budget.value), schedule: {
    enabled: form.elements.schedule_enabled.checked, day: Number(form.elements.day.value),
    time: form.elements.time.value, timezone: form.elements.timezone.value.trim()
  }};
  const button = $('button[type="submit"]', form); button.disabled = true;
  try { await api('/api/settings', payload); await refreshState(); $('#settings-dialog').close(); toast('设置已保存。'); }
  catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});
$('#real-run').addEventListener('click', () => startRun(false));
$('#demo-run').addEventListener('click', () => startRun(true));
$('#preview-run').addEventListener('click', () => startRun(false, true));
$('#continue-search').addEventListener('click', () => {
  if (!currentRun || currentJob) return;
  const topics = diagnosticEntries(currentDiagnostics).map(([id]) => id);
  startRun(false, true, Number(currentRun.search_page || 0) + 1, topics.length ? topics : null, runID(currentRun));
});
$('#candidate-filter').addEventListener('change', renderCandidates);
function renderSemanticStatus() {
  const status = state.semantic_status || {};
  $('#semantic-status').textContent = status.message || (status.ready ? '模型可用，启用后将在本机辅助排序。' : status.available ? '可选依赖已安装；请下载或指定完整模型目录。' : '普通模式可用；语义增强需先安装可选依赖，详见“本地语义模型”使用说明。');
  $('#semantic-setup').disabled = !!currentJob || !status.available;
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
  if (currentJob || $('dialog[open]') || document.activeElement?.closest('.feedback-form')) return;
  try {
    const latestState = await api('/api/state');
    latestState.topics = latestState.topics.map(topic => ({...topic, id: topic.id || topic.topic_id}));
    const changed = latestState.runs.length !== state.runs.length;
    state = latestState; renderState();
    if (changed) toast('有新的推荐结果，可从历史清单中查看。');
  } catch { /* Keep the current reading list while the server restarts. */ }
}, 60000);

$('#export-pdfs').addEventListener('click', async () => {
  if (!currentRun || currentJob) { toast('请选择批次并等待当前任务完成。', true); return; }
  if (isDemo(currentRun)) { toast('离线演示是合成论文，没有全文PDF。', true); return; }
  try {
    $('#export-options').classList.add('hidden');
    const job = await api('/api/pdf-download', {run_id: runID(currentRun)});
    currentJob = job.job_id;
    $('#job-status').classList.remove('hidden');
    $('#job-status').textContent = '正在获取本批次 PDF；需要机构登录时会打开下载浏览器，机构无法获取时转查 Sci-Hub…';
    renderState();
    pollJob();
  } catch (error) { toast(error.message, true); renderState(); }
});

function renderPDFReport(report) {
  const panel = $('#pdf-report');
  const items = report?.items || [];
  panel.classList.toggle('hidden', !items.length);
  if (!items.length) { panel.innerHTML = ''; return; }
  const statusNames = {downloaded:'已下载', already_exists:'已有PDF', no_link:'没有PDF直链', failed:'获取失败', not_selected:'未选中', demo_skipped:'演示无全文'};
  panel.innerHTML = `<strong>PDF下载结果：新下载 ${report.downloaded || 0}，已有 ${report.already_exists || 0}，失败 ${report.failed || 0}，无直链 ${report.no_link || 0}</strong><ul>${items.map(item => `<li>${escapeHTML(item.title)} — ${escapeHTML(statusNames[item.status] || item.status)}${item.message ? `<br>${escapeHTML(item.message)}` : ''}</li>`).join('')}</ul><p>机构与 Sci-Hub 均未取得有效 PDF 的论文会保留失败原因；没有 PDF 时不会生成空 ZIP。</p>`;
}

$('#job-status').addEventListener('click', async event => {
  const button = event.target.closest('[data-pdf-control]');
  if (!button || !currentJob) return;
  button.disabled = true;
  try { await api('/api/pdf-control', {job_id: currentJob, action: button.dataset.pdfControl}); }
  catch (error) { toast(error.message, true); button.disabled = false; }
});

let importTarget = null;
function beginPDFImport(event) {
  const button = event.target.closest('[data-import-pdf]');
  if (!button) return;
  const paper = [...(currentRun?.papers || []), ...currentCandidates].find(p => p.paper_id === button.dataset.importPdf && (!button.dataset.importTopic || p.topic_id === button.dataset.importTopic));
  if (!paper) return;
  importTarget = {run_id: runID(currentRun), paper_id: paper.paper_id, title: paper.title};
  $('#pdf-import-file').value = '';
  $('#pdf-import-file').click();
}
$('#papers').addEventListener('click', beginPDFImport);
$('#candidates').addEventListener('click', beginPDFImport);
$('#pdf-import-file').addEventListener('change', async event => {
  const file = event.target.files[0];
  if (!file || !importTarget) return;
  if (file.size > 25 * 1024 * 1024) return toast('PDF须不超过25 MB。', true);
  const target = {...importTarget};
  try {
    const data = await new Promise((resolve, reject) => {const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = reject; reader.readAsDataURL(file);});
    const result = await api('/api/pdf-import', {...target, data});
    toast(result.message);
    const form = $('#abstract-form');
    form.dataset.paperId = result.paper_id;
    $('#import-paper-title').textContent = target.title;
    form.elements.abstract.value = result.abstract_candidate || '';
    $('#abstract-dialog').showModal();
  } catch (error) {toast(error.message || '导入失败', true);}
});
$('#abstract-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $('button[type="submit"]', form); button.disabled = true;
  try {const result = await api('/api/pdf-abstract', {paper_id: form.dataset.paperId, abstract: form.elements.abstract.value}); $('#abstract-dialog').close(); toast(result.message);}
  catch(error) {toast(error.message, true);}
  finally {button.disabled = false;}
});
