package orchestrator

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/remram-ai/moltbox-gateway/internal/command"
	appconfig "github.com/remram-ai/moltbox-gateway/internal/config"
	"github.com/remram-ai/moltbox-gateway/internal/deploystate"
	"github.com/remram-ai/moltbox-gateway/internal/docker"
	"github.com/remram-ai/moltbox-gateway/pkg/cli"
)

type simulatedRuntimeRunner struct {
	t            *testing.T
	runtimeRoots map[string]string
	commands     [][]string
}

func (r *simulatedRuntimeRunner) Run(_ context.Context, dir string, name string, args ...string) (command.Result, error) {
	r.t.Helper()
	invocation := append([]string{name}, args...)
	r.commands = append(r.commands, invocation)
	if name != "docker" {
		return command.Result{ExitCode: 0}, nil
	}
	if len(args) == 0 {
		return command.Result{ExitCode: 0}, nil
	}

	switch args[0] {
	case "network":
		return command.Result{ExitCode: 0}, nil
	case "compose":
		return command.Result{ExitCode: 0}, nil
	case "build":
		return command.Result{ExitCode: 0}, nil
	case "exec":
		return r.handleExec(args)
	case "cp":
		return r.handleCopy(args)
	default:
		return command.Result{ExitCode: 0}, nil
	}
}

func (r *simulatedRuntimeRunner) handleExec(args []string) (command.Result, error) {
	if len(args) < 5 {
		return command.Result{ExitCode: 1, Stdout: "invalid exec"}, nil
	}
	service := args[1]
	runtimeRoot := r.runtimeRoots[service]
	if runtimeRoot == "" {
		return command.Result{ExitCode: 1, Stdout: "unknown runtime"}, nil
	}

	commandText := args[len(args)-1]
	destination := shellPathValue(commandText, "rm -rf ")
	if destination == "" {
		return command.Result{ExitCode: 0}, nil
	}
	hostPath := runtimeHostPath(runtimeRoot, destination)
	if err := os.RemoveAll(hostPath); err != nil {
		return command.Result{}, err
	}
	if err := os.MkdirAll(hostPath, 0o755); err != nil {
		return command.Result{}, err
	}
	return command.Result{ExitCode: 0}, nil
}

func (r *simulatedRuntimeRunner) handleCopy(args []string) (command.Result, error) {
	if len(args) != 3 {
		return command.Result{ExitCode: 1, Stdout: "invalid cp"}, nil
	}
	source := args[1]
	destination := args[2]

	switch {
	case isContainerSpec(source):
		service, containerPath := splitContainerPath(source)
		runtimeRoot := r.runtimeRoots[service]
		if runtimeRoot == "" {
			return command.Result{ExitCode: 1, Stdout: "unknown runtime"}, nil
		}
		hostSource := runtimeHostPath(runtimeRoot, containerPath)
		if err := copyTree(hostSource, destination); err != nil {
			return command.Result{}, err
		}
		return command.Result{ExitCode: 0}, nil
	case isContainerSpec(destination):
		service, containerPath := splitContainerPath(destination)
		runtimeRoot := r.runtimeRoots[service]
		if runtimeRoot == "" {
			return command.Result{ExitCode: 1, Stdout: "unknown runtime"}, nil
		}
		hostDestination := runtimeHostPath(runtimeRoot, containerPath)
		if err := os.MkdirAll(hostDestination, 0o755); err != nil {
			return command.Result{}, err
		}
		if err := copyTree(strings.TrimSuffix(source, string(filepath.Separator)+"."), hostDestination); err != nil {
			return command.Result{}, err
		}
		return command.Result{ExitCode: 0}, nil
	default:
		return command.Result{ExitCode: 0}, nil
	}
}

func TestRuntimeSkillDeployRecordsReplayStateAndReplaysOnRedeploy(t *testing.T) {
	t.Parallel()

	manager, runner, store, runtimeRoot, skillsRoot := newRuntimeTestManager(t)

	reloadRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimeAction, Action: "reload", Environment: "dev", Runtime: "openclaw-dev"}
	if _, err := manager.DeployService(context.Background(), reloadRoute, "dev"); err != nil {
		t.Fatalf("initial DeployService() error = %v", err)
	}

	log, err := store.LoadReplayLog("openclaw-dev")
	if err != nil {
		t.Fatalf("LoadReplayLog() error = %v", err)
	}
	if len(log.Events) != 0 {
		t.Fatalf("initial replay log = %#v, want empty", log.Events)
	}

	deployRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimeSkill, Action: "deploy", Environment: "dev", Runtime: "openclaw-dev", Subject: "together"}
	result, err := manager.RuntimeSkillDeploy(context.Background(), deployRoute)
	if err != nil {
		t.Fatalf("RuntimeSkillDeploy() error = %v", err)
	}
	if !result.OK || result.CanonicalSkill != "together-escalation" {
		t.Fatalf("deploy result = %#v, want successful together-escalation deploy", result)
	}

	log, err = store.LoadReplayLog("openclaw-dev")
	if err != nil {
		t.Fatalf("LoadReplayLog() error = %v", err)
	}
	if len(log.Events) != 1 || log.Events[0].Skill != "together-escalation" {
		t.Fatalf("replay log = %#v, want one together-escalation event", log.Events)
	}
	if _, err := os.Stat(filepath.Join(runtimeRoot, "skills", "together-escalation", "SKILL.md")); err != nil {
		t.Fatalf("expected together skill in runtime state: %v", err)
	}
	if _, err := os.Stat(filepath.Join(runtimeRoot, "skills", "semantic-router", "SKILL.md")); !os.IsNotExist(err) {
		t.Fatalf("semantic-router should not be staged, stat err = %v", err)
	}

	if err := os.RemoveAll(skillsRoot); err != nil {
		t.Fatalf("remove skills repo: %v", err)
	}
	if _, err := manager.DeployService(context.Background(), reloadRoute, "dev"); err != nil {
		t.Fatalf("second DeployService() error = %v", err)
	}
	if _, err := os.Stat(filepath.Join(runtimeRoot, "skills", "together-escalation", "SKILL.md")); err != nil {
		t.Fatalf("expected together skill after replay-only redeploy: %v", err)
	}

	foundReplayCopy := false
	for _, command := range runner.commands {
		text := strings.Join(command, " ")
		if strings.Contains(text, filepath.Join("deploy", "runtime", "openclaw-dev", "packages")) {
			foundReplayCopy = true
		}
		if strings.Contains(text, "skills-repo") && strings.Contains(text, "docker cp") {
			t.Fatalf("replay should use gateway state, got command %q", text)
		}
	}
	if !foundReplayCopy {
		t.Fatal("expected replay to copy skill package from gateway state")
	}

	history, err := store.ReadDeploymentHistory()
	if err != nil {
		t.Fatalf("ReadDeploymentHistory() error = %v", err)
	}
	if len(history) < 3 {
		t.Fatalf("deployment history len = %d, want at least 3", len(history))
	}
}

func TestRuntimeSkillRollbackRemovesReplayAndRestoresBaseline(t *testing.T) {
	t.Parallel()

	manager, _, store, runtimeRoot, _ := newRuntimeTestManager(t)

	reloadRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimeAction, Action: "reload", Environment: "dev", Runtime: "openclaw-dev"}
	if _, err := manager.DeployService(context.Background(), reloadRoute, "dev"); err != nil {
		t.Fatalf("initial DeployService() error = %v", err)
	}

	deployRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimeSkill, Action: "deploy", Environment: "dev", Runtime: "openclaw-dev", Subject: "together"}
	if _, err := manager.RuntimeSkillDeploy(context.Background(), deployRoute); err != nil {
		t.Fatalf("RuntimeSkillDeploy() error = %v", err)
	}

	rollbackRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimeSkill, Action: "rollback", Environment: "dev", Runtime: "openclaw-dev", Subject: "together"}
	result, err := manager.RuntimeSkillRollback(context.Background(), rollbackRoute)
	if err != nil {
		t.Fatalf("RuntimeSkillRollback() error = %v", err)
	}
	if !result.OK || result.CanonicalSkill != "together-escalation" {
		t.Fatalf("rollback result = %#v, want successful together-escalation rollback", result)
	}

	log, err := store.LoadReplayLog("openclaw-dev")
	if err != nil {
		t.Fatalf("LoadReplayLog() error = %v", err)
	}
	if len(log.Events) != 0 {
		t.Fatalf("replay log = %#v, want empty after rollback", log.Events)
	}
	if _, err := os.Stat(filepath.Join(runtimeRoot, "skills", "together-escalation", "SKILL.md")); !os.IsNotExist(err) {
		t.Fatalf("expected together skill to be absent after rollback, stat err = %v", err)
	}

	history, err := store.ReadDeploymentHistory()
	if err != nil {
		t.Fatalf("ReadDeploymentHistory() error = %v", err)
	}
	if len(history) < 4 {
		t.Fatalf("deployment history len = %d, want at least 4", len(history))
	}
}

func TestRuntimeCheckpointPromotesBaselineAndClearsReplay(t *testing.T) {
	t.Parallel()

	manager, runner, store, runtimeRoot, skillsRoot := newRuntimeTestManager(t)
	reloadRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimeAction, Action: "reload", Environment: "dev", Runtime: "openclaw-dev"}
	if _, err := manager.DeployService(context.Background(), reloadRoute, "dev"); err != nil {
		t.Fatalf("DeployService() error = %v", err)
	}
	deployRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimeSkill, Action: "deploy", Environment: "dev", Runtime: "openclaw-dev", Subject: "together"}
	if _, err := manager.RuntimeSkillDeploy(context.Background(), deployRoute); err != nil {
		t.Fatalf("RuntimeSkillDeploy() error = %v", err)
	}

	checkpointRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimeAction, Action: "checkpoint", Environment: "dev", Runtime: "openclaw-dev"}
	result, err := manager.RuntimeCheckpoint(context.Background(), checkpointRoute)
	if err != nil {
		t.Fatalf("RuntimeCheckpoint() error = %v", err)
	}
	if !result.OK || !result.ReplayCleared {
		t.Fatalf("checkpoint result = %#v, want successful checkpoint", result)
	}

	checkpoint, ok, err := store.LoadCheckpoint("openclaw-dev")
	if err != nil {
		t.Fatalf("LoadCheckpoint() error = %v", err)
	}
	if !ok {
		t.Fatal("expected checkpoint metadata to exist")
	}
	if checkpoint.Image == "" || checkpoint.SnapshotDir == "" {
		t.Fatalf("checkpoint metadata = %#v, want image and snapshot", checkpoint)
	}

	log, err := store.LoadReplayLog("openclaw-dev")
	if err != nil {
		t.Fatalf("LoadReplayLog() error = %v", err)
	}
	if len(log.Events) != 0 {
		t.Fatalf("replay log should be empty after checkpoint, got %#v", log.Events)
	}

	if err := os.RemoveAll(skillsRoot); err != nil {
		t.Fatalf("remove skills repo: %v", err)
	}
	runner.commands = nil
	if _, err := manager.DeployService(context.Background(), reloadRoute, "dev"); err != nil {
		t.Fatalf("post-checkpoint DeployService() error = %v", err)
	}
	if _, err := os.Stat(filepath.Join(runtimeRoot, "skills", "together-escalation", "SKILL.md")); err != nil {
		t.Fatalf("expected together skill from checkpoint baseline: %v", err)
	}

	for _, command := range runner.commands {
		text := strings.Join(command, " ")
		if strings.Contains(text, "/home/node/.openclaw/skills/") {
			t.Fatalf("post-checkpoint redeploy should not replay installs, got %q", text)
		}
	}

	definition, err := manager.LoadServiceDefinition("openclaw-dev")
	if err != nil {
		t.Fatalf("LoadServiceDefinition() error = %v", err)
	}
	outputDir, _, err := manager.RenderServiceAssets("openclaw-dev", definition)
	if err != nil {
		t.Fatalf("RenderServiceAssets() error = %v", err)
	}
	envData, err := os.ReadFile(filepath.Join(outputDir, ".env"))
	if err != nil {
		t.Fatalf("read .env: %v", err)
	}
	if !strings.Contains(string(envData), "OPENCLAW_IMAGE=") || !strings.Contains(string(envData), checkpoint.Image) {
		t.Fatalf("rendered .env = %s, want checkpoint image %s", envData, checkpoint.Image)
	}
}

func newRuntimeTestManager(t *testing.T) (*Manager, *simulatedRuntimeRunner, *deploystate.Store, string, string) {
	t.Helper()

	root := t.TempDir()
	servicesRoot := filepath.Join(root, "services-repo")
	runtimeRepoRoot := filepath.Join(root, "runtime-repo")
	runtimeStateRoot := filepath.Join(root, "runtime-state")
	skillsRoot := filepath.Join(root, "skills-repo")
	stateRoot := filepath.Join(root, "state")

	mustWriteFile(t, filepath.Join(servicesRoot, "services", "openclaw-dev", "service.yaml"), "compose_project: openclaw-dev\ncontainer_names:\n  - openclaw-dev\nruntime_required: true\nskip_pull: true\n")
	mustWriteFile(t, filepath.Join(servicesRoot, "services", "openclaw-dev", "compose.yml.template"), "services:\n  {{ service_name }}:\n    image: \"${OPENCLAW_IMAGE:-ghcr.io/openclaw/openclaw:latest}\"\n    container_name: \"{{ container_name }}\"\n")
	mustWriteFile(t, filepath.Join(runtimeRepoRoot, "openclaw-dev", "openclaw.json.template"), "{}\n")
	mustWriteFile(t, filepath.Join(runtimeRepoRoot, "openclaw-dev", "model-runtime.yml"), "model: local\n")
	mustWriteFile(t, filepath.Join(skillsRoot, "skills", "together-escalation", "SKILL.md"), "---\nname: together-escalation\ndescription: test\n---\n")
	mustWriteFile(t, filepath.Join(skillsRoot, "skills", "semantic-router", "SKILL.md"), "---\nname: semantic-router\ndescription: test\n---\n")
	mustWriteFile(t, filepath.Join(skillsRoot, "skills", "semantic-router", "openclaw.plugin.json"), "{\n  \"id\": \"semantic-router\"\n}\n")

	runner := &simulatedRuntimeRunner{
		t: t,
		runtimeRoots: map[string]string{
			"openclaw-dev": filepath.Join(runtimeStateRoot, "openclaw-dev"),
		},
	}

	containerInfo := docker.ContainerInfo{}
	containerInfo.Name = "/openclaw-dev"
	containerInfo.Config.Image = defaultRuntimeImage
	containerInfo.State.Status = "running"
	containerInfo.State.Running = true
	containerInfo.State.Health = &struct {
		Status string `json:"Status"`
	}{Status: "healthy"}

	manager := NewManager(appconfig.Config{
		Paths: appconfig.PathsConfig{
			StateRoot:   stateRoot,
			RuntimeRoot: runtimeStateRoot,
			LogsRoot:    filepath.Join(root, "logs"),
			SecretsRoot: filepath.Join(root, "secrets"),
		},
		Repos: appconfig.ReposConfig{
			Services: appconfig.RepoConfig{URL: servicesRoot},
			Runtime:  appconfig.RepoConfig{URL: runtimeRepoRoot},
			Skills:   appconfig.RepoConfig{URL: skillsRoot},
		},
		Gateway: appconfig.GatewayConfig{Host: "0.0.0.0", Port: 7460},
	}, fakeInspector{
		containers: map[string]docker.ContainerInfo{
			"openclaw-dev": containerInfo,
		},
	}, runner, nil)

	return manager, runner, deploystate.New(stateRoot), filepath.Join(runtimeStateRoot, "openclaw-dev"), skillsRoot
}

func splitContainerPath(value string) (string, string) {
	parts := strings.SplitN(value, ":", 2)
	return parts[0], filepath.ToSlash(strings.TrimSpace(parts[1]))
}

func isContainerSpec(value string) bool {
	if len(value) < 3 {
		return false
	}
	if len(value) >= 2 && value[1] == ':' {
		return false
	}
	index := strings.Index(value, ":")
	if index <= 0 {
		return false
	}
	return strings.HasPrefix(value[index+1:], "/")
}

func runtimeHostPath(runtimeRoot, containerPath string) string {
	trimmed := strings.TrimPrefix(filepath.ToSlash(containerPath), "/home/node/.openclaw")
	trimmed = strings.TrimPrefix(trimmed, "/")
	if trimmed == "." || trimmed == "" {
		return runtimeRoot
	}
	return filepath.Join(runtimeRoot, filepath.FromSlash(trimmed))
}

func shellPathValue(command, prefix string) string {
	index := strings.Index(command, prefix)
	if index < 0 {
		return ""
	}
	remainder := command[index+len(prefix):]
	remainder = strings.TrimSpace(remainder)
	if !strings.HasPrefix(remainder, "'") {
		return ""
	}
	remainder = strings.TrimPrefix(remainder, "'")
	end := strings.Index(remainder, "'")
	if end < 0 {
		return ""
	}
	return remainder[:end]
}
