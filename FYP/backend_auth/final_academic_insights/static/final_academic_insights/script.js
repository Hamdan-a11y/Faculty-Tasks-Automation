document.addEventListener("DOMContentLoaded", () => {
  // ── DOM Elements ──
  const excelFileInput = document.getElementById("excelFile");
  const fileLabelText = document.querySelector(".file-label-text");
  const uploadForm = document.getElementById("uploadExcelForm");
  const btnUpload = document.getElementById("btnUpload");
  const uploadStatus = document.getElementById("uploadStatus");

  const btnGenerateReport = document.getElementById("btnGenerateReport");
  const btnShowGraph = document.getElementById("btnShowGraph");
  const btnDownloadExcel = document.getElementById("btnDownloadExcel");
  const actionStatus = document.getElementById("actionStatus");

  const statsGrid = document.getElementById("statsGrid");
  const valTotalStudents = document.getElementById("valTotalStudents");
  const valPassedStudents = document.getElementById("valPassedStudents");
  const valFailedStudents = document.getElementById("valFailedStudents");
  const pctPassed = document.getElementById("pctPassed");
  const pctFailed = document.getElementById("pctFailed");
  const valAverageScore = document.getElementById("valAverageScore");
  const valHighestScore = document.getElementById("valHighestScore");

  const chartsRow1 = document.getElementById("chartsRow1");
  const chartsRow2 = document.getElementById("chartsRow2");
  const graphContainer = document.getElementById("graphContainer");
  const gradeDistContainer = document.getElementById("gradeDistContainer");
  const assessmentContainer = document.getElementById("assessmentContainer");
  const trendContainer = document.getElementById("trendContainer");
  const insightsSection = document.getElementById("insightsSection");
  const insightsList = document.getElementById("insightsList");
  const tableContainer = document.getElementById("tableContainer");
  const recordCount = document.getElementById("recordCount");
  const studentTableBody = document.getElementById("studentTableBody");

  // Chart instances for safe destroy/recreate
  let insightsChartInstance = null;
  let gradeDistChartInstance = null;
  let assessmentChartInstance = null;
  let trendChartInstance = null;

  // ── File input label update ──
  excelFileInput.addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (file) {
      fileLabelText.textContent = file.name;
      let fileIndicator = document.getElementById("selectedFileNameIndicator");
      if (!fileIndicator) {
        fileIndicator = document.createElement("span");
        fileIndicator.id = "selectedFileNameIndicator";
        fileIndicator.className = "selected-file-name";
        excelFileInput.parentElement.appendChild(fileIndicator);
      }
      fileIndicator.textContent = `Selected: ${file.name}`;
    } else {
      fileLabelText.textContent = "Select Excel file...";
      const fileIndicator = document.getElementById("selectedFileNameIndicator");
      if (fileIndicator) fileIndicator.remove();
    }
  });

  // ── Upload handler ──
  uploadForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const file = excelFileInput.files[0];
    if (!file) {
      showStatus(uploadStatus, "Please select an Excel file to upload first.", "error");
      return;
    }

    const formData = new FormData();
    formData.append("file", file);

    showStatus(uploadStatus, "Uploading and processing sheet...", "loading");
    btnUpload.disabled = true;

    try {
      const response = await fetch("/final-academic-insights/upload-excel/", {
        method: "POST",
        body: formData,
      });

      const data = await response.json();
      btnUpload.disabled = false;

      if (data.success) {
        showStatus(uploadStatus, (data.message || "Upload successful!") + " Now click 'Generate Report' to view results.", "success");
      } else {
        showStatus(uploadStatus, data.error || "An error occurred during upload.", "error");
      }
    } catch (err) {
      btnUpload.disabled = false;
      showStatus(uploadStatus, "Network error. Failed to reach server.", "error");
      console.error(err);
    }
  });

  // ── Action buttons ──
  btnGenerateReport.addEventListener("click", () => generateReport());
  btnShowGraph.addEventListener("click", () => {
    if (statsGrid.style.display === "none" || statsGrid.style.display === "") {
      showStatus(actionStatus, "Please generate a report first.", "error");
      return;
    }
    // Ensure chart containers are visible
    chartsRow1.style.display = "grid";
    chartsRow2.style.display = "grid";
    renderAllCharts();
    // Auto-scroll to charts
    chartsRow1.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  btnDownloadExcel.addEventListener("click", () => {
    if (statsGrid.style.display === "none" || statsGrid.style.display === "") {
      showStatus(actionStatus, "Please generate a report first.", "error");
      return;
    }
    window.location.href = "/final-academic-insights/download-excel/";
  });

  // ── Helpers ──
  function showStatus(elem, message, type) {
    elem.textContent = message;
    elem.className = "status-msg " + type;
  }

  function escapeHtml(str) {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // ── Chart color palette ──
  const chartColors = {
    pass: "#22c55e",
    fail: "#ef4444",
    grades: [
      "#166534", "#22c55e", "#4ade80", "#facc15",
      "#f97316", "#ef4444", "#b91c1c"
    ],
    assessment: ["#3b82f6", "#8b5cf6", "#f59e0b", "#ef4444"],
    trend: "#1f6f8b",
  };

  // ── Fetch report & populate everything ──
  async function generateReport(silent = false) {
    if (!silent) {
      showStatus(actionStatus, "Generating academic report...", "loading");
    }

    try {
      const response = await fetch("/final-academic-insights/report/");
      const data = await response.json();

      if (response.ok) {
        if (!silent) {
          showStatus(actionStatus, "Report successfully generated.", "success");
        }

        // Show all sections
        statsGrid.style.display = "grid";
        chartsRow1.style.display = "grid";
        chartsRow2.style.display = "grid";
        insightsSection.style.display = "block";
        tableContainer.style.display = "block";
        btnDownloadExcel.style.display = "inline-flex";

        // Auto-scroll to results so user can see the output
        if (!silent) {
          statsGrid.scrollIntoView({ behavior: "smooth", block: "start" });
        }

        // Populate stats
        valTotalStudents.textContent = data.total_students;
        valPassedStudents.textContent = data.passed_students_count;
        valFailedStudents.textContent = data.failed_students_count;
        pctPassed.textContent = `${data.pass_percentage}%`;
        pctFailed.textContent = `${data.fail_percentage}%`;
        valAverageScore.textContent = data.average_score.toFixed(1);
        valHighestScore.textContent = data.highest_score.toFixed(1);

        // Populate insights
        insightsList.innerHTML = "";
        if (data.insights && data.insights.length > 0) {
          data.insights.forEach((insight) => {
            const li = document.createElement("li");
            li.textContent = insight;
            insightsList.appendChild(li);
          });
        }

        // Populate table
        recordCount.textContent = `${data.total_students} records`;
        studentTableBody.innerHTML = "";
        if (data.students && data.students.length > 0) {
          data.students.forEach((student) => {
            const row = document.createElement("tr");
            const statusClass = student.status.toLowerCase() === "pass" ? "pass" : "fail";
            row.innerHTML = `
              <td><strong>${escapeHtml(student.student_name)}</strong></td>
              <td>${student.quiz1}</td>
              <td>${student.quiz2}</td>
              <td>${student.quiz3}</td>
              <td>${student.quiz4}</td>
              <td>${student.assign1}</td>
              <td>${student.assign2}</td>
              <td>${student.assign3}</td>
              <td>${student.assign4}</td>
              <td>${student.midterm}</td>
              <td>${student.final}</td>
              <td><strong>${student.total.toFixed(2)}</strong></td>
              <td><span class="status-pill ${statusClass}">${student.status}</span></td>
            `;
            studentTableBody.appendChild(row);
          });
        } else {
          studentTableBody.innerHTML = `
            <tr>
              <td colspan="13" style="text-align: center; color: #64748b;">No records found. Please upload a marksheet.</td>
            </tr>
          `;
        }

        // Render all charts
        renderAllCharts(data);
      } else {
        if (!silent) {
          showStatus(actionStatus, "Failed to load report data.", "error");
        }
      }
    } catch (err) {
      if (!silent) {
        showStatus(actionStatus, "Network error loading report data.", "error");
      }
      console.error(err);
    }
  }

  // ── Render all charts ──
  function renderAllCharts(dataOverride) {
    // Fetch from server if no data passed, else use passed data
    const fetchPromise = dataOverride
      ? Promise.resolve(dataOverride)
      : fetch("/final-academic-insights/report/").then(r => r.json());

    fetchPromise.then((data) => {
      renderPassFailChart(data);
      renderGradeDistChart(data);
      renderAssessmentChart(data);
      renderTrendChart(data);
    }).catch((err) => {
      console.error("Failed to render charts:", err);
    });
  }

  // ── Pass/Fail Doughnut Chart ──
  function renderPassFailChart(data) {
    const ctx = document.getElementById("insightsChart").getContext("2d");
    if (insightsChartInstance) insightsChartInstance.destroy();

    const total = data.passed_students_count + data.failed_students_count;

    insightsChartInstance = new Chart(ctx, {
      type: "doughnut",
      data: {
        labels: ["Pass", "Fail"],
        datasets: [{
          data: [data.passed_students_count, data.failed_students_count],
          backgroundColor: [chartColors.pass, chartColors.fail],
          borderColor: ["#ffffff", "#ffffff"],
          borderWidth: 2,
          hoverOffset: 4,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: "bottom",
            labels: {
              font: { family: "Inter", weight: "600" },
              color: "#334155",
            },
          },
          tooltip: {
            callbacks: {
              label: function (context) {
                const value = context.raw;
                const pct = total > 0 ? ((value / total) * 100).toFixed(1) : 0;
                return ` ${context.label}: ${value} (${pct}%)`;
              },
            },
          },
        },
        cutout: "65%",
      },
    });
  }

  // ── Grade Distribution Bar Chart ──
  function renderGradeDistChart(data) {
    const ctx = document.getElementById("gradeDistChart").getContext("2d");
    if (gradeDistChartInstance) gradeDistChartInstance.destroy();

    const gd = data.grade_distribution || {};
    const gradeOrder = ["A+", "A", "B+", "B", "C", "D", "F"];
    const labels = gradeOrder.filter(g => gd.hasOwnProperty(g) || Object.keys(gd).length === 0);
    const values = labels.map(g => gd[g] || 0);

    gradeDistChartInstance = new Chart(ctx, {
      type: "bar",
      data: {
        labels: labels.length ? labels : gradeOrder,
        datasets: [{
          label: "Students",
          data: labels.length ? values : gradeOrder.map(() => 0),
          backgroundColor: chartColors.grades.slice(0, labels.length || 7),
          borderColor: "#ffffff",
          borderWidth: 1,
          borderRadius: 4,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (context) {
                const total = data.total_students || 1;
                const pct = ((context.raw / total) * 100).toFixed(1);
                return ` ${context.raw} student(s) (${pct}%)`;
              },
            },
          },
        },
        scales: {
          y: {
            beginAtZero: true,
            ticks: {
              stepSize: 1,
              font: { family: "Inter" },
              color: "#64748b",
            },
            grid: { color: "#e2e8f0" },
          },
          x: {
            ticks: {
              font: { family: "Inter", weight: "600" },
              color: "#334155",
            },
            grid: { display: false },
          },
        },
      },
    });
  }

  // ── Assessment Performance Breakdown Bar Chart ──
  function renderAssessmentChart(data) {
    const ctx = document.getElementById("assessmentChart").getContext("2d");
    if (assessmentChartInstance) assessmentChartInstance.destroy();

    const cats = data.category_averages || {};
    const labels = Object.keys(cats);
    const values = Object.values(cats);

    assessmentChartInstance = new Chart(ctx, {
      type: "bar",
      data: {
        labels: labels,
        datasets: [{
          label: "Average Score",
          data: values,
          backgroundColor: chartColors.assessment.slice(0, labels.length),
          borderColor: "#ffffff",
          borderWidth: 1,
          borderRadius: 4,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (context) {
                return ` Average: ${context.raw.toFixed(1)}`;
              },
            },
          },
        },
        scales: {
          y: {
            beginAtZero: true,
            ticks: {
              font: { family: "Inter" },
              color: "#64748b",
            },
            grid: { color: "#e2e8f0" },
          },
          x: {
            ticks: {
              font: { family: "Inter", weight: "600" },
              color: "#334155",
            },
            grid: { display: false },
          },
        },
      },
    });
  }

  // ── Performance Trend Line Chart ──
  function renderTrendChart(data) {
    const ctx = document.getElementById("trendChart").getContext("2d");
    if (trendChartInstance) trendChartInstance.destroy();

    const trend = data.trend_data || [];
    const labels = ["Quizzes", "Assignments", "Midterm", "Final Exam"];

    trendChartInstance = new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [{
          label: "Average Score",
          data: trend,
          borderColor: chartColors.trend,
          backgroundColor: "rgba(31, 111, 139, 0.08)",
          borderWidth: 3,
          pointBackgroundColor: chartColors.trend,
          pointBorderColor: "#ffffff",
          pointBorderWidth: 2,
          pointRadius: 5,
          pointHoverRadius: 7,
          fill: true,
          tension: 0.3,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (context) {
                return ` Average: ${context.raw.toFixed(1)}`;
              },
            },
          },
        },
        scales: {
          y: {
            beginAtZero: false,
            ticks: {
              font: { family: "Inter" },
              color: "#64748b",
            },
            grid: { color: "#e2e8f0" },
          },
          x: {
            ticks: {
              font: { family: "Inter", weight: "600" },
              color: "#334155",
            },
            grid: { display: false },
          },
        },
      },
    });
  }
});
