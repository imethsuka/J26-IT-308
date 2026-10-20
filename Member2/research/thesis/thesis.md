# Context-Aware Privacy Risk Learning and Management Framework (CAPRLM)

## Member 2 thesis draft

**Project:** J26-IT-308  
**Status:** Submission-review draft; not university-formatted or supervisor-approved  
**Evidence cutoff:** 20 October 2026  
**Author:** [To be completed]  
**Supervisor:** [To be completed]

> **Evidence notice.** This draft is based on the current repository and its
> recorded artifacts. It does not claim that the system is production-ready,
> that the classifier is reliable, or that synthetic tests establish real-world
> privacy effectiveness. See `claims_register.md` and `evidence_inventory.md`.

## Abstract

Desktop screenshots can contain personal identifiers, browsing activity,
network information, file paths, credentials, and contextual information about
the user. CAPRLM is a research prototype for analysing screenshot privacy risk
and selecting adaptive protection actions. The Member 2 component combines
EasyOCR text extraction, deterministic sensitive-entity detection, feature
extraction, a Random Forest risk classifier, a rule-based baseline,
risk-adaptive tokenization, contextual heuristic scoring, feedback-based
threshold recalibration, and pixel-level redaction.

The implemented system was evaluated using a 50-row manually annotated dataset
containing 38 LOW, 10 MEDIUM, and 2 HIGH records, with no CRITICAL records.
The recorded 80/20 split used 40 training rows and 10 test rows; the test fold
contained no HIGH examples. On that split, the Random Forest, rule baseline,
and majority-class baseline all recorded accuracy of 0.80 and macro F1 of
0.4444 in the project evaluation artifact. This result does not support a
claim of machine-learning superiority. It instead shows that the available
feature representation and dataset provide no measured advantage for the
Random Forest on this split.

The engineering validation is stronger than the effectiveness evidence.
Synthetic and regression tests cover entity detection, tokenization privacy
properties, OCR failure paths, incomplete entity mapping, redaction actions,
API response handling, and adaptive-threshold controls. The latest recorded
full backend run after the redaction-validation stage passed 404 tests with
one existing dependency warning. These tests demonstrate specified software
behaviour; they do not estimate real-world OCR recall, redaction readability,
or deployment security. Sixteen of the 50 annotations remain flagged for
human review, inter-annotator agreement is unavailable, authentication is not
implemented, and the redacted-image store is an in-memory prototype mechanism.

The defensible contribution is an auditable prototype and evaluation method
for connecting screenshot risk signals to explainable, fail-safe protection
actions, together with an explicit account of where the evidence is
insufficient.

**Keywords:** desktop privacy, screenshot analysis, OCR, sensitive-entity
detection, Random Forest, adaptive thresholds, image redaction, fail-safe
processing, privacy evaluation.

## Evidence conventions

- **Implemented:** present in source code.
- **Measured:** numerical result stored in a repository artifact or produced
  by a reproducible command.
- **Engineering validation:** a passing unit, integration, or synthetic test.
- **Interpretation:** a reasoned explanation, not a direct measurement.
- **Unverified:** requires human review, a larger experiment, or an approved
  university document.

## Chapter 1 — Introduction

### 1.1 Background

Screenshots are convenient records of desktop activity, but they are also
compound privacy objects. A single image may combine an email address, phone
number, browser URL, file path, network address, application context, and
surrounding conversational or health-related information. The privacy problem
is contextual: the sensitivity of a value depends on where it appears, who
may receive it, and what action is appropriate. This framing is consistent
with contextual integrity, which treats privacy as appropriate information
flow rather than simple secrecy of every value [@nissenbaum2004privacy].

CAPRLM addresses a narrower and testable version of this problem: given a
desktop screenshot and its OCR output, can a prototype detect selected
sensitive entities, estimate a coarse risk level, adapt protection selection
to feedback, and reduce exposure in API and image outputs?

The Member 2 implementation is text-centred. EasyOCR supplies text and
bounding boxes. Downstream components do not use a trained visual encoder or
a trained named-entity recognizer. OCR failures, unreadable text, image-only
content, and contextual sensitivity outside the detector categories therefore
remain possible missed-risk sources.

### 1.2 Problem statement

A working pipeline is not sufficient evidence of a privacy system. A
defensible research prototype must measure its classifier against baselines,
make protection failures visible, and avoid claiming more privacy than its
tests establish. CAPRLM's dataset is small and imbalanced, and the annotation
review identifies 16 unresolved anomalies. The central problem is:

> How can a screenshot privacy prototype provide explainable and fail-safe
> protection decisions while honestly measuring the limits of its detection,
> classification, and redaction evidence?

### 1.3 Aim

The aim is to design, implement, and evaluate an auditable prototype that
connects OCR-derived sensitive-entity signals to adaptive privacy-risk
decisions and protective output handling.

### 1.4 Objectives

The following provisional objectives are derived from the repository's stated
project objectives and implementation:

1. Build a reproducible OCR ingestion path for desktop screenshots.
2. Detect selected sensitive entity categories using explainable rules.
3. Extract features and compare a Random Forest with rule-based and
   majority-class baselines.
4. Select protection actions according to risk and confidence states.
5. Apply per-request tokenization and pixel-level redaction where supported.
6. Adapt thresholds from bounded feedback without claiming per-user model
   retraining.
7. Test failure paths and privacy regression properties explicitly.

No approved university objectives document was found in the repository. These
objectives require supervisor confirmation before submission.

### 1.5 Provisional research questions

No approved research-question document was found. The following questions are
derived from the implementation and must be confirmed:

1. What sensitive-entity and contextual signals can be extracted reproducibly
   from desktop screenshots using an OCR-centred pipeline?
2. How does the recorded Random Forest result compare with explainable rule
   and majority-class baselines on the available annotations?
3. Can risk and confidence states drive explainable protection actions,
   including fail-safe handling of unavailable models and incomplete mapping?
4. What can synthetic and regression tests establish about API privacy,
   tokenization, and pixel-redaction behaviour, and what remains unmeasured?

### 1.6 Scope and boundaries

In scope are Windows-oriented prototype execution, EasyOCR text extraction,
six original entity categories, additional Stage 2 credential-like patterns,
feature extraction, Random Forest inference, rule comparison, adaptive
threshold recalibration, API handling, tokenization, and PIL-based redaction.

Not established are a trained visual model, comprehensive credential
detection, persistent cross-request restoration, authentication,
re-identification experiments, retrieval-utility evaluation,
inter-annotator agreement, statistical significance testing, and production
deployment security.

### 1.7 Contributions

The supported contribution is an auditable prototype architecture with:

- separation of OCR, detection, classification, selection, tokenization, and
  redaction;
- a same-fold comparison among majority, rule, and Random Forest classifiers;
- explainable risk-to-action mapping with an unavailable/unknown fail-safe;
- fail-closed handling of redaction exceptions and incomplete mapping;
- regression tests that distinguish engineering validation from effectiveness;
- an evidence-led account of model and annotation limitations.

Novelty relative to the wider literature has not been established and should
not be claimed without a systematic comparison approved by the supervisor.

### 1.8 Thesis organisation

Chapter 2 reviews the concepts and technologies. Chapter 3 describes the
method and evidence boundaries. Chapter 4 presents system analysis and
design. Chapter 5 describes implementation. Chapter 6 reports measured
evaluation and regression validation. Chapter 7 discusses implications and
threats to validity. Chapter 8 concludes and identifies future work.

## Chapter 2 — Literature Review

### 2.1 Contextual privacy and PII

Nissenbaum's contextual-integrity framework argues that privacy expectations
depend on appropriate information flows within social contexts
[@nissenbaum2004privacy]. This is relevant to screenshots because a value
that is harmless in one interface may become sensitive when copied into an
API response or stored in a log. CAPRLM does not implement a formal
contextual-integrity policy engine; it uses application context as one
feature and risk-adaptive actions as an engineering approximation.

NIST SP 800-122 provides guidance for identifying and protecting personally
identifiable information and assessing confidentiality impact
[@nist800122]. CAPRLM follows the limited engineering principle of reducing
exposure in responses and persistent logs, but it is not a NIST compliance
implementation. FIPS 199 similarly provides a useful vocabulary for
confidentiality impact, not a direct validation method for this prototype
[@fips199].

### 2.2 OCR and screenshot understanding

OCR converts visual characters into machine-readable text, enabling
downstream rules and classifiers. Smith's description of the Tesseract OCR
engine illustrates the separation between recognition and later application
processing [@smith2007tesseract]. CAPRLM uses EasyOCR rather than Tesseract.
It retains bounding boxes because region-level redaction requires spatial
coordinates.

OCR is not complete visual understanding. It may miss text in images,
stylised text, browser chrome, small labels, or low-quality captures. The
project annotation guidelines record these boundaries. Detection measurements
must therefore be understood as conditional on OCR output, not as complete
screenshot privacy measurements.

### 2.3 Sensitive-entity detection

Named-entity recognition commonly uses statistical or neural sequence models.
Lample et al. present a neural NER architecture based on recurrent and
conditional-structured prediction ideas [@lample2016ner]. Dernoncourt et al.
evaluate automated de-identification of protected identifiers in clinical
notes using recurrent neural networks [@dernoncourt2017deidentification].
These works motivate the distinction between trained contextual recognition
and CAPRLM's deterministic rules.

CAPRLM uses regex patterns for email, phone, URL, IP address, date, and
Windows path, then adds conservative credential-like and obfuscated-email
patterns. The contextual service adjusts heuristic scores around matches. It
is not a trained NER model, and the scores are not calibrated probabilities.
The choice is explainable and data-efficient but has limited coverage and
contextual understanding.

### 2.4 Risk classification and baselines

A Random Forest can represent nonlinear interactions among features, but its
usefulness depends on label quality, class coverage, and feature separability.
CAPRLM compares it with deterministic rules and a majority predictor on the
same held-out split. This comparison is preferable to a superiority claim:
the recorded results are identical.

The model is a prototype classifier, not a validated probability estimator.
The dataset contains two HIGH rows and no CRITICAL rows, while the test fold
contains no HIGH examples. The experiment cannot support a claim about
HIGH-class generalisation.

### 2.5 Redaction and de-identification

Redaction may replace, mask, blur, or block image regions. ISO/IEC 20889
provides terminology for de-identification and classes of techniques
[@iso20889]. CAPRLM uses PIL Gaussian blur for mapped sensitive regions,
all-text blur for a stronger action, and opaque black blocking for the
strongest action. The implementation records regions and mapping statistics.

Pixel transformation alone does not establish that text is unrecoverable.
Blur strength, font size, resolution, neighbouring pixels, and reconstruction
methods affect residual disclosure. Current tests validate selected code
paths, not adversarial reconstruction resistance.

Senior et al. discuss visual privacy protection in video
[@senior2005video]. This is an engineering analogy rather than direct
evidence for desktop document screenshots.

### 2.6 Adaptive protection

CAPRLM adapts decision thresholds using bounded feedback history. This is
different from continual model learning: the Random Forest is not retrained
per user. A poisoning guard and rollback mechanism are implemented, but no
longitudinal user study establishes that adaptation improves privacy outcomes.

### 2.7 Research gap and position

The defensible gap is an engineering-evaluation gap, not a claim that no
comparable system exists. CAPRLM combines OCR-derived signals, coarse risk
classification, adaptive action selection, API minimisation, and fail-safe
redaction in one inspectable pipeline. Its research value depends on
transparent evaluation and explicit boundaries: better labels, larger
datasets, visual validation, and deployment security remain necessary.

## Chapter 3 — Research Methodology

### 3.1 Research design

This work uses design-and-evaluation research. The design phase implements a
modular prototype. The evaluation phase combines artifact inspection,
deterministic model evaluation, unit and integration tests, synthetic
detection/redaction tests, and a latency benchmark. These evidence types are
not pooled into one effectiveness score: a passing unit test establishes a
software contract, not recall on unseen screenshots.

### 3.2 Staged development

Development proceeded incrementally. A privacy audit identified fail-open and
data-leakage risks. Stage 1 introduced explicit fail-safe processing. Stage 2
expanded synthetic detection coverage. Stage 3 validated redaction statistics
and selected pixel behaviours. Tests were retained as regression evidence,
without changing the protected model or annotation CSV.

### 3.3 Dataset

The protected CSV contains 50 manually annotated rows: LOW=38, MEDIUM=10,
HIGH=2, and CRITICAL=0. Entity fields are binary flags and application
context is categorical. Sixteen rows are flagged for human review, and no
inter-annotator agreement is recorded. The dataset is therefore the current
experimental artifact, not an established gold standard.

### 3.4 Feature engineering

The classifier uses six entity presence/count features,
`sensitive_entity_present`, OCR text length, and encoded application context.
Runtime counts are binarised to match the annotation feature space. The
context encoder is fitted on training data and applied to the test data;
unseen values map to an unknown code.

Separate layout features include text density, text-box count, sensitive-box
count, sensitive-area ratio, cluster score, and quadrant counts. The source
states that these features are returned for audit and future use and are not
fed into the recorded Random Forest metrics.

### 3.5 Model and baselines

The model is a Random Forest with deterministic random state 42. The recorded
split is 80/20 stratified: 40 training rows and 10 test rows. Cross-validation
is capped at two folds because of the smallest training-class count. The
comparison includes a majority-class predictor, a rule baseline, and the
Random Forest on the same test fold.

### 3.6 Redaction validation

Redaction tests use synthetic PIL images and synthetic OCR detections. They
cover exact and fragmented mapping, missing/malformed boxes, incomplete
mapping statistics, `MONITOR`, `BLUR_SENSITIVE`, `BLUR_AND_ALERT`, `BLOCK`,
source-image immutability, and a nearby benign region. These are
implementation tests, not a benchmark of visual readability or real-world
OCR.

### 3.7 Security testing

Regression tests cover raw OCR and token-map response leakage, OCR/detection/
redaction failure paths, unavailable and unknown risk states, incomplete
mapping, temporary-file cleanup, in-memory redacted-image retrieval, and
feedback/calibration paths. No authentication test can establish production
access control because the prototype has no authentication layer.

### 3.8 Ethics and privacy

Raw screenshots and OCR outputs can contain personal and health information.
They were not copied into this thesis workspace. Synthetic values are used in
regression tests. The prototype avoids intentionally logging raw OCR text and
does not return the token map. Human annotation review must follow
institutional ethics and supervisor requirements.

## Chapter 4 — System Analysis and Design

### 4.1 Functional requirements

| ID | Requirement | Status |
|---|---|---|
| F1 | Ingest screenshots and extract OCR text | Implemented; 1,000-image run recorded |
| F2 | Detect selected sensitive entities | Implemented; regex-based |
| F3 | Build classifier features | Implemented |
| F4 | Predict risk and compare a rule baseline | Implemented and evaluated |
| F5 | Select an explainable protection action | Implemented |
| F6 | Tokenize selected API values per request | Implemented with limitations |
| F7 | Redact image regions or block the image | Implemented |
| F8 | Record feedback and recalibrate thresholds | Implemented; synthetic tests |
| F9 | Fail safely on critical processing failures | Implemented and tested |

### 4.2 Non-functional requirements

The design prioritises explainability, reproducibility, privacy minimisation,
failure visibility, and preservation of the protected dataset/model. It does
not claim authentication, multi-tenant isolation, high availability, or
production-grade key management.

### 4.3 Architecture

The maintainable system architecture diagram (Figure 1) is in
`figures/architecture.mmd`. Screenshots pass
through OCR, detection, feature extraction, risk prediction, protection
selection, tokenization, and redaction. Feedback is persisted in SQLite and
affects thresholds, not the trained model.

### 4.4 Protection states

The ordinary action mapping is LOW→MONITOR, MEDIUM→BLUR_SENSITIVE,
HIGH→BLUR_AND_ALERT, and CRITICAL→BLOCK. Missing, unavailable, or unknown
risk falls back to BLOCK. If a non-monitor action cannot map all detected
entities, the API attempts a BLOCK fallback and reports
`protection_status=incomplete_entity_mapping`. If redaction or fallback
fails, the API returns HTTP 503 rather than a successful protected-image
response.

### 4.5 Storage and data flow

SQLite stores profiles, feedback, analysis metadata, and training-run
metadata. The analysis schema intentionally excludes raw OCR text. Redacted
images are held in an in-memory keyed store. Temporary input files are
removed in a `finally` path. These controls reduce retention but do not
replace authentication or deployment hardening.

## Chapter 5 — Implementation

### 5.1 OCR pipeline

`ocr_service.py` wraps EasyOCR and exposes text-only and text-with-boxes
interfaces. The batch script uses a manifest to resume processing and records
per-image status. The completed processing artifact records 1,000 images done
and zero failed, with a deduplicated candidate set. OCR is the principal
runtime bottleneck in the recorded environment.

### 5.2 Sensitive-entity detection

The original detector includes EMAIL, PHONE, URL, IP_ADDRESS, DATE, and
WINDOWS_PATH. IP octets are validated and URL punctuation is normalised.
Stage 2 added conservative credential-like and obfuscated-email patterns:
password assignments, API-key-like values, bearer-token-like strings,
JWT-shaped values, selected cloud-token formats, and spaced or
`[at]`/`[dot]` email forms. These patterns do not constitute complete secret
detection.

### 5.3 Contextual scoring

The contextual service starts from a heuristic base and adjusts match scores
using nearby labels and negative example context. Because no trained
sequence model or calibration experiment exists, “confidence” means a
heuristic score, not a validated probability.

### 5.4 Features and risk prediction

Feature extraction validates the detector schema and returns counts, total
entity count, OCR length, and application context. The Random Forest service
loads the existing model bundle. The rule baseline uses direct identifiers,
URL/path, browser-context, and no-match rules. Classification results are
compared without changing the protected model.

### 5.5 Protection selection and tokenization

Protection selection returns action, description, reversibility, severity,
received risk, confidence, and affected categories. Tokenization replaces
values with category-scoped placeholders when risk is MEDIUM or higher, when
LOW confidence is below threshold, or when model state is unavailable. The
map is per-request and discarded.

### 5.6 Pixel redaction

The redaction service copies the source image and supports `MONITOR`,
`BLUR_SENSITIVE`, `BLUR_AND_ALERT`, and `BLOCK`. Entity mapping supports
single-box and bounded fragmented matches. The service returns redacted
regions and counts for detected, mapped, and unmapped entities. Stage 3 tests
check that incomplete mapping is not labelled as complete redaction. Most
tests inspect statistics and selected pixels rather than OCR readability after
blur; this is a remaining evidence limitation.

### 5.7 API and temporary files

`/analyze` orchestrates the pipeline and returns summaries rather than raw
token maps. The redacted image is exposed through a random in-memory key.
Processing exceptions return explicit 503 responses. The API has no
authentication layer; a user-controlled identifier is not authorization.

## Chapter 6 — Experimental Evaluation

### 6.1 Dataset statistics

Table 1 summarises the class distribution used by the recorded model
evaluation.

| Label | Count | Percentage |
|---|---:|---:|
| LOW | 38 | 76% |
| MEDIUM | 10 | 20% |
| HIGH | 2 | 4% |
| CRITICAL | 0 | 0% |
| **Total** | **50** | **100%** |

Sixteen rows have unresolved annotation anomalies. No inter-annotator
agreement is available.

### 6.2 Classification results

Table 2 reports the recorded same-fold comparison.

| Classifier | Accuracy | Recorded macro F1 | Test rows |
|---|---:|---:|---:|
| Majority baseline | 0.80 | 0.4444 | 10 |
| Rule baseline | 0.80 | 0.4444 | 10 |
| Random Forest | 0.80 | 0.4444 | 10 |

The test fold contains eight LOW and two MEDIUM examples but no HIGH examples.
The recorded classification report gives LOW F1=0.889 and MEDIUM F1=0.000;
HIGH has zero support. The result shows no measured Random Forest advantage on
this split. It does not prove general equivalence because the sample is too
small and labels remain under review.

### 6.3 Detection and tokenization validation

Table 3 summarises the synthetic and regression validation scopes.

The privacy evaluation artifact records 26/26 synthetic entity-level tests
passing across the six original categories and risk paths. Stage 2 added
synthetic credential, obfuscation, duplicate, overlap, and OCR-fragment
tests. These establish tested implementation contracts, not population
precision, recall, or F1.

### 6.4 Redaction validation

Stage 3 added tests for multi-entity mapping, missing/malformed boxes,
unmappable entities, partial status, exception propagation, block output, and
benign neighbouring pixels. The final full backend run recorded 404 passing
tests and one existing Starlette/httpx deprecation warning.

The tests establish that malformed mapping is counted as incomplete, partial
mapping is not labelled `"redacted"`, and the BLOCK path removes the tested
synthetic colour marker. They do not establish that blurred text is
unreadable, that OCR cannot recover a blurred value, or that all sensitive
pixels change in every redaction path.

### 6.5 Latency

In a 50-run synthetic benchmark excluding OCR, recorded means were: entity
detection 0.032 ms, feature extraction 0.003 ms, Random Forest prediction
5.485 ms, rule prediction 0.002 ms, protection selection 0.003 ms,
tokenization 0.010 ms, contextual scoring 0.056 ms, layout features 0.021
ms, pixel redaction 0.568 ms, and full non-OCR pipeline 6.566 ms. EasyOCR
was measured separately at approximately 30–90 seconds per image on CPU.
These values are environment-specific.

### 6.6 Failure paths

Regression tests cover OCR failure, detection failure, redaction failure,
unavailable model, unknown risk, and incomplete mapping. The intended
outcome is explicit failure or opaque blocking, never a successful response
that quietly returns an unprotected original image.

## Chapter 7 — Discussion

### 7.1 Classifier interpretation

The equal recorded results are consistent with the limitations of the feature
space and labels. Several MEDIUM and HIGH annotations have zero entity flags,
making them difficult to separate from LOW rows using these features. Since
the test fold contains no HIGH rows, the experiment does not test the class
most relevant to strong protection decisions.

The correct conclusion is that no advantage was observed on the recorded
split, not that Random Forest fails universally or that the baselines are
universally equivalent.

### 7.2 Privacy and security implications

The prototype reduces some obvious exposure paths by avoiding raw OCR
persistence, suppressing token maps, using bounded in-memory image storage,
and converting critical failures into errors or blocks. These are engineering
controls, not security guarantees. Without authentication, a deployment
boundary, secure configuration, and threat testing against an actual service,
cross-user authorization remains unresolved.

### 7.3 Adaptive protection trade-offs

Threshold adjustment is simpler and more auditable than retraining from very
small per-user data. It can reflect bounded feedback but does not establish
better detector correctness. The poisoning guard and rollback reduce selected
operational risks; no user study measures whether adaptation is useful.

### 7.4 Threats to validity

- **Construct validity:** regex matches proxy sensitive information; heuristic
  confidence is not calibrated probability.
- **Internal validity:** the split is deterministic, but annotation anomalies
  and single-annotator labels threaten validity.
- **External validity:** 50 rows and one collection context cannot represent
  desktop screenshots generally.
- **Statistical conclusion validity:** no intervals or significance tests are
  reported; the test fold has only 10 rows.
- **Privacy validity:** synthetic tests do not model OCR errors, adversarial
  reconstruction, or multi-tenant deployment.
- **Reproducibility validity:** latency depends on hardware and raw screenshots
  are intentionally not redistributed.

## Chapter 8 — Conclusion and Future Work

### 8.1 Conclusion

CAPRLM Member 2 implements a modular screenshot privacy prototype linking
OCR-derived detection to risk classification, adaptive action selection,
tokenization, and pixel redaction. Source and regression evidence support the
existence of these components and several fail-safe behaviours.

The classification evidence is limited. On the recorded 10-row test fold,
Random Forest, rule, and majority baselines all have accuracy 0.80 and macro
F1 0.4444, with no HIGH test examples. The project cannot claim classifier
superiority or reliable generalisation. The strongest supported result is a
transparent prototype whose evaluation exposes the limitations of its dataset
and representation.

The privacy validation supports software contracts, not complete privacy
protection. The system depends on OCR, detects selected patterns, lacks
authentication, and has incomplete visual-effectiveness evidence.

### 8.2 Objective assessment

| Objective | Assessment |
|---|---|
| OCR ingestion | Implemented and exercised in a recorded 1,000-image run |
| Explainable entity detection | Implemented; synthetic validation passed; real-world recall unknown |
| Risk classification | Implemented; recorded result is weak and baseline-equal |
| Adaptive protection selection | Implemented through mapping and threshold adjustment |
| Tokenization | Implemented per request; persistent restoration absent |
| Pixel redaction | Implemented and regression-tested; visual effectiveness not established |
| Security/failure validation | Implemented for selected paths; authentication absent |

### 8.3 Future work

1. Obtain supervisor approval for annotation policy and complete human review
   of all 16 flagged rows.
2. Expand the dataset with balanced, independently reviewed HIGH and CRITICAL
   examples.
3. Report inter-annotator agreement and confidence intervals where justified.
4. Evaluate OCR recall separately from detector precision/recall.
5. Add rendered synthetic text and controlled OCR residual tests for each
   redaction action, followed by readability assessment.
6. Test credential and obfuscation patterns against a labelled, non-secret
   corpus with explicit false-positive accounting.
7. Integrate layout features only after annotation correction and retraining
   decisions are approved.
8. Define authentication, authorization, retention, key management, and
   deployment boundaries before any production claim.
9. Conduct a systematic literature comparison before claiming novelty.

## References

The draft bibliography is maintained in `references.bib`. The current
verified set includes OCR, NER/de-identification, contextual integrity, NIST
privacy guidance, de-identification terminology, and official software
documentation. The university's citation style and final access-date policy
must be confirmed.

## Appendices

- `appendices/reproducibility.md` — commands and pre-submission checklist.
- `evidence_inventory.md` — artifact-level evidence and safety classification.
- `claims_register.md` — major claims and allowed wording.
- `figures/architecture.mmd` — maintainable architecture diagram.
- `tables/dataset_and_evaluation.md` — reusable dataset and test tables.
