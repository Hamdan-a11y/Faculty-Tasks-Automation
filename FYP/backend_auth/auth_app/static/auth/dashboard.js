// Populate user details from backend session and handle dropdown UI.
const userBtn = document.getElementById('userBtn');
const userMenu = document.getElementById('userMenu');
const welcomeName = document.getElementById('welcomeName');
const facultyNameEl = document.getElementById('facultyName');
const csrfToken = window.CSRF_TOKEN || '';

async function hydrateUser() {
  try {
    const res = await fetch('/auth/user/', { credentials: 'include' });
    if (!res.ok) throw new Error('unauthorized');
    const user = await res.json();
    const name = user.name || user.email || 'Faculty';
    if (welcomeName) welcomeName.textContent = `Welcome, ${name}`;
    if (facultyNameEl) facultyNameEl.textContent = name;
  } catch (err) {
    window.location.href = '/auth/login/';
  }
}

hydrateUser();

if (userBtn && userMenu) {
  userBtn.addEventListener('click', () => {
    const isOpen = userMenu.style.display === 'block';
    userMenu.style.display = isOpen ? 'none' : 'block';
  });
  document.addEventListener('click', (event) => {
    if (!userBtn.contains(event.target) && !userMenu.contains(event.target)) {
      userMenu.style.display = 'none';
    }
  });
}

// ------------------------------------------------------------------
// Live alerts (assignment alerts API)
// ------------------------------------------------------------------
const alertList = document.getElementById('alertList');
const alertEmpty = document.getElementById('alertEmpty');
const alertStack = document.getElementById('alertStack');
const alertsSubhead = document.getElementById('alertsSubhead');
const refreshAlerts = document.getElementById('refreshAlerts');
const runAlertsNowBtn = document.getElementById('runAlertsNow');

function formatDate(value) {
  if (!value) return '—';
  try { return new Date(value).toLocaleDateString(); } catch (_) { return value; }
}

function stageLabel(stage) {
  return ({
    overdue: 'Overdue',
    today: 'Due today',
    one_day: 'Due tomorrow',
    two_days: 'Due in 2 days',
    three_days: 'Due in 3 days',
    single: 'Update',
  })[stage] || 'Update';
}

function stageWeight(stage) {
  return { overdue: 0, today: 1, one_day: 2, two_days: 3, three_days: 4, single: 5, undefined: 6, null: 6 }[stage ?? null] ?? 6;
}

function renderAlertList(alerts) {
  if (!alertList) return;
  alertList.innerHTML = '';
  if (!alerts.length) {
    alertEmpty.hidden = false;
    alertsSubhead.textContent = 'No active alerts.';
    return;
  }
  alertEmpty.hidden = true;
  alertsSubhead.textContent = `${alerts.length} active alert${alerts.length === 1 ? '' : 's'}`;

  alerts.slice(0, 8).forEach((item) => {
    const row = document.createElement('div');
    row.className = 'alert-item';
    const statusClass = item.alertStage === 'overdue' ? 'danger' : ['today', 'one_day', 'two_days'].includes(item.alertStage) ? 'warn' : 'neutral';
    row.innerHTML = `
      <img src="/static/auth/icons/alert.svg" alt="" />
      <div>
        <p class="alert-course">${item.assignmentName}</p>
        <p class="alert-meta">${item.alertType.replaceAll('_', ' ')} · ${stageLabel(item.alertStage)}</p>
      </div>
      <div>
        <p class="alert-meta">Due ${formatDate(item.dueDate)}</p>
        <span class="pill ${statusClass}">${stageLabel(item.alertStage)}</span>
      </div>
    `;
    alertList.appendChild(row);
  });
}

function renderStack(alerts) {
  if (!alertStack) return;
  alertStack.innerHTML = '';
  const urgent = alerts.filter((a) => ['overdue', 'today', 'one_day', 'two_days'].includes(a.alertStage)).slice(0, 3);
  urgent.forEach((item) => {
    const card = document.createElement('div');
    card.className = `toast-card ${item.alertStage === 'overdue' ? 'overdue' : ''}`;
    card.innerHTML = `
      <p class="toast-title">${item.assignmentName}</p>
      <p class="toast-meta">${stageLabel(item.alertStage)} · Due ${formatDate(item.dueDate)}</p>
      <p class="toast-meta">Pending: ${item.notSubmitted ?? '—'} · Submitted: ${item.submitted ?? '—'} / ${item.total ?? '—'}</p>
    `;
    alertStack.appendChild(card);
  });
}

async function loadAlerts() {
  if (refreshAlerts) refreshAlerts.disabled = true;
  if (alertsSubhead) alertsSubhead.textContent = 'Loading latest deadlines…';
  try {
    const res = await fetch('/auto-reminders/assignment-alerts/', { credentials: 'include' });
    if (res.status === 401 || res.status === 403) {
      alertsSubhead.textContent = 'Authorize Google services to view alerts.';
      if (alertList) alertList.innerHTML = '';
      if (alertStack) alertStack.innerHTML = '';
      if (alertEmpty) { alertEmpty.hidden = false; alertEmpty.textContent = 'Sign in and connect Google to load alerts.'; }
      return;
    }
    if (!res.ok) throw new Error('load failed');
    const data = await res.json();
    const alerts = (data.alerts || []).sort((a, b) => {
      const weight = stageWeight(a.alertStage) - stageWeight(b.alertStage);
      if (weight !== 0) return weight;
      return (new Date(a.dueDate || '2100-01-01')) - (new Date(b.dueDate || '2100-01-01'));
    });
    renderAlertList(alerts);
    renderStack(alerts);
  } catch (err) {
    if (alertsSubhead) alertsSubhead.textContent = 'Unable to load alerts';
    if (alertEmpty) { alertEmpty.hidden = false; alertEmpty.textContent = 'Failed to load alerts. Retry shortly.'; }
    if (alertList) alertList.innerHTML = '';
    if (alertStack) alertStack.innerHTML = '';
    console.error(err);
  } finally {
    if (refreshAlerts) refreshAlerts.disabled = false;
  }
}

if (refreshAlerts) refreshAlerts.addEventListener('click', loadAlerts);
if (runAlertsNowBtn) {
  runAlertsNowBtn.addEventListener('click', async () => {
    const prev = runAlertsNowBtn.textContent;
    runAlertsNowBtn.disabled = true;
    runAlertsNowBtn.textContent = 'Running…';
    try {
      const res = await fetch('/auto-reminders/assignment-alerts/send-now/', {
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
      if (!res.ok || (!data.ok && !data.skipped)) {
        if (alertsSubhead) alertsSubhead.textContent = warning || 'Unable to run alerts';
        return;
      }
      if (alertsSubhead) alertsSubhead.textContent = warning || 'Alerts refreshed';
      if (data.logs?.length) console.info('Run-now logs:', data.logs.join('\n'));
      await loadAlerts();
    } catch (err) {
      console.error(err);
      if (alertsSubhead) alertsSubhead.textContent = 'Unable to run alerts';
    } finally {
      runAlertsNowBtn.disabled = false;
      runAlertsNowBtn.textContent = prev;
    }
  });
}
loadAlerts();
setInterval(loadAlerts, 45000);
