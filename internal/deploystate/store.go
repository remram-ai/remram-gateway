package deploystate

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"time"
)

type Store struct {
	stateRoot string
}

type DeploymentRecord struct {
	DeploymentID    string            `json:"deployment_id"`
	Timestamp       string            `json:"timestamp"`
	Actor           string            `json:"actor"`
	Target          string            `json:"target"`
	ArtifactVersion string            `json:"artifact_version"`
	PreviousVersion string            `json:"previous_version,omitempty"`
	Result          string            `json:"result"`
	Operation       string            `json:"operation,omitempty"`
	Runtime         string            `json:"runtime,omitempty"`
	Details         map[string]string `json:"details,omitempty"`
}

type ReplayEvent struct {
	EventID       string            `json:"event_id"`
	DeploymentID  string            `json:"deployment_id"`
	Timestamp     string            `json:"timestamp"`
	Runtime       string            `json:"runtime"`
	Type          string            `json:"type"`
	Skill         string            `json:"skill,omitempty"`
	Plugin        string            `json:"plugin,omitempty"`
	Package       string            `json:"package,omitempty"`
	Version       string            `json:"version,omitempty"`
	Digest        string            `json:"digest,omitempty"`
	Source        string            `json:"source,omitempty"`
	SourcePath    string            `json:"source_path,omitempty"`
	PackageDir    string            `json:"package_dir,omitempty"`
	PackageDigest string            `json:"package_digest,omitempty"`
	ContainerPath string            `json:"container_path,omitempty"`
	Details       map[string]string `json:"details,omitempty"`
}

type ReplayLog struct {
	Runtime            string        `json:"runtime"`
	BaselineCheckpoint string        `json:"baseline_checkpoint,omitempty"`
	Events             []ReplayEvent `json:"events"`
}

type CheckpointSkill struct {
	Name   string `json:"name"`
	Digest string `json:"digest"`
}

type CheckpointPlugin struct {
	Name    string `json:"name"`
	Package string `json:"package,omitempty"`
	Version string `json:"version,omitempty"`
	Digest  string `json:"digest"`
	Source  string `json:"source,omitempty"`
}

type CheckpointMetadata struct {
	Runtime      string             `json:"runtime"`
	CheckpointID string             `json:"checkpoint_id"`
	Timestamp    string             `json:"timestamp"`
	Image        string             `json:"image"`
	SourceImage  string             `json:"source_image,omitempty"`
	SnapshotDir  string             `json:"snapshot_dir"`
	DeploymentID string             `json:"deployment_id"`
	Skills       []CheckpointSkill  `json:"skills,omitempty"`
	Plugins      []CheckpointPlugin `json:"plugins,omitempty"`
}

func New(stateRoot string) *Store {
	return &Store{stateRoot: filepath.Clean(strings.TrimSpace(stateRoot))}
}

func (s *Store) AppendDeployment(record DeploymentRecord) error {
	path := s.deploymentHistoryPath()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("create deployment history dir: %w", err)
	}

	payload, err := json.Marshal(record)
	if err != nil {
		return fmt.Errorf("marshal deployment record: %w", err)
	}

	return withFileLock(path, func() error {
		existing, err := os.ReadFile(path)
		if err != nil && !os.IsNotExist(err) {
			return fmt.Errorf("read deployment history: %w", err)
		}
		if len(existing) > 0 && existing[len(existing)-1] != '\n' {
			existing = append(existing, '\n')
		}
		existing = append(existing, payload...)
		existing = append(existing, '\n')
		if err := writeFileAtomically(path, existing, 0o644); err != nil {
			return fmt.Errorf("write deployment history: %w", err)
		}
		return nil
	})
}

func (s *Store) LoadReplayLog(runtime string) (ReplayLog, error) {
	path := s.replayLogPath(runtime)
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return ReplayLog{
				Runtime: runtime,
				Events:  []ReplayEvent{},
			}, nil
		}
		return ReplayLog{}, fmt.Errorf("read replay log for %s: %w", runtime, err)
	}

	var log ReplayLog
	if err := json.Unmarshal(data, &log); err != nil {
		return ReplayLog{}, fmt.Errorf("decode replay log for %s: %w", runtime, err)
	}
	if log.Events == nil {
		log.Events = []ReplayEvent{}
	}
	if strings.TrimSpace(log.Runtime) == "" {
		log.Runtime = runtime
	}
	return log, nil
}

func (s *Store) SaveReplayLog(runtime string, log ReplayLog) error {
	path := s.replayLogPath(runtime)
	log.Runtime = runtime
	if log.Events == nil {
		log.Events = []ReplayEvent{}
	}

	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("create replay dir for %s: %w", runtime, err)
	}

	payload, err := json.MarshalIndent(log, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal replay log for %s: %w", runtime, err)
	}
	return withFileLock(path, func() error {
		if err := writeFileAtomically(path, append(payload, '\n'), 0o644); err != nil {
			return fmt.Errorf("write replay log for %s: %w", runtime, err)
		}
		return nil
	})
}

func (s *Store) ClearReplayLog(runtime, checkpointID string) error {
	return s.SaveReplayLog(runtime, ReplayLog{
		Runtime:            runtime,
		BaselineCheckpoint: checkpointID,
		Events:             []ReplayEvent{},
	})
}

func (s *Store) RemoveReplayEvent(runtime, eventID string) error {
	log, err := s.LoadReplayLog(runtime)
	if err != nil {
		return err
	}
	filtered := make([]ReplayEvent, 0, len(log.Events))
	for _, event := range log.Events {
		if event.EventID == eventID {
			continue
		}
		filtered = append(filtered, event)
	}
	log.Events = filtered
	return s.SaveReplayLog(runtime, log)
}

func (s *Store) StageReplayPackage(runtime, eventID, sourceDir string) (string, error) {
	destination := s.replayPackageDir(runtime, eventID)
	if err := os.RemoveAll(destination); err != nil {
		return "", fmt.Errorf("reset staged package dir %s: %w", destination, err)
	}
	if err := copyTree(sourceDir, destination); err != nil {
		return "", fmt.Errorf("stage replay package %s: %w", sourceDir, err)
	}
	return destination, nil
}

func (s *Store) StageReplaySource(runtime, eventID, sourcePath string) (string, error) {
	destinationRoot := s.replaySourceDir(runtime, eventID)
	if err := os.RemoveAll(destinationRoot); err != nil {
		return "", fmt.Errorf("reset staged source dir %s: %w", destinationRoot, err)
	}
	destination := filepath.Join(destinationRoot, filepath.Base(sourcePath))
	if err := copyPath(sourcePath, destination); err != nil {
		return "", fmt.Errorf("stage replay source %s: %w", sourcePath, err)
	}
	return destination, nil
}

func (s *Store) LoadCheckpoint(runtime string) (CheckpointMetadata, bool, error) {
	path := s.checkpointMetadataPath(runtime)
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return CheckpointMetadata{}, false, nil
		}
		return CheckpointMetadata{}, false, fmt.Errorf("read checkpoint metadata for %s: %w", runtime, err)
	}

	var metadata CheckpointMetadata
	if err := json.Unmarshal(data, &metadata); err != nil {
		return CheckpointMetadata{}, false, fmt.Errorf("decode checkpoint metadata for %s: %w", runtime, err)
	}
	if strings.TrimSpace(metadata.Runtime) == "" {
		metadata.Runtime = runtime
	}
	return metadata, true, nil
}

func (s *Store) SaveCheckpoint(runtime string, metadata CheckpointMetadata) error {
	path := s.checkpointMetadataPath(runtime)
	metadata.Runtime = runtime
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("create checkpoint dir for %s: %w", runtime, err)
	}

	payload, err := json.MarshalIndent(metadata, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal checkpoint metadata for %s: %w", runtime, err)
	}
	return withFileLock(path, func() error {
		if err := writeFileAtomically(path, append(payload, '\n'), 0o644); err != nil {
			return fmt.Errorf("write checkpoint metadata for %s: %w", runtime, err)
		}
		return nil
	})
}

func (s *Store) DeleteCheckpoint(runtime string) error {
	if err := os.Remove(s.checkpointMetadataPath(runtime)); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("delete checkpoint metadata for %s: %w", runtime, err)
	}
	return nil
}

func (s *Store) CheckpointSnapshotDir(runtime, checkpointID string) string {
	return filepath.Join(s.checkpointRoot(runtime, checkpointID), "snapshot")
}

func (s *Store) CheckpointBuildContextDir(runtime, checkpointID string) string {
	return filepath.Join(s.checkpointRoot(runtime, checkpointID), "image")
}

func (s *Store) ReplayPackageDir(runtime, eventID string) string {
	return s.replayPackageDir(runtime, eventID)
}

func (s *Store) DirectoryDigest(path string) (string, error) {
	return directoryDigest(path)
}

func (s *Store) ReplaySkillState(runtime string) (map[string]CheckpointSkill, error) {
	log, err := s.LoadReplayLog(runtime)
	if err != nil {
		return nil, err
	}
	state := make(map[string]CheckpointSkill, len(log.Events))
	for _, event := range log.Events {
		if event.Type != "skill_install" || strings.TrimSpace(event.Skill) == "" {
			continue
		}
		state[event.Skill] = CheckpointSkill{
			Name:   event.Skill,
			Digest: event.PackageDigest,
		}
	}
	return state, nil
}

func (s *Store) ReplayPluginState(runtime string) (map[string]CheckpointPlugin, error) {
	log, err := s.LoadReplayLog(runtime)
	if err != nil {
		return nil, err
	}
	state := make(map[string]CheckpointPlugin, len(log.Events))
	for _, event := range log.Events {
		name := strings.TrimSpace(event.Plugin)
		if name == "" {
			continue
		}
		switch event.Type {
		case "plugin_install":
			digest := strings.TrimSpace(event.Digest)
			if digest == "" {
				digest = strings.TrimSpace(event.PackageDigest)
			}
			state[name] = CheckpointPlugin{
				Name:    name,
				Package: event.Package,
				Version: event.Version,
				Digest:  digest,
				Source:  event.Source,
			}
		case "plugin_remove":
			delete(state, name)
		}
	}
	return state, nil
}

func (s *Store) ReadDeploymentHistory() ([]DeploymentRecord, error) {
	file, err := os.Open(s.deploymentHistoryPath())
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, fmt.Errorf("open deployment history: %w", err)
	}
	defer file.Close()

	records := []DeploymentRecord{}
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var record DeploymentRecord
		if err := json.Unmarshal([]byte(line), &record); err != nil {
			return nil, fmt.Errorf("decode deployment history entry: %w", err)
		}
		records = append(records, record)
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("scan deployment history: %w", err)
	}
	return records, nil
}

func (s *Store) deploymentHistoryPath() string {
	return filepath.Join(s.stateRoot, "deploy", "history.jsonl")
}

func (s *Store) replayLogPath(runtime string) string {
	return filepath.Join(s.stateRoot, "deploy", "runtime", runtime, "replay-log.json")
}

func (s *Store) replayPackageDir(runtime, eventID string) string {
	return filepath.Join(s.stateRoot, "deploy", "runtime", runtime, "packages", eventID)
}

func (s *Store) replaySourceDir(runtime, eventID string) string {
	return filepath.Join(s.stateRoot, "deploy", "runtime", runtime, "sources", eventID)
}

func (s *Store) checkpointMetadataPath(runtime string) string {
	return filepath.Join(s.stateRoot, "runtime-baselines", runtime, "current.json")
}

func (s *Store) checkpointRoot(runtime, checkpointID string) string {
	return filepath.Join(s.stateRoot, "runtime-baselines", runtime, checkpointID)
}

func directoryDigest(root string) (string, error) {
	info, err := os.Stat(root)
	if err != nil {
		return "", err
	}
	if !info.IsDir() {
		return "", fmt.Errorf("%s is not a directory", root)
	}

	hash := sha256.New()
	paths := make([]string, 0, 16)
	if err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() {
			return nil
		}
		relative, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		paths = append(paths, relative)
		return nil
	}); err != nil {
		return "", err
	}
	sort.Strings(paths)

	for _, relative := range paths {
		if _, err := io.WriteString(hash, filepath.ToSlash(relative)); err != nil {
			return "", err
		}
		if _, err := io.WriteString(hash, "\n"); err != nil {
			return "", err
		}

		data, err := os.ReadFile(filepath.Join(root, relative))
		if err != nil {
			return "", err
		}
		if _, err := hash.Write(data); err != nil {
			return "", err
		}
		if _, err := io.WriteString(hash, "\n"); err != nil {
			return "", err
		}
	}

	return hex.EncodeToString(hash.Sum(nil)), nil
}

func copyTree(source, destination string) error {
	return filepath.Walk(source, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}

		relative, err := filepath.Rel(source, path)
		if err != nil {
			return err
		}
		target := filepath.Join(destination, relative)

		if info.IsDir() {
			return os.MkdirAll(target, 0o755)
		}

		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		return os.WriteFile(target, data, 0o644)
	})
}

func copyPath(source, destination string) error {
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

func withFileLock(path string, fn func() error) error {
	lockPath := path + ".lock"
	deadline := time.Now().Add(5 * time.Second)
	for {
		file, err := os.OpenFile(lockPath, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
		if err == nil {
			_ = file.Close()
			defer os.Remove(lockPath)
			return fn()
		}
		if !os.IsExist(err) {
			return fmt.Errorf("create lock for %s: %w", path, err)
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("timeout acquiring lock for %s", path)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func writeFileAtomically(path string, data []byte, perm os.FileMode) (err error) {
	file, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+".tmp-*")
	if err != nil {
		return fmt.Errorf("create temp file for %s: %w", path, err)
	}
	tmpPath := file.Name()
	defer func() {
		if err != nil {
			_ = os.Remove(tmpPath)
		}
	}()

	if err = file.Chmod(perm); err != nil {
		_ = file.Close()
		return fmt.Errorf("chmod temp file for %s: %w", path, err)
	}
	if _, err = file.Write(data); err != nil {
		_ = file.Close()
		return fmt.Errorf("write temp file for %s: %w", path, err)
	}
	if err = file.Sync(); err != nil {
		_ = file.Close()
		return fmt.Errorf("sync temp file for %s: %w", path, err)
	}
	if err = file.Close(); err != nil {
		return fmt.Errorf("close temp file for %s: %w", path, err)
	}
	if err = replaceFile(tmpPath, path); err != nil {
		return fmt.Errorf("replace %s: %w", path, err)
	}
	return nil
}

func replaceFile(source, destination string) error {
	if err := os.Rename(source, destination); err == nil {
		return nil
	} else if runtime.GOOS != "windows" {
		return err
	}

	if err := os.Remove(destination); err != nil && !os.IsNotExist(err) {
		return err
	}
	return os.Rename(source, destination)
}
