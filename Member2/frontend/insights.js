"use strict";

const API_BASE = "http://127.0.0.1:8765";

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status} — ${url}`);
  return res.json();
}

function fmt(v, decimals = 4) {
  if (v === null || v === undefined) return "Unavailable";
  if (typeof v === "number") return v.toFixed(decimals);
  return String(v);
}

function pct(v) {
  if (v === null || v === undefined) return "Unavailable";
  return (v * 100).toFixed(1) + "%";
}

function metricRow(label, value) {
  return `<dt>${label}</dt><dd>${value}</dd>`;
}

// ---------------------------------------------------------------------------
// Connection status
// ---------------------------------------------------------------------------

function setConnectionStatus(ok, text) {
  const el = document.getElementById("connection-status");
  el.querySelector("span:last-child").textContent = text;
  el.querySelector(".status-indicator").style.background = ok ? "#236b67" : "#9b3f3f";
}

// ---------------------------------------------------------------------------
// Phase 2 — Model Overview
// ---------------------------------------------------------------------------

async function loadModelOverview() {
  const el = document.getElementById("overview-content");
  try {
    const d = await fetchJSON(`${API_BASE}/insights/model-overview`);
    setConnectionStatus(true, "Backend connected");

    if (!d.model_available) {
      el.innerHTML = `<p class="unavailable">Model not available. POST /train to train the model first.</p>`;
      return;
    }

    const featureList = Array.isArray(d.feature_names)
      ? d.feature_names.map((f, i) => `${i + 1}. ${f}`).join(" &nbsp;|&nbsp; ")
      : "Unavailable";

    const classLabels = Array.isArray(d.class_labels)
      ? d.class_labels.join(", ")
      : "Unavailable";

    el.innerHTML = `
      <dl class="metric-list">
        ${metricRow("Algorithm", d.algorithm || "Unavailable")}
        ${metricRow("Number of estimators", fmt(d.n_estimators, 0))}
        ${metricRow("Number of features", fmt(d.n_features, 0))}
        ${metricRow("Class labels (model.classes_)", classLabels)}
        ${metricRow("Model version", d.model_version || "Unavailable")}
        ${metricRow("Random state", d.random_state !== undefined ? String(d.random_state) : "Unavailable")}
        ${metricRow("Training set size", fmt(d.train_size, 0))}
        ${metricRow("Test set size", fmt(d.test_size, 0))}
        ${metricRow("Cross-validation folds", fmt(d.cv_folds, 0))}
        ${metricRow("CV macro F1 (mean ± std)", d.cv_macro_f1_mean !== undefined
          ? `${fmt(d.cv_macro_f1_mean)} ± ${fmt(d.cv_macro_f1_std)}`
          : "Unavailable")}
        ${metricRow("Test macro F1", fmt(d.test_macro_f1))}
        ${metricRow("Model artifact", d.model_artifact || "Unavailable")}
      </dl>
      <p style="margin-top:12px; font-size:0.78rem; color:var(--muted)">
        <strong>Feature names (in order):</strong> ${featureList}
      </p>`;
  } catch (err) {
    el.innerHTML = `<p class="unavailable">Could not load model overview: ${err.message}</p>`;
    setConnectionStatus(false, "Backend unavailable");
  }
}

// ---------------------------------------------------------------------------
// Phase 3 — Feature Importance
// ---------------------------------------------------------------------------

let importanceChartInstance = null;

async function loadFeatureImportance() {
  const noteEl = document.getElementById("importance-note");
  try {
    const d = await fetchJSON(`${API_BASE}/insights/feature-importance`);
    const items = d.feature_importances || [];
    if (!items.length) {
      noteEl.textContent = "No feature importance data available.";
      return;
    }

    const labels = items.map(i => i.feature);
    const values = items.map(i => parseFloat(i.importance.toFixed(6)));

    const ctx = document.getElementById("importance-chart").getContext("2d");
    if (importanceChartInstance) importanceChartInstance.destroy();
    importanceChartInstance = new Chart(ctx, {
      type: "bar",
      data: {
        labels,
        datasets: [{
          label: "Importance",
          data: values,
          backgroundColor: "rgba(35, 107, 103, 0.75)",
          borderColor: "rgba(35, 107, 103, 1)",
          borderWidth: 1,
          borderRadius: 2,
        }],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: ctx => ` ${ctx.parsed.x.toFixed(6)}`,
            },
          },
        },
        scales: {
          x: {
            beginAtZero: true,
            ticks: { font: { size: 11 } },
            title: { display: true, text: "Importance (mean decrease in impurity)", font: { size: 11 } },
          },
          y: {
            ticks: { font: { size: 11 } },
          },
        },
      },
    });

    noteEl.textContent = d.note || "";
  } catch (err) {
    document.getElementById("importance-note").textContent =
      `Could not load feature importance: ${err.message}`;
  }
}

// ---------------------------------------------------------------------------
// Phase 4 — Interactive Prediction Explorer
// ---------------------------------------------------------------------------

function setupPredictionForm() {
  const btn = document.getElementById("predict-button");
  const noteEl = document.getElementById("predict-note");
  const resultEl = document.getElementById("predict-result");

  btn.addEventListener("click", async () => {
    btn.disabled = true;
    noteEl.textContent = "Running…";
    noteEl.className = "action-note";
    resultEl.hidden = true;

    const payload = {
      email:                    document.getElementById("feat-email").checked ? 1 : 0,
      phone:                    document.getElementById("feat-phone").checked ? 1 : 0,
      url:                      document.getElementById("feat-url").checked ? 1 : 0,
      ip_address:               document.getElementById("feat-ip").checked ? 1 : 0,
      date:                     document.getElementById("feat-date").checked ? 1 : 0,
      windows_path:             document.getElementById("feat-path").checked ? 1 : 0,
      sensitive_entity_present: document.getElementById("feat-sensitive").checked ? 1 : 0,
      application_context:      document.getElementById("predict-context").value,
      privacy_preference:       document.getElementById("predict-preference").value,
      historical_protection_level: document.getElementById("predict-history").value,
    };

    try {
      const res = await fetch(`${API_BASE}/insights/predict`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || res.statusText);
      }
      const data = await res.json();
      const rp = data.risk_prediction || {};
      const proba = rp.raw_probabilities || {};

      const probaRows = Object.entries(proba)
        .sort((a, b) => b[1] - a[1])
        .map(([cls, p]) => `
          <dt>${cls}</dt>
          <dd>${(p * 100).toFixed(1)}%</dd>`)
        .join("");

      const inputRows = Object.entries(data.input_features || {})
        .map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`)
        .join("");

      resultEl.innerHTML = `
        <div class="predict-result">
          <p style="margin:0 0 4px; font-size:0.75rem; font-weight:700; color:var(--muted); text-transform:uppercase; letter-spacing:0.06em">Predicted risk level</p>
          <p class="risk-level">${rp.risk_level || "Unavailable"}</p>
          <p style="font-size:0.78rem; color:var(--muted); margin:0 0 12px">
            Confidence: ${rp.confidence !== null && rp.confidence !== undefined ? (rp.confidence * 100).toFixed(1) + "%" : "Unavailable"}
            &nbsp;|&nbsp; Model: ${rp.model_type || "—"}
          </p>
          ${probaRows ? `
            <p style="font-size:0.75rem; font-weight:700; color:var(--muted); text-transform:uppercase; letter-spacing:0.06em; margin-bottom:2px">Class probabilities (raw)</p>
            <dl class="metric-list" style="margin-bottom:12px">${probaRows}</dl>` : ""}
          <p style="font-size:0.75rem; font-weight:700; color:var(--muted); text-transform:uppercase; letter-spacing:0.06em; margin-bottom:2px">Inputs submitted</p>
          <dl class="metric-list">${inputRows}</dl>
          <p style="margin:10px 0 0; font-size:0.75rem; color:#6b5200; background:#fdf6e7; border:1px solid #e8d8a0; padding:7px 10px">
            ${data.disclaimer}
          </p>
        </div>`;
      resultEl.hidden = false;
      noteEl.textContent = "";
    } catch (err) {
      noteEl.textContent = `Error: ${err.message}`;
      noteEl.className = "action-note is-error";
    } finally {
      btn.disabled = false;
    }
  });
}

// ---------------------------------------------------------------------------
// Phase 5 — Decision Tree Explorer
// ---------------------------------------------------------------------------

function renderTreeNode(node, depth) {
  if (!node) return "";
  if (node.type === "truncated") {
    return `<p class="tree-truncated">${node.message}</p>`;
  }
  if (node.type === "leaf") {
    const distRows = Object.entries(node.class_distribution || {})
      .map(([c, n]) => `${c}:${n}`)
      .join(", ");
    return `
      <div class="tree-leaf">
        Leaf &#8594; <strong>${node.predicted_class}</strong>
        &nbsp;(${node.samples} sample${node.samples !== 1 ? "s" : ""},
        gini&nbsp;${node.impurity}${distRows ? ", dist: " + distRows : ""})
      </div>`;
  }
  if (node.type === "split") {
    const openAttr = depth < 2 ? " open" : "";
    return `
      <details class="tree-node"${openAttr}>
        <summary>${node.condition} &nbsp;<span style="font-weight:400; color:var(--muted)">(${node.samples} samples, gini ${node.impurity})</span></summary>
        <p class="tree-branch-label">True (≤ ${node.threshold})</p>
        ${renderTreeNode(node.left, depth + 1)}
        <p class="tree-branch-label">False (&gt; ${node.threshold})</p>
        ${renderTreeNode(node.right, depth + 1)}
      </details>`;
  }
  return "";
}

let totalTrees = 0;

async function setupTreeExplorer() {
  const countLabel = document.getElementById("tree-count-label");
  const treeContent = document.getElementById("tree-content");
  const treeNote = document.getElementById("tree-note");
  const loadBtn = document.getElementById("tree-load-button");
  const indexInput = document.getElementById("tree-index-input");

  // Probe tree count from tree/0
  try {
    const probe = await fetchJSON(`${API_BASE}/insights/tree/0`);
    totalTrees = probe.n_trees_total || 0;
    countLabel.textContent = `${totalTrees} trees`;
    indexInput.max = totalTrees - 1;
    renderTree(probe, treeContent);
  } catch {
    countLabel.textContent = "Unavailable";
    treeContent.innerHTML = `<p class="unavailable">Tree exploration unavailable — model may not expose estimators_.</p>`;
  }

  loadBtn.addEventListener("click", async () => {
    const idx = parseInt(indexInput.value, 10);
    if (isNaN(idx) || idx < 0) {
      treeNote.textContent = "Enter a valid non-negative index.";
      return;
    }
    treeNote.textContent = "Loading…";
    loadBtn.disabled = true;
    try {
      const data = await fetchJSON(`${API_BASE}/insights/tree/${idx}`);
      renderTree(data, treeContent);
      treeNote.textContent = "";
    } catch (err) {
      treeNote.textContent = `Error: ${err.message}`;
    } finally {
      loadBtn.disabled = false;
    }
  });
}

function renderTree(data, container) {
  const html = `
    <p style="font-size:0.78rem; color:var(--muted); margin-bottom:8px">
      Tree ${data.tree_index} of ${data.n_trees_total} &nbsp;|&nbsp;
      Features: ${(data.feature_names || []).join(", ")} &nbsp;|&nbsp;
      Classes: ${(data.class_names || []).join(", ")} &nbsp;|&nbsp;
      Display depth limit: ${data.max_depth_displayed}
    </p>
    ${renderTreeNode(data.tree, 0)}`;
  container.innerHTML = html;
}

// ---------------------------------------------------------------------------
// Phase 6 — Model Evaluation
// ---------------------------------------------------------------------------

async function loadEvaluation() {
  const el = document.getElementById("eval-content");
  try {
    const d = await fetchJSON(`${API_BASE}/insights/evaluation`);

    if (!d.available) {
      el.innerHTML = `<p class="unavailable">${d.message || "Evaluation data unavailable."}</p>`;
      return;
    }

    // Confusion matrix
    const cm = d.confusion_matrix || [];
    const cmLabels = d.confusion_matrix_labels || [];
    let cmHtml = "";
    if (cm.length && cmLabels.length) {
      const headerCells = cmLabels.map(l => `<th>${l}</th>`).join("");
      const rows = cm.map((row, ri) => {
        const cells = row.map((v, ci) =>
          `<td class="${ri === ci ? "diagonal" : ""}">${v}</td>`
        ).join("");
        return `<tr><th>${cmLabels[ri]}</th>${cells}</tr>`;
      }).join("");
      cmHtml = `
        <p style="font-size:0.75rem; font-weight:700; color:var(--muted); text-transform:uppercase; letter-spacing:0.06em; margin:16px 0 4px">
          Confusion Matrix (rows = true label, cols = predicted)
        </p>
        <table class="confusion-table">
          <thead><tr><th>True \\ Pred</th>${headerCells}</tr></thead>
          <tbody>${rows}</tbody>
        </table>`;
    }

    // Per-class metrics
    const perClass = d.per_class_metrics || {};
    let perClassHtml = "";
    if (Object.keys(perClass).length) {
      const classRows = Object.entries(perClass).map(([cls, m]) => `
        <tr>
          <td>${cls}</td>
          <td>${fmt(m.precision)}</td>
          <td>${fmt(m.recall)}</td>
          <td>${fmt(m["f1-score"])}</td>
          <td>${m.support !== undefined ? m.support : "—"}</td>
        </tr>`).join("");
      perClassHtml = `
        <p style="font-size:0.75rem; font-weight:700; color:var(--muted); text-transform:uppercase; letter-spacing:0.06em; margin:16px 0 4px">
          Per-class Metrics (test set)
        </p>
        <table class="per-class-table">
          <thead><tr><th>Class</th><th>Precision</th><th>Recall</th><th>F1</th><th>Support</th></tr></thead>
          <tbody>${classRows}</tbody>
        </table>`;
    }

    // Limitations
    const lims = d.limitations || [];
    const limHtml = lims.length
      ? `<ul class="limitation-list">${lims.map(l => `<li>${l}</li>`).join("")}</ul>`
      : "";

    el.innerHTML = `
      <dl class="metric-list">
        ${metricRow("Evaluation split", d.evaluation_split || "Unavailable")}
        ${metricRow("Model version", d.model_version || "Unavailable")}
        ${metricRow("Trained at", d.trained_at || "Unavailable")}
        ${metricRow("Dataset rows (total)", fmt(d.dataset_rows, 0))}
        ${metricRow("Training set size", fmt(d.train_size, 0))}
        ${metricRow("Test set size", fmt(d.test_size, 0))}
        ${metricRow("Test accuracy", pct(d.test_accuracy))}
        ${metricRow("Test macro F1", fmt(d.test_macro_f1))}
        ${metricRow("Macro precision", fmt(d.macro_precision))}
        ${metricRow("Macro recall", fmt(d.macro_recall))}
        ${metricRow("CV macro F1 (mean ± std)",
          d.cv_macro_f1_mean !== null && d.cv_macro_f1_mean !== undefined
            ? `${fmt(d.cv_macro_f1_mean)} ± ${fmt(d.cv_macro_f1_std)}`
            : "Unavailable")}
      </dl>

      ${cmHtml}
      ${perClassHtml}

      ${lims.length ? `
        <p style="font-size:0.78rem; font-weight:700; color:var(--muted); margin:16px 0 4px">Known limitations</p>
        ${limHtml}` : ""}

      <p style="margin-top:12px; font-size:0.78rem; color:var(--muted)">${d.note || ""}</p>`;
  } catch (err) {
    el.innerHTML = `<p class="unavailable">Could not load evaluation metrics: ${err.message}</p>`;
  }
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

window.addEventListener("DOMContentLoaded", () => {
  Promise.allSettled([
    loadModelOverview(),
    loadFeatureImportance(),
    loadEvaluation(),
  ]);
  setupPredictionForm();
  setupTreeExplorer();
});
