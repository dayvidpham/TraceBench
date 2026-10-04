package corpus

// SessionArtifact is one session with the fields needed to build a
// schema.UnifiedMetadata record. It extends the session row with project,
// host, metrics, commit, and relationship evidence.
type SessionArtifact struct {
	Session
	IngestedMS           int64
	SourceFormat         string
	GitTracking          string
	ToolVersion          string
	RootSessionID        string
	SessionPurpose       string
	AdapterVersion       int64
	LicenseID            string
	ProjectHash          string
	ProjectName          string
	ProjectRemote        string
	HostSlug             string
	InputSubmissionCount *int64

	TurnCount       int
	SubagentCount   int
	ToolCalls       int
	TotalTokens     int
	InputTokens     int
	OutputTokens    int
	DurationMinutes float64
	Outcome         string

	Commits       []CommitRecord
	Associations  []AssociationRecord
	Relationships []RelationshipRecord
	SubagentIDs   []string
}

// CommitRecord is one commit observed during a session.
type CommitRecord struct {
	Hash        string
	Message     string
	AuthorName  string
	AuthorEmail string
	CommitTime  int64
	AuthorTime  int64
}

// AssociationRecord is one durable session-to-commit association.
type AssociationRecord struct {
	ID                 string
	ObservedCommitHash string
	Subject            string
	AuthorTime         int64
}

// RelationshipRecord is one resolved cross-session relationship.
type RelationshipRecord struct {
	Kind          string
	TargetState   string
	TargetLocalID string
	Evidence      string
}

// PulledTranscript is one transcript pulled from a village collective. PullDir
// is the local directory holding transcript.jsonl, metadata.json, and
// pull-manifest.json.
type PulledTranscript struct {
	VillageHost     string
	TranscriptID    string
	OwnerUserID     string
	OwnerUsername   string
	LocalSessionID  string
	Title           string
	Harness         string
	ProjectName     string
	ContentHash     string
	Visibility      string
	LicenseID       string
	PullDir         string
	AnnotationCount int
	FirstPulledAt   int64
	LastPulledAt    int64
}
