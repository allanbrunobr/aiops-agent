# AIOps Agent

A production-ready AI-powered incident response platform that automates diagnosis and remediation of operational issues using advanced LLMs, knowledge graphs, and safety layers.

## Overview

AIOps Agent is an intelligent platform designed to streamline incident response workflows by:

- **Automated Incident Diagnosis**: Uses AI to analyze logs, metrics, and traces to identify root causes
- **Knowledge-Based Enrichment**: Leverages historical incident data via RAG (Retrieval-Augmented Generation)
- **Multi-Source Alert Integration**: Supports PagerDuty, Grafana, and Databricks alerts
- **Safety-First Architecture**: Multiple validation layers prevent unsafe actions
- **Real-time Notifications**: Progressive Slack updates during diagnosis
- **MCP Integration**: Extensible tooling interface for external AI agents

## Architecture

The system is built on a modular, typed-graph architecture with the following key components:

- **Blueprint Engine**: Orchestrates incident response as a typed graph of deterministic and agentic nodes
- **Alert Ingestion**: Webhook server with pluggable normalizers for different alert sources
- **Pipeline Processing**: Sequential stages for parsing, filtering, context compression, and diagnosis
- **Safety Layers**: Multi-tier validation system (telemetry sanitization, pre-execution validation, TNR, version validation)
- **Knowledge Base**: RAG-powered search through historical incidents
- **MCP Server**: Model Context Protocol server for external AI agent integration

## Quick Start

### Prerequisites

- Python 3.9+
- Virtual environment (recommended)

### Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd aiops-agent
```

2. Create and activate a virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Set up configuration:
```bash
cp .env.example .env
# Edit .env with your API keys and configuration
```

### Basic Usage

#### CLI Mode
Run incident diagnosis from command line:
```bash
python main.py --alert-file path/to/alert.json
```

#### Server Mode
Start the webhook server:
```bash
python -m server.app
```

The server will be available at `http://localhost:8000` with:
- Webhook endpoint: `/webhook/{source}`
- Health check: `/health`
- Ready check: `/ready`

#### MCP Server Mode
Start the MCP server for external AI agents:
```bash
python -m mcp_server.app
```

## Configuration

All configuration is managed through YAML files and environment variables:

### Main Configuration (`config/config.yaml`)
```yaml
llm:
  provider: anthropic
  model: claude-3-5-sonnet-20241022
  
safety:
  enabled: true
  strict_mode: true

notifications:
  slack:
    enabled: true
    channel: "#incidents"
```

### Environment Variables
```bash
ANTHROPIC_API_KEY=your-api-key
SLACK_BOT_TOKEN=your-slack-token
DATABRICKS_HOST=your-databricks-host
```

## Available Features

### Alert Processing
- **Multi-source support**: PagerDuty, Grafana, Databricks
- **Intelligent deduplication**: Prevents duplicate processing
- **Webhook authentication**: Source-specific validation
- **Concurrent processing**: Handles multiple alerts simultaneously

### Incident Diagnosis
- **Log analysis**: Drain3-powered log parsing and anomaly detection
- **Metric correlation**: Statistical analysis of time-series data
- **Trace analysis**: Distributed tracing correlation
- **Context compression**: Intelligent summarization for LLM processing

### Safety & Reliability
- **Telemetry sanitization**: Removes PII and sensitive data
- **Pre-execution validation**: Validates actions before execution
- **TNR (Test and Rollback)**: Dry-run validation system
- **Version validation**: Ensures compatible tool versions

### Knowledge Management
- **RAG integration**: Search historical incidents for similar patterns
- **Pattern learning**: Continuously improves from feedback
- **Template system**: Easy addition of new diagnostic scenarios
- **Feedback tracking**: False positive marking and accuracy metrics

## Testing

Run the test suite:
```bash
# All tests
pytest

# Unit tests only
pytest -m unit

# Integration tests
pytest -m integration

# Specific test categories
pytest -m "unit and not slow"
```

Test markers:
- `unit`: Unit tests (no external dependencies)
- `integration`: Integration tests (full pipeline)
- `snapshot`: Deterministic output validation
- `slow`: Performance tests (>10s duration)

## Development

### Project Structure
```
aiops-agent/
├── agents/          # AI agent implementations
├── blueprint/       # Blueprint engine and graph orchestration
├── config/          # Configuration management
├── core/           # Core protocols and shared models
├── data/           # Data models and persistence
├── docs/           # Documentation
├── mcp_server/     # MCP server implementation
├── pipeline/       # Processing pipeline stages
├── rag/            # RAG knowledge base
├── safety/         # Safety validation layers
├── server/         # FastAPI webhook server
├── tests/          # Test suite
├── tools/          # Utility tools
├── utils/          # Utility functions
└── validation/     # Validation logic
```

### Contributing

1. Read [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines
2. Check [features.md](features.md) for current development status
3. Create feature branches following the naming convention
4. Ensure all tests pass and add new tests for features
5. Submit pull requests with clear descriptions

### Development Workflow

The project uses a sophisticated worktree-based development system. See:
- `features.md` - Feature tracking and status
- `WORKTREE_COMPLETE.md` - Worktree development guide
- `CLAUDE.md` - AI-assisted development guidelines

## API Documentation

### Webhook Endpoints

#### POST `/webhook/{source}`
Process alerts from monitoring sources.

**Parameters:**
- `source`: Alert source (pagerduty, grafana, databricks)

**Request Body:** Source-specific alert payload

**Response:**
```json
{
  "status": "received",
  "incident_id": "inc_123456",
  "message": "Alert received and processing started"
}
```

### MCP Tools

The platform exposes three MCP tools for external AI agents:

#### `search_incidents`
Search historical incidents for similar patterns.

#### `validate_action`
Validate proposed remediation actions for safety.

#### `diagnose_incident`
Trigger full incident diagnosis pipeline.

## Monitoring & Observability

### Structured Logging
All components emit structured JSON logs with:
- Correlation IDs for request tracing
- Context binding (pipeline, node, stage)
- Performance metrics and timing data

### Metrics
- Pipeline processing latency
- Alert ingestion rates  
- Diagnostic accuracy scores
- Safety layer rejection rates

### Health Checks
- `/health`: Basic service health
- `/ready`: Readiness including dependencies
- Progressive Slack updates during diagnosis

## Safety Considerations

This system includes multiple safety layers to prevent harmful actions:

1. **Telemetry Sanitization**: Removes PII and credentials
2. **Pre-execution Validation**: Validates actions before execution
3. **Test-and-Rollback (TNR)**: Dry-run validation system
4. **Version Validation**: Ensures tool compatibility
5. **Rate Limiting**: Prevents resource exhaustion
6. **Audit Trail**: Full logging of all safety decisions

## License

[License information]

## Support

For questions and support:
- Check existing documentation in `docs/`
- Review [CONTRIBUTING.md](CONTRIBUTING.md) for development questions
- Create issues for bugs and feature requests

---

*This README reflects the current state of all 33 implemented features. See [features.md](features.md) for detailed feature status and implementation artifacts.*