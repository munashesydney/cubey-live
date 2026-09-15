"""Nonblocking Nav2 lifecycle startup and observable readiness."""
import time

from lifecycle_msgs.srv import ChangeState, GetState


class NavigationHealth:
    NODES = (
        "slam_toolbox", "map_server", "amcl", "map_saver",
        "controller_server", "planner_server", "behavior_server", "bt_navigator",
    )
    MAPPING_SOURCE = ("slam_toolbox",)
    LOCALIZATION_SOURCE = ("map_server", "amcl")
    COMMON_NODES = ("map_saver", "controller_server", "planner_server", "behavior_server", "bt_navigator")

    def __init__(self, node, localization_ready):
        self.node = node
        self.localization_ready = localization_ready
        self.states = {}
        self.queries = {}
        self.readers = {name: node.create_client(GetState, f"/{name}/get_state") for name in self.NODES}
        self.writers = {name: node.create_client(ChangeState, f"/{name}/change_state") for name in self.NODES}
        self.pending = None
        self.attempts = {}
        self.retry_at = 0.0
        self.error = ""
        self.mode = "mapping"
        self.timer = node.create_timer(0.5, self.tick)

    def set_mode(self, mode):
        if mode not in ("mapping", "localization"):
            raise ValueError(f"Unsupported navigation mode: {mode}")
        if self.mode != mode:
            self.node.get_logger().info(f"Navigation source mode: {self.mode} -> {mode}")
            self.mode = mode
            self.reset_retries()

    def _desired_nodes(self):
        source = self.MAPPING_SOURCE if self.mode == "mapping" else self.LOCALIZATION_SOURCE
        return source + self.COMMON_NODES

    def source_ready(self):
        snapshot = self.snapshot()
        desired = self.MAPPING_SOURCE if self.mode == "mapping" else self.LOCALIZATION_SOURCE
        undesired = self.LOCALIZATION_SOURCE if self.mode == "mapping" else self.MAPPING_SOURCE
        return (all(snapshot[name] == "active" for name in desired)
                and all(snapshot[name] != "active" for name in undesired))

    def reset_retries(self):
        self.attempts.clear()
        self.error = ""

    def snapshot(self):
        now = time.monotonic()
        return {name: self.states[name][0] if name in self.states and now-self.states[name][1] < 3.0
                else "unavailable" for name in self.NODES}

    def ready(self):
        snapshot = self.snapshot()
        return self.source_ready() and all(snapshot[name] == "active" for name in self._desired_nodes())

    def reason(self):
        if self.error:
            return self.error
        if self.pending:
            return f"Starting navigation: {self.pending[0]}"
        snapshot = self.snapshot()
        missing = [f"{name}: {snapshot[name]}" for name in self._desired_nodes()
                   if snapshot[name] != "active"]
        return "Waiting for navigation — " + ", ".join(missing) if missing else ""

    def tick(self):
        now = time.monotonic()
        for name, client in self.readers.items():
            query = self.queries.get(name)
            if query:
                future, started = query
                if future.done():
                    try:
                        self.states[name] = (future.result().current_state.label, now)
                    except Exception:
                        self.states.pop(name, None)
                    del self.queries[name]
                elif now-started > 3.0:
                    future.cancel()
                    del self.queries[name]
            if name not in self.queries and client.service_is_ready():
                self.queries[name] = (client.call_async(GetState.Request()), now)

        if self.pending:
            name, future, started = self.pending
            if not future.done():
                if now-started > 12.0:
                    # Do not overlap a transition that may still be running.
                    self.error = f"Navigation startup stalled at {name}; restart navigation services"
                return
            try:
                if not future.result().success:
                    raise RuntimeError("transition rejected")
                self.error = ""
            except Exception as exc:
                self.error = f"Navigation startup failed at {name}: {exc}"
                self.node.get_logger().error(self.error)
            # A state query may have been issued just before the transition
            # completed. Discard it so its stale pre-transition answer cannot
            # undo the post-transition observation requirement.
            query = self.queries.pop(name, None)
            if query and not query[0].done():
                query[0].cancel()
            self.states.pop(name, None)  # Require a post-transition observation.
            self.pending = None
            self.retry_at = now+1.0
            return

        if now < self.retry_at:
            return

        snapshot = self.snapshot()
        undesired = self.LOCALIZATION_SOURCE if self.mode == "mapping" else self.MAPPING_SOURCE
        # A single node must own map->odom. Deactivate the old source before
        # configuring or activating its replacement.
        for name in undesired:
            state = snapshot[name]
            if state != "active":
                continue
            client = self.writers[name]
            if not client.service_is_ready():
                return
            request = ChangeState.Request()
            request.transition.id = 4  # active -> inactive
            self.node.get_logger().info(f"Navigation lifecycle: {name} active -> deactivate")
            self.pending = (name, client.call_async(request), now)
            return

        for name in self._desired_nodes():
            state = snapshot[name]
            if state == "active":
                continue
            if state not in ("unconfigured", "inactive"):
                return
            # Configuration is harmless before a map exists. Activation of
            # navigation waits for real map/odom TF; the saver needs no TF.
            if (state == "inactive" and name not in
                    ("slam_toolbox", "map_server", "amcl", "map_saver")
                    and not self.localization_ready()):
                return
            key = (name, state)
            if self.attempts.get(key, 0) >= 3:
                self.error = f"Navigation startup failed at {name} after 3 attempts; reset mapping to retry"
                return
            client = self.writers[name]
            if not client.service_is_ready():
                return
            request = ChangeState.Request()
            request.transition.id = 1 if state == "unconfigured" else 3
            self.attempts[key] = self.attempts.get(key, 0)+1
            self.node.get_logger().info(f"Navigation lifecycle: {name} {state} -> {'configure' if request.transition.id == 1 else 'activate'}")
            self.pending = (name, client.call_async(request), now)
            return
