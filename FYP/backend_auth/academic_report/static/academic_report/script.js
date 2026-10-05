/**
 * Academic Report Generator - Upload Page Script
 * Handles Excel upload and displays summary results.
 */

(function () {
  'use strict';

  const form = document.getElementById('uploadExcelForm');
  const fileInput = document.getElementById('excelFile');
  const fileLabel = document.querySelector('.file-label-text');
  const uploadStatus = document.getElementById('uploadStatus');
  const btnUpload = document.getElementById('btnUpload');
  const statsGrid = document.getElementById('statsGrid');
  const viewReportArea = document.getElementById('viewReportArea');
  const btnViewReport = document.getElementById('btnViewReport');

  // Show selected file name
  if (fileInput && fileLabel) {
    fileInput.addEventListener('change', function () {
      if (this.files && this.files.length > 0) {
        fileLabel.textContent = this.files[0].name;
      } else {
        fileLabel.textContent = 'Select Excel file...';
      }
    });
  }

  function setStatus(msg, type) {
    if (!uploadStatus) return;
    uploadStatus.textContent = msg;
    uploadStatus.className = 'status-msg ' + type;
  }

  function showSummary(data) {
    const summary = data.summary;
    if (!summary) return;

    document.getElementById('valTotalStudents').textContent = summary.total_students;
    document.getElementById('valEpan').textContent = summary.epan_avg.toFixed(2);
    document.getElementById('valAchievement').textContent = summary.achievement_level;
    document.getElementById('valClo1').textContent = (summary.clo_averages.CLO1 || 0).toFixed(2);
    document.getElementById('valClo2').textContent = (summary.clo_averages.CLO2 || 0).toFixed(2);
    document.getElementById('valClo3').textContent = (summary.clo_averages.CLO3 || 0).toFixed(2);
    document.getElementById('valClo4').textContent = (summary.clo_averages.CLO4 || 0).toFixed(2);

    // Color the achievement card
    const achCard = document.getElementById('achievementCard');
    if (achCard) {
      achCard.className = 'stat-card';
      const level = summary.achievement_level;
      if (level === 'Expert') achCard.style.borderLeft = '5px solid #22c55e';
      else if (level === 'Practitioner') achCard.style.borderLeft = '5px solid #1f6f8b';
      else achCard.style.borderLeft = '5px solid #ef4444';
    }

    if (statsGrid) statsGrid.style.display = '';

    // Show View Report button
    if (data.run_id && btnViewReport && viewReportArea) {
      btnViewReport.href = '/academic-report/report/' + data.run_id + '/';
      viewReportArea.style.display = '';
    }

    setStatus(data.message || 'Report generated successfully!', 'success');
  }

  if (form) {
    form.addEventListener('submit', function (e) {
      e.preventDefault();

      if (!fileInput || !fileInput.files || fileInput.files.length === 0) {
        setStatus('Please select an Excel file first.', 'error');
        return;
      }

      setStatus('Uploading and processing...', 'loading');
      if (btnUpload) btnUpload.disabled = true;

      var formData = new FormData();
      formData.append('file', fileInput.files[0]);

      // Append course info
      var courseCode = document.getElementById('courseCode');
      var courseName = document.getElementById('courseName');
      var instructor = document.getElementById('instructor');
      var semester = document.getElementById('semester');
      var sections = document.getElementById('sections');
      var totalCredits = document.getElementById('totalCredits');
      var academicTerm = document.getElementById('academicTerm');
      var catalogDesc = document.getElementById('catalogDesc');

      if (courseCode) formData.append('course_code', courseCode.value);
      if (courseName) formData.append('course_name', courseName.value);
      if (instructor) formData.append('course_instructor', instructor.value);
      if (semester) formData.append('semester', semester.value);
      if (sections) formData.append('sections', sections.value);
      if (totalCredits) formData.append('total_credits', totalCredits.value);
      if (academicTerm) formData.append('academic_term', academicTerm.value);
      if (catalogDesc) formData.append('catalog_description', catalogDesc.value);

      var csrfToken = window.CSRF_TOKEN || '';

      fetch('/academic-report/upload-excel/', {
        method: 'POST',
        headers: {
          'X-CSRFToken': csrfToken,
        },
        body: formData,
      })
        .then(function (response) {
          return response.json().then(function (data) {
            return { status: response.status, data: data };
          });
        })
        .then(function (result) {
          if (btnUpload) btnUpload.disabled = false;

          if (result.status === 200 && result.data.success) {
            showSummary(result.data);
          } else {
            setStatus(result.data.error || 'Failed to process file.', 'error');
          }
        })
        .catch(function (err) {
          if (btnUpload) btnUpload.disabled = false;
          setStatus('Network error: ' + err.message, 'error');
        });
    });
  }
})();
