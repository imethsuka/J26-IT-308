using CsharpCollector.Sensing;

namespace CsharpCollector.Tests;

public class ContextGateTests
{
    [Fact]
    public void Blocked_until_first_signal_is_allowed()
    {
        var gate = new ContextGate();
        Assert.False(gate.IsContextAllowed);
        Assert.Null(gate.TryGetAllowedToken());

        gate.MarkAllowed(gate.NextSignal());

        Assert.True(gate.IsContextAllowed);
    }

    [Fact]
    public void New_signal_blocks_until_it_is_evaluated()
    {
        var gate = new ContextGate();
        gate.MarkAllowed(gate.NextSignal());

        var pending = gate.NextSignal();
        Assert.False(gate.IsContextAllowed);

        gate.MarkAllowed(pending);
        Assert.True(gate.IsContextAllowed);
    }

    [Fact]
    public void Token_is_invalidated_by_any_later_signal_even_if_allowed()
    {
        var gate = new ContextGate();
        gate.MarkAllowed(gate.NextSignal());
        var token = gate.TryGetAllowedToken()!.Value;

        gate.MarkAllowed(gate.NextSignal());

        Assert.False(gate.IsStillAllowed(token));
    }
}
