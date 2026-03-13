package secrets

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"time"
)

var (
	ErrSecretNotFound = errors.New("secret not found")

	secretNamePattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]*$`)
	scopePattern      = regexp.MustCompile(`^(dev|test|prod|service)$`)
)

type Store struct {
	root    string
	keyPath string
}

type record struct {
	Nonce      string `json:"nonce"`
	Ciphertext string `json:"ciphertext"`
	UpdatedAt  string `json:"updated_at"`
}

func NewStore(root string) *Store {
	return &Store{
		root:    root,
		keyPath: filepath.Join(root, "master.key"),
	}
}

func (s *Store) Set(scope, name, value string) error {
	scope, err := normalizeScope(scope)
	if err != nil {
		return err
	}
	name, err = normalizeName(name)
	if err != nil {
		return err
	}
	if err := s.ensureLayout(scope); err != nil {
		return err
	}

	key, err := s.key()
	if err != nil {
		return err
	}

	block, err := aes.NewCipher(key)
	if err != nil {
		return fmt.Errorf("create cipher: %w", err)
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return fmt.Errorf("create gcm: %w", err)
	}

	nonce := make([]byte, gcm.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return fmt.Errorf("generate nonce: %w", err)
	}

	associatedData := []byte(scope + "/" + name)
	ciphertext := gcm.Seal(nil, nonce, []byte(value), associatedData)
	payload, err := json.MarshalIndent(record{
		Nonce:      base64.StdEncoding.EncodeToString(nonce),
		Ciphertext: base64.StdEncoding.EncodeToString(ciphertext),
		UpdatedAt:  time.Now().UTC().Format(time.RFC3339),
	}, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal secret record: %w", err)
	}
	payload = append(payload, '\n')

	if err := os.WriteFile(s.secretPath(scope, name), payload, 0o600); err != nil {
		return fmt.Errorf("write secret %s/%s: %w", scope, name, err)
	}
	return nil
}

func (s *Store) Get(scope, name string) (string, error) {
	scope, err := normalizeScope(scope)
	if err != nil {
		return "", err
	}
	name, err = normalizeName(name)
	if err != nil {
		return "", err
	}
	if err := s.ensureLayout(scope); err != nil {
		return "", err
	}

	data, err := os.ReadFile(s.secretPath(scope, name))
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return "", ErrSecretNotFound
		}
		return "", fmt.Errorf("read secret %s/%s: %w", scope, name, err)
	}

	var payload record
	if err := json.Unmarshal(data, &payload); err != nil {
		return "", fmt.Errorf("decode secret %s/%s: %w", scope, name, err)
	}

	nonce, err := base64.StdEncoding.DecodeString(payload.Nonce)
	if err != nil {
		return "", fmt.Errorf("decode nonce for %s/%s: %w", scope, name, err)
	}
	ciphertext, err := base64.StdEncoding.DecodeString(payload.Ciphertext)
	if err != nil {
		return "", fmt.Errorf("decode ciphertext for %s/%s: %w", scope, name, err)
	}

	key, err := s.key()
	if err != nil {
		return "", err
	}

	block, err := aes.NewCipher(key)
	if err != nil {
		return "", fmt.Errorf("create cipher: %w", err)
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return "", fmt.Errorf("create gcm: %w", err)
	}

	plaintext, err := gcm.Open(nil, nonce, ciphertext, []byte(scope+"/"+name))
	if err != nil {
		return "", fmt.Errorf("decrypt secret %s/%s: %w", scope, name, err)
	}
	return string(plaintext), nil
}

func (s *Store) Delete(scope, name string) (bool, error) {
	scope, err := normalizeScope(scope)
	if err != nil {
		return false, err
	}
	name, err = normalizeName(name)
	if err != nil {
		return false, err
	}
	if err := s.ensureLayout(scope); err != nil {
		return false, err
	}

	if err := os.Remove(s.secretPath(scope, name)); err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return false, nil
		}
		return false, fmt.Errorf("delete secret %s/%s: %w", scope, name, err)
	}
	return true, nil
}

func (s *Store) List(scope string) ([]string, error) {
	scope, err := normalizeScope(scope)
	if err != nil {
		return nil, err
	}
	if err := s.ensureLayout(scope); err != nil {
		return nil, err
	}

	entries, err := os.ReadDir(filepath.Join(s.root, scope))
	if err != nil {
		return nil, fmt.Errorf("read secrets dir for %s: %w", scope, err)
	}

	names := make([]string, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		names = append(names, entry.Name()[:len(entry.Name())-len(".json")])
	}
	sort.Strings(names)
	return names, nil
}

func (s *Store) Resolve(scope string, names []string) (map[string]string, error) {
	scope, err := normalizeScope(scope)
	if err != nil {
		return nil, err
	}
	resolved := make(map[string]string, len(names))
	for _, name := range names {
		value, err := s.Get(scope, name)
		if err != nil {
			if errors.Is(err, ErrSecretNotFound) {
				continue
			}
			return nil, err
		}
		resolved[name] = value
	}
	return resolved, nil
}

func (s *Store) ensureLayout(scope string) error {
	scopeDir := filepath.Join(s.root, scope)
	if err := os.MkdirAll(scopeDir, 0o700); err != nil {
		return fmt.Errorf("create secrets dir for %s: %w", scope, err)
	}
	_ = os.Chmod(scopeDir, 0o700)
	if _, err := os.Stat(s.keyPath); err == nil {
		_ = os.Chmod(s.keyPath, 0o600)
		return nil
	} else if !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("stat secret key: %w", err)
	}
	if hasSecrets, err := s.hasEncryptedRecords(); err != nil {
		return err
	} else if hasSecrets {
		return fmt.Errorf("secret key missing at %s for existing encrypted records", s.keyPath)
	}

	key := make([]byte, 32)
	if _, err := rand.Read(key); err != nil {
		return fmt.Errorf("generate secret key: %w", err)
	}
	if err := os.WriteFile(s.keyPath, key, 0o600); err != nil {
		return fmt.Errorf("write secret key: %w", err)
	}
	return nil
}

func (s *Store) key() ([]byte, error) {
	key, err := os.ReadFile(s.keyPath)
	if err != nil {
		return nil, fmt.Errorf("read secret key: %w", err)
	}
	if len(key) != 32 {
		return nil, fmt.Errorf("invalid secret key length %d", len(key))
	}
	return key, nil
}

func (s *Store) secretPath(scope, name string) string {
	return filepath.Join(s.root, scope, name+".json")
}

func (s *Store) hasEncryptedRecords() (bool, error) {
	for _, scope := range []string{"dev", "test", "prod", "service"} {
		entries, err := os.ReadDir(filepath.Join(s.root, scope))
		if err != nil {
			if errors.Is(err, os.ErrNotExist) {
				continue
			}
			return false, fmt.Errorf("read secrets dir for %s: %w", scope, err)
		}
		for _, entry := range entries {
			if !entry.IsDir() && filepath.Ext(entry.Name()) == ".json" {
				return true, nil
			}
		}
	}
	return false, nil
}

func normalizeScope(scope string) (string, error) {
	if !scopePattern.MatchString(scope) {
		return "", fmt.Errorf("invalid secret scope %q", scope)
	}
	return scope, nil
}

func normalizeName(name string) (string, error) {
	if !secretNamePattern.MatchString(name) {
		return "", fmt.Errorf("invalid secret name %q", name)
	}
	return name, nil
}
