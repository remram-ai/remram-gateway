package orchestrator

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path"
	"path/filepath"
	stdruntime "runtime"
	"sort"
	"strings"
	"time"

	"github.com/remram-ai/moltbox-gateway/internal/deploystate"
	"github.com/remram-ai/moltbox-gateway/pkg/cli"
)

const defaultRuntimeImage = "ghcr.io/openclaw/openclaw:latest"

const (
	defaultOpenClawStateRoot = "/home/node/.openclaw"
	openClawConfigEnvName    = "OPENCLAW_CONFIG_DIR"
	pluginPackBuilderImage   = "node:20-bookworm"
	runtimePluginSourceGit   = "git"
	runtimePluginSourceLocal = "local"
	runtimePluginSourceNPM   = "npm"
)

var runtimeSkillAliases = map[string]string{
	"together": "together-escalation",
}

type deployableSkill struct {
	Name      string
	SourceDir string
	Digest    string
}

type deployablePlugin struct {
	Name      string
	Package   string
	Version   string
	SourceDir string
	Digest    string
}

type runtimePluginState struct {
	Name    string
	Package string
	Version string
	Digest  string
	Source  string
	HostDir string
}

type runtimePluginManifest struct {
	ID string `json:"id"`
}

type runtimePluginPackage struct {
	Name    string `json:"name"`
	Version string `json:"version"`
}

type runtimeOpenClawConfig struct {
	Agents struct {
		Defaults struct {
			Workspace string `json:"workspace"`
		} `json:"defaults"`
	} `json:"agents"`
}

func isRuntimeService(service string) bool {
	switch canonicalServiceName(service) {
	case "openclaw-dev", "openclaw-test", "openclaw-prod":
		return true
	default:
		return false
	}
}

func (m *Manager) prepareRuntimeDeploy(_ context.Context, route *cli.Route, service string) error {
	return m.restoreRuntimeBaseline(service)
}

func (m *Manager) restoreRuntimeBaseline(service string) error {
	destination := m.config.RuntimeComponentDir(service)
	checkpoint, ok, err := m.stateStore.LoadCheckpoint(service)
	if err != nil {
		return err
	}

	if err := os.RemoveAll(destination); err != nil {
		return fmt.Errorf("reset runtime state for %s: %w", service, err)
	}
	if err := os.MkdirAll(destination, 0o755); err != nil {
		return fmt.Errorf("create runtime state dir for %s: %w", service, err)
	}

	if ok && strings.TrimSpace(checkpoint.SnapshotDir) != "" {
		if err := copyTree(checkpoint.SnapshotDir, destination); err != nil {
			return fmt.Errorf("restore checkpoint snapshot for %s: %w", service, err)
		}
	}
	if err := ensureRuntimeStateOwnership(destination); err != nil {
		return fmt.Errorf("set runtime state ownership for %s: %w", service, err)
	}
	return nil
}

func (m *Manager) RuntimeSkillDeploy(ctx context.Context, route *cli.Route) (cli.RuntimeSkillResult, error) {
	service := runtimeService(route)
	if !isRuntimeService(service) {
		return cli.RuntimeSkillResult{}, fmt.Errorf("skill deploy is only supported for runtime services")
	}

	skill, canonicalSkill, err := m.resolveDeployableSkill(route.Subject)
	if err != nil {
		return cli.RuntimeSkillResult{}, err
	}

	baselineDigest, baselineCheckpointID, err := m.baselineSkillDigest(service, canonicalSkill)
	if err != nil {
		return cli.RuntimeSkillResult{}, err
	}
	if baselineDigest != "" && baselineDigest == skill.Digest {
		log, err := m.stateStore.LoadReplayLog(service)
		if err != nil {
			return cli.RuntimeSkillResult{}, err
		}
		return cli.RuntimeSkillResult{
			OK:             true,
			Route:          route,
			Runtime:        service,
			Skill:          strings.TrimSpace(route.Subject),
			CanonicalSkill: canonicalSkill,
			Action:         route.Action,
			Message:        fmt.Sprintf("skill %q is already present in baseline checkpoint %s for %s", canonicalSkill, baselineCheckpointID, service),
			ReplayCount:    len(log.Events),
		}, nil
	}

	previousDigest, err := m.effectiveSkillDigest(service, canonicalSkill)
	if err != nil {
		return cli.RuntimeSkillResult{}, err
	}

	deploymentID := newGatewayID("deploy")
	eventID := newGatewayID("event")
	stagedDir, err := m.stateStore.StageReplayPackage(service, eventID, skill.SourceDir)
	if err != nil {
		return cli.RuntimeSkillResult{}, err
	}
	stateRoot := m.runtimeOpenClawStateRoot(ctx, service)

	event := deploystate.ReplayEvent{
		EventID:       eventID,
		DeploymentID:  deploymentID,
		Timestamp:     time.Now().UTC().Format(time.RFC3339),
		Runtime:       service,
		Type:          "skill_install",
		Skill:         canonicalSkill,
		PackageDir:    stagedDir,
		PackageDigest: skill.Digest,
		ContainerPath: path.Join(stateRoot, "skills", canonicalSkill),
		Details: map[string]string{
			"requested_skill": strings.TrimSpace(route.Subject),
		},
	}

	log, err := m.stateStore.LoadReplayLog(service)
	if err != nil {
		return cli.RuntimeSkillResult{}, err
	}
	previousLog := log
	log.Events = append(log.Events, event)
	if err := m.stateStore.SaveReplayLog(service, log); err != nil {
		return cli.RuntimeSkillResult{}, err
	}

	reloadRoute := &cli.Route{
		Resource:    route.Resource,
		Kind:        cli.KindRuntimeAction,
		Action:      "reload",
		Environment: route.Environment,
		Runtime:     service,
	}
	if _, err := m.DeployService(ctx, reloadRoute, service); err != nil {
		_ = m.stateStore.SaveReplayLog(service, previousLog)
		return cli.RuntimeSkillResult{}, err
	}

	record := deploystate.DeploymentRecord{
		DeploymentID:    deploymentID,
		Timestamp:       event.Timestamp,
		Actor:           deploymentActor(route),
		Target:          service + "/skill/" + canonicalSkill,
		ArtifactVersion: skill.Digest,
		PreviousVersion: previousDigest,
		Result:          "success",
		Operation:       "runtime_skill_deploy",
		Runtime:         service,
		Details: map[string]string{
			"event_id":        eventID,
			"package_dir":     stagedDir,
			"requested_skill": strings.TrimSpace(route.Subject),
		},
	}
	if err := m.stateStore.AppendDeployment(record); err != nil {
		return cli.RuntimeSkillResult{}, err
	}

	return cli.RuntimeSkillResult{
		OK:             true,
		Route:          route,
		Runtime:        service,
		Skill:          strings.TrimSpace(route.Subject),
		CanonicalSkill: canonicalSkill,
		Action:         route.Action,
		DeploymentID:   deploymentID,
		EventID:        eventID,
		PackageDir:     stagedDir,
		ReplayCount:    len(log.Events),
	}, nil
}

func (m *Manager) RuntimeSkillList(ctx context.Context, route *cli.Route) (cli.CommandResult, error) {
	service := runtimeService(route)
	if !isRuntimeService(service) {
		return cli.CommandResult{}, fmt.Errorf("skill list is only supported for runtime services")
	}

	nativeRoute := &cli.Route{
		Resource:    route.Resource,
		Kind:        cli.KindRuntimeNative,
		Action:      "openclaw",
		Environment: route.Environment,
		Runtime:     service,
		NativeArgs:  []string{"skills", "list"},
	}
	result, err := m.RuntimeOpenClaw(ctx, nativeRoute)
	if err != nil {
		return cli.CommandResult{}, err
	}
	result.Route = route
	return result, nil
}

func (m *Manager) RuntimePluginInstall(ctx context.Context, route *cli.Route) (cli.RuntimePluginResult, error) {
	service := runtimeService(route)
	if !isRuntimeService(service) {
		return cli.RuntimePluginResult{}, fmt.Errorf("plugin install is only supported for runtime services")
	}

	requested := strings.TrimSpace(route.Subject)
	if requested == "" {
		return cli.RuntimePluginResult{}, fmt.Errorf("missing plugin package")
	}

	plugin, source, err := m.resolveDeployablePlugin(requested)
	if err != nil {
		return cli.RuntimePluginResult{}, err
	}

	deploymentID := newGatewayID("deploy")
	eventID := newGatewayID("event")
	timestamp := time.Now().UTC().Format(time.RFC3339)

	backupDir, err := m.snapshotRuntimeComponent(service, eventID)
	if err != nil {
		return cli.RuntimePluginResult{}, err
	}
	defer os.RemoveAll(backupDir)
	keepArtifacts := false
	packageDir := ""
	defer func() {
		if keepArtifacts {
			return
		}
		if packageDir != "" {
			_ = os.RemoveAll(packageDir)
		}
	}()

	restored := false
	restoreRuntime := func() {
		if restored {
			return
		}
		_ = m.restoreRuntimeComponent(service, backupDir)
		restored = true
	}

	beforePlugins, err := m.discoverInstalledPlugins(service)
	if err != nil {
		restoreRuntime()
		return cli.RuntimePluginResult{}, err
	}

	packageDir, err = m.stateStore.StageReplayPackage(service, eventID, plugin.SourceDir)
	if err != nil {
		restoreRuntime()
		return cli.RuntimePluginResult{}, err
	}
	installSourcePath, err := m.prepareRuntimePluginPackage(ctx, packageDir)
	if err != nil {
		restoreRuntime()
		return cli.RuntimePluginResult{}, err
	}
	packageDigest, err := m.stateStore.DirectoryDigest(packageDir)
	if err != nil {
		restoreRuntime()
		return cli.RuntimePluginResult{}, err
	}

	installedPlugin, err := m.installRuntimePlugin(ctx, service, eventID, requested, plugin, source, packageDir, installSourcePath, beforePlugins)
	if err != nil {
		restoreRuntime()
		return cli.RuntimePluginResult{}, err
	}

	baselineDigest, baselineCheckpointID, err := m.baselinePluginDigest(service, plugin.Name)
	if err != nil {
		restoreRuntime()
		return cli.RuntimePluginResult{}, err
	}
	if baselineDigest != "" && baselineDigest == installedPlugin.Digest {
		log, err := m.stateStore.LoadReplayLog(service)
		if err != nil {
			restoreRuntime()
			return cli.RuntimePluginResult{}, err
		}
		restoreRuntime()
		return cli.RuntimePluginResult{
			OK:          true,
			Route:       route,
			Runtime:     service,
			Plugin:      installedPlugin.Name,
			Package:     installedPlugin.Package,
			Version:     installedPlugin.Version,
			Digest:      installedPlugin.Digest,
			Source:      source,
			Action:      route.Action,
			Message:     fmt.Sprintf("plugin %q is already present in baseline checkpoint %s for %s", installedPlugin.Name, baselineCheckpointID, service),
			ReplayCount: len(log.Events),
		}, nil
	}

	previousDigest, err := m.effectivePluginDigest(service, installedPlugin.Name)
	if err != nil {
		restoreRuntime()
		return cli.RuntimePluginResult{}, err
	}
	if previousDigest != "" && previousDigest == installedPlugin.Digest {
		log, err := m.stateStore.LoadReplayLog(service)
		if err != nil {
			restoreRuntime()
			return cli.RuntimePluginResult{}, err
		}
		restoreRuntime()
		return cli.RuntimePluginResult{
			OK:          true,
			Route:       route,
			Runtime:     service,
			Plugin:      installedPlugin.Name,
			Package:     installedPlugin.Package,
			Version:     installedPlugin.Version,
			Digest:      installedPlugin.Digest,
			Source:      source,
			Action:      route.Action,
			Message:     fmt.Sprintf("plugin %q is already present in runtime %s", installedPlugin.Name, service),
			ReplayCount: len(log.Events),
		}, nil
	}

	event := deploystate.ReplayEvent{
		EventID:       eventID,
		DeploymentID:  deploymentID,
		Timestamp:     timestamp,
		Runtime:       service,
		Type:          "plugin_install",
		Plugin:        installedPlugin.Name,
		Package:       installedPlugin.Package,
		Version:       installedPlugin.Version,
		Digest:        installedPlugin.Digest,
		Source:        source,
		PackageDir:    packageDir,
		PackageDigest: packageDigest,
		Details: map[string]string{
			"requested_package": requested,
		},
	}

	log, err := m.stateStore.LoadReplayLog(service)
	if err != nil {
		restoreRuntime()
		return cli.RuntimePluginResult{}, err
	}
	previousLog := log
	log.Events = append(log.Events, event)
	if err := m.stateStore.SaveReplayLog(service, log); err != nil {
		restoreRuntime()
		return cli.RuntimePluginResult{}, err
	}

	reloadRoute := &cli.Route{
		Resource:    route.Resource,
		Kind:        cli.KindRuntimeAction,
		Action:      "reload",
		Environment: route.Environment,
		Runtime:     service,
	}
	if _, err := m.DeployService(ctx, reloadRoute, service); err != nil {
		_ = m.stateStore.SaveReplayLog(service, previousLog)
		restoreRuntime()
		return cli.RuntimePluginResult{}, err
	}

	record := deploystate.DeploymentRecord{
		DeploymentID:    deploymentID,
		Timestamp:       timestamp,
		Actor:           deploymentActor(route),
		Target:          service + "/plugin/" + installedPlugin.Name,
		ArtifactVersion: installedPlugin.Digest,
		PreviousVersion: previousDigest,
		Result:          "success",
		Operation:       "runtime_plugin_install",
		Runtime:         service,
		Details: map[string]string{
			"event_id":          eventID,
			"package_dir":       packageDir,
			"requested_package": requested,
			"resolved_package":  installedPlugin.Package,
			"source":            source,
		},
	}
	if err := m.stateStore.AppendDeployment(record); err != nil {
		restoreRuntime()
		return cli.RuntimePluginResult{}, err
	}
	keepArtifacts = true

	return cli.RuntimePluginResult{
		OK:           true,
		Route:        route,
		Runtime:      service,
		Plugin:       installedPlugin.Name,
		Package:      installedPlugin.Package,
		Version:      installedPlugin.Version,
		Digest:       installedPlugin.Digest,
		Source:       source,
		Action:       route.Action,
		DeploymentID: deploymentID,
		EventID:      eventID,
		PackageDir:   packageDir,
		ReplayCount:  len(log.Events),
	}, nil
}

func (m *Manager) RuntimePluginList(_ context.Context, route *cli.Route) (cli.RuntimePluginListResult, error) {
	service := runtimeService(route)
	if !isRuntimeService(service) {
		return cli.RuntimePluginListResult{}, fmt.Errorf("plugin list is only supported for runtime services")
	}

	plugins, err := m.currentCheckpointPlugins(service)
	if err != nil {
		return cli.RuntimePluginListResult{}, err
	}

	items := make([]cli.RuntimePluginInfo, 0, len(plugins))
	for _, plugin := range plugins {
		items = append(items, cli.RuntimePluginInfo{
			Plugin:  plugin.Name,
			Package: plugin.Package,
			Version: plugin.Version,
			Digest:  plugin.Digest,
			Source:  plugin.Source,
		})
	}

	return cli.RuntimePluginListResult{
		OK:      true,
		Route:   route,
		Runtime: service,
		Plugins: items,
	}, nil
}

func (m *Manager) RuntimePluginRemove(ctx context.Context, route *cli.Route) (cli.RuntimePluginResult, error) {
	service := runtimeService(route)
	if !isRuntimeService(service) {
		return cli.RuntimePluginResult{}, fmt.Errorf("plugin remove is only supported for runtime services")
	}

	plugin := canonicalRuntimePluginName(route.Subject)
	log, err := m.stateStore.LoadReplayLog(service)
	if err != nil {
		return cli.RuntimePluginResult{}, err
	}

	index, event, ok := latestReplayPluginEvent(log, plugin)
	if !ok {
		baselineDigest, checkpointID, err := m.baselinePluginDigest(service, plugin)
		if err != nil {
			return cli.RuntimePluginResult{}, err
		}
		if baselineDigest != "" {
			return cli.RuntimePluginResult{}, fmt.Errorf("plugin %q is part of baseline checkpoint %s in %s", plugin, checkpointID, service)
		}
		return cli.RuntimePluginResult{}, fmt.Errorf("no replay deployment found for plugin %q in %s", plugin, service)
	}

	updated := log
	updated.Events = append(append([]deploystate.ReplayEvent(nil), log.Events[:index]...), log.Events[index+1:]...)
	if err := m.stateStore.SaveReplayLog(service, updated); err != nil {
		return cli.RuntimePluginResult{}, err
	}

	reloadRoute := &cli.Route{
		Resource:    route.Resource,
		Kind:        cli.KindRuntimeAction,
		Action:      "reload",
		Environment: route.Environment,
		Runtime:     service,
	}
	if _, err := m.DeployService(ctx, reloadRoute, service); err != nil {
		_ = m.stateStore.SaveReplayLog(service, log)
		return cli.RuntimePluginResult{}, err
	}

	deploymentID := newGatewayID("deploy")
	previousDigest := strings.TrimSpace(event.Digest)
	if previousDigest == "" {
		previousDigest = strings.TrimSpace(event.PackageDigest)
	}
	record := deploystate.DeploymentRecord{
		DeploymentID:    deploymentID,
		Timestamp:       time.Now().UTC().Format(time.RFC3339),
		Actor:           deploymentActor(route),
		Target:          service + "/plugin/" + event.Plugin,
		ArtifactVersion: "",
		PreviousVersion: previousDigest,
		Result:          "success",
		Operation:       "runtime_plugin_remove",
		Runtime:         service,
		Details: map[string]string{
			"event_id": event.EventID,
			"package":  event.Package,
			"source":   event.Source,
		},
	}
	if err := m.stateStore.AppendDeployment(record); err != nil {
		return cli.RuntimePluginResult{}, err
	}

	return cli.RuntimePluginResult{
		OK:           true,
		Route:        route,
		Runtime:      service,
		Plugin:       event.Plugin,
		Package:      event.Package,
		Version:      event.Version,
		Digest:       previousDigest,
		Source:       event.Source,
		Action:       route.Action,
		DeploymentID: deploymentID,
		EventID:      event.EventID,
		ReplayCount:  len(updated.Events),
	}, nil
}

func (m *Manager) RuntimeSkillRemove(ctx context.Context, route *cli.Route) (cli.RuntimeSkillResult, error) {
	normalized := cloneRoute(route)
	normalized.Action = "remove"
	return m.runtimeSkillRemove(ctx, normalized)
}

func (m *Manager) RuntimeSkillRollback(ctx context.Context, route *cli.Route) (cli.RuntimeSkillResult, error) {
	normalized := cloneRoute(route)
	normalized.Action = "remove"
	return m.runtimeSkillRemove(ctx, normalized)
}

func (m *Manager) runtimeSkillRemove(ctx context.Context, route *cli.Route) (cli.RuntimeSkillResult, error) {
	service := runtimeService(route)
	if !isRuntimeService(service) {
		return cli.RuntimeSkillResult{}, fmt.Errorf("skill remove is only supported for runtime services")
	}

	canonicalSkill := canonicalRuntimeSkillName(route.Subject)
	log, err := m.stateStore.LoadReplayLog(service)
	if err != nil {
		return cli.RuntimeSkillResult{}, err
	}

	index, event, ok := latestReplaySkillEvent(log, canonicalSkill)
	if !ok {
		return cli.RuntimeSkillResult{}, fmt.Errorf("no replay deployment found for skill %q in %s", canonicalSkill, service)
	}

	updated := log
	updated.Events = append(append([]deploystate.ReplayEvent(nil), log.Events[:index]...), log.Events[index+1:]...)
	if err := m.stateStore.SaveReplayLog(service, updated); err != nil {
		return cli.RuntimeSkillResult{}, err
	}

	reloadRoute := &cli.Route{
		Resource:    route.Resource,
		Kind:        cli.KindRuntimeAction,
		Action:      "reload",
		Environment: route.Environment,
		Runtime:     service,
	}
	if _, err := m.DeployService(ctx, reloadRoute, service); err != nil {
		_ = m.stateStore.SaveReplayLog(service, log)
		return cli.RuntimeSkillResult{}, err
	}

	deploymentID := newGatewayID("deploy")
	record := deploystate.DeploymentRecord{
		DeploymentID:    deploymentID,
		Timestamp:       time.Now().UTC().Format(time.RFC3339),
		Actor:           deploymentActor(route),
		Target:          service + "/skill/" + event.Skill,
		ArtifactVersion: "",
		PreviousVersion: event.PackageDigest,
		Result:          "success",
		Operation:       "runtime_skill_remove",
		Runtime:         service,
		Details: map[string]string{
			"event_id":        event.EventID,
			"requested_skill": strings.TrimSpace(route.Subject),
		},
	}
	if err := m.stateStore.AppendDeployment(record); err != nil {
		return cli.RuntimeSkillResult{}, err
	}

	return cli.RuntimeSkillResult{
		OK:             true,
		Route:          route,
		Runtime:        service,
		Skill:          strings.TrimSpace(route.Subject),
		CanonicalSkill: event.Skill,
		Action:         route.Action,
		DeploymentID:   deploymentID,
		EventID:        event.EventID,
		ReplayCount:    len(updated.Events),
	}, nil
}

func (m *Manager) replayRuntimeDeployHistory(ctx context.Context, route *cli.Route, service string) error {
	log, err := m.stateStore.LoadReplayLog(service)
	if err != nil {
		return err
	}
	events := orderedRuntimeReplayEvents(log.Events)
	pluginRestartPending := false
	pluginRestarted := false
	for _, event := range events {
		if event.Type == "skill_install" && pluginRestartPending && !pluginRestarted {
			if err := m.restartRuntimeContainer(ctx, service); err != nil {
				return err
			}
			pluginRestarted = true
		}
		changed, err := m.executeReplayEvent(ctx, service, event)
		if err != nil {
			return err
		}
		if event.Type == "plugin_install" && changed {
			pluginRestartPending = true
		}
	}
	if pluginRestartPending && !pluginRestarted {
		if err := m.restartRuntimeContainer(ctx, service); err != nil {
			return err
		}
	}

	if len(log.Events) == 0 {
		return nil
	}

	record := deploystate.DeploymentRecord{
		DeploymentID:    newGatewayID("deploy"),
		Timestamp:       time.Now().UTC().Format(time.RFC3339),
		Actor:           deploymentActor(route),
		Target:          service,
		ArtifactVersion: m.selectedRuntimeImage(service),
		Result:          "replayed",
		Operation:       "runtime_replay",
		Runtime:         service,
		Details: map[string]string{
			"event_count": fmt.Sprintf("%d", len(log.Events)),
		},
	}
	return m.stateStore.AppendDeployment(record)
}

func (m *Manager) executeReplayEvent(ctx context.Context, service string, event deploystate.ReplayEvent) (bool, error) {
	switch event.Type {
	case "plugin_install":
		return m.installPluginFromGatewayState(ctx, service, event)
	case "skill_install":
		return false, m.installSkillFromGatewayState(ctx, service, event)
	default:
		return false, fmt.Errorf("unsupported replay event type %q for %s", event.Type, service)
	}
}

func (m *Manager) installSkillFromGatewayState(ctx context.Context, service string, event deploystate.ReplayEvent) error {
	if strings.TrimSpace(event.PackageDir) == "" {
		return fmt.Errorf("replay event %s is missing package dir", event.EventID)
	}
	info, err := os.Stat(event.PackageDir)
	if err != nil {
		return fmt.Errorf("replay event %s package dir unavailable: %w", event.EventID, err)
	}
	if !info.IsDir() {
		return fmt.Errorf("replay event %s package dir %s is not a directory", event.EventID, event.PackageDir)
	}
	if strings.TrimSpace(event.PackageDigest) == "" {
		return fmt.Errorf("replay event %s is missing package digest", event.EventID)
	}
	actualDigest, err := m.stateStore.DirectoryDigest(event.PackageDir)
	if err != nil {
		return fmt.Errorf("replay event %s digest verification failed: %w", event.EventID, err)
	}
	if actualDigest != strings.TrimSpace(event.PackageDigest) {
		return fmt.Errorf("replay event %s package digest mismatch: got %s want %s", event.EventID, actualDigest, strings.TrimSpace(event.PackageDigest))
	}

	destination := strings.TrimSpace(event.ContainerPath)
	if destination == "" {
		destination = path.Join(m.runtimeOpenClawStateRoot(ctx, service), "skills", event.Skill)
	}
	parent := path.Dir(destination)
	stagingRoot := path.Join("/tmp/moltbox-skill-replay", event.EventID)
	stagingPath := path.Join(stagingRoot, filepath.Base(event.PackageDir))
	command := fmt.Sprintf(
		"rm -rf %s %s && mkdir -p %s %s",
		shellQuote(destination),
		shellQuote(stagingPath),
		shellQuote(parent),
		shellQuote(stagingRoot),
	)
	resetResult, err := m.runner.Run(ctx, "", "docker", "exec", "-u", "0", service, "sh", "-lc", command)
	if err != nil {
		return fmt.Errorf("reset skill destination for %s: %w", event.Skill, err)
	}
	if resetResult.ExitCode != 0 {
		return fmt.Errorf("reset skill destination for %s failed: %s", event.Skill, strings.TrimSpace(resetResult.Stdout))
	}

	copyResult, err := m.runner.Run(ctx, "", "docker", "cp", event.PackageDir, fmt.Sprintf("%s:%s", service, stagingRoot))
	if err != nil {
		return fmt.Errorf("copy skill package for %s: %w", event.Skill, err)
	}
	if copyResult.ExitCode != 0 {
		return fmt.Errorf("copy skill package for %s failed: %s", event.Skill, strings.TrimSpace(copyResult.Stdout))
	}

	moveCommand := fmt.Sprintf(
		"rm -rf %s && mv %s %s && chown -R 1000:1000 %s",
		shellQuote(destination),
		shellQuote(stagingPath),
		shellQuote(destination),
		shellQuote(destination),
	)
	moveResult, err := m.runner.Run(ctx, "", "docker", "exec", "-u", "0", service, "sh", "-lc", moveCommand)
	if err != nil {
		return fmt.Errorf("activate skill package for %s: %w", event.Skill, err)
	}
	if moveResult.ExitCode != 0 {
		return fmt.Errorf("activate skill package for %s failed: %s", event.Skill, strings.TrimSpace(moveResult.Stdout))
	}
	return nil
}

func (m *Manager) installPluginFromGatewayState(ctx context.Context, service string, event deploystate.ReplayEvent) (bool, error) {
	plugin := canonicalRuntimePluginName(event.Plugin)
	if plugin == "" {
		return false, fmt.Errorf("replay event %s is missing plugin name", event.EventID)
	}
	if strings.TrimSpace(event.PackageDir) == "" {
		return false, fmt.Errorf("replay event %s is missing package dir", event.EventID)
	}
	info, err := os.Stat(event.PackageDir)
	if err != nil {
		return false, fmt.Errorf("replay event %s package dir unavailable: %w", event.EventID, err)
	}
	if !info.IsDir() {
		return false, fmt.Errorf("replay event %s package dir %s is not a directory", event.EventID, event.PackageDir)
	}

	expectedPackageDigest := strings.TrimSpace(event.PackageDigest)
	if expectedPackageDigest == "" {
		expectedPackageDigest = strings.TrimSpace(event.Digest)
	}
	if expectedPackageDigest == "" {
		return false, fmt.Errorf("replay event %s is missing plugin package digest", event.EventID)
	}
	actualDigest, err := m.stateStore.DirectoryDigest(event.PackageDir)
	if err != nil {
		return false, fmt.Errorf("replay event %s digest verification failed: %w", event.EventID, err)
	}
	if actualDigest != expectedPackageDigest {
		return false, fmt.Errorf("replay event %s package digest mismatch: got %s want %s", event.EventID, actualDigest, expectedPackageDigest)
	}

	expectedPluginDigest := strings.TrimSpace(event.Digest)
	if expectedPluginDigest == "" {
		expectedPluginDigest = expectedPackageDigest
	}

	currentPlugins, err := m.discoverInstalledPlugins(service)
	if err != nil {
		return false, err
	}
	if currentPlugin, ok := currentPlugins[plugin]; ok && currentPlugin.Digest == expectedPluginDigest {
		return false, nil
	}

	if err := m.installReplayPlugin(ctx, service, event); err != nil {
		return false, err
	}
	updatedPlugins, err := m.discoverInstalledPlugins(service)
	if err != nil {
		return false, err
	}
	updatedPlugin, ok := updatedPlugins[plugin]
	if !ok {
		return false, fmt.Errorf("plugin %q is missing from runtime %s after replay install", plugin, service)
	}
	if updatedPlugin.Digest != expectedPluginDigest {
		return false, fmt.Errorf("plugin %q digest mismatch after replay: got %s want %s", plugin, updatedPlugin.Digest, expectedPluginDigest)
	}
	return true, nil
}

func runtimeService(route *cli.Route) string {
	if route == nil {
		return ""
	}
	service := canonicalServiceName(route.Resource)
	if strings.TrimSpace(route.Runtime) != "" {
		service = canonicalServiceName(route.Runtime)
	}
	return service
}

func cloneRoute(route *cli.Route) *cli.Route {
	if route == nil {
		return &cli.Route{}
	}
	copy := *route
	return &copy
}

func canonicalRuntimeSkillName(name string) string {
	normalized := strings.ToLower(strings.TrimSpace(name))
	if canonical, ok := runtimeSkillAliases[normalized]; ok {
		return canonical
	}
	return normalized
}

func ensureRuntimeStateOwnership(root string) error {
	if stdruntime.GOOS == "windows" {
		return nil
	}
	return filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		return os.Chown(path, 1000, 1000)
	})
}

func latestReplaySkillEvent(log deploystate.ReplayLog, skill string) (int, deploystate.ReplayEvent, bool) {
	for index := len(log.Events) - 1; index >= 0; index-- {
		event := log.Events[index]
		if event.Type != "skill_install" {
			continue
		}
		if strings.EqualFold(strings.TrimSpace(event.Skill), strings.TrimSpace(skill)) {
			return index, event, true
		}
	}
	return -1, deploystate.ReplayEvent{}, false
}

func latestReplayPluginEvent(log deploystate.ReplayLog, plugin string) (int, deploystate.ReplayEvent, bool) {
	for index := len(log.Events) - 1; index >= 0; index-- {
		event := log.Events[index]
		if event.Type != "plugin_install" {
			continue
		}
		if strings.EqualFold(strings.TrimSpace(event.Plugin), strings.TrimSpace(plugin)) {
			return index, event, true
		}
	}
	return -1, deploystate.ReplayEvent{}, false
}

func (m *Manager) effectiveSkillDigest(service, skill string) (string, error) {
	checkpoint, _, err := m.stateStore.LoadCheckpoint(service)
	if err != nil {
		return "", err
	}
	state := checkpointSkillState(checkpoint)
	logSkills, err := m.stateStore.ReplaySkillState(service)
	if err != nil {
		return "", err
	}
	for name, replaySkill := range logSkills {
		state[name] = replaySkill.Digest
	}
	return state[skill], nil
}

func (m *Manager) effectivePluginDigest(service, plugin string) (string, error) {
	checkpoint, _, err := m.stateStore.LoadCheckpoint(service)
	if err != nil {
		return "", err
	}
	state := checkpointPluginState(checkpoint)
	logPlugins, err := m.stateStore.ReplayPluginState(service)
	if err != nil {
		return "", err
	}
	for name, replayPlugin := range logPlugins {
		state[name] = replayPlugin
	}
	return state[plugin].Digest, nil
}

func (m *Manager) baselineSkillDigest(service, skill string) (string, string, error) {
	checkpoint, ok, err := m.stateStore.LoadCheckpoint(service)
	if err != nil {
		return "", "", err
	}
	if !ok {
		return "", "", nil
	}
	for _, checkpointSkill := range checkpoint.Skills {
		if strings.EqualFold(strings.TrimSpace(checkpointSkill.Name), strings.TrimSpace(skill)) {
			return checkpointSkill.Digest, checkpoint.CheckpointID, nil
		}
	}
	return "", checkpoint.CheckpointID, nil
}

func (m *Manager) baselinePluginDigest(service, plugin string) (string, string, error) {
	checkpoint, ok, err := m.stateStore.LoadCheckpoint(service)
	if err != nil {
		return "", "", err
	}
	if !ok {
		return "", "", nil
	}
	for _, checkpointPlugin := range checkpoint.Plugins {
		if strings.EqualFold(strings.TrimSpace(checkpointPlugin.Name), strings.TrimSpace(plugin)) {
			return checkpointPlugin.Digest, checkpoint.CheckpointID, nil
		}
	}
	return "", checkpoint.CheckpointID, nil
}

func (m *Manager) resolveDeployableSkill(requested string) (deployableSkill, string, error) {
	skills, err := m.discoverPureSkills()
	if err != nil {
		return deployableSkill{}, "", err
	}

	canonical := canonicalRuntimeSkillName(requested)
	for _, skill := range skills {
		if strings.EqualFold(skill.Name, canonical) {
			return skill, skill.Name, nil
		}
	}

	names := make([]string, 0, len(skills))
	for _, skill := range skills {
		names = append(names, skill.Name)
	}
	sort.Strings(names)
	if strings.TrimSpace(requested) == "" {
		return deployableSkill{}, "", fmt.Errorf("missing skill name")
	}
	if len(names) == 0 {
		return deployableSkill{}, "", fmt.Errorf("no deployable skills are available in %s", m.config.SkillsRepoRoot())
	}
	return deployableSkill{}, "", fmt.Errorf("unknown deployable skill %q (available: %s)", strings.TrimSpace(requested), strings.Join(names, ", "))
}

func (m *Manager) resolveDeployablePlugin(requested string) (deployablePlugin, string, error) {
	trimmed := strings.TrimSpace(requested)
	if trimmed == "" {
		return deployablePlugin{}, "", fmt.Errorf("missing plugin package")
	}

	if info, err := os.Stat(trimmed); err == nil {
		if !info.IsDir() {
			return deployablePlugin{}, "", fmt.Errorf("local plugin source %q must be a directory", trimmed)
		}
		absolute, absErr := filepath.Abs(trimmed)
		if absErr != nil {
			return deployablePlugin{}, "", fmt.Errorf("resolve plugin source %s: %w", trimmed, absErr)
		}
		plugin, readErr := m.readDeployablePlugin(absolute, "")
		if readErr != nil {
			return deployablePlugin{}, "", readErr
		}
		return plugin, runtimePluginSourceLocal, nil
	}
	if filepath.IsAbs(trimmed) || strings.HasPrefix(trimmed, ".") {
		return deployablePlugin{}, "", fmt.Errorf("plugin source path %q does not exist", trimmed)
	}

	plugins, err := m.discoverDeployablePlugins()
	if err != nil {
		return deployablePlugin{}, "", err
	}

	canonical := canonicalRuntimePluginName(trimmed)
	for _, plugin := range plugins {
		if strings.EqualFold(plugin.Name, canonical) {
			return plugin, runtimePluginSourceGit, nil
		}
	}

	names := make([]string, 0, len(plugins))
	for _, plugin := range plugins {
		names = append(names, plugin.Name)
	}
	sort.Strings(names)
	if len(names) == 0 {
		return deployablePlugin{}, "", fmt.Errorf("no deployable plugins are available in %s", m.config.SkillsRepoRoot())
	}
	return deployablePlugin{}, "", fmt.Errorf("unknown deployable plugin %q (available: %s)", trimmed, strings.Join(names, ", "))
}

func (m *Manager) RuntimeCheckpoint(ctx context.Context, route *cli.Route) (cli.RuntimeCheckpointResult, error) {
	service := runtimeService(route)
	if !isRuntimeService(service) {
		return cli.RuntimeCheckpointResult{}, fmt.Errorf("checkpoint is only supported for runtime services")
	}

	checkpointID := newGatewayID("checkpoint")
	selectedImage := m.selectedRuntimeImage(service)
	if selectedImage == "" {
		selectedImage = defaultRuntimeImage
	}

	snapshotDir := m.stateStore.CheckpointSnapshotDir(service, checkpointID)
	if err := m.captureRuntimeState(ctx, service, snapshotDir); err != nil {
		return cli.RuntimeCheckpointResult{}, err
	}

	checkpointImage, err := m.buildCheckpointImage(ctx, service, checkpointID, selectedImage, snapshotDir)
	if err != nil {
		return cli.RuntimeCheckpointResult{}, err
	}

	previousCheckpoint, hadCheckpoint, err := m.stateStore.LoadCheckpoint(service)
	if err != nil {
		return cli.RuntimeCheckpointResult{}, err
	}
	previousReplayLog, err := m.stateStore.LoadReplayLog(service)
	if err != nil {
		return cli.RuntimeCheckpointResult{}, err
	}

	skills, err := m.currentCheckpointSkills(service)
	if err != nil {
		return cli.RuntimeCheckpointResult{}, err
	}
	plugins, err := m.currentCheckpointPlugins(service)
	if err != nil {
		return cli.RuntimeCheckpointResult{}, err
	}
	metadata := deploystate.CheckpointMetadata{
		Runtime:      service,
		CheckpointID: checkpointID,
		Timestamp:    time.Now().UTC().Format(time.RFC3339),
		Image:        checkpointImage,
		SourceImage:  selectedImage,
		SnapshotDir:  snapshotDir,
		DeploymentID: newGatewayID("deploy"),
		Skills:       skills,
		Plugins:      plugins,
	}
	if err := m.stateStore.SaveCheckpoint(service, metadata); err != nil {
		return cli.RuntimeCheckpointResult{}, err
	}
	if err := m.stateStore.ClearReplayLog(service, checkpointID); err != nil {
		return cli.RuntimeCheckpointResult{}, err
	}

	if _, err := m.DeployService(ctx, route, service); err != nil {
		if hadCheckpoint {
			_ = m.stateStore.SaveCheckpoint(service, previousCheckpoint)
		} else {
			_ = m.stateStore.DeleteCheckpoint(service)
		}
		_ = m.stateStore.SaveReplayLog(service, previousReplayLog)
		return cli.RuntimeCheckpointResult{}, err
	}

	record := deploystate.DeploymentRecord{
		DeploymentID:    metadata.DeploymentID,
		Timestamp:       metadata.Timestamp,
		Actor:           deploymentActor(route),
		Target:          service,
		ArtifactVersion: checkpointImage,
		PreviousVersion: selectedImage,
		Result:          "success",
		Operation:       "runtime_checkpoint",
		Runtime:         service,
		Details: map[string]string{
			"checkpoint_id": checkpointID,
			"snapshot_dir":  snapshotDir,
		},
	}
	if err := m.stateStore.AppendDeployment(record); err != nil {
		return cli.RuntimeCheckpointResult{}, err
	}

	return cli.RuntimeCheckpointResult{
		OK:            true,
		Route:         route,
		Runtime:       service,
		CheckpointID:  checkpointID,
		Image:         checkpointImage,
		SnapshotDir:   snapshotDir,
		ReplayCleared: true,
	}, nil
}

func (m *Manager) captureRuntimeState(ctx context.Context, service, snapshotDir string) error {
	if err := os.RemoveAll(snapshotDir); err != nil {
		return fmt.Errorf("reset checkpoint snapshot dir for %s: %w", service, err)
	}
	if err := os.MkdirAll(snapshotDir, 0o755); err != nil {
		return fmt.Errorf("create checkpoint snapshot dir for %s: %w", service, err)
	}

	stateRoot := m.runtimeOpenClawStateRoot(ctx, service)
	result, err := m.runner.Run(ctx, "", "docker", "cp", fmt.Sprintf("%s:%s/.", service, stateRoot), snapshotDir)
	if err != nil {
		return fmt.Errorf("capture runtime state for %s: %w", service, err)
	}
	if result.ExitCode != 0 {
		return fmt.Errorf("capture runtime state for %s failed: %s", service, strings.TrimSpace(result.Stdout))
	}
	return nil
}

func (m *Manager) buildCheckpointImage(ctx context.Context, service, checkpointID, baseImage, snapshotDir string) (string, error) {
	buildDir := m.stateStore.CheckpointBuildContextDir(service, checkpointID)
	if err := os.RemoveAll(buildDir); err != nil {
		return "", fmt.Errorf("reset checkpoint build dir for %s: %w", service, err)
	}
	if err := os.MkdirAll(buildDir, 0o755); err != nil {
		return "", fmt.Errorf("create checkpoint build dir for %s: %w", service, err)
	}

	stateDir := filepath.Join(buildDir, "runtime-state")
	if err := copyTree(snapshotDir, stateDir); err != nil {
		return "", fmt.Errorf("stage checkpoint snapshot for %s: %w", service, err)
	}
	dockerfile := strings.Join([]string{
		"ARG BASE_IMAGE",
		"FROM ${BASE_IMAGE}",
		"ARG RUNTIME_NAME",
		"ARG CHECKPOINT_ID",
		"LABEL org.remram.runtime=${RUNTIME_NAME}",
		"LABEL org.remram.checkpoint=${CHECKPOINT_ID}",
		"COPY runtime-state /opt/moltbox/runtime-baseline/${RUNTIME_NAME}",
	}, "\n") + "\n"
	if err := os.WriteFile(filepath.Join(buildDir, "Dockerfile"), []byte(dockerfile), 0o644); err != nil {
		return "", fmt.Errorf("write checkpoint Dockerfile for %s: %w", service, err)
	}

	image := fmt.Sprintf("moltbox-runtime:%s-%s", service, checkpointID)
	result, err := m.runner.Run(ctx, buildDir, "docker", "build", "-t", image, "--build-arg", "BASE_IMAGE="+baseImage, "--build-arg", "RUNTIME_NAME="+service, "--build-arg", "CHECKPOINT_ID="+checkpointID, buildDir)
	if err != nil {
		return "", fmt.Errorf("build checkpoint image for %s: %w", service, err)
	}
	if result.ExitCode != 0 {
		return "", fmt.Errorf("build checkpoint image for %s failed: %s", service, strings.TrimSpace(result.Stdout))
	}
	return image, nil
}

func (m *Manager) currentCheckpointSkills(service string) ([]deploystate.CheckpointSkill, error) {
	checkpoint, _, err := m.stateStore.LoadCheckpoint(service)
	if err != nil {
		return nil, err
	}
	state := checkpointSkillState(checkpoint)
	logSkills, err := m.stateStore.ReplaySkillState(service)
	if err != nil {
		return nil, err
	}
	for name, skill := range logSkills {
		state[name] = skill.Digest
	}

	names := make([]string, 0, len(state))
	for name := range state {
		names = append(names, name)
	}
	sort.Strings(names)

	skills := make([]deploystate.CheckpointSkill, 0, len(names))
	for _, name := range names {
		skills = append(skills, deploystate.CheckpointSkill{Name: name, Digest: state[name]})
	}
	return skills, nil
}

func (m *Manager) currentCheckpointPlugins(service string) ([]deploystate.CheckpointPlugin, error) {
	checkpoint, _, err := m.stateStore.LoadCheckpoint(service)
	if err != nil {
		return nil, err
	}
	state := checkpointPluginState(checkpoint)
	logPlugins, err := m.stateStore.ReplayPluginState(service)
	if err != nil {
		return nil, err
	}
	for name, plugin := range logPlugins {
		state[name] = plugin
	}

	names := make([]string, 0, len(state))
	for name := range state {
		names = append(names, name)
	}
	sort.Strings(names)

	plugins := make([]deploystate.CheckpointPlugin, 0, len(names))
	for _, name := range names {
		plugins = append(plugins, state[name])
	}
	return plugins, nil
}

func (m *Manager) selectedRuntimeImage(service string) string {
	if !isRuntimeService(service) {
		return ""
	}
	checkpoint, ok, err := m.stateStore.LoadCheckpoint(service)
	if err != nil || !ok {
		return ""
	}
	return strings.TrimSpace(checkpoint.Image)
}

func (m *Manager) recordServiceDeployment(route *cli.Route, service, result string) error {
	record := deploystate.DeploymentRecord{
		DeploymentID:    newGatewayID("deploy"),
		Timestamp:       time.Now().UTC().Format(time.RFC3339),
		Actor:           deploymentActor(route),
		Target:          service,
		ArtifactVersion: serviceArtifactVersion(service, m.selectedRuntimeImage(service)),
		Result:          result,
		Operation:       "service_deploy",
		Runtime: func() string {
			if isRuntimeService(service) {
				return service
			}
			return ""
		}(),
	}
	return m.stateStore.AppendDeployment(record)
}

func (m *Manager) discoverPureSkills() ([]deployableSkill, error) {
	skillsRoot := filepath.Join(m.config.SkillsRepoRoot(), "skills")
	if strings.TrimSpace(m.config.SkillsRepoRoot()) == "" {
		return nil, nil
	}

	entries, err := os.ReadDir(skillsRoot)
	if err != nil {
		if errorsIsNotExist(err) {
			return nil, nil
		}
		return nil, fmt.Errorf("read skills repo: %w", err)
	}

	skills := make([]deployableSkill, 0, len(entries))
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}

		sourceDir := filepath.Join(skillsRoot, entry.Name())
		skillFile := filepath.Join(sourceDir, "SKILL.md")
		if info, err := os.Stat(skillFile); err != nil || info.IsDir() {
			if err == nil || errorsIsNotExist(err) {
				continue
			}
			return nil, fmt.Errorf("stat skill %s: %w", entry.Name(), err)
		}
		// Managed skill deploy only stages pure skill packages. Plugin-backed packages
		// are handled by the parallel runtime plugin deploy contract.
		if _, err := os.Stat(filepath.Join(sourceDir, "openclaw.plugin.json")); err == nil {
			continue
		} else if err != nil && !errorsIsNotExist(err) {
			return nil, fmt.Errorf("stat plugin manifest for %s: %w", entry.Name(), err)
		}

		digest, err := m.stateStore.DirectoryDigest(sourceDir)
		if err != nil {
			return nil, fmt.Errorf("digest skill %s: %w", entry.Name(), err)
		}
		skills = append(skills, deployableSkill{
			Name:      entry.Name(),
			SourceDir: sourceDir,
			Digest:    digest,
		})
	}

	sort.Slice(skills, func(i, j int) bool {
		return skills[i].Name < skills[j].Name
	})
	return skills, nil
}

func (m *Manager) discoverDeployablePlugins() ([]deployablePlugin, error) {
	skillsRoot := filepath.Join(m.config.SkillsRepoRoot(), "skills")
	if strings.TrimSpace(m.config.SkillsRepoRoot()) == "" {
		return nil, nil
	}

	entries, err := os.ReadDir(skillsRoot)
	if err != nil {
		if errorsIsNotExist(err) {
			return nil, nil
		}
		return nil, fmt.Errorf("read skills repo: %w", err)
	}

	plugins := make([]deployablePlugin, 0, len(entries))
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}

		sourceDir := filepath.Join(skillsRoot, entry.Name())
		if _, err := os.Stat(filepath.Join(sourceDir, "openclaw.plugin.json")); err != nil {
			if err == nil || errorsIsNotExist(err) {
				continue
			}
			return nil, fmt.Errorf("stat plugin manifest for %s: %w", entry.Name(), err)
		}

		plugin, err := m.readDeployablePlugin(sourceDir, entry.Name())
		if err != nil {
			return nil, err
		}
		plugins = append(plugins, plugin)
	}

	sort.Slice(plugins, func(i, j int) bool {
		return plugins[i].Name < plugins[j].Name
	})
	return plugins, nil
}

func orderedRuntimeReplayEvents(events []deploystate.ReplayEvent) []deploystate.ReplayEvent {
	ordered := make([]deploystate.ReplayEvent, 0, len(events))
	for _, event := range events {
		if event.Type == "plugin_install" {
			ordered = append(ordered, event)
		}
	}
	for _, event := range events {
		if event.Type != "plugin_install" {
			ordered = append(ordered, event)
		}
	}
	return ordered
}

func canonicalRuntimePluginName(name string) string {
	return strings.ToLower(strings.TrimSpace(name))
}

func (m *Manager) runtimeOpenClawStateRoot(ctx context.Context, service string) string {
	if workspaceRoot := strings.TrimSpace(m.runtimeOpenClawWorkspaceRoot(service)); workspaceRoot != "" {
		return path.Clean(workspaceRoot)
	}
	if configRoot := strings.TrimSpace(m.runtimeOpenClawConfigDir(ctx, service)); configRoot != "" {
		return path.Clean(configRoot)
	}
	return defaultOpenClawStateRoot
}

func (m *Manager) runtimeOpenClawConfigDir(ctx context.Context, service string) string {
	if m.inspector != nil {
		if info, err := m.inspector.InspectContainer(ctx, service); err == nil {
			if value := containerEnvValue(info.Config.Env, openClawConfigEnvName); value != "" {
				return value
			}
		}
	}
	return ""
}

func (m *Manager) runtimeOpenClawWorkspaceRoot(service string) string {
	configPath := filepath.Join(m.config.ServiceStateDir(service), "config", service, "openclaw.json")
	data, err := os.ReadFile(configPath)
	if err != nil {
		return ""
	}
	var cfg runtimeOpenClawConfig
	if err := json.Unmarshal(data, &cfg); err != nil {
		return ""
	}
	workspace := strings.TrimSpace(cfg.Agents.Defaults.Workspace)
	if workspace == "" {
		return ""
	}
	if path.IsAbs(workspace) {
		return path.Dir(workspace)
	}
	configRoot := strings.TrimSpace(m.runtimeOpenClawConfigDir(context.Background(), service))
	if configRoot == "" {
		configRoot = defaultOpenClawStateRoot
	}
	return path.Clean(path.Join(configRoot, path.Dir(workspace)))
}

func containerEnvValue(env []string, key string) string {
	prefix := key + "="
	for _, entry := range env {
		if strings.HasPrefix(entry, prefix) {
			return strings.TrimSpace(strings.TrimPrefix(entry, prefix))
		}
	}
	return ""
}

func (m *Manager) snapshotRuntimeComponent(service, token string) (string, error) {
	root := filepath.Join(m.config.Paths.StateRoot, "tmp", "runtime-plugin")
	if err := os.MkdirAll(root, 0o755); err != nil {
		return "", fmt.Errorf("create runtime plugin temp root: %w", err)
	}

	backupDir := filepath.Join(root, token)
	if err := os.RemoveAll(backupDir); err != nil {
		return "", fmt.Errorf("reset runtime plugin backup dir: %w", err)
	}
	if err := os.MkdirAll(backupDir, 0o755); err != nil {
		return "", fmt.Errorf("create runtime plugin backup dir: %w", err)
	}

	runtimeRoot := m.config.RuntimeComponentDir(service)
	info, err := os.Stat(runtimeRoot)
	switch {
	case err == nil && info.IsDir():
		if err := copyTree(runtimeRoot, backupDir); err != nil {
			return "", fmt.Errorf("backup runtime state for %s: %w", service, err)
		}
	case err == nil:
		return "", fmt.Errorf("runtime state root %s is not a directory", runtimeRoot)
	case !errorsIsNotExist(err):
		return "", fmt.Errorf("stat runtime state root %s: %w", runtimeRoot, err)
	}
	return backupDir, nil
}

func (m *Manager) restoreRuntimeComponent(service, backupDir string) error {
	destination := m.config.RuntimeComponentDir(service)
	if err := os.MkdirAll(destination, 0o755); err != nil {
		return fmt.Errorf("ensure runtime state dir for %s: %w", service, err)
	}
	entries, err := os.ReadDir(destination)
	if err != nil {
		return fmt.Errorf("read runtime state dir for %s: %w", service, err)
	}
	for _, entry := range entries {
		if err := os.RemoveAll(filepath.Join(destination, entry.Name())); err != nil {
			return fmt.Errorf("clear runtime state for %s: %w", service, err)
		}
	}
	if err := copyTree(backupDir, destination); err != nil {
		return fmt.Errorf("restore runtime state for %s: %w", service, err)
	}
	if err := ensureRuntimeStateOwnership(destination); err != nil {
		return fmt.Errorf("set runtime state ownership for %s: %w", service, err)
	}
	return nil
}

func (m *Manager) discoverInstalledPlugins(service string) (map[string]runtimePluginState, error) {
	pluginsRoot := filepath.Join(m.config.RuntimeComponentDir(service), "extensions")
	entries, err := os.ReadDir(pluginsRoot)
	if err != nil {
		if errorsIsNotExist(err) {
			return map[string]runtimePluginState{}, nil
		}
		return nil, fmt.Errorf("read installed plugins for %s: %w", service, err)
	}

	plugins := make(map[string]runtimePluginState, len(entries))
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		plugin, err := m.readInstalledPlugin(filepath.Join(pluginsRoot, entry.Name()))
		if err != nil {
			return nil, err
		}
		plugins[plugin.Name] = plugin
	}
	return plugins, nil
}

func (m *Manager) readInstalledPlugin(root string) (runtimePluginState, error) {
	digest, err := m.stateStore.DirectoryDigest(root)
	if err != nil {
		return runtimePluginState{}, fmt.Errorf("digest installed plugin %s: %w", root, err)
	}

	manifestID := readRuntimePluginManifestID(root)
	packageName, version := readRuntimePluginPackageInfo(root)
	name := canonicalRuntimePluginName(manifestID)
	if name == "" {
		name = canonicalRuntimePluginName(filepath.Base(root))
	}
	if name == "" {
		name = canonicalRuntimePluginName(packageName)
	}
	if name == "" {
		return runtimePluginState{}, fmt.Errorf("unable to determine plugin id for %s", root)
	}
	if strings.TrimSpace(packageName) == "" {
		packageName = name
	}

	return runtimePluginState{
		Name:    name,
		Package: strings.TrimSpace(packageName),
		Version: strings.TrimSpace(version),
		Digest:  digest,
		HostDir: root,
	}, nil
}

func readRuntimePluginManifestID(root string) string {
	manifestPath := filepath.Join(root, "openclaw.plugin.json")
	data, err := os.ReadFile(manifestPath)
	if err != nil {
		return ""
	}
	var manifest runtimePluginManifest
	if err := json.Unmarshal(data, &manifest); err != nil {
		return ""
	}
	return strings.TrimSpace(manifest.ID)
}

func readRuntimePluginPackageInfo(root string) (string, string) {
	packagePath := filepath.Join(root, "package.json")
	data, err := os.ReadFile(packagePath)
	if err != nil {
		return "", ""
	}
	var pkg runtimePluginPackage
	if err := json.Unmarshal(data, &pkg); err != nil {
		return "", ""
	}
	return strings.TrimSpace(pkg.Name), strings.TrimSpace(pkg.Version)
}

func (m *Manager) prepareRuntimePluginPackage(ctx context.Context, packageDir string) (string, error) {
	matches, err := filepath.Glob(filepath.Join(packageDir, "*.tgz"))
	if err != nil {
		return "", fmt.Errorf("scan packed plugin artifacts in %s: %w", packageDir, err)
	}
	for _, match := range matches {
		if err := os.Remove(match); err != nil {
			return "", fmt.Errorf("remove stale packed plugin artifact %s: %w", match, err)
		}
	}

	result, err := m.runner.Run(ctx, "", "docker", "run", "--rm", "-v", fmt.Sprintf("%s:/src", packageDir), "-w", "/src", pluginPackBuilderImage, "sh", "-lc", "npm pack --quiet")
	if err != nil {
		return "", fmt.Errorf("pack plugin package %s: %w", packageDir, err)
	}
	if result.ExitCode != 0 {
		return "", fmt.Errorf("pack plugin package %s failed: %s", packageDir, strings.TrimSpace(result.Stdout))
	}

	if artifact := strings.TrimSpace(lastMatchingOutputLine(result.Stdout, ".tgz")); artifact != "" {
		return filepath.Join(packageDir, artifact), nil
	}
	matches, err = filepath.Glob(filepath.Join(packageDir, "*.tgz"))
	if err != nil {
		return "", fmt.Errorf("scan packed plugin artifacts in %s: %w", packageDir, err)
	}
	if len(matches) == 1 {
		return matches[0], nil
	}
	if len(matches) > 1 {
		sort.Strings(matches)
		return matches[len(matches)-1], nil
	}
	return "", fmt.Errorf("pack plugin package %s did not produce a .tgz artifact", packageDir)
}

func (m *Manager) installRuntimePlugin(ctx context.Context, service, eventID, requested string, deployable deployablePlugin, source, stagedPackageDir, installSourcePath string, beforePlugins map[string]runtimePluginState) (runtimePluginState, error) {
	if strings.TrimSpace(stagedPackageDir) == "" {
		return runtimePluginState{}, fmt.Errorf("missing staged plugin package for %s", requested)
	}
	if strings.TrimSpace(installSourcePath) == "" {
		installSourcePath = stagedPackageDir
	}

	installSpec, cleanup, err := m.stagePluginSourceInRuntime(ctx, service, eventID, installSourcePath)
	if err != nil {
		return runtimePluginState{}, err
	}
	defer cleanup()

	if err := m.installRuntimePluginSpec(ctx, service, installSpec, false); err != nil {
		return runtimePluginState{}, err
	}

	afterPlugins, err := m.discoverInstalledPlugins(service)
	if err != nil {
		return runtimePluginState{}, err
	}
	plugin, ok := afterPlugins[deployable.Name]
	if !ok {
		plugin, err = identifyInstalledPlugin(requested, source, stagedPackageDir, beforePlugins, afterPlugins)
		if err != nil {
			return runtimePluginState{}, err
		}
	}
	if err := m.restartRuntimeContainer(ctx, service); err != nil {
		return runtimePluginState{}, err
	}
	if err := m.verifyRuntimePluginPresence(ctx, service, plugin.Name); err != nil {
		return runtimePluginState{}, err
	}

	plugin.Source = source
	if strings.TrimSpace(plugin.Package) == "" {
		plugin.Package = deployable.Package
	}
	if strings.TrimSpace(plugin.Version) == "" {
		plugin.Version = deployable.Version
	}
	return plugin, nil
}

func (m *Manager) installReplayPlugin(ctx context.Context, service string, event deploystate.ReplayEvent) error {
	source := strings.TrimSpace(event.Source)
	if source == "" {
		switch {
		case strings.TrimSpace(event.SourcePath) != "":
			source = runtimePluginSourceLocal
		case strings.TrimSpace(event.PackageDir) != "":
			source = runtimePluginSourceGit
		default:
			source = runtimePluginSourceNPM
		}
	}

	installSpec := strings.TrimSpace(event.Package)
	cleanup := func() {}
	switch source {
	case runtimePluginSourceNPM:
		if installSpec == "" {
			return fmt.Errorf("replay event %s is missing plugin package", event.EventID)
		}
	case runtimePluginSourceGit:
		if strings.TrimSpace(event.PackageDir) == "" {
			return fmt.Errorf("replay event %s is missing staged plugin package", event.EventID)
		}
		installSourcePath, packErr := m.prepareRuntimePluginPackage(ctx, event.PackageDir)
		if packErr != nil {
			return packErr
		}
		var err error
		installSpec, cleanup, err = m.stagePluginSourceInRuntime(ctx, service, event.EventID, installSourcePath)
		if err != nil {
			return err
		}
	case runtimePluginSourceLocal:
		sourcePath := strings.TrimSpace(event.SourcePath)
		if sourcePath == "" {
			sourcePath = strings.TrimSpace(event.PackageDir)
		}
		if sourcePath == "" {
			return fmt.Errorf("replay event %s is missing staged local source", event.EventID)
		}
		installSourcePath := sourcePath
		if strings.TrimSpace(event.PackageDir) != "" {
			if packedPath, packErr := m.prepareRuntimePluginPackage(ctx, event.PackageDir); packErr == nil {
				installSourcePath = packedPath
			}
		}
		var err error
		installSpec, cleanup, err = m.stagePluginSourceInRuntime(ctx, service, event.EventID, installSourcePath)
		if err != nil {
			return err
		}
	default:
		return fmt.Errorf("replay event %s uses unsupported plugin source %q", event.EventID, source)
	}
	defer cleanup()

	return m.installRuntimePluginSpec(ctx, service, installSpec, false)
}

func (m *Manager) installRuntimePluginSpec(ctx context.Context, service, installSpec string, pin bool) error {
	args := []string{"plugins", "install", installSpec}
	if pin {
		args = append(args, "--pin")
	}
	result, err := m.runRuntimeOpenClaw(ctx, service, args...)
	if err != nil {
		return err
	}
	if !result.OK {
		return fmt.Errorf("install plugin %q in %s failed: %s", installSpec, service, commandFailureSummary(result))
	}
	return nil
}

func (m *Manager) stagePluginSourceInRuntime(ctx context.Context, service, token, hostSourcePath string) (string, func(), error) {
	// Stage plugin source under /tmp because the runtime state bind mount may not
	// allow creating new temporary subdirectories reliably inside ~/.openclaw.
	stagingRoot := filepath.ToSlash(filepath.Join("/tmp/moltbox-plugin-source", token))
	prepareCommand := fmt.Sprintf("rm -rf %s && mkdir -p %s", shellQuote(stagingRoot), shellQuote(stagingRoot))
	prepareResult, err := m.runner.Run(ctx, "", "docker", "exec", "-u", "0", service, "sh", "-lc", prepareCommand)
	if err != nil {
		return "", nil, fmt.Errorf("prepare plugin staging for %s: %w", service, err)
	}
	if prepareResult.ExitCode != 0 {
		return "", nil, fmt.Errorf("prepare plugin staging for %s failed: %s", service, strings.TrimSpace(prepareResult.Stdout))
	}

	copyResult, err := m.runner.Run(ctx, "", "docker", "cp", hostSourcePath, fmt.Sprintf("%s:%s", service, stagingRoot))
	if err != nil {
		return "", nil, fmt.Errorf("copy plugin source into %s: %w", service, err)
	}
	if copyResult.ExitCode != 0 {
		return "", nil, fmt.Errorf("copy plugin source into %s failed: %s", service, strings.TrimSpace(copyResult.Stdout))
	}

	installSpec := filepath.ToSlash(filepath.Join(stagingRoot, filepath.Base(hostSourcePath)))
	cleanup := func() {
		cleanupCommand := fmt.Sprintf("rm -rf %s", shellQuote(stagingRoot))
		_, _ = m.runner.Run(context.Background(), "", "docker", "exec", "-u", "0", service, "sh", "-lc", cleanupCommand)
	}
	return installSpec, cleanup, nil
}

func (m *Manager) verifyRuntimePluginPresence(ctx context.Context, service, plugin string) error {
	result, err := m.runRuntimeOpenClaw(ctx, service, "plugins", "info", plugin)
	if err != nil {
		return err
	}
	if !result.OK {
		return fmt.Errorf("verify plugin %q in %s failed: %s", plugin, service, commandFailureSummary(result))
	}
	return nil
}

func (m *Manager) runRuntimeOpenClaw(ctx context.Context, service string, nativeArgs ...string) (cli.CommandResult, error) {
	return m.RuntimeOpenClaw(ctx, &cli.Route{
		Resource:   service,
		Kind:       cli.KindRuntimeNative,
		Action:     "openclaw",
		Runtime:    service,
		NativeArgs: nativeArgs,
	})
}

func commandFailureSummary(result cli.CommandResult) string {
	if text := strings.TrimSpace(result.Stdout); text != "" {
		return text
	}
	if text := strings.TrimSpace(result.Stderr); text != "" {
		return text
	}
	return fmt.Sprintf("exit code %d", result.ExitCode)
}

func lastMatchingOutputLine(output, suffix string) string {
	lines := strings.Split(output, "\n")
	for index := len(lines) - 1; index >= 0; index-- {
		line := strings.TrimSpace(lines[index])
		if line == "" {
			continue
		}
		if strings.HasSuffix(line, suffix) {
			return line
		}
	}
	return ""
}

func identifyInstalledPlugin(requested, source, sourcePath string, beforePlugins, afterPlugins map[string]runtimePluginState) (runtimePluginState, error) {
	changed := make([]runtimePluginState, 0, len(afterPlugins))
	for name, plugin := range afterPlugins {
		previous, ok := beforePlugins[name]
		if !ok || previous.Digest != plugin.Digest {
			changed = append(changed, plugin)
		}
	}
	sort.Slice(changed, func(i, j int) bool {
		return changed[i].Name < changed[j].Name
	})

	if predicted := predictedRuntimePluginName(requested, source, sourcePath); predicted != "" {
		if plugin, ok := afterPlugins[predicted]; ok {
			return plugin, nil
		}
	}

	requestedPackage := strings.TrimSpace(npmPackageNameFromSpec(requested))
	if requestedPackage != "" {
		for _, plugin := range changed {
			if strings.EqualFold(strings.TrimSpace(plugin.Package), requestedPackage) {
				return plugin, nil
			}
		}
		for _, plugin := range afterPlugins {
			if strings.EqualFold(strings.TrimSpace(plugin.Package), requestedPackage) {
				return plugin, nil
			}
		}
	}

	if len(changed) == 1 {
		return changed[0], nil
	}
	if len(changed) == 0 {
		return runtimePluginState{}, fmt.Errorf("plugin install for %q completed but gateway could not determine which plugin was installed", requested)
	}

	names := make([]string, 0, len(changed))
	for _, plugin := range changed {
		names = append(names, plugin.Name)
	}
	return runtimePluginState{}, fmt.Errorf("plugin install for %q changed multiple plugins: %s", requested, strings.Join(names, ", "))
}

func predictedRuntimePluginName(requested, source, sourcePath string) string {
	if source == runtimePluginSourceLocal || source == runtimePluginSourceGit {
		if id := readRuntimePluginManifestID(sourcePath); id != "" {
			return canonicalRuntimePluginName(id)
		}
		if packageName, _ := readRuntimePluginPackageInfo(sourcePath); packageName != "" {
			return canonicalRuntimePluginName(packageName)
		}
		base := strings.TrimSuffix(filepath.Base(sourcePath), filepath.Ext(sourcePath))
		return canonicalRuntimePluginName(base)
	}
	return canonicalRuntimePluginName(npmPackageNameFromSpec(requested))
}

func (m *Manager) readDeployablePlugin(sourceDir, fallbackName string) (deployablePlugin, error) {
	digest, err := m.stateStore.DirectoryDigest(sourceDir)
	if err != nil {
		return deployablePlugin{}, fmt.Errorf("digest plugin %s: %w", filepath.Base(sourceDir), err)
	}

	name := canonicalRuntimePluginName(readRuntimePluginManifestID(sourceDir))
	packageName, version := readRuntimePluginPackageInfo(sourceDir)
	if name == "" {
		name = canonicalRuntimePluginName(packageName)
	}
	if name == "" {
		name = canonicalRuntimePluginName(fallbackName)
	}
	if name == "" {
		return deployablePlugin{}, fmt.Errorf("unable to determine plugin id for %s", sourceDir)
	}
	if strings.TrimSpace(packageName) == "" {
		packageName = name
	}

	return deployablePlugin{
		Name:      name,
		Package:   strings.TrimSpace(packageName),
		Version:   strings.TrimSpace(version),
		SourceDir: sourceDir,
		Digest:    digest,
	}, nil
}

func npmPackageNameFromSpec(spec string) string {
	trimmed := strings.TrimSpace(spec)
	if trimmed == "" {
		return ""
	}
	if strings.HasPrefix(trimmed, "@") {
		if index := strings.LastIndex(trimmed, "@"); index > 0 {
			return trimmed[:index]
		}
		return trimmed
	}
	if index := strings.Index(trimmed, "@"); index > 0 {
		return trimmed[:index]
	}
	return trimmed
}

func (m *Manager) restartRuntimeContainer(ctx context.Context, service string) error {
	result, err := m.runner.Run(ctx, "", "docker", "restart", service)
	if err != nil {
		return fmt.Errorf("restart runtime %s: %w", service, err)
	}
	if result.ExitCode != 0 {
		return fmt.Errorf("restart runtime %s failed: %s", service, strings.TrimSpace(result.Stdout))
	}
	_, err = m.waitForContainers(ctx, []string{service}, 2*time.Minute)
	return err
}

func checkpointSkillState(checkpoint deploystate.CheckpointMetadata) map[string]string {
	state := make(map[string]string, len(checkpoint.Skills))
	for _, skill := range checkpoint.Skills {
		if strings.TrimSpace(skill.Name) == "" {
			continue
		}
		state[skill.Name] = skill.Digest
	}
	return state
}

func checkpointPluginState(checkpoint deploystate.CheckpointMetadata) map[string]deploystate.CheckpointPlugin {
	state := make(map[string]deploystate.CheckpointPlugin, len(checkpoint.Plugins))
	for _, plugin := range checkpoint.Plugins {
		if strings.TrimSpace(plugin.Name) == "" {
			continue
		}
		state[plugin.Name] = plugin
	}
	return state
}

func serviceArtifactVersion(service, image string) string {
	if isRuntimeService(service) {
		if strings.TrimSpace(image) != "" {
			return image
		}
		return defaultRuntimeImage
	}
	return service + ":latest"
}

func deploymentActor(route *cli.Route) string {
	if route == nil {
		return "gateway"
	}
	if strings.TrimSpace(route.Resource) == "" {
		return "gateway"
	}
	return route.Resource
}

func newGatewayID(prefix string) string {
	return fmt.Sprintf("%s-%d", prefix, time.Now().UTC().UnixNano())
}
