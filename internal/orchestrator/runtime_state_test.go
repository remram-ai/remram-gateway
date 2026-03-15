package orchestrator

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
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
	case "run":
		return r.handleRun(args)
	case "restart":
		return command.Result{ExitCode: 0}, nil
	case "exec":
		return r.handleExec(args)
	case "cp":
		return r.handleCopy(args)
	default:
		return command.Result{ExitCode: 0}, nil
	}
}

func (r *simulatedRuntimeRunner) handleRun(args []string) (command.Result, error) {
	if len(args) == 0 {
		return command.Result{ExitCode: 1, Stdout: "invalid run"}, nil
	}
	if !strings.Contains(strings.Join(args, " "), "npm pack --quiet") {
		return command.Result{ExitCode: 0}, nil
	}

	sourceDir := ""
	for index := 0; index < len(args)-1; index++ {
		if args[index] != "-v" {
			continue
		}
		mount := args[index+1]
		split := strings.LastIndex(mount, ":")
		if split <= 0 || mount[split+1:] != "/src" {
			continue
		}
		sourceDir = mount[:split]
		break
	}
	if sourceDir == "" {
		return command.Result{ExitCode: 1, Stdout: "missing pack source"}, nil
	}

	packageName, version := readRuntimePluginPackageInfo(sourceDir)
	if packageName == "" {
		packageName = filepath.Base(sourceDir)
	}
	if version == "" {
		version = "1.2.0"
	}
	artifactName := packageName + "-" + version + ".tgz"
	artifactPath := filepath.Join(sourceDir, artifactName)
	if err := os.WriteFile(artifactPath, []byte("packed plugin artifact\n"), 0o644); err != nil {
		return command.Result{}, err
	}
	return command.Result{ExitCode: 0, Stdout: artifactName + "\n"}, nil
}

func (r *simulatedRuntimeRunner) handleExec(args []string) (command.Result, error) {
	if len(args) < 5 {
		return command.Result{ExitCode: 1, Stdout: "invalid exec"}, nil
	}
	serviceIndex := 1
	if len(args) >= 4 && args[1] == "-u" {
		serviceIndex = 3
	}
	service := args[serviceIndex]
	runtimeRoot := r.runtimeRoots[service]
	if runtimeRoot == "" {
		return command.Result{ExitCode: 1, Stdout: "unknown runtime"}, nil
	}
	if serviceIndex+1 < len(args) && args[serviceIndex+1] == "openclaw" {
		return r.handleOpenClaw(runtimeRoot, args[serviceIndex+2:])
	}

	commandText := args[len(args)-1]
	for _, path := range shellQuotedArgsAfter(commandText, "rm -rf ") {
		hostPath := runtimeHostPath(runtimeRoot, path)
		if err := os.RemoveAll(hostPath); err != nil {
			return command.Result{}, err
		}
	}
	for _, path := range shellQuotedArgsAfter(commandText, "mkdir -p ") {
		hostPath := runtimeHostPath(runtimeRoot, path)
		if err := os.MkdirAll(hostPath, 0o755); err != nil {
			return command.Result{}, err
		}
	}
	moveArgs := shellQuotedArgsAfter(commandText, "mv ")
	if len(moveArgs) == 2 {
		source := runtimeHostPath(runtimeRoot, moveArgs[0])
		destination := runtimeHostPath(runtimeRoot, moveArgs[1])
		if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
			return command.Result{}, err
		}
		if err := os.Rename(source, destination); err != nil {
			return command.Result{}, err
		}
	}
	return command.Result{ExitCode: 0}, nil
}

func (r *simulatedRuntimeRunner) handleOpenClaw(runtimeRoot string, args []string) (command.Result, error) {
	if len(args) == 0 {
		return command.Result{ExitCode: 1, Stdout: "missing openclaw command"}, nil
	}
	switch args[0] {
	case "skills":
		if len(args) >= 2 && args[1] == "list" {
			skillsRoot := filepath.Join(runtimeRoot, "skills")
			entries, err := os.ReadDir(skillsRoot)
			if err != nil && !os.IsNotExist(err) {
				return command.Result{}, err
			}
			names := make([]string, 0, len(entries))
			for _, entry := range entries {
				if entry.IsDir() {
					names = append(names, entry.Name())
				}
			}
			sort.Strings(names)
			if len(names) == 0 {
				return command.Result{ExitCode: 0}, nil
			}
			return command.Result{ExitCode: 0, Stdout: strings.Join(names, "\n") + "\n"}, nil
		}
	case "plugins":
		return r.handlePlugins(runtimeRoot, args[1:])
	}
	return command.Result{ExitCode: 0}, nil
}

func (r *simulatedRuntimeRunner) handlePlugins(runtimeRoot string, args []string) (command.Result, error) {
	if len(args) == 0 {
		return command.Result{ExitCode: 1, Stdout: "missing plugins command"}, nil
	}
	switch args[0] {
	case "install":
		if len(args) < 2 {
			return command.Result{ExitCode: 1, Stdout: "missing plugin spec"}, nil
		}
		return r.installPlugin(runtimeRoot, args[1])
	case "info":
		if len(args) < 2 {
			return command.Result{ExitCode: 1, Stdout: "missing plugin name"}, nil
		}
		pluginRoot := filepath.Join(runtimeRoot, "extensions", canonicalRuntimePluginName(args[1]))
		if _, err := os.Stat(pluginRoot); err != nil {
			if os.IsNotExist(err) {
				return command.Result{ExitCode: 1, Stdout: "not installed"}, nil
			}
			return command.Result{}, err
		}
		return command.Result{ExitCode: 0, Stdout: args[1] + "\n"}, nil
	case "list":
		return r.listPlugins(runtimeRoot, args[1:])
	default:
		return command.Result{ExitCode: 1, Stdout: "unsupported plugins command"}, nil
	}
}

func (r *simulatedRuntimeRunner) installPlugin(runtimeRoot, spec string) (command.Result, error) {
	extensionsRoot := filepath.Join(runtimeRoot, "extensions")
	if err := os.MkdirAll(extensionsRoot, 0o755); err != nil {
		return command.Result{}, err
	}

	sourcePath := runtimePluginHostPath(runtimeRoot, spec)
	pluginID := canonicalRuntimePluginName(npmPackageNameFromSpec(spec))
	packageName := strings.TrimSpace(npmPackageNameFromSpec(spec))
	version := "1.2.0"
	if sourcePath != "" {
		if id := readRuntimePluginManifestID(sourcePath); id != "" {
			pluginID = canonicalRuntimePluginName(id)
		}
		if name, sourceVersion := readRuntimePluginPackageInfo(sourcePath); name != "" {
			packageName = name
			if sourceVersion != "" {
				version = sourceVersion
			}
		}
		if strings.EqualFold(filepath.Ext(sourcePath), ".tgz") {
			if packedName, packedVersion := packedPluginInfo(filepath.Base(sourcePath)); packedName != "" {
				packageName = packedName
				pluginID = canonicalRuntimePluginName(packedName)
				if packedVersion != "" {
					version = packedVersion
				}
			}
		}
	}
	if pluginID == "" {
		pluginID = canonicalRuntimePluginName(strings.TrimSuffix(filepath.Base(spec), filepath.Ext(spec)))
	}
	if packageName == "" {
		packageName = pluginID
	}
	if parsedVersion := runtimePackageVersionFromSpec(spec); parsedVersion != "" {
		version = parsedVersion
	}

	pluginRoot := filepath.Join(extensionsRoot, pluginID)
	if err := os.RemoveAll(pluginRoot); err != nil {
		return command.Result{}, err
	}
	if sourcePath != "" {
		info, err := os.Stat(sourcePath)
		if err != nil {
			return command.Result{}, err
		}
		if info.IsDir() {
			if err := copyTree(sourcePath, pluginRoot); err != nil {
				return command.Result{}, err
			}
		} else {
			if err := os.MkdirAll(pluginRoot, 0o755); err != nil {
				return command.Result{}, err
			}
			if err := copyTestPath(sourcePath, filepath.Join(pluginRoot, filepath.Base(sourcePath))); err != nil {
				return command.Result{}, err
			}
		}
	} else {
		if err := os.MkdirAll(pluginRoot, 0o755); err != nil {
			return command.Result{}, err
		}
	}

	manifestPath := filepath.Join(pluginRoot, "openclaw.plugin.json")
	if _, err := os.Stat(manifestPath); os.IsNotExist(err) {
		if err := os.WriteFile(manifestPath, []byte("{\n  \"id\": \""+pluginID+"\"\n}\n"), 0o644); err != nil {
			return command.Result{}, err
		}
	}
	packagePath := filepath.Join(pluginRoot, "package.json")
	if _, err := os.Stat(packagePath); os.IsNotExist(err) {
		payload := map[string]string{
			"name":    packageName,
			"version": version,
		}
		data, err := json.MarshalIndent(payload, "", "  ")
		if err != nil {
			return command.Result{}, err
		}
		if err := os.WriteFile(packagePath, append(data, '\n'), 0o644); err != nil {
			return command.Result{}, err
		}
	}
	return command.Result{ExitCode: 0, Stdout: pluginID + "\n"}, nil
}

func packedPluginInfo(filename string) (string, string) {
	base := strings.TrimSuffix(strings.TrimSpace(filename), filepath.Ext(filename))
	if base == "" {
		return "", ""
	}
	index := strings.LastIndex(base, "-")
	if index <= 0 || index >= len(base)-1 {
		return base, ""
	}
	name := base[:index]
	version := base[index+1:]
	if strings.Count(version, ".") >= 2 {
		return name, version
	}
	return base, ""
}

func (r *simulatedRuntimeRunner) listPlugins(runtimeRoot string, args []string) (command.Result, error) {
	plugins, err := r.pluginInventory(runtimeRoot)
	if err != nil {
		return command.Result{}, err
	}
	for _, arg := range args {
		if arg == "--json" {
			data, err := json.Marshal(plugins)
			if err != nil {
				return command.Result{}, err
			}
			return command.Result{ExitCode: 0, Stdout: string(data)}, nil
		}
	}
	lines := make([]string, 0, len(plugins))
	for _, plugin := range plugins {
		lines = append(lines, plugin.Plugin)
	}
	sort.Strings(lines)
	if len(lines) == 0 {
		return command.Result{ExitCode: 0}, nil
	}
	return command.Result{ExitCode: 0, Stdout: strings.Join(lines, "\n") + "\n"}, nil
}

func (r *simulatedRuntimeRunner) pluginInventory(runtimeRoot string) ([]cli.RuntimePluginInfo, error) {
	extensionsRoot := filepath.Join(runtimeRoot, "extensions")
	entries, err := os.ReadDir(extensionsRoot)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	plugins := make([]cli.RuntimePluginInfo, 0, len(entries))
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		root := filepath.Join(extensionsRoot, entry.Name())
		name := canonicalRuntimePluginName(readRuntimePluginManifestID(root))
		if name == "" {
			name = canonicalRuntimePluginName(entry.Name())
		}
		packageName, version := readRuntimePluginPackageInfo(root)
		digest, err := deploystate.New("").DirectoryDigest(root)
		if err != nil {
			return nil, err
		}
		plugins = append(plugins, cli.RuntimePluginInfo{
			Plugin:  name,
			Package: strings.TrimSpace(packageName),
			Version: strings.TrimSpace(version),
			Digest:  digest,
			Source:  runtimePluginSourceNPM,
		})
	}
	sort.Slice(plugins, func(i, j int) bool {
		return plugins[i].Plugin < plugins[j].Plugin
	})
	return plugins, nil
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
		copyTarget := hostDestination
		if info, err := os.Stat(source); err == nil {
			copyTarget = filepath.Join(hostDestination, filepath.Base(source))
			if info.IsDir() {
				copyTarget = filepath.Join(hostDestination, filepath.Base(source))
			}
		}
		if err := copyTestPath(source, copyTarget); err != nil {
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

func TestRuntimeSkillDeploySkipsReplayWhenSkillAlreadyInBaseline(t *testing.T) {
	t.Parallel()

	manager, runner, store, _, _ := newRuntimeTestManager(t)

	reloadRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimeAction, Action: "reload", Environment: "dev", Runtime: "openclaw-dev"}
	if _, err := manager.DeployService(context.Background(), reloadRoute, "dev"); err != nil {
		t.Fatalf("initial DeployService() error = %v", err)
	}

	deployRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimeSkill, Action: "deploy", Environment: "dev", Runtime: "openclaw-dev", Subject: "together"}
	if _, err := manager.RuntimeSkillDeploy(context.Background(), deployRoute); err != nil {
		t.Fatalf("RuntimeSkillDeploy() error = %v", err)
	}

	checkpointRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimeAction, Action: "checkpoint", Environment: "dev", Runtime: "openclaw-dev"}
	if _, err := manager.RuntimeCheckpoint(context.Background(), checkpointRoute); err != nil {
		t.Fatalf("RuntimeCheckpoint() error = %v", err)
	}

	historyBefore, err := store.ReadDeploymentHistory()
	if err != nil {
		t.Fatalf("ReadDeploymentHistory() error = %v", err)
	}
	runner.commands = nil

	result, err := manager.RuntimeSkillDeploy(context.Background(), deployRoute)
	if err != nil {
		t.Fatalf("RuntimeSkillDeploy() after checkpoint error = %v", err)
	}
	if !result.OK || !strings.Contains(result.Message, "already present in baseline checkpoint") {
		t.Fatalf("deploy result = %#v, want baseline no-op message", result)
	}
	if result.EventID != "" || result.DeploymentID != "" {
		t.Fatalf("deploy result = %#v, want no deployment or replay identifiers for baseline no-op", result)
	}
	if len(runner.commands) != 0 {
		t.Fatalf("baseline no-op should not redeploy runtime, got commands %#v", runner.commands)
	}

	log, err := store.LoadReplayLog("openclaw-dev")
	if err != nil {
		t.Fatalf("LoadReplayLog() error = %v", err)
	}
	if len(log.Events) != 0 {
		t.Fatalf("replay log = %#v, want empty after baseline no-op", log.Events)
	}

	historyAfter, err := store.ReadDeploymentHistory()
	if err != nil {
		t.Fatalf("ReadDeploymentHistory() error = %v", err)
	}
	if len(historyAfter) != len(historyBefore) {
		t.Fatalf("deployment history len = %d, want unchanged %d", len(historyAfter), len(historyBefore))
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

func TestRuntimePluginInstallRecordsReplayStateAndReplaysOnRedeploy(t *testing.T) {
	t.Parallel()

	manager, runner, store, runtimeRoot, _ := newRuntimeTestManager(t)

	reloadRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimeAction, Action: "reload", Environment: "dev", Runtime: "openclaw-dev"}
	if _, err := manager.DeployService(context.Background(), reloadRoute, "dev"); err != nil {
		t.Fatalf("initial DeployService() error = %v", err)
	}

	installRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimePlugin, Action: "install", Environment: "dev", Runtime: "openclaw-dev", Subject: "semantic-router"}
	result, err := manager.RuntimePluginInstall(context.Background(), installRoute)
	if err != nil {
		t.Fatalf("RuntimePluginInstall() error = %v", err)
	}
	if !result.OK || result.Plugin != "semantic-router" || result.Package != "semantic-router" || result.Source != runtimePluginSourceGit {
		t.Fatalf("install result = %#v, want semantic-router install", result)
	}

	log, err := store.LoadReplayLog("openclaw-dev")
	if err != nil {
		t.Fatalf("LoadReplayLog() error = %v", err)
	}
	if len(log.Events) != 1 || log.Events[0].Type != "plugin_install" || log.Events[0].Plugin != "semantic-router" {
		t.Fatalf("replay log = %#v, want one semantic-router plugin event", log.Events)
	}
	if log.Events[0].Source != runtimePluginSourceGit {
		t.Fatalf("replay event source = %q, want %q", log.Events[0].Source, runtimePluginSourceGit)
	}
	if log.Events[0].PackageDir == "" || log.Events[0].PackageDigest == "" {
		t.Fatalf("replay event = %#v, want staged package metadata", log.Events[0])
	}
	if _, err := os.Stat(filepath.Join(runtimeRoot, "extensions", "semantic-router", "openclaw.plugin.json")); err != nil {
		t.Fatalf("expected semantic-router plugin in runtime state: %v", err)
	}

	foundStagedInstall := false
	foundRestart := false
	for _, command := range runner.commands {
		text := strings.Join(command, " ")
		if strings.Contains(text, "openclaw plugins install") && strings.Contains(text, "/tmp/moltbox-plugin-source/") {
			foundStagedInstall = true
		}
		if text == "docker restart openclaw-dev" {
			foundRestart = true
		}
	}
	if !foundStagedInstall {
		t.Fatalf("expected plugin install to use staged package path, got commands %#v", runner.commands)
	}
	if !foundRestart {
		t.Fatalf("expected plugin replay to restart the runtime, got commands %#v", runner.commands)
	}

	if err := os.RemoveAll(filepath.Join(runtimeRoot, "extensions")); err != nil {
		t.Fatalf("remove runtime plugins: %v", err)
	}
	runner.commands = nil
	if _, err := manager.DeployService(context.Background(), reloadRoute, "dev"); err != nil {
		t.Fatalf("second DeployService() error = %v", err)
	}
	if _, err := os.Stat(filepath.Join(runtimeRoot, "extensions", "semantic-router", "openclaw.plugin.json")); err != nil {
		t.Fatalf("expected semantic-router plugin after replay-only redeploy: %v", err)
	}
}

func TestRuntimePluginRemoveRemovesReplayAndRestoresBaseline(t *testing.T) {
	t.Parallel()

	manager, _, store, runtimeRoot, _ := newRuntimeTestManager(t)

	reloadRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimeAction, Action: "reload", Environment: "dev", Runtime: "openclaw-dev"}
	if _, err := manager.DeployService(context.Background(), reloadRoute, "dev"); err != nil {
		t.Fatalf("initial DeployService() error = %v", err)
	}

	installRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimePlugin, Action: "install", Environment: "dev", Runtime: "openclaw-dev", Subject: "semantic-router"}
	if _, err := manager.RuntimePluginInstall(context.Background(), installRoute); err != nil {
		t.Fatalf("RuntimePluginInstall() error = %v", err)
	}

	removeRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimePlugin, Action: "remove", Environment: "dev", Runtime: "openclaw-dev", Subject: "semantic-router"}
	result, err := manager.RuntimePluginRemove(context.Background(), removeRoute)
	if err != nil {
		t.Fatalf("RuntimePluginRemove() error = %v", err)
	}
	if !result.OK || result.Plugin != "semantic-router" {
		t.Fatalf("remove result = %#v, want successful semantic-router removal", result)
	}

	log, err := store.LoadReplayLog("openclaw-dev")
	if err != nil {
		t.Fatalf("LoadReplayLog() error = %v", err)
	}
	if len(log.Events) != 0 {
		t.Fatalf("replay log = %#v, want empty after plugin remove", log.Events)
	}
	if _, err := os.Stat(filepath.Join(runtimeRoot, "extensions", "semantic-router")); !os.IsNotExist(err) {
		t.Fatalf("expected semantic-router plugin to be absent after remove, stat err = %v", err)
	}
}

func TestRuntimePluginCheckpointPromotesBaselineAndClearsReplay(t *testing.T) {
	t.Parallel()

	manager, runner, store, runtimeRoot, _ := newRuntimeTestManager(t)
	reloadRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimeAction, Action: "reload", Environment: "dev", Runtime: "openclaw-dev"}
	if _, err := manager.DeployService(context.Background(), reloadRoute, "dev"); err != nil {
		t.Fatalf("DeployService() error = %v", err)
	}
	installRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimePlugin, Action: "install", Environment: "dev", Runtime: "openclaw-dev", Subject: "semantic-router"}
	if _, err := manager.RuntimePluginInstall(context.Background(), installRoute); err != nil {
		t.Fatalf("RuntimePluginInstall() error = %v", err)
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
	if !ok || len(checkpoint.Plugins) != 1 || checkpoint.Plugins[0].Name != "semantic-router" {
		t.Fatalf("checkpoint plugins = %#v, want semantic-router baseline", checkpoint.Plugins)
	}
	if checkpoint.Plugins[0].Source != runtimePluginSourceGit || checkpoint.Plugins[0].Package != "semantic-router" {
		t.Fatalf("checkpoint plugins = %#v, want git-backed semantic-router metadata", checkpoint.Plugins)
	}

	log, err := store.LoadReplayLog("openclaw-dev")
	if err != nil {
		t.Fatalf("LoadReplayLog() error = %v", err)
	}
	if len(log.Events) != 0 {
		t.Fatalf("replay log should be empty after checkpoint, got %#v", log.Events)
	}

	if err := os.RemoveAll(filepath.Join(runtimeRoot, "extensions")); err != nil {
		t.Fatalf("remove runtime plugins: %v", err)
	}
	runner.commands = nil
	if _, err := manager.DeployService(context.Background(), reloadRoute, "dev"); err != nil {
		t.Fatalf("post-checkpoint DeployService() error = %v", err)
	}
	if _, err := os.Stat(filepath.Join(runtimeRoot, "extensions", "semantic-router", "openclaw.plugin.json")); err != nil {
		t.Fatalf("expected semantic-router plugin from checkpoint baseline: %v", err)
	}

	for _, command := range runner.commands {
		text := strings.Join(command, " ")
		if strings.Contains(text, "plugins install") {
			t.Fatalf("post-checkpoint redeploy should not replay plugin installs, got %q", text)
		}
	}
}

func TestRuntimePluginRemoveAfterCheckpointUsesReplayTombstone(t *testing.T) {
	t.Parallel()

	manager, runner, store, runtimeRoot, _ := newRuntimeTestManager(t)
	reloadRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimeAction, Action: "reload", Environment: "dev", Runtime: "openclaw-dev"}
	if _, err := manager.DeployService(context.Background(), reloadRoute, "dev"); err != nil {
		t.Fatalf("initial DeployService() error = %v", err)
	}

	installRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimePlugin, Action: "install", Environment: "dev", Runtime: "openclaw-dev", Subject: "semantic-router"}
	if _, err := manager.RuntimePluginInstall(context.Background(), installRoute); err != nil {
		t.Fatalf("RuntimePluginInstall() error = %v", err)
	}

	checkpointRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimeAction, Action: "checkpoint", Environment: "dev", Runtime: "openclaw-dev"}
	if _, err := manager.RuntimeCheckpoint(context.Background(), checkpointRoute); err != nil {
		t.Fatalf("RuntimeCheckpoint() error = %v", err)
	}

	removeRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimePlugin, Action: "remove", Environment: "dev", Runtime: "openclaw-dev", Subject: "semantic-router"}
	result, err := manager.RuntimePluginRemove(context.Background(), removeRoute)
	if err != nil {
		t.Fatalf("RuntimePluginRemove() error = %v", err)
	}
	if !result.OK || result.Plugin != "semantic-router" {
		t.Fatalf("remove result = %#v, want successful semantic-router removal", result)
	}

	log, err := store.LoadReplayLog("openclaw-dev")
	if err != nil {
		t.Fatalf("LoadReplayLog() error = %v", err)
	}
	if len(log.Events) != 1 || log.Events[0].Type != "plugin_remove" || log.Events[0].Plugin != "semantic-router" {
		t.Fatalf("replay log = %#v, want semantic-router plugin_remove tombstone", log.Events)
	}

	plugins, err := manager.currentCheckpointPlugins("openclaw-dev")
	if err != nil {
		t.Fatalf("currentCheckpointPlugins() error = %v", err)
	}
	if len(plugins) != 0 {
		t.Fatalf("current checkpoint plugins = %#v, want tombstone to hide semantic-router", plugins)
	}

	if _, err := os.Stat(filepath.Join(runtimeRoot, "extensions", "semantic-router")); !os.IsNotExist(err) {
		t.Fatalf("expected semantic-router plugin to be absent after remove, stat err = %v", err)
	}

	runner.commands = nil
	if _, err := manager.DeployService(context.Background(), reloadRoute, "dev"); err != nil {
		t.Fatalf("post-remove DeployService() error = %v", err)
	}
	if _, err := os.Stat(filepath.Join(runtimeRoot, "extensions", "semantic-router")); !os.IsNotExist(err) {
		t.Fatalf("expected semantic-router plugin to remain absent after replay reload, stat err = %v", err)
	}

	foundRestart := false
	for _, command := range runner.commands {
		if strings.Join(command, " ") == "docker restart openclaw-dev" {
			foundRestart = true
			break
		}
	}
	if !foundRestart {
		t.Fatalf("expected plugin removal replay to restart the runtime, got commands %#v", runner.commands)
	}
}

func TestRuntimePluginInstallIsIdempotentAgainstBaseline(t *testing.T) {
	t.Parallel()

	manager, _, store, runtimeRoot, _ := newRuntimeTestManager(t)

	reloadRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimeAction, Action: "reload", Environment: "dev", Runtime: "openclaw-dev"}
	if _, err := manager.DeployService(context.Background(), reloadRoute, "dev"); err != nil {
		t.Fatalf("initial DeployService() error = %v", err)
	}

	installRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimePlugin, Action: "install", Environment: "dev", Runtime: "openclaw-dev", Subject: "semantic-router"}
	if _, err := manager.RuntimePluginInstall(context.Background(), installRoute); err != nil {
		t.Fatalf("RuntimePluginInstall() error = %v", err)
	}

	checkpointRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimeAction, Action: "checkpoint", Environment: "dev", Runtime: "openclaw-dev"}
	if _, err := manager.RuntimeCheckpoint(context.Background(), checkpointRoute); err != nil {
		t.Fatalf("RuntimeCheckpoint() error = %v", err)
	}

	historyBefore, err := store.ReadDeploymentHistory()
	if err != nil {
		t.Fatalf("ReadDeploymentHistory() error = %v", err)
	}

	result, err := manager.RuntimePluginInstall(context.Background(), installRoute)
	if err != nil {
		t.Fatalf("RuntimePluginInstall() after checkpoint error = %v", err)
	}
	if !result.OK || !strings.Contains(result.Message, "already present in baseline checkpoint") {
		t.Fatalf("install result = %#v, want baseline no-op message", result)
	}
	if result.EventID != "" || result.DeploymentID != "" {
		t.Fatalf("install result = %#v, want no deployment or replay identifiers for baseline no-op", result)
	}

	log, err := store.LoadReplayLog("openclaw-dev")
	if err != nil {
		t.Fatalf("LoadReplayLog() error = %v", err)
	}
	if len(log.Events) != 0 {
		t.Fatalf("replay log = %#v, want empty after baseline no-op", log.Events)
	}
	if _, err := os.Stat(filepath.Join(runtimeRoot, "extensions", "semantic-router", "openclaw.plugin.json")); err != nil {
		t.Fatalf("expected semantic-router plugin to remain after baseline no-op: %v", err)
	}

	historyAfter, err := store.ReadDeploymentHistory()
	if err != nil {
		t.Fatalf("ReadDeploymentHistory() error = %v", err)
	}
	if len(historyAfter) != len(historyBefore) {
		t.Fatalf("deployment history len = %d, want unchanged %d", len(historyAfter), len(historyBefore))
	}
}

func TestRuntimePluginInstallRejectsUnknownBarePlugin(t *testing.T) {
	t.Parallel()

	manager, _, _, _, _ := newRuntimeTestManager(t)

	installRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimePlugin, Action: "install", Environment: "dev", Runtime: "openclaw-dev", Subject: "missing-plugin"}
	_, err := manager.RuntimePluginInstall(context.Background(), installRoute)
	if err == nil || !strings.Contains(err.Error(), "unknown deployable plugin") {
		t.Fatalf("RuntimePluginInstall() error = %v, want unknown deployable plugin", err)
	}
}

func TestRuntimeReplayInstallsPluginsBeforeSkills(t *testing.T) {
	t.Parallel()

	manager, runner, _, _, _ := newRuntimeTestManager(t)
	reloadRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimeAction, Action: "reload", Environment: "dev", Runtime: "openclaw-dev"}
	if _, err := manager.DeployService(context.Background(), reloadRoute, "dev"); err != nil {
		t.Fatalf("initial DeployService() error = %v", err)
	}

	installPluginRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimePlugin, Action: "install", Environment: "dev", Runtime: "openclaw-dev", Subject: "semantic-router"}
	if _, err := manager.RuntimePluginInstall(context.Background(), installPluginRoute); err != nil {
		t.Fatalf("RuntimePluginInstall() error = %v", err)
	}
	installSkillRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimeSkill, Action: "deploy", Environment: "dev", Runtime: "openclaw-dev", Subject: "together"}
	if _, err := manager.RuntimeSkillDeploy(context.Background(), installSkillRoute); err != nil {
		t.Fatalf("RuntimeSkillDeploy() error = %v", err)
	}

	runner.commands = nil
	if _, err := manager.DeployService(context.Background(), reloadRoute, "dev"); err != nil {
		t.Fatalf("replay DeployService() error = %v", err)
	}

	pluginInstallIndex := -1
	skillReplayIndex := -1
	for index, command := range runner.commands {
		text := strings.Join(command, " ")
		if pluginInstallIndex < 0 && strings.Contains(text, "openclaw plugins install") {
			pluginInstallIndex = index
		}
		if skillReplayIndex < 0 && strings.Contains(text, "/home/node/.openclaw/skills/together-escalation") {
			skillReplayIndex = index
		}
	}
	if pluginInstallIndex < 0 || skillReplayIndex < 0 || pluginInstallIndex >= skillReplayIndex {
		t.Fatalf("expected plugin replay before skill replay, got commands %#v", runner.commands)
	}
}

func TestRuntimeReplayFailsWhenStagedPackageDigestChanges(t *testing.T) {
	t.Parallel()

	manager, runner, store, _, _ := newRuntimeTestManager(t)

	reloadRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimeAction, Action: "reload", Environment: "dev", Runtime: "openclaw-dev"}
	if _, err := manager.DeployService(context.Background(), reloadRoute, "dev"); err != nil {
		t.Fatalf("initial DeployService() error = %v", err)
	}

	deployRoute := &cli.Route{Resource: "dev", Kind: cli.KindRuntimeSkill, Action: "deploy", Environment: "dev", Runtime: "openclaw-dev", Subject: "together"}
	result, err := manager.RuntimeSkillDeploy(context.Background(), deployRoute)
	if err != nil {
		t.Fatalf("RuntimeSkillDeploy() error = %v", err)
	}

	if err := os.WriteFile(filepath.Join(result.PackageDir, "SKILL.md"), []byte("tampered\n"), 0o644); err != nil {
		t.Fatalf("tamper staged package: %v", err)
	}
	runner.commands = nil

	_, err = manager.DeployService(context.Background(), reloadRoute, "dev")
	if err == nil || !strings.Contains(err.Error(), "package digest mismatch") {
		t.Fatalf("DeployService() error = %v, want package digest mismatch", err)
	}

	for _, command := range runner.commands {
		text := strings.Join(command, " ")
		if strings.Contains(text, result.PackageDir) {
			t.Fatalf("replay should fail before docker cp, got command %q", text)
		}
	}

	log, err := store.LoadReplayLog("openclaw-dev")
	if err != nil {
		t.Fatalf("LoadReplayLog() error = %v", err)
	}
	if len(log.Events) != 1 {
		t.Fatalf("replay log len = %d, want original event preserved", len(log.Events))
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
	mustWriteFile(t, filepath.Join(runtimeRepoRoot, "openclaw-dev", "openclaw.json.template"), "{\n  \"agents\": {\n    \"defaults\": {\n      \"workspace\": \"/home/node/.openclaw/workspace\"\n    }\n  }\n}\n")
	mustWriteFile(t, filepath.Join(runtimeRepoRoot, "openclaw-dev", "model-runtime.yml"), "model: local\n")
	mustWriteFile(t, filepath.Join(skillsRoot, "skills", "together-escalation", "SKILL.md"), "---\nname: together-escalation\ndescription: test\n---\n")
	mustWriteFile(t, filepath.Join(skillsRoot, "skills", "semantic-router", "SKILL.md"), "---\nname: semantic-router\ndescription: test\n---\n")
	mustWriteFile(t, filepath.Join(skillsRoot, "skills", "semantic-router", "package.json"), "{\n  \"name\": \"semantic-router\",\n  \"version\": \"1.2.0\"\n}\n")
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
	containerInfo.Config.Env = []string{"OPENCLAW_CONFIG_DIR=/app/config/openclaw"}
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

func runtimePluginHostPath(runtimeRoot, spec string) string {
	if strings.HasPrefix(filepath.ToSlash(spec), "/") {
		return runtimeHostPath(runtimeRoot, spec)
	}
	if _, err := os.Stat(spec); err == nil {
		return spec
	}
	return ""
}

func runtimePackageVersionFromSpec(spec string) string {
	trimmed := strings.TrimSpace(spec)
	if trimmed == "" {
		return ""
	}
	if strings.HasPrefix(trimmed, "@") {
		if index := strings.LastIndex(trimmed, "@"); index > 0 {
			return trimmed[index+1:]
		}
		return ""
	}
	if index := strings.Index(trimmed, "@"); index > 0 {
		return trimmed[index+1:]
	}
	return ""
}

func copyTestPath(source, destination string) error {
	info, err := os.Stat(source)
	if err != nil {
		return err
	}
	if info.IsDir() {
		return copyTree(source, destination)
	}
	data, err := os.ReadFile(source)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
		return err
	}
	return os.WriteFile(destination, data, 0o644)
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
	trimmed := filepath.ToSlash(containerPath)
	for _, prefix := range []string{"/home/node/.openclaw", "/app/config/openclaw"} {
		if strings.HasPrefix(trimmed, prefix) {
			trimmed = strings.TrimPrefix(trimmed, prefix)
			break
		}
	}
	trimmed = strings.TrimPrefix(trimmed, "/")
	if trimmed == "." || trimmed == "" {
		return runtimeRoot
	}
	return filepath.Join(runtimeRoot, filepath.FromSlash(trimmed))
}

func shellQuotedArgsAfter(command, prefix string) []string {
	index := strings.Index(command, prefix)
	if index < 0 {
		return nil
	}
	remainder := command[index+len(prefix):]
	if separator := strings.Index(remainder, " && "); separator >= 0 {
		remainder = remainder[:separator]
	}
	paths := []string{}
	for {
		start := strings.Index(remainder, "'")
		if start < 0 {
			break
		}
		remainder = remainder[start+1:]
		end := strings.Index(remainder, "'")
		if end < 0 {
			break
		}
		paths = append(paths, remainder[:end])
		remainder = remainder[end+1:]
	}
	return paths
}
