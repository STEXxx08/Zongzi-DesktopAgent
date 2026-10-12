class EventKeyRegistry:
    """
    All event names should be registered here.
    """

    class Dev:
        TEST = "test"

    class System:
        CONFIG_FILE_MODIFIED = "system.config_file_modified"
        LANG_CHANGE = "lang_change"
        SYSTEM_UNHANDLED_ERROR = "system.error"
        SYSTEM_CRASHED = "system.crashed"
        SECOND = "system.second"

    class Pipeline:
        ASR = "pipeline.asr"
        LLM = "pipeline.llm"
        TTS = "pipeline.tts"
        IMG_CAP = "pipeline.img_cap"
        OCR = "pipeline.ocr"

    class Device:
        SCREEN_CAPTURED = "device.screen_captured"
        KEYBOARD_HOTKEY_PRESS = "devices.keyboard"
        MICROPHONE_VAD = "service.vad.speech_chunk"
        MICROPHONE_SWITCH = "switch_vad"
        SPEAKER_PLAY = 'device.speaker.over'
        DESKTOP_AUDIO = "device.desktop_audio"

    class Character:
        STATE = "character.state"
