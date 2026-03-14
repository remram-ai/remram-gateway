package tokens

import (
	"testing"

	"github.com/remram-ai/moltbox-gateway/pkg/cli"
)

func TestManagerLifecycleAndValidation(t *testing.T) {
	t.Parallel()

	manager := NewManager(t.TempDir())
	route := &cli.Route{Resource: "gateway", Kind: cli.KindGatewayToken, Subject: "mcp_http_token"}

	listBefore, err := manager.List(route)
	if err != nil {
		t.Fatalf("List() before create error = %v", err)
	}
	if len(listBefore.Tokens) != 0 {
		t.Fatalf("tokens before create = %v, want empty", listBefore.Tokens)
	}

	created, err := manager.Create(route)
	if err != nil {
		t.Fatalf("Create() error = %v", err)
	}
	if created.Token == "" {
		t.Fatal("Create() returned empty token")
	}

	valid, err := manager.ValidateBearerToken("Bearer " + created.Token)
	if err != nil {
		t.Fatalf("ValidateBearerToken() error = %v", err)
	}
	if !valid {
		t.Fatal("ValidateBearerToken() = false, want true")
	}

	listAfter, err := manager.List(route)
	if err != nil {
		t.Fatalf("List() after create error = %v", err)
	}
	if len(listAfter.Tokens) != 1 || listAfter.Tokens[0].Name != secretName {
		t.Fatalf("tokens after create = %v, want singleton %q", listAfter.Tokens, secretName)
	}

	rotated, err := manager.Rotate(route)
	if err != nil {
		t.Fatalf("Rotate() error = %v", err)
	}
	if rotated.Token == "" || rotated.Token == created.Token {
		t.Fatalf("Rotate() token = %q, want non-empty value different from create token %q", rotated.Token, created.Token)
	}

	valid, err = manager.ValidateBearerToken("Bearer " + rotated.Token)
	if err != nil {
		t.Fatalf("ValidateBearerToken(rotated) error = %v", err)
	}
	if !valid {
		t.Fatal("ValidateBearerToken(rotated) = false, want true")
	}

	deleted, err := manager.Delete(route)
	if err != nil {
		t.Fatalf("Delete() error = %v", err)
	}
	if !deleted.Deleted {
		t.Fatal("Delete() = false, want true")
	}

	valid, err = manager.ValidateBearerToken("Bearer " + rotated.Token)
	if err != nil {
		t.Fatalf("ValidateBearerToken(after delete) error = %v", err)
	}
	if valid {
		t.Fatal("ValidateBearerToken(after delete) = true, want false")
	}
}
