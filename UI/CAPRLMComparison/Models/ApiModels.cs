using System.Text.Json.Serialization;

namespace CAPRLMComparison.Models;

// Wire shapes of research/api/service.py. Property names are the API's snake_case.

public sealed record ContextDto(
    [property: JsonPropertyName("app")] string App,
    [property: JsonPropertyName("domain")] string? Domain,
    [property: JsonPropertyName("title")] string? Title,
    [property: JsonPropertyName("source")] string Source);

public sealed record AuditEntry(
    [property: JsonPropertyName("category")] string Category,
    [property: JsonPropertyName("risk_tier")] string RiskTier,
    [property: JsonPropertyName("action")] string Action);

public sealed record FilterResultDto(
    [property: JsonPropertyName("text")] string Text,
    [property: JsonPropertyName("blocked")] bool Blocked,
    [property: JsonPropertyName("redactions")] int Redactions,
    [property: JsonPropertyName("filter_version")] string FilterVersion,
    [property: JsonPropertyName("audit")] List<AuditEntry> Audit,
    [property: JsonPropertyName("error")] string? Error);

public sealed record CompareResponse(
    [property: JsonPropertyName("results")] Dictionary<string, FilterResultDto> Results,
    [property: JsonPropertyName("ocr_text")] string? OcrText);

public sealed record MetricSet(
    [property: JsonPropertyName("precision")] double Precision,
    [property: JsonPropertyName("recall")] double Recall,
    [property: JsonPropertyName("f1")] double F1,
    [property: JsonPropertyName("leakage_rate")] double LeakageRate,
    [property: JsonPropertyName("reidentification_rate")] double ReidentificationRate,
    [property: JsonPropertyName("semantic_similarity")] double SemanticSimilarity,
    [property: JsonPropertyName("blocked_rate")] double BlockedRate,
    [property: JsonPropertyName("errors")] int Errors);

public sealed record DatasetDetail(
    [property: JsonPropertyName("label_protocol")] string? LabelProtocol,
    [property: JsonPropertyName("n_train_scenarios")] int NTrain,
    [property: JsonPropertyName("n_test_scenarios")] int NTest,
    [property: JsonPropertyName("n_test_sensitive_spans")] int NTestSensitiveSpans);

public sealed record ModelSelection(
    [property: JsonPropertyName("scores")] Dictionary<string, double> Scores,
    [property: JsonPropertyName("rule")] string? Rule,
    [property: JsonPropertyName("chosen")] string? Chosen);

public sealed record ModelInfo(
    [property: JsonPropertyName("version")] string Version,
    [property: JsonPropertyName("selection")] ModelSelection? Selection);

public sealed record DetectionInfo(
    [property: JsonPropertyName("ner_model")] string? NerModel);

public sealed record EvalResults(
    [property: JsonPropertyName("dataset")] string Dataset,
    [property: JsonPropertyName("dataset_detail")] DatasetDetail? DatasetDetail,
    [property: JsonPropertyName("generated_at")] string? GeneratedAt,
    [property: JsonPropertyName("detection")] DetectionInfo? Detection,
    [property: JsonPropertyName("model")] ModelInfo? Model,
    [property: JsonPropertyName("similarity_method")] string? SimilarityMethod,
    [property: JsonPropertyName("metrics")] Dictionary<string, MetricSet> Metrics,
    [property: JsonPropertyName("notes")] List<string>? Notes);

// ---- Privacy review / policy (research/review.py, profile_store.py) ----

public sealed record EntityDto(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("category")] string Category,
    [property: JsonPropertyName("group")] string? Group,
    [property: JsonPropertyName("risk")] double Risk,
    [property: JsonPropertyName("tier")] string Tier,
    [property: JsonPropertyName("masked")] bool Masked,
    [property: JsonPropertyName("action")] string Action,
    [property: JsonPropertyName("decided_by")] string DecidedBy,
    [property: JsonPropertyName("reason")] string Reason,
    [property: JsonPropertyName("token")] string? Token,
    [property: JsonPropertyName("value")] string? Value);

public sealed record SegmentDto(
    [property: JsonPropertyName("text")] string Text,
    [property: JsonPropertyName("kind")] string Kind);

public sealed record OutboundDto(
    [property: JsonPropertyName("mask_before_send")] bool MaskBeforeSend,
    [property: JsonPropertyName("provider")] string Provider,
    [property: JsonPropertyName("model")] string Model,
    [property: JsonPropertyName("connected")] bool Connected,
    [property: JsonPropertyName("text")] string Text,
    [property: JsonPropertyName("masked")] int Masked,
    [property: JsonPropertyName("total")] int Total);

public sealed record ReviewResult(
    [property: JsonPropertyName("level")] string Level,
    [property: JsonPropertyName("tau")] double Tau,
    [property: JsonPropertyName("taus")] Dictionary<string, double> Taus,
    [property: JsonPropertyName("entities")] List<EntityDto> Entities,
    [property: JsonPropertyName("redacted_text")] string RedactedText,
    [property: JsonPropertyName("segments")] List<SegmentDto> Segments,
    [property: JsonPropertyName("residual_risk")] int ResidualRisk,
    [property: JsonPropertyName("flagged")] int Flagged,
    [property: JsonPropertyName("masked")] int Masked,
    [property: JsonPropertyName("total")] int Total,
    [property: JsonPropertyName("utility_kept")] double UtilityKept,
    [property: JsonPropertyName("outbound")] OutboundDto Outbound,
    [property: JsonPropertyName("filter_version")] string FilterVersion);

public sealed record LearnedRule(
    [property: JsonPropertyName("category")] string Category,
    [property: JsonPropertyName("decision")] string Decision,
    [property: JsonPropertyName("evidence")] int Evidence);

public sealed record ProfileDto(
    [property: JsonPropertyName("corrections")] int Corrections,
    [property: JsonPropertyName("rules")] List<LearnedRule> Rules,
    [property: JsonPropertyName("evidence_needed")] int EvidenceNeeded);

public sealed record GroupPolicyDto(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("label")] string Label,
    [property: JsonPropertyName("categories")] List<string> Categories,
    [property: JsonPropertyName("action")] string Action,
    [property: JsonPropertyName("enabled")] bool Enabled);

public sealed record LlmPolicyDto(
    [property: JsonPropertyName("mask_before_send")] bool MaskBeforeSend,
    [property: JsonPropertyName("provider")] string Provider,
    [property: JsonPropertyName("model")] string Model);

public sealed record PolicyDto(
    [property: JsonPropertyName("level")] string Level,
    [property: JsonPropertyName("taus")] Dictionary<string, double> Taus,
    [property: JsonPropertyName("groups")] List<GroupPolicyDto> Groups,
    [property: JsonPropertyName("llm")] LlmPolicyDto Llm);

public sealed record DayStat(
    [property: JsonPropertyName("date")] string Date,
    [property: JsonPropertyName("weekday")] string Weekday,
    [property: JsonPropertyName("flagged")] int Flagged,
    [property: JsonPropertyName("redacted")] int Redacted);

public sealed record StatsDto(
    [property: JsonPropertyName("flagged_7d")] int Flagged7d,
    [property: JsonPropertyName("redacted_7d")] int Redacted7d,
    [property: JsonPropertyName("utility_kept")] double? UtilityKept,
    [property: JsonPropertyName("reviews_7d")] int Reviews7d,
    [property: JsonPropertyName("learned_rules")] int LearnedRules,
    [property: JsonPropertyName("learnable_categories")] int LearnableCategories,
    [property: JsonPropertyName("weekly")] List<DayStat> Weekly);
