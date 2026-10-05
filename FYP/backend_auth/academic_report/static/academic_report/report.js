/**
 * Academic Report Generator - Report Page Charts
 */

(function () {
  'use strict';

  var data = window.REPORT_DATA;
  if (!data) return;

  // Grade Distribution Chart
  var gradeCtx = document.getElementById('gradeChart');
  if (gradeCtx && data.gradeLabels && data.gradeValues) {
    var gradeColors = {
      'A': '#22c55e',
      'B': '#3b82f6',
      'C': '#eab308',
      'D': '#f97316',
      'F': '#ef4444'
    };
    var bgColors = data.gradeLabels.map(function (g) { return gradeColors[g] || '#94a3b8'; });

    new Chart(gradeCtx, {
      type: 'bar',
      data: {
        labels: data.gradeLabels,
        datasets: [{
          label: 'Students',
          data: data.gradeValues,
          backgroundColor: bgColors,
          borderRadius: 6,
        }]
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: false },
        },
        scales: {
          y: {
            beginAtZero: true,
            ticks: { stepSize: 1 },
          }
        }
      }
    });
  }

  // CLO Averages Chart
  var cloCtx = document.getElementById('cloChart');
  if (cloCtx && data.cloLabels && data.cloValues) {
    var cloColors = ['#1f6f8b', '#0d9488', '#6366f1', '#8b5cf6'];
    new Chart(cloCtx, {
      type: 'bar',
      data: {
        labels: data.cloLabels,
        datasets: [{
          label: 'CLO Average',
          data: data.cloValues,
          backgroundColor: cloColors,
          borderRadius: 6,
        }]
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: false },
        },
        scales: {
          y: {
            beginAtZero: true,
            max: 4.0,
            ticks: {
              stepSize: 0.5,
              callback: function (value) { return value.toFixed(1); }
            }
          }
        }
      }
    });
  }
})();
