import importlib.util
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch


def load_health():
    services = NS(GetState=NS(Request=lambda: NS()),
                  ChangeState=NS(Request=lambda: NS(transition=NS(id=0))))
    spec = importlib.util.spec_from_file_location("health_under_test", Path("ros2/nodes/navigation_health.py"))
    module = importlib.util.module_from_spec(spec)
    with patch.dict("sys.modules", {"lifecycle_msgs": NS(), "lifecycle_msgs.srv": services}):
        spec.loader.exec_module(module)
    return module


def harness():
    module = load_health()
    states = {name: "unconfigured" for name in module.NavigationHealth.NODES}
    calls = []
    node = MagicMock()
    def client(_kind, path):
        name, operation = path.strip("/").split("/")
        result = MagicMock()
        def invoke(request):
            future = Future()
            if operation == "get_state":
                future.set_result(NS(current_state=NS(label=states[name])))
            else:
                calls.append((name, request.transition.id))
                states[name] = "inactive" if request.transition.id in (1, 4) else "active"
                future.set_result(NS(success=True))
            return future
        result.call_async.side_effect = invoke
        return result
    node.create_client.side_effect = client
    localized = [False]
    health = module.NavigationHealth(node, lambda: localized[0])
    return module, health, states, calls, localized


def test_cold_start_waits_for_localization_then_activates_every_component():
    module, health, states, calls, localized = harness()
    with patch.object(module.time, "monotonic", return_value=0.) as clock:
        for tick in range(20):
            clock.return_value = tick
            health.tick()
        assert not health.ready()
        assert ("slam_toolbox", 3) in calls
        assert ("controller_server", 3) not in calls
        localized[0] = True
        for tick in range(20, 60):
            clock.return_value = tick
            health.tick()
        assert health.ready()
        assert [name for name, transition in calls if transition == 3] == list(health._desired_nodes())
        assert states["map_server"] == "unconfigured"
        assert states["amcl"] == "unconfigured"
        clock.return_value = 65
        assert not health.ready()  # A cached active state is not a heartbeat.


def test_inactive_navigator_is_detected_and_reactivated_without_motion():
    module, health, states, calls, localized = harness()
    localized[0] = True
    states.update({name: "active" for name in health._desired_nodes()})
    states["map_server"] = "unconfigured"
    states["amcl"] = "unconfigured"
    states["bt_navigator"] = "inactive"
    with patch.object(module.time, "monotonic", return_value=10.):
        health.tick()
        health.tick()
        assert not health.ready()
        assert "bt_navigator" in health.reason()
        assert calls == [("bt_navigator", 3)]


def test_unavailable_services_never_report_ready_or_attempt_activation():
    module, health, states, calls, localized = harness()
    for reader in health.readers.values():
        reader.service_is_ready.return_value = False
    health.tick()
    assert not health.ready()
    assert "unavailable" in health.reason()
    assert calls == []


def test_switch_to_global_localization_deactivates_slam_before_amcl():
    module, health, states, calls, localized = harness()
    localized[0] = True
    states.update({name: "active" for name in health._desired_nodes()})
    states["map_server"] = "unconfigured"
    states["amcl"] = "unconfigured"
    health.set_mode("localization")
    with patch.object(module.time, "monotonic", return_value=10.) as clock:
        for tick in range(80):
            clock.return_value = 10+tick
            health.tick()
        assert health.source_ready()
        assert health.ready()
    assert states["slam_toolbox"] == "inactive"
    assert states["map_server"] == "active"
    assert states["amcl"] == "active"
    slam_off = calls.index(("slam_toolbox", 4))
    assert slam_off < calls.index(("map_server", 3))
    assert slam_off < calls.index(("amcl", 3))


def test_supervisor_covers_every_launched_lifecycle_node():
    import ast
    module = load_health()
    tree = ast.parse(Path("ros2/launch/cubey_bringup.launch.py").read_text())
    assignment = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "lifecycle_nodes" for t in n.targets))
    assert list(module.NavigationHealth.NODES) == ast.literal_eval(assignment.value)
