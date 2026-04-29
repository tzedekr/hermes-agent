import json

from tools import tts_tool


def test_preferred_voice_output_extension_uses_ogg_only_for_native_telegram_provider():
    assert tts_tool.preferred_voice_output_extension("telegram", provider="elevenlabs") == ".ogg"
    assert tts_tool.preferred_voice_output_extension("telegram", provider="openai") == ".ogg"
    assert tts_tool.preferred_voice_output_extension("telegram", provider="edge") == ".mp3"
    assert tts_tool.preferred_voice_output_extension("discord", provider="elevenlabs") == ".mp3"


def test_telegram_edge_tts_mp3_intermediate_returns_converted_ogg(tmp_path, monkeypatch):
    """GatewayRunner can pass an MP3 hint for Edge; tts_tool must return OGG."""

    async def fake_generate_edge_tts(text, output_path, tts_config):
        assert output_path.endswith(".mp3")
        with open(output_path, "wb") as f:
            f.write(b"mp3")
        return output_path

    def fake_convert_to_opus(mp3_path):
        ogg_path = mp3_path.rsplit(".", 1)[0] + ".ogg"
        with open(ogg_path, "wb") as f:
            f.write(b"ogg")
        return ogg_path

    monkeypatch.setattr(tts_tool, "_load_tts_config", lambda: {"provider": "edge"})
    monkeypatch.setattr(tts_tool, "_get_provider", lambda _config: "edge")
    monkeypatch.setattr(tts_tool, "_import_edge_tts", lambda: object())
    monkeypatch.setattr(tts_tool, "_generate_edge_tts", fake_generate_edge_tts)
    monkeypatch.setattr(tts_tool, "_convert_to_opus", fake_convert_to_opus)

    from gateway import session_context

    monkeypatch.setattr(session_context, "get_session_env", lambda key, default="": "telegram" if key == "HERMES_SESSION_PLATFORM" else default)

    result = json.loads(tts_tool.text_to_speech_tool("hello", output_path=str(tmp_path / "reply.mp3")))

    assert result["success"] is True
    assert result["file_path"].endswith(".ogg")
    assert result["voice_compatible"] is True
