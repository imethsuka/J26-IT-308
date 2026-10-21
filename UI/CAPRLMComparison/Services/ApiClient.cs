using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using CAPRLMComparison.Models;

namespace CAPRLMComparison.Services;

/// <summary>The service isn't running (or not reachable). The message names the command to start it.</summary>
public sealed class ServiceUnreachableException(string message) : Exception(message);

/// <summary>The service answered with an error (e.g. 503 "OCR unavailable — content blocked").</summary>
public sealed class ServiceErrorException(string message) : Exception(message);

/// <summary>
/// HttpClient + System.Text.Json against the local Python service. Fail-closed on this side too:
/// any failure throws, and callers show the error instead of anything resembling a result.
/// </summary>
public sealed class ApiClient : IDisposable
{
    public const string BaseUrl = "http://localhost:8787";
    public const string RunCommand = "cd CAPRLM; python -m uvicorn research.api.service:app --port 8787";

    private static readonly JsonSerializerOptions Json = new()
    {
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    // First request after startup can be slow (spaCy warm-up, OCR), hence the generous timeout.
    private readonly HttpClient _http = new() { BaseAddress = new Uri(BaseUrl), Timeout = TimeSpan.FromSeconds(120) };

    public Task<CompareResponse> CompareAsync(string text, ContextDto context) =>
        PostAsync<CompareResponse>("/compare", new { text, context });

    public Task<CompareResponse> CompareImageAsync(byte[] image, ContextDto context) =>
        PostAsync<CompareResponse>("/compare-image", new { image_base64 = Convert.ToBase64String(image), context });

    public Task<EvalResults> GetEvalResultsAsync() => SendAsync<EvalResults>(HttpMethod.Get, "/eval-results");

    public Task<ReviewResult> ReviewAsync(string text, ContextDto context, string? level,
                                          IReadOnlyDictionary<string, string> overrides, bool recordActivity) =>
        SendAsync<ReviewResult>(HttpMethod.Post, "/review",
            new { text, context, level, overrides, record_activity = recordActivity });

    public Task<ProfileDto> AddCorrectionAsync(string category, string decision) =>
        SendAsync<ProfileDto>(HttpMethod.Post, "/corrections", new { category, decision });

    public Task<ProfileDto> GetProfileAsync() => SendAsync<ProfileDto>(HttpMethod.Get, "/profile");

    public Task<ProfileDto> ResetProfileAsync() => SendAsync<ProfileDto>(HttpMethod.Delete, "/profile");

    public Task<PolicyDto> GetPolicyAsync() => SendAsync<PolicyDto>(HttpMethod.Get, "/policy");

    /// <summary>Partial update: only the fields present in <paramref name="update"/> change.</summary>
    public Task<PolicyDto> UpdatePolicyAsync(object update) => SendAsync<PolicyDto>(HttpMethod.Put, "/policy", update);

    public Task<StatsDto> GetStatsAsync() => SendAsync<StatsDto>(HttpMethod.Get, "/stats");

    private Task<T> PostAsync<T>(string path, object body) => SendAsync<T>(HttpMethod.Post, path, body);

    private async Task<T> SendAsync<T>(HttpMethod method, string path, object? body = null)
    {
        try
        {
            using var req = new HttpRequestMessage(method, path);
            if (body is not null) req.Content = JsonContent.Create(body, options: Json);
            using var resp = await _http.SendAsync(req);
            return await ReadAsync<T>(resp);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            throw Unreachable(ex);
        }
    }

    private static async Task<T> ReadAsync<T>(HttpResponseMessage resp)
    {
        if (resp.IsSuccessStatusCode)
        {
            var value = await resp.Content.ReadFromJsonAsync<T>(Json);
            return value ?? throw new ServiceErrorException("The service returned an empty response.");
        }
        throw new ServiceErrorException(await DescribeAsync(resp));
    }

    private static async Task<string> DescribeAsync(HttpResponseMessage resp)
    {
        var raw = await resp.Content.ReadAsStringAsync();
        try
        {
            using var doc = JsonDocument.Parse(raw);
            if (doc.RootElement.TryGetProperty("detail", out var detail))
                return detail.ValueKind == JsonValueKind.String ? detail.GetString()! : detail.ToString();
        }
        catch (JsonException) { }
        return $"The service returned HTTP {(int)resp.StatusCode}.";
    }

    private static ServiceUnreachableException Unreachable(Exception inner) =>
        new($"The CAPRLM service is not reachable at {BaseUrl} ({inner.GetType().Name}). " +
            $"Start it with:  {RunCommand}");

    public void Dispose() => _http.Dispose();
}
