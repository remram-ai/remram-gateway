package sshwrap

import (
	"fmt"
	"strings"
)

const (
	ModeAutomation = "automation"
	ModeBootstrap  = "bootstrap"
)

// Resolve validates and tokenizes SSH_ORIGINAL_COMMAND without invoking a shell.
// It returns the argv after the leading "moltbox" token.
func Resolve(mode, raw string) ([]string, string, error) {
	tokens, err := split(raw)
	if err != nil {
		return nil, "", err
	}
	if len(tokens) == 0 {
		return nil, "expected a moltbox command", nil
	}
	if tokens[0] != "moltbox" {
		return nil, "only moltbox commands are allowed", nil
	}

	args := append([]string(nil), tokens[1:]...)
	if len(args) == 0 {
		return nil, "missing moltbox arguments", nil
	}

	switch mode {
	case ModeAutomation:
		return args, "", nil
	case ModeBootstrap:
		return applyBootstrapPolicy(args)
	default:
		return nil, "", fmt.Errorf("unsupported ssh wrapper mode %q", mode)
	}
}

func DenyPrefix(mode string) string {
	switch mode {
	case ModeBootstrap:
		return "bootstrap"
	default:
		return "automation"
	}
}

func applyBootstrapPolicy(args []string) ([]string, string, error) {
	switch args[0] {
	case "dev":
		return args, "", nil
	case "gateway":
		if len(args) == 2 && (args[1] == "status" || args[1] == "logs") {
			return args, "", nil
		}
		if len(args) == 4 && args[1] == "service" && args[2] == "status" {
			return args, "", nil
		}
		return nil, "gateway access is limited to status, logs, and service status", nil
	case "test", "prod":
		if len(args) == 2 && args[1] == "reload" {
			return nil, "reload is not permitted for diagnostic-only environments", nil
		}
		if len(args) >= 3 && args[1] == "openclaw" {
			switch args[2] {
			case "status", "inspect", "logs", "health":
				return args, "", nil
			}
		}
		if len(args) == 3 && args[1] == "secrets" && args[2] == "list" {
			return nil, "secret access is not permitted for diagnostic-only environments", nil
		}
		return nil, "test/prod access is limited to openclaw status, inspect, logs, and health", nil
	default:
		return nil, "unsupported command", nil
	}
}

func split(raw string) ([]string, error) {
	var (
		args         []string
		current      strings.Builder
		tokenStarted bool
		inSingle     bool
		inDouble     bool
		escaped      bool
	)

	flush := func() {
		if !tokenStarted {
			return
		}
		args = append(args, current.String())
		current.Reset()
		tokenStarted = false
	}

	for _, r := range raw {
		switch {
		case escaped:
			current.WriteRune(r)
			tokenStarted = true
			escaped = false
		case inSingle:
			if r == '\'' {
				inSingle = false
				continue
			}
			current.WriteRune(r)
			tokenStarted = true
		case inDouble:
			switch r {
			case '"':
				inDouble = false
			case '\\':
				escaped = true
			default:
				current.WriteRune(r)
				tokenStarted = true
			}
		default:
			switch r {
			case ' ', '\t':
				flush()
			case '\'':
				inSingle = true
				tokenStarted = true
			case '"':
				inDouble = true
				tokenStarted = true
			case '\\':
				escaped = true
				tokenStarted = true
			case ';', '|', '&', '<', '>', '\n', '\r', '(', ')':
				return nil, fmt.Errorf("unsupported shell operator %q", string(r))
			default:
				current.WriteRune(r)
				tokenStarted = true
			}
		}
	}

	if escaped {
		return nil, fmt.Errorf("unterminated escape sequence")
	}
	if inSingle || inDouble {
		return nil, fmt.Errorf("unterminated quoted string")
	}

	flush()
	return args, nil
}
