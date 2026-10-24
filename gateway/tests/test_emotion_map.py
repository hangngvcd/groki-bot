from __future__ import annotations

import pytest

from stackchan_mcp.device_emotion import face_to_device_emotion, normalize_device_emotion
from stackchan_mcp.emotion_map import INTENSITIES, MOODS, EmotionPlan, resolve


def _brightness(plan: EmotionPlan) -> int:
    return sum(plan.led_rgb)


def _angle_amplitude(plan: EmotionPlan) -> int:
    return abs(plan.head_pitch_offset) + abs(plan.head_yaw_offset)


def test_resolve_covers_all_supported_moods_and_intensities():
    expected_faces = {
        "excited": "happy",
        "happy": "happy",
        "neutral": "idle",
        "tired": "sleeping",
        "sad": "idle",
        "curious": "doubt",
        "apologetic": "embarrassed",
    }

    assert set(MOODS) == set(expected_faces)
    assert INTENSITIES == ("low", "high")
    for mood, face in expected_faces.items():
        for intensity in INTENSITIES:
            plan = resolve(mood, intensity)
            assert plan.face == face
            assert all(0 <= channel <= 255 for channel in plan.led_rgb)
            assert 0 <= plan.head_pitch <= 60
            assert -90 <= plan.head_yaw <= 90


def test_curious_does_not_use_thinking_face():
    for intensity in INTENSITIES:
        assert resolve("curious", intensity).face != "thinking"
        assert resolve("curious", intensity).face == "doubt"


def test_unknown_mood_falls_back_to_neutral():
    assert resolve("confused", "low") == resolve("neutral", "low")


def test_unknown_intensity_falls_back_to_low():
    assert resolve("happy", "medium") == resolve("happy", "low")


def test_high_intensity_increases_brightness_and_motion_amplitude():
    low = resolve("curious", "low")
    high = resolve("curious", "high")

    assert _brightness(high) > _brightness(low)
    assert _angle_amplitude(high) > _angle_amplitude(low)


def test_tired_and_sad_use_low_head_pose():
    assert resolve("tired", "low").head_pitch < resolve("neutral", "low").head_pitch
    assert resolve("sad", "high").head_pitch < resolve("neutral", "low").head_pitch


@pytest.mark.parametrize("profile", [None, "0"])
def test_device_emotion_mapping_unchanged_without_child_profile(monkeypatch, profile):
    if profile is None:
        monkeypatch.delenv("STACKCHAN_CHILD_PROFILE", raising=False)
    else:
        monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", profile)
    # Snapshot the legacy vocabulary and aliases independently of the map constants.
    for emotion in (
        "neutral", "happy", "sad", "angry", "doubt", "sleepy", "laughing",
        "funny", "loving", "delicious", "kissy", "winking", "silly", "crying",
        "shocked", "surprised", "thinking", "confused", "embarrassed", "relaxed",
        "cool", "confident",
    ):
        assert normalize_device_emotion(emotion) == emotion
        assert face_to_device_emotion(f" {emotion.upper()} ") == emotion
    for face, expected in {
        "idle": "neutral", "working_typing": "thinking", "juggling": "happy",
        "sweeping": "neutral", "sleeping": "sleepy", "error": "shocked",
        "notification": "surprised", "excited": "neutral", "curious": "neutral",
        "affection": "neutral", "bored": "neutral", "unknown": "neutral",
        "": "neutral", None: "neutral",
    }.items():
        assert face_to_device_emotion(face) == expected
    assert normalize_device_emotion("unknown", default="sleepy") == "sleepy"


def test_child_device_emotion_mapping(monkeypatch):
    monkeypatch.setenv("STACKCHAN_CHILD_PROFILE", "1")
    for emotion in (
        "neutral", "happy", "excited", "curious", "thinking", "affection",
        "surprised", "sad",
        # XiaoZhi aliases the firmware maps onto whitelisted faces
        "laughing", "loving", "winking", "crying", "shocked",
    ):
        assert face_to_device_emotion(f" {emotion.title()} ") == emotion
        assert normalize_device_emotion(emotion) == emotion
    for emotion in (
        "angry", "bored", "doubt", "sleepy", "sleeping", "embarrassed",
        "confused", "unknown", "", None,
    ):
        assert face_to_device_emotion(emotion) == "neutral"
        assert normalize_device_emotion(emotion, default="angry") == "neutral"
    assert face_to_device_emotion("working_typing") == "thinking"
    assert face_to_device_emotion("notification") == "surprised"
    # The physical mood plan remains unchanged; its Doubt face is filtered at send time.
    assert face_to_device_emotion(resolve("curious", "low").face) == "neutral"
