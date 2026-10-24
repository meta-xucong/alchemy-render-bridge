"""Export the implemented, strict experimental schemas without starting services."""
import json
from pathlib import Path

from render_bridge.node_api import HeartbeatRequest
from render_bridge.schemas import InputCreate, TaskCreate, ReceiptAck, WithdrawalRequest, WithdrawalProof, RoutingAvailability, TerminalFailureRequest

root = Path(__file__).resolve().parents[1] / "contracts" / "v1"
root.mkdir(parents=True, exist_ok=True)
for name, model in (("input", InputCreate), ("task", TaskCreate), ("ack", ReceiptAck), ("withdrawal", WithdrawalRequest), ("heartbeat", HeartbeatRequest),
                    ("withdrawal-proof", WithdrawalProof), ("routing-availability", RoutingAvailability), ("terminal-failure", TerminalFailureRequest)):
    (root / f"{name}.schema.json").write_text(
        json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
