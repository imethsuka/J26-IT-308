"""ONNX encoder: preprocessing must equal MobileCLIP's transform; backend selection; (optional) real model."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

import features.mobileclip_encoder as encoder_module
from features.onnx_encoder import INPUT_SIZE, find_onnx_model, preprocess


def random_image(width: int, height: int, seed: int = 0) -> Image.Image:
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(0, 256, (height, width, 3), dtype=np.uint8), mode="RGB")


class TestPreprocess:
    @pytest.mark.parametrize("size", [(1920, 1080), (1080, 1920), (1000, 1000), (300, 700), (256, 256), (2560, 1440), (257, 255)])
    def test_equals_mobileclip_transform(self, size):
        torchvision = pytest.importorskip("torchvision.transforms")
        transform = torchvision.Compose([
            torchvision.Resize(INPUT_SIZE, interpolation=torchvision.InterpolationMode.BILINEAR),
            torchvision.CenterCrop(INPUT_SIZE),
            torchvision.ToTensor(),
        ])
        image = random_image(*size)
        expected = transform(image).unsqueeze(0).numpy()
        np.testing.assert_allclose(preprocess(image), expected, atol=1e-6)

    def test_shape_dtype_and_range(self):
        out = preprocess(random_image(640, 360))
        assert out.shape == (1, 3, INPUT_SIZE, INPUT_SIZE) and out.dtype == np.float32
        assert 0.0 <= out.min() and out.max() <= 1.0

    def test_bgra_frame_from_the_collector(self):
        bgra = np.zeros((360, 640, 4), dtype=np.uint8)
        bgra[..., 0] = 255  # blue channel in BGRA
        out = preprocess(bgra)
        assert out[0, 2].min() == 1.0 and out[0, 0].max() == 0.0  # blue lands in the RGB blue plane


class TestBackendSelection:
    @pytest.fixture(autouse=True)
    def fresh(self, monkeypatch):
        monkeypatch.setattr(encoder_module, "_default_encoder", None)

    def test_prefers_onnx_when_available(self, monkeypatch):
        sentinel = type("E", (), {"path": "x.onnx"})()
        import features.onnx_encoder as onnx_module

        monkeypatch.setattr(onnx_module, "default_onnx_encoder", lambda: sentinel)
        monkeypatch.delenv("MOBILECLIP_BACKEND", raising=False)
        assert encoder_module.default_encoder() is sentinel

    def test_falls_back_to_torch_when_onnx_is_missing(self, monkeypatch):
        import features.onnx_encoder as onnx_module

        def missing():
            raise FileNotFoundError("no model")

        torch_sentinel = object()
        monkeypatch.setattr(onnx_module, "default_onnx_encoder", missing)
        monkeypatch.setattr(encoder_module, "load_image_encoder", lambda *a, **k: torch_sentinel)
        monkeypatch.delenv("MOBILECLIP_BACKEND", raising=False)
        assert encoder_module.default_encoder() is torch_sentinel

    def test_forcing_onnx_does_not_fall_back(self, monkeypatch):
        import features.onnx_encoder as onnx_module

        def missing():
            raise FileNotFoundError("no model")

        monkeypatch.setattr(onnx_module, "default_onnx_encoder", missing)
        monkeypatch.setenv("MOBILECLIP_BACKEND", "onnx")
        with pytest.raises(FileNotFoundError):
            encoder_module.default_encoder()

    def test_forcing_torch_skips_onnx(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(encoder_module, "load_image_encoder", lambda *a, **k: sentinel)
        monkeypatch.setenv("MOBILECLIP_BACKEND", "torch")
        assert encoder_module.default_encoder() is sentinel

    def test_bad_backend_name_is_rejected(self, monkeypatch):
        monkeypatch.setenv("MOBILECLIP_BACKEND", "tensorrt")
        with pytest.raises(ValueError):
            encoder_module.default_encoder()


def test_find_onnx_model_honours_the_environment(tmp_path, monkeypatch):
    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")
    monkeypatch.setenv("MOBILECLIP_ONNX_MODEL", str(model))
    assert find_onnx_model() == model
    assert find_onnx_model(tmp_path / "nope.onnx") == model  # a missing explicit path falls through to the next candidate


@pytest.mark.skipif(find_onnx_model() is None, reason="no exported ONNX model on this machine")
def test_real_model_returns_a_unit_vector_and_is_deterministic():
    from features.onnx_encoder import OnnxImageEncoder

    encoder = OnnxImageEncoder(find_onnx_model())
    image = random_image(800, 450, seed=3)
    a, b = encoder.embed_frame(image), encoder.embed_frame(image)
    assert a.shape == (512,) and a.dtype == np.float32
    assert np.linalg.norm(a) == pytest.approx(1.0, abs=1e-5)
    np.testing.assert_array_equal(a, b)
    gradient = Image.fromarray(np.tile(np.linspace(0, 255, 800, dtype=np.uint8), (450, 1))[..., None].repeat(3, axis=2), mode="RGB")
    assert float(a @ encoder.embed_frame(gradient)) < 0.95  # a structurally different image differs
