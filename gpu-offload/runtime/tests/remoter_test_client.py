from __future__ import annotations

import concurrent.futures
import json
import os

from remoter import autoremote, remoter


def main() -> None:
    autoremote.start(False)

    from tests import remoter_test_fixture

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(remoter_test_fixture.add, value, 10) for value in (1, 2, 3)]
        concurrent_results = [future.result(timeout=10) for future in futures]

    remote_error = None
    try:
        remoter_test_fixture.fail("expected remote failure")
    except remoter.RemoteExecutionError as error:
        remote_error = str(error)

    constructor_accumulator = remoter_test_fixture.Accumulator(10)
    constructor_initial_value = constructor_accumulator.get_value()
    constructor_added_value = constructor_accumulator.add(5)
    constructor_accumulator.value = 21
    constructor_accumulator.syncwithremote()

    factory_accumulator = remoter_test_fixture.create_accumulator(30)
    factory_initial_value = factory_accumulator.get_value()
    factory_added_value = factory_accumulator.add(4)

    first_singleton = remoter_test_fixture.SingletonAccumulator(100)
    first_singleton_id = first_singleton.get_instance_id()
    first_singleton.add(5)
    second_singleton = remoter_test_fixture.SingletonAccumulator(999)
    second_singleton_id = second_singleton.get_instance_id()
    second_singleton_initial_value = second_singleton.get_value()
    second_singleton.add(5)

    stub_accumulator = remoter_test_fixture.create_stub_accumulator(50)
    stub_added_value = stub_accumulator.add(5)

    result = {
        "add": remoter_test_fixture.add(7, 8),
        "multiply": remoter_test_fixture.multiply(6, 7),
        "first_server_pid": remoter_test_fixture.first_server_pid(),
        "second_server_pid": remoter_test_fixture.second_server_pid(),
        "concurrent": concurrent_results,
        "remote_error": remote_error,
        "client_pid": os.getpid(),
        "constructor_initial_value": constructor_initial_value,
        "constructor_added_value": constructor_added_value,
        "constructor_synced_value": constructor_accumulator.__dict__["value"],
        "constructor_server_pid": constructor_accumulator.get_created_by_pid(),
        "factory_initial_value": factory_initial_value,
        "factory_added_value": factory_added_value,
        "factory_server_pid": factory_accumulator.get_created_by_pid(),
        "distinct_remote_objects": constructor_accumulator.uuid_rmt0bf != factory_accumulator.uuid_rmt0bf,
        "singleton_same_id": first_singleton_id == second_singleton_id,
        "singleton_second_initial_value": second_singleton_initial_value,
        "singleton_shared_value": first_singleton.get_value(),
        "stub_proxy_type": type(stub_accumulator).__name__,
        "stub_added_value": stub_added_value,
        "stub_attribute_value": stub_accumulator.value,
        "stub_description": stub_accumulator.describe(),
        "stub_server_pid": stub_accumulator.get_created_by_pid(),
    }
    print(f"REMOTER_TEST_RESULT={json.dumps(result, sort_keys=True)}", flush=True)


if __name__ == "__main__":
    main()
