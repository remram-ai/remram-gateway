package deploystate

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestStoreWritesGatewayStateWithoutLeavingTemps(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	store := New(root)

	if err := store.AppendDeployment(DeploymentRecord{
		DeploymentID:    "deploy-1",
		Timestamp:       "2026-03-14T20:00:00Z",
		Actor:           "dev",
		Target:          "openclaw-dev",
		ArtifactVersion: "v1",
		Result:          "success",
		Operation:       "service_deploy",
		Runtime:         "openclaw-dev",
	}); err != nil {
		t.Fatalf("AppendDeployment() error = %v", err)
	}
	if err := store.AppendDeployment(DeploymentRecord{
		DeploymentID:    "deploy-2",
		Timestamp:       "2026-03-14T20:01:00Z",
		Actor:           "dev",
		Target:          "openclaw-dev/skill/together-escalation",
		ArtifactVersion: "digest-1",
		Result:          "success",
		Operation:       "runtime_skill_deploy",
		Runtime:         "openclaw-dev",
	}); err != nil {
		t.Fatalf("AppendDeployment() second error = %v", err)
	}

	if err := store.SaveReplayLog("openclaw-dev", ReplayLog{
		Runtime:            "openclaw-dev",
		BaselineCheckpoint: "checkpoint-1",
		Events: []ReplayEvent{
			{
				EventID:      "event-plugin-1",
				DeploymentID: "deploy-plugin-1",
				Timestamp:    "2026-03-14T20:00:30Z",
				Runtime:      "openclaw-dev",
				Type:         "plugin_install",
				Plugin:       "semantic-router",
				Package:      "semantic-router@1.2.0",
				Version:      "1.2.0",
				Digest:       "sha256:plugin-digest-1",
				Source:       "npm",
				PackageDir:   "/srv/moltbox-state/deploy/runtime/openclaw-dev/packages/event-plugin-1",
			},
			{
				EventID:       "event-1",
				DeploymentID:  "deploy-2",
				Timestamp:     "2026-03-14T20:01:00Z",
				Runtime:       "openclaw-dev",
				Type:          "skill_install",
				Skill:         "together-escalation",
				PackageDir:    "/srv/moltbox-state/deploy/runtime/openclaw-dev/packages/event-1",
				PackageDigest: "digest-1",
			},
		},
	}); err != nil {
		t.Fatalf("SaveReplayLog() error = %v", err)
	}

	if err := store.SaveCheckpoint("openclaw-dev", CheckpointMetadata{
		Runtime:      "openclaw-dev",
		CheckpointID: "checkpoint-1",
		Timestamp:    "2026-03-14T20:02:00Z",
		Image:        "moltbox-runtime:openclaw-dev-checkpoint-1",
		SnapshotDir:  "/srv/moltbox-state/runtime-baselines/openclaw-dev/checkpoint-1/snapshot",
		DeploymentID: "deploy-3",
		Plugins: []CheckpointPlugin{
			{Name: "semantic-router", Package: "semantic-router@1.2.0", Version: "1.2.0", Digest: "sha256:plugin-digest-1", Source: "npm"},
		},
		Skills: []CheckpointSkill{
			{Name: "together-escalation", Digest: "digest-1"},
		},
	}); err != nil {
		t.Fatalf("SaveCheckpoint() error = %v", err)
	}

	history, err := store.ReadDeploymentHistory()
	if err != nil {
		t.Fatalf("ReadDeploymentHistory() error = %v", err)
	}
	if len(history) != 2 {
		t.Fatalf("deployment history len = %d, want 2", len(history))
	}

	log, err := store.LoadReplayLog("openclaw-dev")
	if err != nil {
		t.Fatalf("LoadReplayLog() error = %v", err)
	}
	if len(log.Events) != 2 {
		t.Fatalf("replay log = %#v, want two events", log.Events)
	}

	checkpoint, ok, err := store.LoadCheckpoint("openclaw-dev")
	if err != nil {
		t.Fatalf("LoadCheckpoint() error = %v", err)
	}
	if !ok || checkpoint.CheckpointID != "checkpoint-1" {
		t.Fatalf("checkpoint = %#v, ok=%v, want checkpoint-1", checkpoint, ok)
	}
	if len(checkpoint.Plugins) != 1 || checkpoint.Plugins[0].Name != "semantic-router" {
		t.Fatalf("checkpoint plugins = %#v, want semantic-router", checkpoint.Plugins)
	}

	plugins, err := store.ReplayPluginState("openclaw-dev")
	if err != nil {
		t.Fatalf("ReplayPluginState() error = %v", err)
	}
	if plugin := plugins["semantic-router"]; plugin.Digest != "sha256:plugin-digest-1" {
		t.Fatalf("replay plugin state = %#v, want semantic-router digest", plugins)
	}

	err = filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		name := info.Name()
		if strings.HasSuffix(name, ".lock") || strings.Contains(name, ".tmp-") {
			t.Fatalf("unexpected temp or lock file left behind: %s", path)
		}
		return nil
	})
	if err != nil {
		t.Fatalf("Walk() error = %v", err)
	}
}

func TestReplayPluginStateAppliesRemoveTombstones(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	store := New(root)

	if err := store.SaveReplayLog("openclaw-dev", ReplayLog{
		Runtime: "openclaw-dev",
		Events: []ReplayEvent{
			{
				EventID:    "event-plugin-install",
				Runtime:    "openclaw-dev",
				Type:       "plugin_install",
				Plugin:     "semantic-router",
				Package:    "semantic-router",
				Version:    "1.2.0",
				Digest:     "sha256:plugin-digest-1",
				Source:     "git",
				PackageDir: "/srv/moltbox-state/deploy/runtime/openclaw-dev/packages/event-plugin-install",
			},
			{
				EventID: "event-plugin-remove",
				Runtime: "openclaw-dev",
				Type:    "plugin_remove",
				Plugin:  "semantic-router",
			},
		},
	}); err != nil {
		t.Fatalf("SaveReplayLog() error = %v", err)
	}

	plugins, err := store.ReplayPluginState("openclaw-dev")
	if err != nil {
		t.Fatalf("ReplayPluginState() error = %v", err)
	}
	if len(plugins) != 0 {
		t.Fatalf("replay plugin state = %#v, want empty after plugin_remove", plugins)
	}
}
