from dataclasses import dataclass
from datetime import datetime
from math import floor
from typing import Any, Dict

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)


# -----------------------------
# Data model
# -----------------------------
@dataclass
class OrderContext:
    warehouse_id: str
    active_riders: int
    orders_in_queue: int
    avg_delivery_time: float   # minutes
    order_created_time: datetime
    distance: float            # km
    items_count: int


# -----------------------------
# Utility functions
# -----------------------------
def clip(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def is_peak_hour(dt: datetime) -> bool:
    return dt.hour in {8, 9, 10, 12, 13, 14}


def estimate_pick_pack_time(items_count: int) -> float:
    base_pick = 4.0
    per_item_pick = 0.6
    return base_pick + per_item_pick * items_count


def estimate_travel_time(distance_km: float) -> float:
    base_handover = 5.0
    speed_kmph = 20.0
    return base_handover + (distance_km / speed_kmph) * 60.0


def estimate_queue_wait(
    orders_in_queue: int,
    active_riders: int,
    avg_delivery_time: float
) -> float:
    rider_load = (orders_in_queue + 1) / max(active_riders, 1)
    return max(0.0, rider_load - 1.0) * avg_delivery_time * 0.6


def compute_dynamic_queue_limit(
    active_riders: int,
    avg_delivery_time: float,
    sla_minutes: float,
    peak: bool,
    distance_km: float,
    items_count: int
) -> int:
    base_limit = active_riders * 1.5

    latency_factor = clip(sla_minutes / max(avg_delivery_time, 1.0), 0.5, 1.3)
    peak_factor = 0.85 if peak else 1.0
    distance_factor = 0.9 if distance_km > 5 else 1.0
    complexity_factor = 0.9 if items_count > 10 else 1.0

    dynamic_limit = floor(
        base_limit
        * latency_factor
        * peak_factor
        * distance_factor
        * complexity_factor
    )

    return max(3, dynamic_limit)


# -----------------------------
# Main decision engine
# -----------------------------
def decide_order(
    order: OrderContext,
    sla_minutes: float = 45.0,
    max_service_distance_km: float = 12.0
) -> Dict[str, Any]:
    peak = is_peak_hour(order.order_created_time)

    if order.active_riders <= 0:
        return {
            "decision": "REJECT",
            "reason": "NO_ACTIVE_RIDERS"
        }

    if order.distance > max_service_distance_km:
        return {
            "decision": "REJECT",
            "reason": "OUT_OF_SERVICE_RADIUS"
        }

    pick_pack_time = estimate_pick_pack_time(order.items_count)
    travel_time = estimate_travel_time(order.distance)
    queue_wait = estimate_queue_wait(
        order.orders_in_queue,
        order.active_riders,
        order.avg_delivery_time
    )

    congestion_penalty = max(0.0, order.avg_delivery_time - sla_minutes) * 0.35
    peak_penalty = 5.0 if peak else 0.0

    predicted_eta = (
        pick_pack_time
        + travel_time
        + queue_wait
        + congestion_penalty
        + peak_penalty
    )

    dynamic_queue_limit = compute_dynamic_queue_limit(
        active_riders=order.active_riders,
        avg_delivery_time=order.avg_delivery_time,
        sla_minutes=sla_minutes,
        peak=peak,
        distance_km=order.distance,
        items_count=order.items_count
    )

    queue_after_accept = order.orders_in_queue + 1

    if predicted_eta > sla_minutes:
        return {
            "decision": "REJECT",
            "reason": "SLA_RISK",
            "warehouse_id": order.warehouse_id,
            "predicted_eta": round(predicted_eta, 2),
            "sla_minutes": sla_minutes,
            "dynamic_queue_limit": dynamic_queue_limit,
            "queue_after_accept": queue_after_accept,
            "peak_hour": peak,
            "details": {
                "pick_pack_time": round(pick_pack_time, 2),
                "travel_time": round(travel_time, 2),
                "queue_wait": round(queue_wait, 2),
                "congestion_penalty": round(congestion_penalty, 2),
                "peak_penalty": round(peak_penalty, 2),
            }
        }

    if queue_after_accept > dynamic_queue_limit:
        return {
            "decision": "REJECT",
            "reason": "CAPACITY_EXCEEDED",
            "warehouse_id": order.warehouse_id,
            "predicted_eta": round(predicted_eta, 2),
            "sla_minutes": sla_minutes,
            "dynamic_queue_limit": dynamic_queue_limit,
            "queue_after_accept": queue_after_accept,
            "peak_hour": peak,
            "details": {
                "pick_pack_time": round(pick_pack_time, 2),
                "travel_time": round(travel_time, 2),
                "queue_wait": round(queue_wait, 2),
                "congestion_penalty": round(congestion_penalty, 2),
                "peak_penalty": round(peak_penalty, 2),
            }
        }

    return {
        "decision": "ACCEPT",
        "reason": "WITHIN_CAPACITY",
        "warehouse_id": order.warehouse_id,
        "predicted_eta": round(predicted_eta, 2),
        "sla_minutes": sla_minutes,
        "dynamic_queue_limit": dynamic_queue_limit,
        "queue_after_accept": queue_after_accept,
        "peak_hour": peak,
        "details": {
            "pick_pack_time": round(pick_pack_time, 2),
            "travel_time": round(travel_time, 2),
            "queue_wait": round(queue_wait, 2),
            "congestion_penalty": round(congestion_penalty, 2),
            "peak_penalty": round(peak_penalty, 2),
        }
    }


# -----------------------------
# Parsing helpers
# -----------------------------
def parse_datetime_local(value: str) -> datetime:
    """
    Expects format like: 2026-03-25T13:30
    """
    if not value:
        return datetime.now()
    return datetime.strptime(value, "%Y-%m-%dT%H:%M")


def build_order_context(data: Dict[str, Any]) -> OrderContext:
    return OrderContext(
        warehouse_id=str(data.get("warehouse_id", "")).strip(),
        active_riders=int(data.get("active_riders", 0)),
        orders_in_queue=int(data.get("orders_in_queue", 0)),
        avg_delivery_time=float(data.get("avg_delivery_time", 0)),
        order_created_time=parse_datetime_local(str(data.get("order_created_time", "")).strip()),
        distance=float(data.get("distance", 0)),
        items_count=int(data.get("items_count", 0)),
    )


# -----------------------------
# Routes
# -----------------------------
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/decide", methods=["POST"])
def decide():
    try:
        data = request.get_json(silent=True) or request.form.to_dict()
        order = build_order_context(data)

        if not order.warehouse_id:
            return jsonify({"error": "warehouse_id is required"}), 400

        if order.active_riders < 0 or order.orders_in_queue < 0 or order.distance < 0 or order.items_count < 0:
            return jsonify({"error": "Numeric values cannot be negative"}), 400

        if order.avg_delivery_time <= 0:
            return jsonify({"error": "avg_delivery_time must be greater than 0"}), 400

        result = decide_order(order)
        return jsonify(result)

    except ValueError:
        return jsonify({"error": "Please enter valid numeric values"}), 400
    except Exception as exc:
        return jsonify({"error": f"Unexpected error: {str(exc)}"}), 500


if __name__ == "__main__":
    app.run(debug=True)