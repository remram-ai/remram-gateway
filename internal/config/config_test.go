package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadParsesGatewayRepoAndCLIPaths(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	configPath := filepath.Join(root, "config.yaml")
	if err := os.WriteFile(configPath, []byte(
		"paths:\n"+
			"  state_root: /srv/moltbox-state\n"+
			"  runtime_root: /srv/moltbox-state/runtime\n"+
			"  logs_root: /srv/moltbox-logs\n"+
			"  secrets_root: /var/lib/moltbox/secrets\n"+
			"repos:\n"+
			"  gateway:\n"+
			"    url: /srv/moltbox-state/upstream/moltbox-gateway\n"+
			"  services:\n"+
			"    url: /srv/moltbox-state/upstream/moltbox-services\n"+
			"  runtime:\n"+
			"    url: /srv/moltbox-state/upstream/moltbox-runtime\n"+
			"  skills:\n"+
			"    url: /srv/moltbox-state/upstream/remram-skills\n"+
			"gateway:\n"+
			"  host: 0.0.0.0\n"+
			"  port: 7460\n"+
			"cli:\n"+
			"  path: /home/jpekovitch/.local/bin/moltbox\n"+
			"  config_path: /home/jpekovitch/.config/moltbox/config.yaml\n",
	), 0o644); err != nil {
		t.Fatalf("write config: %v", err)
	}

	cfg, err := Load(configPath)
	if err != nil {
		t.Fatalf("Load() error = %v", err)
	}

	if got := cfg.GatewayRepoRoot(); got != "/srv/moltbox-state/upstream/moltbox-gateway" {
		t.Fatalf("GatewayRepoRoot() = %q", got)
	}
	if got := cfg.CLI.Path; got != "/home/jpekovitch/.local/bin/moltbox" {
		t.Fatalf("CLI.Path = %q", got)
	}
	if got := cfg.CLI.ConfigPath; got != "/home/jpekovitch/.config/moltbox/config.yaml" {
		t.Fatalf("CLI.ConfigPath = %q", got)
	}
}

func TestConfigPathPrefersUserConfig(t *testing.T) {
	home := t.TempDir()
	userConfigPath := filepath.Join(home, ".config", "moltbox", "config.yaml")
	if err := os.MkdirAll(filepath.Dir(userConfigPath), 0o755); err != nil {
		t.Fatalf("mkdir user config: %v", err)
	}
	if err := os.WriteFile(userConfigPath, []byte("gateway:\n  port: 7460\n"), 0o644); err != nil {
		t.Fatalf("write user config: %v", err)
	}

	t.Setenv("HOME", home)
	t.Setenv("MOLTBOX_CONFIG_PATH", "")

	if got := ConfigPath(); got != userConfigPath {
		t.Fatalf("ConfigPath() = %q, want %q", got, userConfigPath)
	}
}
