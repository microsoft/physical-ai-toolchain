from __future__ import annotations

import os
import uuid


def add(left: int, right: int) -> int:
    return left + right


def multiply(left: int, right: int) -> int:
    return left * right


def first_server_pid() -> int:
    return os.getpid()


def second_server_pid() -> int:
    return os.getpid()


def fail(message: str) -> None:
    raise ValueError(message)


def create_accumulator(value: int) -> Accumulator:
    return Accumulator(value)


def create_stub_accumulator(value: int) -> StubAccumulator:
    raise NotImplementedError("client-facing stub; the server runs create_stubbed_accumulator")


def create_stubbed_accumulator(value: int) -> StubbedAccumulator:
    return StubbedAccumulator(value)


class Accumulator:
    def __init__(self, value: int) -> None:
        self.value = value
        self.created_by_pid = os.getpid()

    def add(self, amount: int) -> int:
        self.value += amount
        return self.value

    def get_value(self) -> int:
        return self.value

    def get_created_by_pid(self) -> int:
        return self.created_by_pid


class SingletonAccumulator:
    def __init__(self, value: int) -> None:
        self.value = value
        self.instance_id = str(uuid.uuid4())

    def add(self, amount: int) -> int:
        self.value += amount
        return self.value

    def get_instance_id(self) -> str:
        return self.instance_id

    def get_value(self) -> int:
        return self.value


class StubAccumulator:
    def __init__(self, value: int) -> None:
        raise NotImplementedError("client-facing stub")

    def add(self, amount: int) -> int:
        raise NotImplementedError("client-facing stub")

    def describe(self) -> str:
        raise NotImplementedError("client-facing stub")

    def get_created_by_pid(self) -> int:
        raise NotImplementedError("client-facing stub")


class StubbedAccumulator:
    def __init__(self, value: int) -> None:
        self.value = value
        self.created_by_pid = os.getpid()

    def add(self, amount: int) -> int:
        self.value += amount
        return self.value

    def describe(self) -> str:
        return f"stubbed:{type(self).__name__}"

    def get_created_by_pid(self) -> int:
        return self.created_by_pid
