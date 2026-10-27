"""Canonical symptom types.

Shared by the failure patterns (campus_data/failure_patterns.yaml), the repair history,
the simulation and, from Nov 30, the intake agent. Safety conditions (gas smell, smoke,
water near electrical, elevator entrapment, exposed wiring) are safety flags handled by
the policy layer, not symptom types.
"""

from typing import Literal, get_args

from agent.campus.graph import Domain

SymptomType = Literal[
    "too_cold",
    "too_hot",
    "no_airflow",
    "noise",
    "odor",
    "leak",
    "no_hot_water",
    "clog",
    "low_water_pressure",
    "no_power",
    "lights_out",
    "flickering_lights",
    "elevator_stopped",
    "door_lock",
    "spill",
]

SYMPTOM_TYPES: tuple[str, ...] = get_args(SymptomType)

# Which upstream walks a symptom triggers in root-cause search. Odor and leak can come
# from air handling or plumbing, so they walk both.
SYMPTOM_DOMAINS: dict[str, tuple[Domain, ...]] = {
    "too_cold": ("hvac",),
    "too_hot": ("hvac",),
    "no_airflow": ("hvac",),
    "noise": ("hvac",),
    "odor": ("hvac", "plumbing"),
    "leak": ("plumbing", "hvac"),
    "no_hot_water": ("plumbing",),
    "clog": ("plumbing",),
    "low_water_pressure": ("plumbing",),
    "no_power": ("electrical",),
    "lights_out": ("electrical",),
    "flickering_lights": ("electrical",),
    "elevator_stopped": ("elevator",),
    "door_lock": (),
    "spill": (),
}
