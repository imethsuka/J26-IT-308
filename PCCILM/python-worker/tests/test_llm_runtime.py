"""Where the memory model runs (dedicated GPU or CPU), falling back to the CPU, pausing, and the light-load gate."""

from __future__ import annotations

import urllib.error
from pathlib import Path

import pytest

from adaptation.host_state import check_light_load
from adaptation.llm_runtime import Device, LocalModel, choose_device, parse_devices
from adaptation.summarizer import ModelPaused

LISTING = """\
ggml_vulkan: Found 2 Vulkan devices:
Available devices:
  Vulkan0: Intel(R) UHD Graphics (8052 MiB, 7394 MiB free)
  Vulkan1: NVIDIA GeForce RTX 2050 (3962 MiB, 3367 MiB free)
"""
GB = 1024 ** 3
MODEL = int(1.0 * GB)


def test_the_device_listing_is_parsed_and_integrated_graphics_are_not_dedicated() -> None:
    intel, nvidia = parse_devices(LISTING)
    assert (intel.name, intel.free_mib, intel.dedicated) == ("Vulkan0", 7394, False)
    assert (nvidia.name, nvidia.description, nvidia.dedicated) == ("Vulkan1", "NVIDIA GeForce RTX 2050", True)


@pytest.mark.parametrize("description, dedicated", [
    ("AMD Radeon RX 7800 XT", True), ("AMD Radeon(TM) Graphics", False), ("AMD Radeon 780M Graphics", False),
    ("Intel(R) Arc(TM) A770 Graphics", True), ("Intel(R) Arc(TM) Graphics", False), ("NVIDIA RTX A2000", True),
    ("Microsoft Basic Render Driver", False), ("Parsec Virtual Display Adapter", False),
])
def test_dedicated_cards_are_told_apart_from_integrated_ones(description: str, dedicated: bool) -> None:
    assert Device("Vulkan0", description, 8000, 8000).dedicated is dedicated


def test_on_ac_the_dedicated_gpu_is_chosen_on_battery_the_cpu() -> None:
    devices = parse_devices(LISTING)
    assert choose_device(devices, power="ac", model_bytes=MODEL).name == "Vulkan1"
    assert choose_device(devices, power="unknown", model_bytes=MODEL).name == "Vulkan1"   # a desktop
    assert choose_device(devices, power="battery", model_bytes=MODEL) is None


def test_a_gpu_that_holds_at_least_half_the_model_is_used_for_part_of_it() -> None:
    devices = parse_devices(LISTING)                                          # 3367 MiB free on the RTX 2050
    assert choose_device(devices, power="ac", model_bytes=int(2.74 * GB)).name == "Vulkan1"   # Qwen3.5-4B: partly offloaded
    assert choose_device(devices, power="ac", model_bytes=7 * GB) is None                    # too little would fit


def test_no_dedicated_gpu_means_cpu() -> None:
    only_intel = [d for d in parse_devices(LISTING) if not d.dedicated]
    assert choose_device(only_intel, power="ac", model_bytes=MODEL) is None


# --------------------------------------------------------------------------- LocalModel


class FakeServer:
    """Stands in for LlamaServerModel. Records where it started; can fail on the GPU."""

    started: list[str] = []
    gpu_start_fails = False
    gpu_request_fails = False

    def __init__(self, model_path: Path, server: Path, *, device: Device | None) -> None:
        if device is not None and FakeServer.gpu_start_fails:
            raise RuntimeError("vulkan init failed")
        self.device = device
        self.where = device.description if device else "CPU"
        self.closed = False
        FakeServer.started.append(self.where)

    def complete(self, system: str, user: str, *, max_tokens: int) -> str:
        if self.device is not None and FakeServer.gpu_request_fails:
            raise urllib.error.URLError("connection reset")
        return f"You read it on {self.where}."

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def reset_fake() -> None:
    FakeServer.started = []
    FakeServer.gpu_start_fails = FakeServer.gpu_request_fails = False


def local(tmp_path, *, power="ac", idle=600.0, background=True) -> LocalModel:
    model = tmp_path / "qwen.gguf"
    model.write_bytes(b"x" * 1024)
    state = {"power": power, "idle": idle}
    m = LocalModel(model, background=background, server=tmp_path / "llama-server.exe",
                   power_fn=lambda: state["power"], idle_fn=lambda: state["idle"],
                   devices_fn=lambda _: parse_devices(LISTING), start_fn=FakeServer)
    m.state = state  # type: ignore[attr-defined]
    return m


def test_it_starts_lazily_on_the_gpu_and_stops_on_close(tmp_path) -> None:
    m = local(tmp_path)
    assert m() is m and FakeServer.started == []           # the factory does not load anything
    assert m.complete("s", "u", max_tokens=5) == "You read it on NVIDIA GeForce RTX 2050."
    server = m._model
    m.close()
    assert server.closed and m._model is None


def test_a_gpu_that_will_not_start_falls_back_to_the_cpu(tmp_path) -> None:
    FakeServer.gpu_start_fails = True
    assert local(tmp_path).complete("s", "u", max_tokens=5) == "You read it on CPU."


def test_a_gpu_that_fails_mid_run_is_replaced_by_the_cpu(tmp_path) -> None:
    m = local(tmp_path)
    m.complete("s", "u", max_tokens=5)
    FakeServer.gpu_request_fails = True
    assert m.complete("s", "u", max_tokens=5) == "You read it on CPU."
    assert FakeServer.started == ["NVIDIA GeForce RTX 2050", "CPU"]


def test_unplugging_moves_a_manual_run_to_the_cpu(tmp_path) -> None:
    m = local(tmp_path, background=False)
    m.complete("s", "u", max_tokens=5)
    m.state["power"] = "battery"
    assert m.complete("s", "u", max_tokens=5) == "You read it on CPU."


def test_the_background_run_pauses_when_the_user_is_back_or_unplugs(tmp_path) -> None:
    m = local(tmp_path)
    m.complete("s", "u", max_tokens=5)
    m.state["idle"] = 3.0
    with pytest.raises(ModelPaused):
        m.complete("s", "u", max_tokens=5)
    m.state.update(idle=600.0, power="battery")
    with pytest.raises(ModelPaused):
        m.complete("s", "u", max_tokens=5)


def test_a_manual_run_does_not_pause_for_the_user(tmp_path) -> None:
    m = local(tmp_path, idle=0.0, background=False)
    assert m.complete("s", "u", max_tokens=5).startswith("You read it")


# --------------------------------------------------------------------------- light-load gate and the stages


def test_the_model_waits_for_a_quiet_pc() -> None:
    assert check_light_load(MODEL, load_fn=lambda: 10.0, memory_fn=lambda: 8000.0) is None
    assert "CPU busy" in check_light_load(MODEL, load_fn=lambda: 80.0, memory_fn=lambda: 8000.0)
    assert "free memory" in check_light_load(MODEL, load_fn=lambda: 10.0, memory_fn=lambda: 1500.0)


def test_summaries_stop_cleanly_when_paused(tmp_path) -> None:
    from adaptation.summarizer import SummarizeConfig, SummarizeJob, run_summaries

    class Memory:
        def pending_summaries(self, *, limit):
            return [{"id": 1, "doc_id": "d", "entry": {}}, {"id": 2, "doc_id": "d", "entry": {}}]

        def load_document(self, doc_id):
            from features.text_merge import TextDocument
            from storage.memory_store import StoredDocument

            doc = TextDocument()
            doc.merge("some page text that is long enough to read")
            return StoredDocument(doc_id, 1, doc, "f")

        def site_pages(self, app, domain):
            return []

        def set_summary(self, *a):
            raise AssertionError("nothing may be written for a paused entry")

    class Paused:
        name = "m"

        def complete(self, *a, **k):
            raise ModelPaused("the user is back")

    report = run_summaries(SummarizeJob(Memory(), lambda: Paused(), SummarizeConfig(min_words=0)))
    assert report["paused"] == "the user is back" and report["failed"] == 0


def test_a_folder_of_models_gives_the_gpu_the_largest_that_fits_and_the_cpu_the_smallest(tmp_path) -> None:
    folder = tmp_path / "llm"
    folder.mkdir()
    (folder / "small.gguf").write_bytes(b"x" * 10)
    (folder / "large.gguf").write_bytes(b"x" * 20)
    state = {"power": "ac"}
    m = LocalModel(folder, background=False, server=tmp_path / "llama-server.exe", power_fn=lambda: state["power"],
                   idle_fn=lambda: 600.0, devices_fn=lambda _: parse_devices(LISTING), start_fn=FakeServer)
    assert m.size_bytes == 20
    m.complete("s", "u", max_tokens=5)
    assert (m.name, m.where) == ("large", "NVIDIA GeForce RTX 2050")
    state["power"] = "battery"
    m.complete("s", "u", max_tokens=5)                           # unplugged: the CPU, with the small model
    assert (m.name, m.where) == ("small", "CPU")
