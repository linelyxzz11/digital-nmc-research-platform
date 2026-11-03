"""Finite Root resources and positive, explicitly clocked service latencies."""
from dataclasses import dataclass, fields
import json
from pathlib import Path


class RootCapacityError(ValueError):
    pass


@dataclass(frozen=True)
class RootTiming:
    rx_queue_packets: int = 2
    reduction_entries: int = 256
    done_queue_packets: int = 1
    lanes_per_cycle: int = 4
    read_cycles: int = 1
    add_cycles: int = 1
    update_cycles: int = 1
    write_cycles: int = 1

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for value in vars(self).values()):
            raise ValueError("Root capacities and service cycles must be positive integers")
        if self.lanes_per_cycle > 4:
            raise ValueError("Root supports at most four INT32 lanes per service group")

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict):
            raise ValueError("Root configuration must be a JSON object")
        unknown = set(payload) - {field.name for field in fields(cls)}
        if unknown:
            raise ValueError(f"unknown Root configuration fields: {sorted(unknown)}")
        return cls(**payload)

    @classmethod
    def load(cls, path):
        try:
            return cls.from_dict(json.loads(Path(path).read_text(encoding='utf-8')))
        except (OSError, ValueError) as error:
            raise ValueError(f"invalid Root configuration {path}: {error}") from error
