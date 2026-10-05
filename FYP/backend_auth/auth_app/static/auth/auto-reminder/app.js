const API_BASE = '/auto-reminders';
const csrfToken = window.CSRF_TOKEN || '';

const ALERT_LABELS = {
  assignment: 'Assignment',
  created: 'Created',
  deadline: 'Deadline',
  summary: 'Summary',
  entry_deadline: 'Entry Deadline',
  quiz_entry_deadline: 'Quiz Entry',
  mid_term_event: 'Mid Term',
  final_term_event: 'Final Term',
  academic_event: 'Academic',
};

const tableBody = document.getElementById('tableBody');
const emptyState = document.getElementById('emptyState');
const toast = document.getElementById('toast');
const toggle = document.getElementById('enableToggle');
const refreshBtn = document.getElementById('refreshBtn');
const refreshLabel = refreshBtn?.querySelector('.btn-label');
const refreshSpinner = refreshBtn?.querySelector('.btn-spinner');
const runNowBtn = document.getElementById('runNowBtn');
const runLabel = runNowBtn?.querySelector('.btn-label');
const runSpinner = runNowBtn?.querySelector('.btn-spinner');
const createEventsBtn = document.getElementById('createEventsBtn');
const createLabel = createEventsBtn?.querySelector('.btn-label');
const createSpinner = createEventsBtn?.querySelector('.btn-spinner');
const deleteEventsBtn = document.getElementById('deleteEventsBtn');
const deleteLabel = deleteEventsBtn?.querySelector('.btn-label');
const deleteSpinner = deleteEventsBtn?.querySelector('.btn-spinner');
const userBtn = document.getElementById('userBtn');
const userMenu = document.getElementById('userMenu');
const createBtn = document.getElementById('createBtn');
const createTitle = document.getElementById('createTitle');
const createDue = document.getElementById('createDue');
const createType = document.getElementById('createType');

const LIST_URL = `${API_BASE}/assignment-alerts/list/`;
const SEND_NOW_URL = `${API_BASE}/assignment-alerts/send-now/`;
const CREATE_EVENTS_URL = `${API_BASE}/assignment-alerts/create-events/`;
const DELETE_EVENTS_URL = `${API_BASE}/assignment-alerts/delete-events/`;
const MARK_COMPLETE_URL = `${API_BASE}/assignment-alerts/mark-complete/`;
const DELETE_URL = `${API_BASE}/assignment-alerts/delete/`;
const CREATE_URL = `${API_BASE}/assignment-alerts/create/`;

const ACTION_LOCKS = new Set();

function lockAction(key) {
  if (ACTION_LOCKS.has(key)) return false;
  ACTION_LOCKS.add(key);
  return true;
}

function unlockAction(key) {
  ACTION_LOCKS.delete(key);
}

async function fetchWithTimeout(url, options = {}, timeoutMs = 15000) {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { ...options, signal: controller.signal });
    return res;
  } catch (err) {
    if (err?.name === 'AbortError') {
      throw new Error('Request timed out. Please try again.');
    }
    throw err;
  } finally {
    clearTimeout(id);
  }
}

function parseApiError(data, fallback = 'Request failed') {
  if (!data) return fallback;
  return data.detail || data.user_message || data.message || fallback;
}

function setLoading(isLoading) {
  if (!refreshBtn) return;
  refreshBtn.disabled = isLoading;
  refreshBtn.classList.toggle('loading', isLoading);
  if (refreshLabel) refreshLabel.textContent = isLoading ? 'Refreshing…' : 'Refresh';
}

function setRunLoading(isLoading) {
  if (!runNowBtn) return;
  runNowBtn.disabled = isLoading;
  runNowBtn.classList.toggle('loading', isLoading);
  if (runLabel) runLabel.textContent = isLoading ? 'Running…' : 'Run alerts now';
  if (runSpinner) runSpinner.style.display = isLoading ? 'inline-block' : 'none';
}

function setCreateLoading(isLoading) {
  if (!createEventsBtn) return;
  createEventsBtn.disabled = isLoading;
  createEventsBtn.classList.toggle('loading', isLoading);
  if (createLabel) createLabel.textContent = isLoading ? 'Creating…' : 'Create Calendar Events';
  if (createSpinner) createSpinner.style.display = isLoading ? 'inline-block' : 'none';
}

function setDeleteLoading(isLoading) {
  if (!deleteEventsBtn) return;
  deleteEventsBtn.disabled = isLoading;
  deleteEventsBtn.classList.toggle('loading', isLoading);
  if (deleteLabel) deleteLabel.textContent = isLoading ? 'Deleting…' : 'Delete Calendar Events';
  if (deleteSpinner) deleteSpinner.style.display = isLoading ? 'inline-block' : 'none';
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 2000);
}

function formatDate(value) {
  if (!value) return '—';
  try {
    return new Date(value).toLocaleDateString();
  } catch (_) {
    return value;
  }
}

function renderRows(alerts) {
  tableBody.innerHTML = '';
  emptyState.hidden = alerts.length > 0;

  alerts.forEach((item) => {
    const typeLabel = ALERT_LABELS[item.alertType] || item.alertType;
    const submitted = item.submitted ?? '—';
    const total = item.total ?? '—';
    const pending = item.notSubmitted ?? '—';
      const isCompleted = !!item.isCompleted;
      const isOverdue = item.alertStage === 'overdue' || item.status === 'overdue';
      const statusText = (item.status || '').toLowerCase();
      const isUpcoming = statusText === 'upcoming';
      const statusClass = isCompleted ? 'pill-success' : isOverdue ? 'pill-danger' : 'pill-warn';
      const statusLabel = isCompleted ? 'Completed' : isOverdue ? 'Overdue' : (isUpcoming ? 'Upcoming' : 'Pending');
    const row = document.createElement('div');
      row.className = `table-row${isCompleted ? ' table-row--completed' : ''}`;
      row.dataset.id = item.id;
    row.innerHTML = `
      <div>${item.assignmentName}</div>
      <div>${formatDate(item.dueDate)}</div>
      <div>${submitted} / ${total}</div>
      <div>${pending}</div>
      <div class="pill pill-soft">${typeLabel}</div>
      <div class="pill ${statusClass}">${statusLabel}</div>
      <div>
        <label class="complete-check">
          <input type="checkbox" data-id="${item.id || ''}" ${(!item.id || isCompleted) ? 'checked disabled' : ''} />
          <span>Completed on Zabdesk</span>
        </label>
      </div>
      <div>
        <button class="link-btn" data-delete-id="${item.id}">Delete</button>
      </div>
    `;
    tableBody.appendChild(row);
    });
}

async function fetchAlerts() {
  if (!lockAction('refresh')) return;
  setLoading(true);
  try {
    const res = await fetchWithTimeout(LIST_URL, { credentials: 'include' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(parseApiError(data, 'Load failed'));
    renderRows(Array.isArray(data.alerts) ? data.alerts : []);
  } catch (err) {
    console.error(err);
    showToast(err?.message || 'Failed to load alerts');
    renderRows([]);
  } finally {
    setLoading(false);
    unlockAction('refresh');
  }
}

async function fetchToggle() {
  try {
    const res = await fetchWithTimeout(`${API_BASE}/assignment-alerts/toggle/`, { credentials: 'include' });
    if (!res.ok) return;
    const data = await res.json();
    toggle.checked = !!data.enabled;
  } catch (err) {
    console.error(err);
  }
}

async function setToggle(value) {
  if (!toggle) return;
  if (!lockAction('toggle')) return;
  toggle.disabled = true;
  try {
    const res = await fetchWithTimeout(`${API_BASE}/assignment-alerts/toggle/`, {
      method: 'PUT',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken,
      },
      body: JSON.stringify({ enabled: value }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(parseApiError(data, 'Update failed'));
  } catch (err) {
    console.error(err);
    showToast(err?.message || 'Unable to update');
    toggle.checked = !value;
  } finally {
    toggle.disabled = false;
    unlockAction('toggle');
  }
}

async function runAlertsNow() {
  if (!lockAction('run')) return;
  setRunLoading(true);
  try {
    const res = await fetchWithTimeout(SEND_NOW_URL, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken,
      },
      body: JSON.stringify({ force_send: true }),
    });
    const data = await res.json().catch(() => ({}));
    const warning = (data.warnings && data.warnings[0]) || (data.errors && data.errors[0]);
    if (data.skipped) {
      if (data.reason === 'alerts_disabled') {
        showToast('Alerts are disabled. Turn on Enable Alerts.');
      } else if (data.reason === 'email_not_configured') {
        showToast('Email not configured. Set EMAIL_HOST_USER and EMAIL_HOST_PASSWORD.');
      } else {
        showToast(warning || 'Alerts were skipped.');
      }
      return;
    }
    if (!res.ok || !data.ok) {
      showToast(parseApiError(data, warning || 'Failed to trigger alerts'));
      return;
    }
    const sentCount = data.sent ?? 0;
    if (sentCount > 0) {
      showToast(warning || `${sentCount} alerts sent`);
    } else if (data.email_skipped) {
      showToast('Email not configured. Set EMAIL_HOST_USER and EMAIL_HOST_PASSWORD.');
    } else {
      showToast('No new alerts to send.');
    }
    if (data.logs?.length) console.info('Run-now logs:', data.logs.join('\n'));
    await fetchAlerts();
  } catch (err) {
    console.error(err);
    showToast(err?.message || 'Failed to trigger alerts');
  } finally {
    setRunLoading(false);
    unlockAction('run');
  }
}

async function createCalendarEvents() {
  if (!lockAction('createEvents')) return;
  setCreateLoading(true);
  try {
    const res = await fetchWithTimeout(CREATE_EVENTS_URL, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken,
      },
      body: JSON.stringify({}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      const msg = data.user_message || (data.errors && data.errors[0]?.message) || parseApiError(data, 'Calendar event creation failed');
      showToast(msg);
      return;
    }
    const created = data.created ?? 0;
    const skipped = data.skipped ?? 0;
    const failed = data.failed ?? 0;
    const details = data.details || {};
    if (created === 0 && skipped === 0 && failed === 0 && data.user_message) {
      showToast(data.user_message);
    } else {
      const parts = [`Created ${created}`];
      if (skipped) parts.push(`Skipped ${skipped}`);
      if (failed) parts.push(`Failed ${failed}`);
      showToast(parts.join(' • '));
      if (failed && data.errors?.length) {
        console.warn('Calendar creation errors:', data.errors);
      }
      if (details && (details.skipped_past || details.skipped_duplicates || details.skipped_no_deadline)) {
        console.info('Calendar creation details:', details);
      }
    }
    await fetchAlerts();
  } catch (err) {
    console.error(err);
    showToast(err?.message || 'Network error while creating calendar events');
  } finally {
    setCreateLoading(false);
    unlockAction('createEvents');
  }
}

async function deleteCalendarEvents() {
  if (!window.confirm('Delete all calendar events created from alerts?')) return;
  if (!lockAction('deleteEvents')) return;
  setDeleteLoading(true);
  try {
    const res = await fetchWithTimeout(DELETE_EVENTS_URL, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken,
      },
      body: JSON.stringify({}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      const msg = data.user_message || (data.errors && data.errors[0]?.message) || parseApiError(data, 'Calendar event deletion failed');
      showToast(msg);
      return;
    }
    const deleted = data.deleted ?? 0;
    const skipped = data.skipped ?? 0;
    const failed = data.failed ?? 0;
    if (deleted === 0 && skipped === 0 && failed === 0 && data.user_message) {
      showToast(data.user_message);
    } else {
      const parts = [`Deleted ${deleted}`];
      if (skipped) parts.push(`Skipped ${skipped}`);
      if (failed) parts.push(`Failed ${failed}`);
      showToast(parts.join(' • '));
      if (failed && data.errors?.length) {
        console.warn('Calendar deletion errors:', data.errors);
      }
    }
    await fetchAlerts();
  } catch (err) {
    console.error(err);
    showToast(err?.message || 'Network error while deleting calendar events');
  } finally {
    setDeleteLoading(false);
    unlockAction('deleteEvents');
  }
}

async function markComplete(alertId, checkboxEl) {
  const lockKey = `complete:${alertId}`;
  if (!lockAction(lockKey)) return;
  try {
    const res = await fetchWithTimeout(MARK_COMPLETE_URL, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken,
      },
      body: JSON.stringify({ alert_id: alertId }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(parseApiError(data, 'Unable to mark completed'));
    showToast('Marked completed');
    await fetchAlerts();
  } catch (err) {
    console.error(err);
    showToast(err?.message || 'Unable to mark completed');
    if (checkboxEl) {
      checkboxEl.checked = false;
      checkboxEl.disabled = false;
    }
  } finally {
    unlockAction(lockKey);
  }
}

async function deleteAlert(alertId, btnEl) {
  const lockKey = `delete:${alertId}`;
  if (!lockAction(lockKey)) return;
  try {
    const res = await fetchWithTimeout(DELETE_URL, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken,
      },
      body: JSON.stringify({ alert_id: alertId }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(parseApiError(data, 'Unable to delete alert'));
    const row = btnEl?.closest('.table-row');
    if (row) row.remove();
    showToast('Alert deleted');
  } catch (err) {
    console.error(err);
    showToast(err?.message || 'Unable to delete');
    if (btnEl) btnEl.disabled = false;
  } finally {
    unlockAction(lockKey);
  }
}

async function createAlert() {
  const title = (createTitle?.value || '').trim();
  const due = createDue?.value || '';
  const type = createType?.value || 'mid_term_event';
  if (!title) {
    showToast('Title is required');
    return;
  }
  if (!lockAction('createAlert')) return;
  if (createBtn) createBtn.disabled = true;
  try {
    const res = await fetchWithTimeout(CREATE_URL, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken,
      },
      body: JSON.stringify({ title, due_date: due, alert_type: type }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(parseApiError(data, 'Unable to create alert'));
    showToast('Alert created');
    await fetchAlerts();
    if (createTitle) createTitle.value = '';
    if (createDue) createDue.value = '';
    if (createType) createType.value = 'mid_term_event';
  } catch (err) {
    console.error(err);
    showToast(err?.message || 'Unable to create alert');
  } finally {
    if (createBtn) createBtn.disabled = false;
    unlockAction('createAlert');
  }
}

refreshBtn?.addEventListener('click', fetchAlerts);
runNowBtn?.addEventListener('click', runAlertsNow);
createEventsBtn?.addEventListener('click', createCalendarEvents);
deleteEventsBtn?.addEventListener('click', deleteCalendarEvents);
toggle?.addEventListener('change', (e) => setToggle(e.target.checked));

tableBody?.addEventListener('change', (e) => {
  const target = e.target;
  if (target && target.matches('input[type="checkbox"][data-id]')) {
    const alertId = target.getAttribute('data-id');
    if (!alertId) {
      showToast('Cannot mark completed: alert not ready yet. Refresh once.');
      target.checked = false;
      return;
    }
    target.disabled = true;
    markComplete(alertId, target);
  }
});

function handleDeleteClick(e) {
  const target = e.target;
  if (target && target.matches('button[data-delete-id]')) {
    const alertId = target.getAttribute('data-delete-id');
    target.disabled = true;
    deleteAlert(alertId, target);
  }
}

tableBody?.addEventListener('click', handleDeleteClick);

createBtn?.addEventListener('click', createAlert);

if (userBtn && userMenu) {
  userBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    userMenu.classList.toggle('open');
  });
  document.addEventListener('click', () => userMenu.classList.remove('open'));
}

fetchToggle();
