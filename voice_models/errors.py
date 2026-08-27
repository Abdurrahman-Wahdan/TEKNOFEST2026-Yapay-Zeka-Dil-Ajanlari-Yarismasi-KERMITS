"""Stable errors shared by local and remote voice providers."""


class VoiceTranscriptionUnavailable(RuntimeError):
    """The configured transcription provider cannot currently serve requests."""


class VoiceTranscriptionFailed(RuntimeError):
    """The supplied audio could not be decoded or transcribed."""


class VoiceSpeechUnavailable(RuntimeError):
    """The configured speech provider cannot currently serve requests."""


class VoiceSpeechFailed(RuntimeError):
    """The supplied text could not be synthesized."""
