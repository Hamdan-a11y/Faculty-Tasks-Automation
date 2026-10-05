// Main interactivity for CLO Mapper
const closTable = document.getElementById('closTable');
const questionsTable = document.getElementById('questionsTable');
const mappingTable = document.getElementById('mappingTable');
const closEmpty = document.getElementById('closEmpty');
const questionsEmpty = document.getElementById('questionsEmpty');
const mappingEmpty = document.getElementById('mappingEmpty');
const cloStatus = document.getElementById('cloStatus');
const questionStatus = document.getElementById('questionStatus');
const mappingStatus = document.getElementById('mappingStatus');
const cloCourseInput = document.getElementById('cloCourse');
const mappingCourse = document.getElementById('mappingCourse');

let closCache = [];
let questionCache = [];

const setMessage = (el, msg) => {
  if (!el) return;
  el.textContent = msg;
};

const setLoading = (btn, isLoading, label) => {
  if (!btn) return;
  btn.disabled = isLoading;
  if (label) btn.textContent = isLoading ? label : btn.dataset.label || btn.textContent;
};

const renderClos = (clos = []) => {
  closCache = clos;
  closTable.innerHTML = '';
  if (!clos.length) {
    closEmpty.style.display = 'block';
    return;
  }
  closEmpty.style.display = 'none';
  clos.forEach((clo) => {
    const row = document.createElement('tr');
    row.innerHTML = `
      <td>${clo.code}</td>
      <td>${clo.description}</td>
      <td>${clo.domain || ''}</td>
      <td>${clo.bt_level || ''}</td>
    `;
    closTable.appendChild(row);
  });
  updateCourseOptions();
};

const renderQuestions = (questions = []) => {
  questionCache = questions;
  questionsTable.innerHTML = '';
  if (!questions.length) {
    questionsEmpty.style.display = 'block';
    return;
  }
  questionsEmpty.style.display = 'none';
  questions.forEach((q) => {
    const row = document.createElement('tr');
    row.innerHTML = `<td>${q.text}</td><td>${q.source_filename || ''}</td>`;
    questionsTable.appendChild(row);
  });
};

const renderMappings = (results = []) => {
  mappingTable.innerHTML = '';
  if (!results.length) {
    mappingEmpty.style.display = 'block';
    return;
  }
  mappingEmpty.style.display = 'none';
  results.forEach((item) => {
    const status = item.status || (item.clo_code ? 'mapped' : 'unmapped');
    const reason = item.reason ? ` (${item.reason})` : '';
    const row = document.createElement('tr');
    row.innerHTML = `
      <td>${item.question_text}</td>
      <td>${item.clo_code ? `${item.clo_code} - ${item.clo_description || ''}` : ''} <span class="muted">${status}${reason}</span></td>
    `;
    mappingTable.appendChild(row);
  });
};

const fetchState = async () => {
  try {
    const res = await fetch('/clomapper/state/');
    const data = await res.json();
    renderClos(data.clos || []);
    renderQuestions(data.questions || []);
    const mappedResults = (data.questions || [])
      .filter((q) => q.mapping)
      .map((q) => ({
        question_id: q.id,
        question_text: q.text,
        clo_id: q.mapping.clo_id,
        clo_code: q.mapping.clo_code,
        clo_description: q.mapping.clo_description,
        score: q.mapping.score,
        status: q.mapping.auto_mapped ? 'mapped' : 'manual_override',
      }));
    renderMappings(mappedResults);
  } catch (err) {
    console.error(err);
    setMessage(mappingStatus, 'Unable to load CLO Mapper data. Please click Refresh Lists.');
  }
};

const updateCourseOptions = () => {
  if (!mappingCourse) return;
  const selected = mappingCourse.value;
  const unique = Array.from(new Set((closCache || []).map((c) => (c.course || '').trim()).filter(Boolean)));
  mappingCourse.innerHTML = '<option value="">All courses</option>';
  unique.forEach((course) => {
    const opt = document.createElement('option');
    opt.value = course;
    opt.textContent = course;
    mappingCourse.appendChild(opt);
  });
  if (selected && unique.includes(selected)) {
    mappingCourse.value = selected;
  }
};

const handleCloUpload = async (event) => {
  event.preventDefault();
  const fileInput = document.getElementById('cloFile');
  if (!fileInput.files || !fileInput.files[0]) {
    setMessage(cloStatus, 'Please choose a .docx or .pdf file.');
    return;
  }
  const submitBtn = event.target.querySelector('button[type="submit"]');
  if (submitBtn) submitBtn.dataset.label = submitBtn.textContent;
  setLoading(submitBtn, true, 'Uploading...');
  const formData = new FormData();
  formData.append('file', fileInput.files[0]);
  if (cloCourseInput && cloCourseInput.value.trim()) {
    formData.append('course', cloCourseInput.value.trim());
  }
  try {
    const res = await fetch('/clomapper/upload_clos/', { method: 'POST', body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Upload failed');
    setMessage(cloStatus, `Saved ${data.clos.length} CLOs`);
    renderClos(data.clos);
  } catch (err) {
    console.error(err);
    setMessage(cloStatus, `${err.message} You can fix the file and try again.`);
  } finally {
    setLoading(submitBtn, false);
  }
};

const handleQuestionUpload = async (event) => {
  event.preventDefault();
  const submitBtn = event.target.querySelector('button[type="submit"]');
  if (submitBtn) submitBtn.dataset.label = submitBtn.textContent;
  setLoading(submitBtn, true, 'Uploading...');
  const formData = new FormData();
  const assessment = document.getElementById('assessmentType').value;
  const text = document.getElementById('questionText').value.trim();
  const files = document.getElementById('questionFiles').files;
  formData.append('assessment_type', assessment);
  if (text) formData.append('text', text);
  if (files && files.length) {
    Array.from(files).forEach((file) => formData.append('files', file));
  }
  if (!text && (!files || !files.length)) {
    setMessage(questionStatus, 'Provide text or upload at least one file.');
    setLoading(submitBtn, false);
    return;
  }
  try {
    const res = await fetch('/clomapper/upload_questions/', { method: 'POST', body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Upload failed');
    setMessage(questionStatus, `Saved ${data.questions.length} questions`);
    // Refresh full state so previously saved questions stay visible alongside new ones.
    await fetchState();
    // Clear inputs after successful upload
    document.getElementById('questionText').value = '';
    document.getElementById('questionFiles').value = '';
  } catch (err) {
    console.error(err);
    setMessage(questionStatus, `${err.message} You can correct the input and retry.`);
  } finally {
    setLoading(submitBtn, false);
  }
};

const runMapping = async () => {
  const runBtn = document.getElementById('runMappingBtn');
  if (runBtn) runBtn.dataset.label = runBtn.textContent;
  setLoading(runBtn, true, 'Mapping...');
  const assessment = document.getElementById('mappingAssessment').value;
  const course = mappingCourse ? mappingCourse.value.trim() : '';
  const formData = new FormData();
  if (assessment) formData.append('assessment_type', assessment);
  if (course) formData.append('course', course);
  try {
    const res = await fetch('/clomapper/run_mapping/', { method: 'POST', body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Mapping failed');
    setMessage(mappingStatus, `Mapped ${data.results.length} questions`);
    // Refresh full state to retain previous mappings and show newly mapped ones together.
    await fetchState();
  } catch (err) {
    console.error(err);
    setMessage(mappingStatus, `${err.message} Please review CLOs/questions and try again.`);
  } finally {
    setLoading(runBtn, false);
  }
};

const saveManualMapping = async (questionId, cloId, score) => {
  const formData = new FormData();
  formData.append('question_id', questionId);
  formData.append('clo_id', cloId);
  if (score !== undefined && score !== null) formData.append('score', score);
  try {
    const res = await fetch('/clomapper/save_manual_mapping/', { method: 'POST', body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Save failed');
    setMessage(mappingStatus, 'Mapping saved');
    await fetchState();
  } catch (err) {
    console.error(err);
    setMessage(mappingStatus, `${err.message} You can retry the manual mapping.`);
  }
};

const bindEvents = () => {
  const cloForm = document.getElementById('cloUploadForm');
  const questionForm = document.getElementById('questionForm');
  const runBtn = document.getElementById('runMappingBtn');
  const refreshBtn = document.getElementById('refreshBtn');
  const clearClosBtn = document.getElementById('clearClosBtn');
  const clearQuestionsBtn = document.getElementById('clearQuestionsBtn');

  if (cloForm) cloForm.addEventListener('submit', handleCloUpload);
  if (questionForm) questionForm.addEventListener('submit', handleQuestionUpload);
  if (runBtn) runBtn.addEventListener('click', runMapping);
  if (refreshBtn) refreshBtn.addEventListener('click', fetchState);
  if (clearClosBtn) clearClosBtn.addEventListener('click', () => clearData('clos'));
  if (clearQuestionsBtn) clearQuestionsBtn.addEventListener('click', () => clearData('questions'));
};

const clearData = async (scope) => {
  const formData = new FormData();
  formData.append('scope', scope);
  try {
    const res = await fetch('/clomapper/clear/', { method: 'POST', body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Clear failed');
    setMessage(mappingStatus, `Cleared ${scope}`);
    await fetchState();
  } catch (err) {
    console.error(err);
    setMessage(mappingStatus, `${err.message} Please retry.`);
  }
};

bindEvents();
fetchState();
