import asyncio

from skelly import audio_io


def test_combined_output_is_rebuilt_even_when_it_already_exists(monkeypatch):
    """A combined sink left over from before a speaker reconnected plays into nothing: rebuild it."""
    calls = []
    sinks = ["bluez_output.AA_AA_AA_AA_38_03.1", "bluez_output.BB_BB_BB_BB_2A_F8.1"]

    async def pactl_json(*args):
        return [{"name": "module-combine-sink", "index": 7,
                 "argument": f"sink_name={audio_io.COMBINED} slaves={','.join(sinks)}"}]

    async def pactl(*args):
        calls.append(args)

    async def devices():
        return {"speakers": [{"name": audio_io.COMBINED}]}

    monkeypatch.setattr(audio_io, "_pactl_json", pactl_json)
    monkeypatch.setattr(audio_io, "_pactl", pactl)
    monkeypatch.setattr(audio_io, "list_devices", devices)
    assert asyncio.run(audio_io.output_for(sinks)) == audio_io.COMBINED
    assert calls[0] == ("unload-module", "7")
    assert calls[1][:2] == ("load-module", "module-combine-sink")


def test_one_speaker_plays_directly():
    assert asyncio.run(audio_io.output_for(["bluez_output.AA.1", ""])) == "bluez_output.AA.1"
