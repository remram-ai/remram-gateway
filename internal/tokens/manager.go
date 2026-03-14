package tokens

import (
	"crypto/rand"
	"encoding/base64"
	"fmt"
	"strings"

	"github.com/remram-ai/moltbox-gateway/internal/secrets"
	"github.com/remram-ai/moltbox-gateway/pkg/cli"
)

const (
	secretScope = "service"
	secretName  = "MCP_HTTP_TOKEN"
)

type Manager struct {
	store *secrets.Store
}

func NewManager(root string) *Manager {
	return &Manager{store: secrets.NewStore(root)}
}

func (m *Manager) Create(route *cli.Route) (cli.GatewayTokenCreateResult, error) {
	token, err := generateToken()
	if err != nil {
		return cli.GatewayTokenCreateResult{}, err
	}
	if err := m.store.Set(secretScope, secretName, token); err != nil {
		return cli.GatewayTokenCreateResult{}, err
	}
	return cli.GatewayTokenCreateResult{
		OK:      true,
		Route:   route,
		Name:    secretName,
		Token:   token,
		Created: true,
	}, nil
}

func (m *Manager) Rotate(route *cli.Route) (cli.GatewayTokenRotateResult, error) {
	token, err := generateToken()
	if err != nil {
		return cli.GatewayTokenRotateResult{}, err
	}
	if err := m.store.Set(secretScope, secretName, token); err != nil {
		return cli.GatewayTokenRotateResult{}, err
	}
	return cli.GatewayTokenRotateResult{
		OK:      true,
		Route:   route,
		Name:    secretName,
		Token:   token,
		Rotated: true,
	}, nil
}

func (m *Manager) Delete(route *cli.Route) (cli.GatewayTokenDeleteResult, error) {
	deleted, err := m.store.Delete(secretScope, secretName)
	if err != nil {
		return cli.GatewayTokenDeleteResult{}, err
	}
	return cli.GatewayTokenDeleteResult{
		OK:      true,
		Route:   route,
		Name:    secretName,
		Deleted: deleted,
	}, nil
}

func (m *Manager) List(route *cli.Route) (cli.GatewayTokenListResult, error) {
	names, err := m.store.List(secretScope)
	if err != nil {
		return cli.GatewayTokenListResult{}, err
	}
	result := cli.GatewayTokenListResult{
		OK:     true,
		Route:  route,
		Tokens: make([]cli.GatewayTokenInfo, 0, 1),
	}
	for _, name := range names {
		if name != secretName {
			continue
		}
		result.Tokens = append(result.Tokens, cli.GatewayTokenInfo{Name: name})
	}
	return result, nil
}

func (m *Manager) ValidateBearerToken(header string) (bool, error) {
	token := strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(header), "Bearer "))
	if token == "" || !strings.HasPrefix(strings.TrimSpace(header), "Bearer ") {
		return false, nil
	}
	stored, err := m.store.Get(secretScope, secretName)
	if err != nil {
		if err == secrets.ErrSecretNotFound {
			return false, nil
		}
		return false, err
	}
	return stored == token, nil
}

func generateToken() (string, error) {
	buf := make([]byte, 32)
	if _, err := rand.Read(buf); err != nil {
		return "", fmt.Errorf("generate token: %w", err)
	}
	return base64.RawURLEncoding.EncodeToString(buf), nil
}
