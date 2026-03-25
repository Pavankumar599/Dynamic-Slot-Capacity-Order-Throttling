from dataclasses import dataclass
from datetime import datetime
from math import floor


@dataclass
class OrderContext:
    warehouse_id: str
    active_riders: int
    orders_in_queue: int
    avg_delivery_time: float   # minutes
    order_created_time: datetime
    distance: float            # km
    items_count: int


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


def decide_order(
    order: OrderContext,
    sla_minutes: float = 45.0,
    max_service_distance_km: float = 12.0
) -> dict:
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
            "predicted_eta": round(predicted_eta, 2),
            "dynamic_queue_limit": dynamic_queue_limit,
            "queue_after_accept": queue_after_accept
        }

    if queue_after_accept > dynamic_queue_limit:
        return {
            "decision": "REJECT",
            "reason": "CAPACITY_EXCEEDED",
            "predicted_eta": round(predicted_eta, 2),
            "dynamic_queue_limit": dynamic_queue_limit,
            "queue_after_accept": queue_after_accept
        }

    return {
        "decision": "ACCEPT",
        "reason": "WITHIN_CAPACITY",
        "predicted_eta": round(predicted_eta, 2),
        "dynamic_queue_limit": dynamic_queue_limit,
        "queue_after_accept": queue_after_accept
    }