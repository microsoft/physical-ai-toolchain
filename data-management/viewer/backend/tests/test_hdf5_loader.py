"""Tests for src/api/services/hdf5_loader.py."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

h5py = pytest.importorskip("h5py")

from src.api.services import hdf5_loader as mod
from src.api.services.hdf5_loader import (
    HDF5EpisodeData,
    HDF5Loader,
    HDF5LoaderError,
    get_hdf5_loader,
    load_all_frames,
    load_single_frame,
)

# ---------- helpers ----------


def _write_episode(
    path: Path,
    length: int = 4,
    *,
    with_qvel: bool = True,
    with_ee_pose: bool = True,
    with_gripper: bool = True,
    with_actions: bool = True,
    with_timestamps: bool = True,
    with_images: bool = True,
    image_group: str = "observations/images",
    cameras: tuple[str, ...] = ("cam0", "cam1"),
    fps: float | None = 30.0,
    task_index: object = 7,
    with_metadata_group: bool = True,
    extra_root_attrs: dict | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        f.create_dataset("data/qpos", data=np.arange(length * 2, dtype=np.float64).reshape(length, 2))
        if with_qvel:
            f.create_dataset("data/qvel", data=np.zeros((length, 2), dtype=np.float64))
        if with_ee_pose:
            f.create_dataset("data/ee_pose", data=np.zeros((length, 6), dtype=np.float64))
        if with_gripper:
            f.create_dataset("data/gripper", data=np.zeros((length,), dtype=np.float64))
        if with_actions:
            f.create_dataset("data/action", data=np.zeros((length, 2), dtype=np.float64))
        if with_timestamps:
            f.create_dataset("data/timestamps", data=np.linspace(0.0, 1.0, length))
        if with_images:
            for cam in cameras:
                f.create_dataset(
                    f"{image_group}/{cam}",
                    data=np.zeros((length, 4, 4, 3), dtype=np.uint8),
                )
        if fps is not None:
            f.attrs["fps"] = fps
        f.attrs["task_index"] = task_index
        f.attrs["bytes_attr"] = np.bytes_(b"hello")
        f.attrs["arr_attr"] = np.array([1, 2, 3])
        if extra_root_attrs:
            for k, v in extra_root_attrs.items():
                f.attrs[k] = v
        if with_metadata_group:
            grp = f.create_group("metadata")
            grp.attrs["author"] = np.bytes_(b"alice")
            grp.attrs["weights"] = np.array([0.5, 0.25])


# ---------- HDF5LoaderError ----------


def test_hdf5_loader_error_carries_cause() -> None:
    cause = ValueError("bad")
    err = HDF5LoaderError("oops", cause=cause)
    assert err.cause is cause
    assert "oops" in str(err)


# ---------- HDF5Loader.__init__ ----------


def test_init_raises_when_h5py_unavailable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(mod, "HDF5_AVAILABLE", False)
    with pytest.raises(ImportError, match="HDF5 support requires h5py"):
        HDF5Loader(tmp_path)


# ---------- _find_episode_file ----------


@pytest.mark.parametrize(
    "rel",
    [
        "episode_000003.hdf5",
        "episode_3.hdf5",
        "ep_000003.hdf5",
        "ep_3.hdf5",
        "data/episode_000003.hdf5",
        "data/episode_3.hdf5",
        "episodes/episode_000003.hdf5",
        "episodes/episode_3.hdf5",
    ],
)
def test_find_episode_file_patterns(tmp_path: Path, rel: str) -> None:
    target = tmp_path / rel
    _write_episode(target, length=2, with_images=False, with_metadata_group=False)
    loader = HDF5Loader(tmp_path)
    found = loader._find_episode_file(3)
    assert found == target
    # cache hit on second call
    assert loader._find_episode_file(3) == target


def test_find_episode_file_missing_raises(tmp_path: Path) -> None:
    loader = HDF5Loader(tmp_path)
    with pytest.raises(HDF5LoaderError, match="No HDF5 file found"):
        loader._find_episode_file(99)


# ---------- list_episodes ----------


def test_list_episodes_discovers_across_dirs(tmp_path: Path) -> None:
    _write_episode(tmp_path / "episode_000001.hdf5", length=1, with_images=False, with_metadata_group=False)
    _write_episode(tmp_path / "data" / "episode_000002.hdf5", length=1, with_images=False, with_metadata_group=False)
    _write_episode(tmp_path / "episodes" / "ep_3.hdf5", length=1, with_images=False, with_metadata_group=False)
    # unparseable name should be skipped
    (tmp_path / "random.hdf5").write_bytes(b"")
    loader = HDF5Loader(tmp_path)
    assert loader.list_episodes() == [1, 2, 3]


# ---------- _parse_episode_index ----------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("episode_000005.hdf5", 5),
        ("ep_3.hdf5", 3),
        ("garbage.hdf5", None),
        ("episode_abc.hdf5", None),
    ],
)
def test_parse_episode_index(name: str, expected: int | None) -> None:
    assert HDF5Loader._parse_episode_index(Path(name)) == expected


# ---------- load_episode happy path ----------


def test_load_episode_happy_path(tmp_path: Path) -> None:
    _write_episode(tmp_path / "episode_0.hdf5", length=4)
    loader = HDF5Loader(tmp_path)
    ep = loader.load_episode(0, load_images=True)
    assert isinstance(ep, HDF5EpisodeData)
    assert ep.episode_index == 0
    assert ep.length == 4
    assert ep.timestamps.shape == (4,)
    assert ep.joint_velocities is not None
    assert ep.end_effector_pose is not None
    assert ep.gripper_states is not None
    assert ep.actions is not None
    assert set(ep.images.keys()) == {"cam0", "cam1"}
    assert ep.task_index == 7
    assert ep.metadata["bytes_attr"] == "hello"
    assert ep.metadata["arr_attr"] == [1, 2, 3]
    assert ep.metadata["author"] == "alice"
    assert ep.metadata["weights"] == [0.5, 0.25]
    # cameras discovery only runs if not present; metadata group didn't set "cameras"
    assert ep.metadata["cameras"] == ["cam0", "cam1"]


def test_load_episode_filters_image_cameras(tmp_path: Path) -> None:
    _write_episode(tmp_path / "episode_0.hdf5", length=2)
    loader = HDF5Loader(tmp_path)
    ep = loader.load_episode(0, load_images=True, image_cameras=["cam0"])
    assert set(ep.images.keys()) == {"cam0"}


def test_load_episode_generates_timestamps_when_missing(tmp_path: Path) -> None:
    _write_episode(
        tmp_path / "episode_0.hdf5",
        length=3,
        with_timestamps=False,
        with_images=False,
        with_metadata_group=False,
        fps=10.0,
    )
    loader = HDF5Loader(tmp_path)
    ep = loader.load_episode(0)
    assert np.allclose(ep.timestamps, np.arange(3) / 10.0)


def test_load_episode_missing_qpos_raises(tmp_path: Path) -> None:
    target = tmp_path / "episode_0.hdf5"
    with h5py.File(target, "w") as f:
        f.create_dataset("data/timestamps", data=np.array([0.0]))
    loader = HDF5Loader(tmp_path)
    with pytest.raises(HDF5LoaderError, match="No joint position data"):
        loader.load_episode(0)


def test_load_episode_wraps_generic_exception(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_episode(tmp_path / "episode_0.hdf5", length=1, with_images=False, with_metadata_group=False)
    loader = HDF5Loader(tmp_path)

    def boom(self, f, idx, load_images, image_cameras):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(HDF5Loader, "_parse_hdf5_file", boom)
    with pytest.raises(HDF5LoaderError) as exc_info:
        loader.load_episode(0)
    assert isinstance(exc_info.value.cause, RuntimeError)


def test_load_episode_bytes_task_index(tmp_path: Path) -> None:
    _write_episode(
        tmp_path / "episode_0.hdf5",
        length=1,
        with_images=False,
        with_metadata_group=False,
        task_index=np.bytes_(b"42"),
    )
    loader = HDF5Loader(tmp_path)
    ep = loader.load_episode(0)
    assert ep.task_index == 42


def test_load_episode_uses_existing_cameras_metadata(tmp_path: Path) -> None:
    _write_episode(
        tmp_path / "episode_0.hdf5",
        length=1,
        with_images=False,
        with_metadata_group=False,
        extra_root_attrs={"cameras": np.array([b"preset"])},
    )
    loader = HDF5Loader(tmp_path)
    ep = loader.load_episode(0)
    # cameras already in metadata, discovery loop is skipped
    assert ep.metadata["cameras"] == [b"preset"]


# ---------- _load_images corrupt-dataset branch ----------


def test_load_images_skips_corrupt_dataset(tmp_path: Path) -> None:
    target = tmp_path / "episode_0.hdf5"
    with h5py.File(target, "w") as f:
        f.create_dataset("data/qpos", data=np.zeros((2, 2)))
        # cam0 valid; cam1 will raise on read
        f.create_dataset("observations/images/cam0", data=np.zeros((2, 2, 2, 3), dtype=np.uint8))
        f.create_dataset("observations/images/cam1", data=np.zeros((2, 2, 2, 3), dtype=np.uint8))
    loader = HDF5Loader(tmp_path)

    real_asarray = np.asarray

    def fake_asarray(data, dtype=None, **kwargs):
        if dtype is np.uint8 and getattr(data, "shape", None) == (2, 2, 2, 3):
            # Trigger exception only on the second camera
            fake_asarray.calls += 1
            if fake_asarray.calls == 2:
                raise RuntimeError("corrupt")
        return real_asarray(data, dtype=dtype, **kwargs) if dtype is not None else real_asarray(data, **kwargs)

    fake_asarray.calls = 0
    monkey_target = mod.np
    orig = monkey_target.asarray
    try:
        monkey_target.asarray = fake_asarray
        ep = loader.load_episode(0, load_images=True)
    finally:
        monkey_target.asarray = orig
    assert "cam0" in ep.images
    assert "cam1" not in ep.images


# ---------- get_episode_info ----------


def test_get_episode_info_happy(tmp_path: Path) -> None:
    _write_episode(tmp_path / "episode_0.hdf5", length=5)
    loader = HDF5Loader(tmp_path)
    info = loader.get_episode_info(0)
    assert info["episode_index"] == 0
    assert info["length"] == 5
    assert info["fps"] == 30.0
    assert info["cameras"] == ["cam0", "cam1"]
    assert info["task_index"] == 7
    assert info["file_path"].endswith("episode_0.hdf5")


def test_get_episode_info_wraps_exception(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_episode(tmp_path / "episode_0.hdf5", length=1, with_images=False, with_metadata_group=False)
    loader = HDF5Loader(tmp_path)

    real_file = h5py.File

    def bad_file(*args, **kwargs):
        raise RuntimeError("io error")

    monkeypatch.setattr(mod.h5py, "File", bad_file)
    try:
        with pytest.raises(HDF5LoaderError) as exc_info:
            loader.get_episode_info(0)
        assert isinstance(exc_info.value.cause, RuntimeError)
    finally:
        monkeypatch.setattr(mod.h5py, "File", real_file)


def test_get_episode_info_zero_task_index(tmp_path: Path) -> None:
    _write_episode(
        tmp_path / "episode_0.hdf5",
        length=2,
        with_images=False,
        with_metadata_group=False,
        task_index=0,
    )
    loader = HDF5Loader(tmp_path)
    info = loader.get_episode_info(0)
    assert info["task_index"] == 0


# ---------- factory ----------


def test_get_hdf5_loader_returns_loader(tmp_path: Path) -> None:
    loader = get_hdf5_loader(tmp_path)
    assert isinstance(loader, HDF5Loader)
    assert loader.base_path == tmp_path


# ---------- module-level load_single_frame ----------


def test_load_single_frame_in_bounds(tmp_path: Path) -> None:
    target = tmp_path / "ep.hdf5"
    with h5py.File(target, "w") as f:
        f.create_dataset("observations/images/cam0", data=np.ones((3, 4, 4, 3), dtype=np.uint8) * 5)
    out = load_single_frame(target, "cam0", 1)
    assert out is not None
    assert out.shape == (4, 4, 3)
    assert int(out[0, 0, 0]) == 5


def test_load_single_frame_out_of_bounds_returns_none(tmp_path: Path) -> None:
    target = tmp_path / "ep.hdf5"
    with h5py.File(target, "w") as f:
        f.create_dataset("observations/images/cam0", data=np.zeros((2, 4, 4, 3), dtype=np.uint8))
    assert load_single_frame(target, "cam0", 5) is None
    assert load_single_frame(target, "cam0", -1) is None


def test_load_single_frame_missing_camera_returns_none(tmp_path: Path) -> None:
    target = tmp_path / "ep.hdf5"
    with h5py.File(target, "w") as f:
        f.create_dataset("observations/images/cam0", data=np.zeros((1, 4, 4, 3), dtype=np.uint8))
    assert load_single_frame(target, "missing", 0) is None


def test_load_single_frame_bad_file_returns_none(tmp_path: Path) -> None:
    bogus = tmp_path / "nope.hdf5"
    bogus.write_bytes(b"not hdf5")
    assert load_single_frame(bogus, "cam0", 0) is None


# ---------- module-level load_all_frames ----------


def test_load_all_frames_happy(tmp_path: Path) -> None:
    target = tmp_path / "ep.hdf5"
    with h5py.File(target, "w") as f:
        f.create_dataset("observations/images/cam0", data=np.ones((2, 4, 4, 3), dtype=np.uint8) * 9)
    out = load_all_frames(target, "cam0")
    assert out is not None
    assert out.shape == (2, 4, 4, 3)
    assert int(out[0, 0, 0, 0]) == 9


def test_load_all_frames_missing_camera_returns_none(tmp_path: Path) -> None:
    target = tmp_path / "ep.hdf5"
    with h5py.File(target, "w") as f:
        f.create_dataset("observations/images/cam0", data=np.zeros((1, 4, 4, 3), dtype=np.uint8))
    assert load_all_frames(target, "missing") is None


def test_load_all_frames_bad_file_returns_none(tmp_path: Path) -> None:
    bogus = tmp_path / "nope.hdf5"
    bogus.write_bytes(b"not hdf5")
    assert load_all_frames(bogus, "cam0") is None
