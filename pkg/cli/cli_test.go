package cli

import "testing"

func TestParseRuntimeOpenClawAgentNormalizesMessageFlag(t *testing.T) {
	t.Parallel()

	result := Parse([]string{
		"dev",
		"openclaw",
		"agent",
		"--agent",
		"main",
		"--local",
		"--thinking",
		"off",
		"--message",
		"Say",
		"hello",
		"in",
		"one",
		"sentence.",
		"--json",
	})
	if result.Route == nil {
		t.Fatal("Parse() route = nil")
	}

	want := []string{"agent", "--agent", "main", "--local", "--thinking", "off", "--message", "Say hello in one sentence.", "--json"}
	if !equalArgs(result.Route.NativeArgs, want) {
		t.Fatalf("Parse() native_args = %#v, want %#v", result.Route.NativeArgs, want)
	}
}

func TestParseScopedSecretsSetJoinsInlineValue(t *testing.T) {
	t.Parallel()

	result := Parse([]string{"dev", "secrets", "set", "TEST_SECRET", "value", "with", "spaces"})
	if result.Route == nil {
		t.Fatal("Parse() route = nil")
	}
	if len(result.Route.NativeArgs) != 1 || result.Route.NativeArgs[0] != "value with spaces" {
		t.Fatalf("Parse() native_args = %#v, want [\"value with spaces\"]", result.Route.NativeArgs)
	}
}

func equalArgs(got, want []string) bool {
	if len(got) != len(want) {
		return false
	}
	for i := range got {
		if got[i] != want[i] {
			return false
		}
	}
	return true
}
