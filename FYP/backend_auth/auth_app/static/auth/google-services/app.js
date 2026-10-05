const buttons = Array.from(document.querySelectorAll('.action-btn'));
const toast = document.getElementById('toast');
const toastText = document.getElementById('toastText');
const statusValue = document.getElementById('statusValue');
const root = document.body;
const isConnected = (root.dataset.googleConnected || '').toLowerCase() === 'true';
const connectUrl = root.dataset.connectUrl || '/google/connect/';
const disconnectUrl = root.dataset.disconnectUrl || '/google/disconnect/';
const disconnectButtons = Array.from(document.querySelectorAll('.disconnect-btn'));
const driveForm = document.getElementById('driveUploadForm');
const driveFolderInput = document.getElementById('driveFolderInput');
const driveFileInput = document.getElementById('driveFileInput');
const driveUploadStatus = document.getElementById('driveUploadStatus');
const driveUploadsList = document.getElementById('driveUploadsList');
const loadCoursesBtn = document.getElementById('loadCoursesBtn');
const coursesStatus = document.getElementById('coursesStatus');
const courseList = document.getElementById('courseList');
const courseSelect = document.getElementById('courseSelect');
const courseworkForm = document.getElementById('courseworkForm');
const courseworkTitle = document.getElementById('courseworkTitle');
const courseworkDescription = document.getElementById('courseworkDescription');
const courseworkPoints = document.getElementById('courseworkPoints');
const courseworkDueDate = document.getElementById('courseworkDueDate');
const courseworkDueTime = document.getElementById('courseworkDueTime');
const courseworkDueTimeHour = document.getElementById('courseworkDueTimeHour');
const courseworkDueTimeMinute = document.getElementById('courseworkDueTimeMinute');
const courseworkDueTimePeriod = document.getElementById('courseworkDueTimePeriod');
const courseworkDriveFile = document.getElementById('courseworkDriveFile');
const courseworkFile = document.getElementById('courseworkFile');
const courseworkStatus = document.getElementById('courseworkStatus');
const courseCreateForm = document.getElementById('courseCreateForm');
const courseNameInput = document.getElementById('courseNameInput');
const courseSectionInput = document.getElementById('courseSectionInput');
const courseDescriptionHeadingInput = document.getElementById('courseDescriptionHeadingInput');
const courseRoomInput = document.getElementById('courseRoomInput');
const courseDescriptionInput = document.getElementById('courseDescriptionInput');
const courseCreateStatus = document.getElementById('courseCreateStatus');
const materialForm = document.getElementById('materialForm');
const materialCourseSelect = document.getElementById('materialCourseSelect');
const materialTitle = document.getElementById('materialTitle');
const materialDescription = document.getElementById('materialDescription');
const materialFolder = document.getElementById('materialFolder');
const materialFile = document.getElementById('materialFile');
const materialStatus = document.getElementById('materialStatus');
const materialDriveFile = document.getElementById('materialDriveFile');
let toastTimer;
let cachedCourses = [];
let driveRefreshTimer;

// Remove non-functional cards if they still exist in DOM
['Lecture Upload', 'Grade Import'].forEach((feature) => {
  document.querySelectorAll(`.action-btn[data-feature="${feature}"]`).forEach((btn) => {
    const card = btn.closest('.feature-card');
    if (card) card.remove();
  });
});

function normalizeDueTimeTo24h(value) {
  if (!value) return '';
  const trimmed = value.trim();
  const match = trimmed.match(/^(\d{1,2}):(\d{2})\s*(AM|PM)$/i);
  if (!match) return trimmed;
  const hourNum = Number(match[1]);
  const minuteNum = Number(match[2]);
  if (Number.isNaN(hourNum) || Number.isNaN(minuteNum) || minuteNum > 59 || hourNum === 0 || hourNum > 12) {
    return trimmed;
  }
  let h24 = hourNum % 12;
  if (match[3].toUpperCase() === 'PM') h24 += 12;
  return `${h24.toString().padStart(2, '0')}:${minuteNum.toString().padStart(2, '0')}`;
}

function clampDueTimeForToday(dueDate, dueTime24) {
  if (!dueDate || !dueTime24) return dueTime24 || '';
  const parsed = new Date(`${dueDate}T${dueTime24}:00`);
  if (Number.isNaN(parsed.getTime())) return dueTime24;
  const now = new Date();
  const sameDay = parsed.toDateString() === now.toDateString();
  if (!sameDay) return dueTime24;
  if (parsed.getTime() > now.getTime()) return dueTime24;

  const shifted = new Date(now.getTime() + 5 * 60 * 1000);
  return `${shifted.getHours().toString().padStart(2, '0')}:${shifted.getMinutes().toString().padStart(2, '0')}`;
}

function buildDueTimeFromSelectors() {
  if (!courseworkDueTimeHour || !courseworkDueTimeMinute || !courseworkDueTimePeriod) {
    return courseworkDueTime?.value.trim() || '';
  }
  const h = courseworkDueTimeHour.value;
  const m = courseworkDueTimeMinute.value;
  const p = courseworkDueTimePeriod.value;
  if (!h || !m || !p) return '';
  return `${h}:${m} ${p}`;
}

function populateTimeSelectors() {
  if (!courseworkDueTimeHour || !courseworkDueTimeMinute) return;
  if (courseworkDueTimeHour.options.length <= 1) {
    for (let h = 1; h <= 12; h += 1) {
      const opt = document.createElement('option');
      opt.value = h.toString().padStart(2, '0');
      opt.textContent = opt.value;
      courseworkDueTimeHour.appendChild(opt);
    }
  }
  if (courseworkDueTimeMinute.options.length <= 1) {
    for (let m = 0; m < 60; m += 5) {
      const opt = document.createElement('option');
      opt.value = m.toString().padStart(2, '0');
      opt.textContent = opt.value;
      courseworkDueTimeMinute.appendChild(opt);
    }
  }
}

function showToast(message) {
  toastText.textContent = message;
  toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('show'), 2400);
}

function setInlineStatus(el, message, isError = false) {
  if (!el) return;
  el.textContent = message;
  el.classList.toggle('is-error', Boolean(isError));
}

function setFormBusy(form, isBusy, busyText) {
  if (!form) return;
  const submitBtn = form.querySelector('button[type="submit"]');
  const fields = Array.from(form.querySelectorAll('input, select, textarea, button'));
  fields.forEach((field) => {
    field.disabled = isBusy;
  });
  if (submitBtn && !submitBtn.dataset.originalText) {
    submitBtn.dataset.originalText = submitBtn.textContent;
  }
  if (submitBtn && busyText) {
    submitBtn.textContent = isBusy ? busyText : submitBtn.dataset.originalText || submitBtn.textContent;
  }
}

function parseApiError(data, fallback = 'Request failed') {
  if (!data) return { message: fallback };
  const detail = data.detail || data.message || fallback;
  const nextSteps = data.next_steps || data.nextSteps || '';
  const combined = nextSteps ? `${detail} (${nextSteps})` : detail;
  return { message: combined, category: data.category || 'error' };
}

function markConnected() {
  statusValue.textContent = 'Google Connected ✓';
  showToast('Google token is already connected.');
}

buttons.forEach((btn) => {
  if (isConnected) {
    btn.setAttribute('disabled', 'disabled');
    btn.textContent = 'Google Connected ✓';
    return;
  }

  btn.addEventListener('click', () => {
    const feature = btn.dataset.feature || 'Google Services';
    statusValue.textContent = `Redirecting to Google for ${feature} scopes...`;
    showToast('Redirecting to Google for one-time authorization.');
    window.location.href = connectUrl;
  });
});

if (isConnected) {
  markConnected();
} else {
  statusValue.textContent = 'Connect Google Services';
}

disconnectButtons.forEach((btn) => {
  if (!isConnected) {
    btn.disabled = true;
    btn.textContent = 'Disconnect Google';
    return;
  }

  btn.addEventListener('click', async () => {
    if (!window.confirm('Disconnect Google services? You can re-authorize again.')) return;
    btn.disabled = true;
    btn.textContent = 'Disconnecting…';
    try {
      const res = await fetch(disconnectUrl, { method: 'POST', credentials: 'include' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const err = parseApiError(data, 'Disconnect failed');
        throw new Error(err.message);
      }
      showToast('Google services disconnected.');
      window.location.reload();
    } catch (err) {
      console.error(err);
      showToast(`Could not disconnect: ${err.message}`);
      btn.disabled = false;
      btn.textContent = 'Disconnect Google';
    }
  });
});

function renderUploads(files = []) {
  if (!driveUploadsList) return;
  if (!files.length) {
    driveUploadsList.innerHTML = '<div class="empty-row">No Drive uploads yet.</div>';
    return;
  }
  driveUploadsList.innerHTML = files
    .map(
      (file) => `
      <div class="upload-row" data-drive-id="${file.drive_file_id}">
        <div>
          <div class="upload-name">${file.name}</div>
          <div class="upload-meta">${file.mime_type || 'unknown'} • ${(file.size_bytes || 0) / 1024 | 0} KB</div>
        </div>
        <div class="upload-actions">
          <a href="${file.web_view_link || '#'}" target="_blank" rel="noreferrer">Open</a>
          <button type="button" class="link-btn danger delete-drive-file" data-drive-id="${file.drive_file_id}">Delete</button>
        </div>
      </div>`
    )
    .join('');
}

async function fetchUploads() {
  if (!driveUploadsList) return;
  try {
    const res = await fetch('/google/drive/files/', { credentials: 'include' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = parseApiError(data, 'Failed to load uploads');
      throw new Error(err.message);
    }
    renderUploads(data.files || []);
    populateDriveOptions(data.files || []);
  } catch (err) {
    console.error(err);
    driveUploadsList.innerHTML = '<div class="empty-row">Could not load uploads.</div>';
  }
}

if (driveUploadsList) {
  driveUploadsList.addEventListener('click', (e) => {
    const btn = e.target.closest('.delete-drive-file');
    if (!btn) return;
    const id = btn.dataset.driveId;
    if (id) deleteDriveFile(id);
  });
}

function scheduleDriveRefresh(delay = 200) {
  clearTimeout(driveRefreshTimer);
  driveRefreshTimer = setTimeout(() => {
    fetchUploads();
  }, delay);
}

async function deleteDriveFile(driveId) {
  if (!driveId) return;
  const row = driveUploadsList?.querySelector(`[data-drive-id="${CSS.escape(driveId)}"]`);
  const btn = row?.querySelector('.delete-drive-file');
  if (row) row.classList.add('is-deleting');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Deleting…';
  }
  try {
    const res = await fetch(`/google/drive/files/${encodeURIComponent(driveId)}/`, {
      method: 'DELETE',
      credentials: 'include',
    });
    let payload = {};
    if (res.status !== 204) {
      payload = await res.json().catch(() => ({}));
    }
    if (!res.ok && res.status !== 404) {
      const err = parseApiError(payload, 'Delete failed');
      throw new Error(err.message);
    }
    if (row) row.remove();
    showToast(payload?.detail || 'File deleted.');
    scheduleDriveRefresh();
  } catch (err) {
    console.error(err);
    showToast(`Delete failed: ${err.message}`);
    if (row) row.classList.remove('is-deleting');
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Delete';
    }
  }
}

function renderCourses(courses = []) {
  cachedCourses = courses;
  if (!courseList || !courseSelect) return;
  if (!courses.length) {
    courseList.innerHTML = '<div class="empty-row">No courses found.</div>';
    courseSelect.innerHTML = '<option value="">-- Select a course --</option>';
    if (materialCourseSelect) {
      materialCourseSelect.innerHTML = '<option value="">-- Select a course --</option>';
    }
    return;
  }
  courseList.innerHTML = courses
    .map(
      (c) => `
        <div class="course-row">
          <div class="name">${c.name || 'Untitled'} ${c.section ? '(' + c.section + ')' : ''}</div>
          <div class="meta">ID: ${c.course_id} • State: ${c.state || 'N/A'} • Code: ${c.enrollment_code || '—'}</div>
          ${c.alternate_link ? `<a class="pill-link" href="${c.alternate_link}" target="_blank" rel="noreferrer">Open Classroom</a>` : ''}
        </div>`
    )
    .join('');
  const optionsHtml = ['<option value="">-- Select a course --</option>', ...courses.map((c) => `<option value="${c.course_id}">${c.name || c.course_id}</option>`)];
  courseSelect.innerHTML = optionsHtml.join('');
  if (materialCourseSelect) {
    materialCourseSelect.innerHTML = optionsHtml.join('');
  }
}

async function fetchCourses() {
  if (!loadCoursesBtn) return;
  loadCoursesBtn.disabled = true;
  setInlineStatus(coursesStatus, 'Loading courses...');
  try {
    const res = await fetch('/google/classroom/courses/', { credentials: 'include' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = parseApiError(data, 'Failed to load courses');
      throw new Error(err.message);
    }
    renderCourses(data.courses || []);
    if (data.warnings?.detail) {
      setInlineStatus(coursesStatus, data.warnings.detail, true);
    } else {
      setInlineStatus(coursesStatus, 'Courses loaded.');
    }
  } catch (err) {
    console.error(err);
    setInlineStatus(coursesStatus, `Load failed: ${err.message}`, true);
    showToast(`Failed to load courses: ${err.message}`);
  } finally {
    loadCoursesBtn.disabled = false;
  }
}

function populateDriveOptions(files = []) {
  if (!courseworkDriveFile) return;
  const options = ['<option value="">-- None --</option>'];
  files.forEach((f) => {
    options.push(`<option value="${f.drive_file_id}">${f.name}</option>`);
  });
  courseworkDriveFile.innerHTML = options.join('');
  if (materialDriveFile) {
    materialDriveFile.innerHTML = options.join('');
  }
}

if (driveForm) {
  driveForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!isConnected) {
      showToast('Connect Google first.');
      return;
    }
    if (!driveFileInput?.files?.length) {
      showToast('Choose a file to upload.');
      return;
    }
    const fd = new FormData();
    fd.append('file', driveFileInput.files[0]);
    if (driveFolderInput && driveFolderInput.value.trim()) {
      fd.append('folder_id', driveFolderInput.value.trim());
    }
    setInlineStatus(driveUploadStatus, 'Uploading...');
    setFormBusy(driveForm, true, 'Uploading…');
    try {
      const res = await fetch('/google/drive/upload/', {
        method: 'POST',
        body: fd,
        credentials: 'include',
      });
      let payload;
      try {
        payload = await res.clone().json();
      } catch (jsonErr) {
        const text = await res.text();
        throw new Error(text || jsonErr.message);
      }
      if (!res.ok) {
        const parsed = parseApiError(payload, 'Upload failed');
        const apiMessage = parsed.message;
        const driveError = payload.error?.error;
        const isNotFound = driveError?.errors?.some((item) => item.reason === 'notFound');
        if (isNotFound && driveFolderInput?.value?.trim()) {
          throw new Error('Drive folder ID not found. Use a valid folder ID or leave it blank for root.');
        }
        throw new Error(apiMessage);
      }
      showToast('Uploaded to Drive.');
      setInlineStatus(driveUploadStatus, 'Uploaded.');
      renderUploads([payload, ...(driveUploadsList?.children.length ? [] : [])]);
      fetchUploads();
    } catch (err) {
      console.error(err);
      setInlineStatus(driveUploadStatus, `Upload failed: ${err.message}`, true);
      showToast(`Upload failed: ${err.message}`);
    } finally {
      setFormBusy(driveForm, false);
    }
  });
}

if (isConnected) {
  fetchUploads();
}

populateTimeSelectors();

if (loadCoursesBtn) {
  loadCoursesBtn.addEventListener('click', () => {
    if (!isConnected) {
      showToast('Connect Google first.');
      return;
    }
    fetchCourses();
  });
}

function upsertCourse(course) {
  if (!course) return;
  const idx = cachedCourses.findIndex((c) => c.course_id === course.course_id);
  if (idx >= 0) {
    cachedCourses[idx] = course;
  } else {
    cachedCourses = [course, ...cachedCourses];
  }
  renderCourses([...cachedCourses]);
}

if (courseCreateForm) {
  courseCreateForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!isConnected) {
      showToast('Connect Google first.');
      return;
    }
    const name = courseNameInput?.value.trim();
    if (!name) {
      showToast('Course name is required.');
      return;
    }
    const payload = {
      name,
      section: courseSectionInput?.value.trim() || '',
      description_heading: courseDescriptionHeadingInput?.value.trim() || '',
      description: courseDescriptionInput?.value.trim() || '',
      room: courseRoomInput?.value.trim() || '',
    };

    setInlineStatus(courseCreateStatus, 'Creating...');
    setFormBusy(courseCreateForm, true, 'Creating…');
    try {
      const res = await fetch('/google/classroom/create-course/', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const err = parseApiError(data, 'Failed to create course');
        throw new Error(err.message);
      }
      upsertCourse(data.course);
      setInlineStatus(courseCreateStatus, 'Created.');
      showToast('Classroom course created.');
      courseCreateForm.reset();
    } catch (err) {
      console.error(err);
      setInlineStatus(courseCreateStatus, `Create failed: ${err.message}`, true);
      showToast(`Create failed: ${err.message}`);
    } finally {
      setFormBusy(courseCreateForm, false);
    }
  });
}

if (courseworkForm) {
  courseworkForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!isConnected) {
      showToast('Connect Google first.');
      return;
    }
    const courseId = courseSelect?.value;
    const title = courseworkTitle?.value.trim();
    const hasUpload = courseworkFile?.files?.length;
    const chosenDriveId = courseworkDriveFile?.value || '';
    if (!courseId) {
      showToast('Select a course.');
      return;
    }
    if (!title) {
      showToast('Title is required.');
      return;
    }
    if (!hasUpload && !chosenDriveId) {
      showToast('Select a Drive file or upload one.');
      return;
    }

    // Upload the file first if provided
    let driveFileId = chosenDriveId;
    if (hasUpload) {
      setInlineStatus(courseworkStatus, 'Uploading file...');
      setFormBusy(courseworkForm, true, 'Uploading…');
      try {
        const fd = new FormData();
        fd.append('file', courseworkFile.files[0]);
        const uploadRes = await fetch('/google/drive/upload/', {
          method: 'POST',
          body: fd,
          credentials: 'include',
        });
        let uploadData;
        try {
          uploadData = await uploadRes.clone().json();
        } catch (jsonErr) {
          const text = await uploadRes.text();
          throw new Error(text || jsonErr.message);
        }
        if (!uploadRes.ok) {
          const parsed = parseApiError(uploadData, 'Upload failed');
          throw new Error(parsed.message);
        }
        driveFileId = uploadData.drive_file_id || uploadData.id || driveFileId;
      } catch (err) {
        console.error(err);
        setInlineStatus(courseworkStatus, `Upload failed: ${err.message}`, true);
        showToast(`Upload failed: ${err.message}`);
        setFormBusy(courseworkForm, false);
        return;
      }
    }

    const payload = {
      course_id: courseId,
      title,
      description: courseworkDescription?.value.trim() || '',
      max_points: courseworkPoints?.value ? Number(courseworkPoints.value) : null,
      due_date: courseworkDueDate?.value.trim() || '',
      due_time: clampDueTimeForToday(
        courseworkDueDate?.value.trim() || '',
        normalizeDueTimeTo24h(buildDueTimeFromSelectors())
      ),
      drive_file_id: driveFileId || '',
    };
    setInlineStatus(courseworkStatus, 'Publishing...');
    setFormBusy(courseworkForm, true, 'Publishing…');
    try {
      const res = await fetch('/google/classroom/coursework/', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const err = parseApiError(data, 'Failed to publish');
        throw new Error(err.message);
      }
      setInlineStatus(courseworkStatus, 'Published.');
      showToast('Assignment published to Classroom.');
      courseworkForm.reset();
    } catch (err) {
      console.error(err);
      setInlineStatus(courseworkStatus, `Publish failed: ${err.message}`, true);
      showToast(`Publish failed: ${err.message}`);
    } finally {
      setFormBusy(courseworkForm, false);
    }
  });
}

if (materialForm) {
  materialForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!isConnected) {
      showToast('Connect Google first.');
      return;
    }
    const courseId = materialCourseSelect?.value;
    if (!courseId) {
      showToast('Select a course.');
      return;
    }
    const hasFile = !!materialFile?.files?.length;
    const chosenDriveId = materialDriveFile?.value || '';
    if (!hasFile && !chosenDriveId) {
      showToast('Choose a file or select an existing Drive file.');
      return;
    }

    const fd = new FormData();
    fd.append('course_id', courseId);
    if (hasFile) {
      fd.append('file', materialFile.files[0]);
    }
    if (chosenDriveId) {
      fd.append('drive_file_id', chosenDriveId);
    }
    if (materialTitle?.value.trim()) fd.append('title', materialTitle.value.trim());
    if (materialDescription?.value.trim()) fd.append('description', materialDescription.value.trim());
    if (materialFolder?.value.trim()) fd.append('folder_id', materialFolder.value.trim());

    setInlineStatus(materialStatus, 'Uploading...');
    setFormBusy(materialForm, true, 'Uploading…');
    try {
      const res = await fetch('/google/classroom/material/upload/', {
        method: 'POST',
        body: fd,
        credentials: 'include',
      });
      let data;
      try {
        data = await res.clone().json();
      } catch (jsonErr) {
        const text = await res.text();
        throw new Error(text || jsonErr.message);
      }
      if (!res.ok) {
        const parsed = parseApiError(data, 'Failed to upload');
        throw new Error(parsed.message);
      }
      setInlineStatus(materialStatus, 'Uploaded.');
      showToast('Uploaded to Drive and Classroom.');
      const keepCourse = courseId;
      materialForm.reset();
      if (keepCourse && materialCourseSelect) {
        materialCourseSelect.value = keepCourse;
      }
      fetchUploads();
    } catch (err) {
      console.error(err);
      setInlineStatus(materialStatus, `Upload failed: ${err.message}`, true);
      showToast(`Material upload failed: ${err.message}`);
    } finally {
      setFormBusy(materialForm, false);
    }
  });
}

// preload courses & uploads when connected
if (isConnected) {
  fetchCourses();
}
