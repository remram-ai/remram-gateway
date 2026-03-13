package command

import (
	"context"
	"fmt"
	"os/exec"
)

type Result struct {
	Stdout   string
	Stderr   string
	ExitCode int
}

type Runner interface {
	Run(ctx context.Context, dir string, name string, args ...string) (Result, error)
}

type ExecRunner struct{}

func NewExecRunner() ExecRunner {
	return ExecRunner{}
}

func (ExecRunner) Run(ctx context.Context, dir string, name string, args ...string) (Result, error) {
	command := exec.CommandContext(ctx, name, args...)
	command.Dir = dir
	output, err := command.CombinedOutput()
	result := Result{
		Stdout:   string(output),
		Stderr:   "",
		ExitCode: 0,
	}

	if err == nil {
		return result, nil
	}

	if exitError, ok := err.(*exec.ExitError); ok {
		result.ExitCode = exitError.ExitCode()
		return result, nil
	}

	return Result{}, fmt.Errorf("run %s: %w", name, err)
}
