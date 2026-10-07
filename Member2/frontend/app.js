const contextInput = document.querySelector("#context-input");
const characterCount = document.querySelector("#character-count");
const clearButton = document.querySelector("#clear-button");
const screenshotInput = document.querySelector("#screenshot-input");
const screenshotPreview = document.querySelector("#screenshot-preview");
const screenshotFilename = document.querySelector("#screenshot-filename");
const screenshotImage = document.querySelector("#screenshot-image");
const removeScreenshotButton = document.querySelector("#remove-screenshot");
const applicationContext = document.querySelector("#application-context");
const privacyPreference = document.querySelector("#privacy-preference");
const historicalProtection = document.querySelector("#historical-protection");
const analyzeButton = document.querySelector("#analyze-button");
const actionNote = document.querySelector("#action-note");
const analysisStatus = document.querySelector("#analysis-status");
const detectionOutput = document.querySelector("#detection-output");
const riskOutput = document.querySelector("#risk-output");
const protectionOutput = document.querySelector("#protection-output");
const featuresOutput = document.querySelector("#features-output");
const modelStatus = document.querySelector("#model-status");

const ANALYZE_URL = "http://127.0.0.1:8000/analyze";

let screenshotObjectUrl = null;

function updateCharacterCount() {
  const count = contextInput.value.length;
  characterCount.textContent = `${count} character${count === 1 ? "" : "s"}`;
}

contextInput.addEventListener("input", updateCharacterCount);

clearButton.addEventListener("click", () => {
  contextInput.value = "";
  updateCharacterCount();
  contextInput.focus();
});

function clearScreenshotPreview() {
  if (screenshotObjectUrl) {
    URL.revokeObjectURL(screenshotObjectUrl);
    screenshotObjectUrl = null;
  }

  screenshotInput.value = "";
  screenshotFilename.textContent = "";
  screenshotImage.removeAttribute("src");
  screenshotPreview.hidden = true;
}

screenshotInput.addEventListener("change", () => {
  const [selectedFile] = screenshotInput.files;

  if (!selectedFile) {
    clearScreenshotPreview();
    return;
  }

  if (screenshotObjectUrl) {
    URL.revokeObjectURL(screenshotObjectUrl);
  }

  screenshotObjectUrl = URL.createObjectURL(selectedFile);
  screenshotFilename.textContent = selectedFile.name;
  screenshotImage.src = screenshotObjectUrl;
  screenshotPreview.hidden = false;
});

removeScreenshotButton.addEventListener("click", clearScreenshotPreview);

function hasRequiredInputs() {
  return Boolean(
    screenshotInput.files[0]
    && applicationContext.value
    && privacyPreference.value
    && historicalProtection.value
  );
}

function updateAnalyzeButton() {
  analyzeButton.disabled = !hasRequiredInputs();
  if (!analyzeButton.disabled && !analyzeButton.dataset.running) {
    actionNote.textContent = "Ready to send the selected screenshot for analysis.";
    actionNote.classList.remove("is-error");
  }
}

function setAnalysisStatus(title, message, state = "") {
  analysisStatus.classList.toggle("is-error", state === "error");
  analysisStatus.querySelector("strong").textContent = title;
  analysisStatus.querySelector("span").textContent = message;
}

function setModelStatus(message, state = "") {
  modelStatus.classList.toggle("is-error", state === "error");
  modelStatus.classList.toggle("is-analyzing", state === "analyzing");
  modelStatus.querySelector("span:last-child").textContent = message;
}

function displayValue(value) {
  if (value === null) {
    return "null (backend response)";
  }
  if (value === undefined) {
    return "undefined (backend response)";
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

function renderMetrics(container, values) {
  container.replaceChildren();
  const metricList = document.createElement("dl");
  metricList.className = "metric-list";

  Object.entries(values).forEach(([key, value]) => {
    const label = document.createElement("dt");
    const result = document.createElement("dd");
    label.textContent = key.replaceAll("_", " ");
    result.textContent = displayValue(value);
    metricList.append(label, result);
  });

  container.append(metricList);
}

function renderDetectionResults(ocrSummary, detectionResults) {
  detectionOutput.replaceChildren();
  const detectionNote = document.createElement("p");
  detectionNote.className = "response-note";
  detectionNote.textContent = `OCR detection count: ${displayValue(ocrSummary.detections_count)}`;
  detectionOutput.append(detectionNote);

  const categoryCounts = {};
  Object.entries(detectionResults).forEach(([category, value]) => {
    if (category === "total_sensitive_entity_count") {
      categoryCounts[category] = value;
      return;
    }
    if (value && typeof value === "object" && !Array.isArray(value)) {
      categoryCounts[`${category} count`] = value.count;
    } else {
      categoryCounts[category] = value;
    }
  });
  renderMetrics(detectionOutput, categoryCounts);
}

function renderBackendResponse(response) {
  renderDetectionResults(response.ocr_summary || {}, response.detection_results || {});
  renderMetrics(featuresOutput, response.extracted_features || {});
  renderMetrics(riskOutput, response.risk_prediction || {});
  renderMetrics(protectionOutput, response.protection_selection || {});

  const modelStatusValue = response.risk_prediction?.model_status;
  setModelStatus(`Model status: ${displayValue(modelStatusValue)}`);
}

function resetResults() {
  detectionOutput.innerHTML = "<p class=\"response-empty\">No analysis has been performed.</p>";
  riskOutput.innerHTML = "<p class=\"response-empty\">No risk assessment has been performed.</p>";
  protectionOutput.innerHTML = "<p class=\"response-empty\">No protection decision is available.</p>";
  featuresOutput.innerHTML = "<p class=\"response-empty\">No extracted features are available.</p>";
  setModelStatus("Model status: awaiting analysis");
}

[screenshotInput, applicationContext, privacyPreference, historicalProtection].forEach((input) => {
  input.addEventListener("change", updateAnalyzeButton);
});

const originalClearScreenshot = clearScreenshotPreview;
screenshotInput.addEventListener("change", updateAnalyzeButton);
removeScreenshotButton.addEventListener("click", () => {
  originalClearScreenshot();
  updateAnalyzeButton();
});

analyzeButton.addEventListener("click", async () => {
  if (!hasRequiredInputs()) {
    updateAnalyzeButton();
    return;
  }

  analyzeButton.dataset.running = "true";
  analyzeButton.disabled = true;
  analyzeButton.textContent = "Analyzing...";
  actionNote.textContent = "Sending the screenshot to the local backend.";
  actionNote.classList.remove("is-error");
  setAnalysisStatus("Analyzing", "The selected screenshot is being processed by the local backend.");
  setModelStatus("Model status: analysis in progress", "analyzing");

  const formData = new FormData();
  formData.append("image", screenshotInput.files[0]);
  formData.append("application_context", applicationContext.value);
  formData.append("privacy_preference", privacyPreference.value);
  formData.append("historical_protection_level", historicalProtection.value);

  try {
    const response = await fetch(ANALYZE_URL, {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      throw new Error(`HTTP_${response.status}`);
    }

    const result = await response.json();
    if (!result || typeof result !== "object") {
      throw new Error("UNEXPECTED_RESPONSE");
    }

    renderBackendResponse(result);
    setAnalysisStatus("Analysis complete", "Actual values returned by the local backend are shown below.");
    actionNote.textContent = "Analysis completed.";
  } catch (error) {
    resetResults();
    setModelStatus("Model status: unavailable", "error");
    actionNote.classList.add("is-error");
    if (error instanceof TypeError) {
      setAnalysisStatus("Backend unavailable", "Could not connect to the local analysis service.", "error");
      actionNote.textContent = "Start the FastAPI backend and try again.";
    } else if (error.message === "UNEXPECTED_RESPONSE") {
      setAnalysisStatus("Unexpected response", "The backend returned an unreadable analysis response.", "error");
      actionNote.textContent = "No results were displayed.";
    } else {
      setAnalysisStatus("Analysis failed", "The backend rejected the screenshot or request.", "error");
      actionNote.textContent = "Check the screenshot and configuration values, then try again.";
    }
  } finally {
    analyzeButton.dataset.running = "";
    analyzeButton.textContent = "Analyze";
    updateAnalyzeButton();
  }
});

updateAnalyzeButton();
