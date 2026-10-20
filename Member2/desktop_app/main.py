"""Native CAPRLM model inspection UI. Run from the Member2 directory."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QHBoxLayout, QLabel, QListWidget,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QSpinBox,
    QStackedWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from desktop_app.services import model_inspector as inspector


class CaprlmWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("CAPRLM | Model Workbench")
        self.setMinimumSize(980, 650)
        self.resize(1240, 800)
        self.info = None
        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        self.nav = QListWidget()
        self.nav.setFixedWidth(210)
        self.nav.addItems(["Model overview", "Feature importance", "Prediction explorer",
                           "Decision tree", "Evaluation", "System information"])
        self.pages = QStackedWidget()
        outer.addWidget(self.nav)
        outer.addWidget(self.pages, 1)
        self.setCentralWidget(root)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        self._build_pages()
        self._load()

    def _page(self, title: str, subtitle: str = "") -> tuple[QWidget, QVBoxLayout]:
        w = QWidget(); layout = QVBoxLayout(w)
        layout.setContentsMargins(30, 24, 30, 24); layout.setSpacing(14)
        heading = QLabel(title); heading.setObjectName("Heading")
        layout.addWidget(heading)
        if subtitle:
            note = QLabel(subtitle); note.setWordWrap(True); note.setObjectName("Muted")
            layout.addWidget(note)
        self.pages.addWidget(w)
        return w, layout

    def _build_pages(self) -> None:
        _, self.overview = self._page("Model overview", "Metadata is read from the serialized model bundle.")
        self.overview_text = QTextBrowser(); self.overview.addWidget(self.overview_text)
        _, self.importance = self._page("Feature importance", "Mean decrease in impurity is model reliance, not causal evidence.")
        self.importance_rows = QVBoxLayout(); self.importance.addLayout(self.importance_rows); self.importance.addStretch()

        _, self.prediction = self._page("Interactive prediction explorer",
            "Feature-based prediction only. These manually selected values are not observations from a screenshot; results do not prove a screenshot is safe.")
        form_host = QWidget(); form = QFormLayout(form_host)
        self.inputs = {}
        for feature in ["EMAIL", "PHONE", "URL", "IP_ADDRESS", "DATE", "WINDOWS_PATH", "sensitive_entity_present"]:
            spin = QSpinBox(); spin.setRange(0, 1); spin.setToolTip("0 = absent, 1 = present")
            form.addRow(feature, spin); self.inputs[feature] = spin
        self.context = QComboBox(); self.context.setEditable(True); self.context.addItems(["Unknown", "ChatGPT", "Google Chrome", "VS Code"])
        form.addRow("Application context", self.context)
        self.prediction.addWidget(form_host)
        run = QPushButton("Run prediction"); run.clicked.connect(self._predict); self.prediction.addWidget(run)
        self.prediction_result = QTextBrowser(); self.prediction_result.setMaximumHeight(180); self.prediction.addWidget(self.prediction_result)

        _, self.tree_page = self._page("Decision tree", "Inspect one estimator from the forest. It does not represent the full Random Forest; expansion is limited to depth 4.")
        self.tree_select = QComboBox(); self.tree_select.currentIndexChanged.connect(self._show_tree)
        self.tree_page.addWidget(self.tree_select)
        self.tree_text = QTextBrowser(); self.tree_page.addWidget(self.tree_text, 1)

        _, self.eval_page = self._page("Model evaluation", "Only the recorded held-out evaluation is shown. Training metrics are not substituted.")
        self.eval_text = QTextBrowser(); self.eval_page.addWidget(self.eval_text)
        _, self.system_page = self._page("System information", "Local runtime and model loading status.")
        self.system_text = QTextBrowser(); self.system_page.addWidget(self.system_text)

    def _load(self) -> None:
        try:
            self.info = inspector.load_model_info()
            b, m = self.info["bundle"], self.info["model"]
            rows = [("Status", "Ready"), ("Type", self.info["model_type"]),
                    ("Model version", self.info["version"] or "Not available"),
                    ("Artifact", self.info["artifact"]), ("Feature count", len(self.info["feature_names"])),
                    ("Features in expected order", " → ".join(self.info["feature_names"])),
                    ("Trained classes", ", ".join(self.info["classes"])),
                    ("Estimators", self.info["n_estimators"] or "Not available"),
                    ("Maximum depth parameter", self.info["max_depth"] or "Not available"),
                    ("Training rows", b.get("train_size", "Not available")),
                    ("Held-out rows", b.get("test_size", "Not available")),
                    ("Model tree depth", max((t.tree_.max_depth for t in getattr(m, "estimators_", [])), default="Not available"))]
            self.overview_text.setHtml("<br>".join(f"<b>{k}:</b> {v}" for k, v in rows))
            self._show_importance()
            count = len(getattr(m, "estimators_", []))
            self.tree_select.addItems([str(i) for i in range(count)])
            self._show_tree()
            self._show_evaluation()
            self.system_text.setHtml("<br>".join(f"<b>{k}:</b> {v}" for k, v in inspector.system_info().items()) +
                                     "<br><br><b>Application:</b> Ready")
        except Exception as exc:
            safe = str(exc).replace("<", "&lt;").replace(">", "&gt;")
            self.overview_text.setHtml(f"<b>Model status:</b> unavailable<br>{safe}<br><br>Check that the artifact exists and dependencies are installed.")
            self.system_text.setPlainText("Model loading failed. See Model overview for actionable details.")
            self.prediction_result.setPlainText("Prediction unavailable until the model loads.")

    def _show_importance(self) -> None:
        rows = inspector.feature_importances(self.info)
        if not rows:
            self.importance_rows.addWidget(QLabel("Feature importance is not available for this model.")); return
        for name, value in rows:
            line = QHBoxLayout(); label = QLabel(name); label.setMinimumWidth(225)
            bar = QProgressBar(); bar.setRange(0, 1000); bar.setValue(int(value * 1000)); bar.setFormat(f"{value:.6f}")
            line.addWidget(label); line.addWidget(bar, 1); self.importance_rows.addLayout(line)

    def _predict(self) -> None:
        try:
            context = self.context.currentText().strip()
            if not context:
                raise ValueError("Application context must not be empty.")
            values = {k: v.value() for k, v in self.inputs.items()}
            result = inspector.predict(values, context)
            # risk_prediction adds a synthetic CRITICAL=0 compatibility entry
            # when the estimator has no such class; only show trained classes.
            trained_classes = self.info["classes"] if self.info else []
            probs = {k: v for k, v in (result.get("raw_probabilities") or {}).items()
                     if k in trained_classes}
            shown = "<br>".join(f"{c}: {p:.4f}" for c, p in probs.items()) if probs else "Probability estimates unavailable."
            self.prediction_result.setHtml(f"<b>Risk level after existing preference thresholds:</b> {result['risk_level']}<br><b>Raw probabilities for trained classes:</b><br>{shown}<br><br>Probability estimates are not evaluated for calibration.")
        except Exception as exc:
            QMessageBox.warning(self, "Prediction failed", str(exc))

    def _show_tree(self, *_args) -> None:
        if not self.info: return
        trees = getattr(self.info["model"], "estimators_", [])
        if not trees or self.tree_select.currentIndex() < 0:
            self.tree_text.setPlainText("Tree inspection is unavailable for this model."); return
        tree = trees[self.tree_select.currentIndex()].tree_
        names = self.info["feature_names"]
        classes = self.info["classes"]
        lines = []
        def visit(node: int, depth: int, branch: str = "") -> None:
            indent = "  " * depth
            if depth >= 4 and tree.children_left[node] != -1:
                lines.append(indent + branch + "… display truncated at depth 4"); return
            values = tree.value[node][0]
            distribution = ", ".join(f"{c}: {v:g}" for c, v in zip(classes, values))
            if tree.children_left[node] == -1:
                lines.append(f"{indent}{branch}Leaf — samples {tree.n_node_samples[node]}, {distribution}"); return
            feature = names[tree.feature[node]] if tree.feature[node] < len(names) else f"feature {tree.feature[node]}"
            lines.append(f"{indent}{branch}{feature} ≤ {tree.threshold[node]:.4f} — samples {tree.n_node_samples[node]}, {distribution}")
            visit(tree.children_left[node], depth + 1, "TRUE  → ")
            visit(tree.children_right[node], depth + 1, "FALSE → ")
        visit(0, 0)
        self.tree_text.setPlainText(
            f"Tree {self.tree_select.currentIndex()} of {len(trees)} — actual depth {tree.max_depth}, "
            f"{tree.node_count} nodes.\n\n" + "\n".join(lines)
        )

    def _show_evaluation(self) -> None:
        record = inspector.evaluation()
        if not record:
            self.eval_text.setPlainText("Evaluation unavailable: backend/data/training_log.json is missing or empty."); return
        report = record.get("test_classification_report", {})
        labels = record.get("confusion_matrix_labels", [])
        cm = record.get("test_confusion_matrix", [])
        text = [f"Dataset: CAPRLM_annotation_50.csv ({record.get('dataset_rows', 'Not available')} rows)",
                f"Split: held-out test set ({record.get('test_size', 'Not available')} rows)",
                "Observed label distribution: " + ", ".join(f"{k} {v}" for k, v in record.get("label_distribution", {}).items()),
                f"Accuracy: {report.get('accuracy', 'Not available')}",
                f"Macro F1: {report.get('macro avg', {}).get('f1-score', 'Not available')}",
                f"Weighted F1: {report.get('weighted avg', {}).get('f1-score', 'Not available')}",
                "", "Per-class precision / recall / F1 / support:"]
        for cls in labels:
            row = report.get(cls, {})
            text.append(f"{cls}: {row.get('precision','N/A')} / {row.get('recall','N/A')} / {row.get('f1-score','N/A')} / {row.get('support','N/A')}")
        text += ["", f"Confusion matrix labels: {', '.join(labels)}", str(cm), "", "Limitations:"]
        text += [f"• {item}" for item in record.get("limitations", [])]
        self.eval_text.setPlainText("\n".join(text))


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet("""
      QWidget { background:#f5f7fb; color:#172033; font-family:'Segoe UI'; font-size:14px; }
      QListWidget { background:#111a2e; color:#dbe4f3; border:0; padding:12px; }
      QListWidget::item { padding:13px 10px; border-radius:6px; }
      QListWidget::item:selected { background:#294a77; color:white; }
      QLabel#Heading { font-size:25px; font-weight:650; color:#14213a; }
      QLabel#Muted { color:#59677e; }
      QTextBrowser { background:white; border:1px solid #dce3ee; border-radius:8px; padding:14px; }
      QProgressBar { background:#e8edf5; border:0; border-radius:5px; height:19px; text-align:center; }
      QProgressBar::chunk { background:#5278b8; border-radius:5px; }
      QPushButton { background:#315c98; color:white; padding:10px 18px; border:0; border-radius:6px; }
    """)
    window = CaprlmWindow(); window.show(); return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
