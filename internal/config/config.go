package config

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

const DefaultConfigPath = "/etc/moltbox/config.yaml"

type Config struct {
	Paths   PathsConfig
	Repos   ReposConfig
	Gateway GatewayConfig
	CLI     CLIConfig
}

type PathsConfig struct {
	StateRoot   string
	RuntimeRoot string
	LogsRoot    string
	SecretsRoot string
}

type ReposConfig struct {
	Gateway  RepoConfig
	Services RepoConfig
	Runtime  RepoConfig
	Skills   RepoConfig
}

type RepoConfig struct {
	URL string
}

type GatewayConfig struct {
	Host string
	Port int
}

type CLIConfig struct {
	Path       string
	ConfigPath string
}

func Default() Config {
	return Config{
		Paths: PathsConfig{
			StateRoot:   "/srv/moltbox-state",
			RuntimeRoot: "/srv/moltbox-state/runtime",
			LogsRoot:    "/srv/moltbox-logs",
			SecretsRoot: "/var/lib/moltbox/secrets",
		},
		Repos: ReposConfig{
			Gateway: RepoConfig{URL: "/srv/moltbox-state/upstream/moltbox-gateway"},
		},
		Gateway: GatewayConfig{
			Host: "0.0.0.0",
			Port: 7460,
		},
		CLI: CLIConfig{
			Path:       "moltbox",
			ConfigPath: "",
		},
	}
}

func Load(path string) (Config, error) {
	cfg := Default()

	data, err := os.ReadFile(path)
	if err != nil {
		return Config{}, fmt.Errorf("read gateway config %s: %w", path, err)
	}

	var (
		section    string
		subsection string
		inList     string
	)

	scanner := bufio.NewScanner(strings.NewReader(string(data)))
	for scanner.Scan() {
		raw := scanner.Text()
		line := strings.TrimRight(raw, " \t")
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}

		indent := len(line) - len(strings.TrimLeft(line, " "))
		if strings.HasPrefix(trimmed, "- ") {
			if inList == "" {
				continue
			}
			continue
		}

		key, value, hasValue := splitYAMLKeyValue(trimmed)
		if !hasValue {
			switch indent {
			case 0:
				section = key
				subsection = ""
				inList = ""
			case 2:
				subsection = key
				inList = ""
			case 4:
				inList = key
			}
			continue
		}

		inList = ""
		switch {
		case indent == 2 && section == "paths":
			switch key {
			case "state_root":
				cfg.Paths.StateRoot = value
			case "runtime_root":
				cfg.Paths.RuntimeRoot = value
			case "logs_root":
				cfg.Paths.LogsRoot = value
			case "secrets_root":
				cfg.Paths.SecretsRoot = value
			}
		case indent == 2 && section == "gateway":
			switch key {
			case "host":
				cfg.Gateway.Host = value
			case "port":
				port, err := strconv.Atoi(value)
				if err != nil {
					return Config{}, fmt.Errorf("parse gateway.port: %w", err)
				}
				cfg.Gateway.Port = port
			}
		case indent == 2 && section == "cli":
			switch key {
			case "path":
				cfg.CLI.Path = value
			case "config_path":
				cfg.CLI.ConfigPath = value
			}
		case indent == 4 && section == "repos":
			switch subsection {
			case "gateway":
				if key == "url" {
					cfg.Repos.Gateway.URL = value
				}
			case "services":
				if key == "url" {
					cfg.Repos.Services.URL = value
				}
			case "runtime":
				if key == "url" {
					cfg.Repos.Runtime.URL = value
				}
			case "skills":
				if key == "url" {
					cfg.Repos.Skills.URL = value
				}
			}
		}
	}
	if err := scanner.Err(); err != nil {
		return Config{}, fmt.Errorf("scan gateway config: %w", err)
	}

	return cfg, nil
}

func ConfigPath() string {
	if value := strings.TrimSpace(os.Getenv("MOLTBOX_CONFIG_PATH")); value != "" {
		return value
	}
	if home, err := os.UserHomeDir(); err == nil && strings.TrimSpace(home) != "" {
		candidate := filepath.Join(home, ".config", "moltbox", "config.yaml")
		if _, err := os.Stat(candidate); err == nil {
			return candidate
		}
	}
	return DefaultConfigPath
}

func (c Config) ListenAddress() string {
	if c.Gateway.Port == 0 {
		c.Gateway.Port = 7460
	}
	host := strings.TrimSpace(c.Gateway.Host)
	if host == "" || host == "0.0.0.0" {
		return fmt.Sprintf(":%d", c.Gateway.Port)
	}
	return fmt.Sprintf("%s:%d", host, c.Gateway.Port)
}

func (c Config) ServicesRepoRoot() string {
	return strings.TrimSpace(c.Repos.Services.URL)
}

func (c Config) GatewayRepoRoot() string {
	return strings.TrimSpace(c.Repos.Gateway.URL)
}

func (c Config) RuntimeRepoRoot() string {
	return strings.TrimSpace(c.Repos.Runtime.URL)
}

func (c Config) SkillsRepoRoot() string {
	return strings.TrimSpace(c.Repos.Skills.URL)
}

func (c Config) ServiceStateDir(service string) string {
	return filepath.Join(c.Paths.StateRoot, "services", service)
}

func (c Config) RuntimeComponentDir(service string) string {
	return filepath.Join(c.Paths.RuntimeRoot, service)
}

func splitYAMLKeyValue(line string) (string, string, bool) {
	index := strings.Index(line, ":")
	if index < 0 {
		return "", "", false
	}
	key := strings.TrimSpace(line[:index])
	value := strings.TrimSpace(line[index+1:])
	if value == "" {
		return key, "", false
	}
	return key, strings.Trim(value, `"'`), true
}
