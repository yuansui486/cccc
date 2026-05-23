"""WebSocket message types and helpers shared between Router and Worker."""
from __future__ import annotations

# Worker lifecycle
WORKER_REGISTER = "worker.register"
WORKER_REGISTERED = "worker.registered"
HEARTBEAT = "heartbeat"
HEARTBEAT_ACK = "heartbeat.ack"

# Submit flow (Router -> Worker)
SUBMIT_START = "submit.start"
SUBMIT_PARAMS = "submit.params"
FILE_START = "file.start"
FILE_CHUNK = "file.chunk"
FILE_END = "file.end"
SUBMIT_COMPLETE = "submit.complete"
SUBMIT_CANCEL = "submit.cancel"

# Submit flow (Worker -> Router)
SUBMIT_ACCEPTED = "submit.accepted"
SUBMIT_RESULT = "submit.result"
SUBMIT_ERROR = "submit.error"

# Status query
STATUS_QUERY = "status.query"
STATUS_RESULT = "status.result"

# Result download
RESULT_OPEN = "result.open"
RESULT_START = "result.start"
RESULT_CHUNK = "result.chunk"
RESULT_END = "result.end"
RESULT_ERROR = "result.error"


FILE_ID_IMAGE = "image"
FILE_ID_AUDIO = "audio"
