package sshwrap

import (
	"reflect"
	"testing"
)

func TestResolveAutomationPreservesQuotedArgs(t *testing.T) {
	t.Parallel()

	args, deny, err := Resolve(ModeAutomation, `moltbox dev openclaw agent --message "Say hello in one sentence." --json`)
	if err != nil {
		t.Fatalf("Resolve() error = %v", err)
	}
	if deny != "" {
		t.Fatalf("Resolve() deny = %q, want empty", deny)
	}

	want := []string{"dev", "openclaw", "agent", "--message", "Say hello in one sentence.", "--json"}
	if !reflect.DeepEqual(args, want) {
		t.Fatalf("Resolve() args = %#v, want %#v", args, want)
	}
}

func TestResolveRejectsShellOperators(t *testing.T) {
	t.Parallel()

	_, _, err := Resolve(ModeAutomation, `moltbox dev openclaw health --json; whoami`)
	if err == nil {
		t.Fatal("Resolve() error = nil, want unsupported shell operator")
	}
}

func TestResolveBootstrapPolicy(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name     string
		raw      string
		wantDeny string
	}{
		{
			name: "dev allowed",
			raw:  `moltbox dev openclaw health --json`,
		},
		{
			name:     "test reload denied",
			raw:      `moltbox test reload`,
			wantDeny: "reload is not permitted for diagnostic-only environments",
		},
		{
			name: "test health allowed",
			raw:  `moltbox test openclaw health --json`,
		},
	}

	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()

			_, deny, err := Resolve(ModeBootstrap, test.raw)
			if err != nil {
				t.Fatalf("Resolve() error = %v", err)
			}
			if deny != test.wantDeny {
				t.Fatalf("Resolve() deny = %q, want %q", deny, test.wantDeny)
			}
		})
	}
}
