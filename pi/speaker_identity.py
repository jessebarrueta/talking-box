#!/usr/bin/env python3
"""Local speaker recognition for Talking Box / Jerry."""
from __future__ import annotations

import argparse
import json
import math
import os
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

DEFAULT_MODEL = Path(__file__).resolve().parent.parent / "models" / "speaker" / "wespeaker_en_voxceleb_resnet34.onnx"
DEFAULT_PROFILES = Path.home() / ".talking_box_speakers.json"
DEFAULT_THRESHOLD = float(os.getenv("TALKING_BOX_SPEAKER_THRESHOLD", "0.60"))
DEFAULT_MARGIN = float(os.getenv("TALKING_BOX_SPEAKER_MARGIN", "0.08"))
MIN_AUDIO_SECONDS = float(os.getenv("TALKING_BOX_SPEAKER_MIN_SECONDS", "0.8"))
MIN_RMS = float(os.getenv("TALKING_BOX_SPEAKER_MIN_RMS", "0.002"))
MAX_EMBEDDINGS_PER_SPEAKER = int(os.getenv("TALKING_BOX_SPEAKER_MAX_EMBEDDINGS", "20"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(vector) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm <= 1e-8:
        raise ValueError("Speaker embedding has zero/invalid norm")
    return vector / norm


def _slug(value: str) -> str:
    value = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    value = "-".join(part for part in value.split("-") if part)
    if not value:
        raise ValueError("Speaker id cannot be empty")
    return value[:80]


def _read_wav(path: str | Path) -> tuple[np.ndarray, int, float]:
    path = Path(path)
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frames = wav.getnframes()
        raw = wav.readframes(frames)
    if sample_width != 2:
        raise ValueError(f"{path}: expected 16-bit PCM WAV; got {sample_width * 8}-bit")
    if channels < 1:
        raise ValueError(f"{path}: WAV has no channels")
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32)
    if channels > 1:
        samples = samples.reshape(-1, channels)[:, 0]
    samples /= 32768.0
    samples = np.ascontiguousarray(samples, dtype=np.float32)
    duration = len(samples) / float(sample_rate or 1)
    return samples, sample_rate, duration


def wav_quality(path: str | Path) -> dict[str, Any]:
    samples, sample_rate, duration = _read_wav(path)
    rms = float(np.sqrt(np.mean(np.square(samples)))) if len(samples) else 0.0
    reasons = []
    if duration < MIN_AUDIO_SECONDS:
        reasons.append(f"need at least {MIN_AUDIO_SECONDS:.1f}s; got {duration:.2f}s")
    if rms < MIN_RMS:
        reasons.append(f"recording too quiet: rms={rms:.5f}, minimum={MIN_RMS:.5f}")
    return {
        "valid": not reasons,
        "duration_seconds": round(duration, 3),
        "sample_rate": int(sample_rate),
        "rms": round(rms, 6),
        "minimum_rms": MIN_RMS,
        "reason": "; ".join(reasons) if reasons else None,
    }


class SpeakerIdentity:
    def __init__(self, model_path=DEFAULT_MODEL, profiles_path=DEFAULT_PROFILES,
                 threshold=DEFAULT_THRESHOLD, margin=DEFAULT_MARGIN, num_threads=2):
        self.model_path = Path(model_path)
        self.profiles_path = Path(profiles_path)
        self.threshold = float(threshold)
        self.margin = float(margin)
        self.num_threads = int(num_threads)
        if not self.model_path.is_file():
            raise FileNotFoundError(f"Speaker model not found: {self.model_path}")
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise RuntimeError("sherpa-onnx is not installed. Run pi/setup-speaker-id.sh") from exc
        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(self.model_path), num_threads=self.num_threads,
            debug=False, provider="cpu")
        if not config.validate():
            raise RuntimeError(f"Invalid speaker model config: {config}")
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        self.embedding_dim = int(self.extractor.dim)
        self.data = self._load_profiles()

    def _empty_data(self):
        return {"version": 1, "model": self.model_path.name,
                "embedding_dim": self.embedding_dim, "speakers": {}}

    def _load_profiles(self):
        if not self.profiles_path.exists():
            return self._empty_data()
        data = json.loads(self.profiles_path.read_text())
        if not isinstance(data, dict):
            raise ValueError("Speaker profile file is not a JSON object")
        model = data.get("model")
        dim = data.get("embedding_dim")
        if model and model != self.model_path.name:
            raise RuntimeError(f"Speaker profile model mismatch: profiles use {model}, current model is {self.model_path.name}")
        if dim and int(dim) != self.embedding_dim:
            raise RuntimeError(f"Speaker embedding dimension mismatch: profiles use {dim}, current model uses {self.embedding_dim}")
        data.setdefault("version", 1)
        data["model"] = self.model_path.name
        data["embedding_dim"] = self.embedding_dim
        data.setdefault("speakers", {})
        return data

    def _save_profiles(self):
        self.profiles_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.profiles_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(self.profiles_path)
        os.chmod(self.profiles_path, 0o600)

    def embedding_from_wav(self, path):
        quality = wav_quality(path)
        if not quality["valid"]:
            raise ValueError(quality["reason"] or "Recording is not usable")
        samples, sample_rate, _ = _read_wav(path)
        stream = self.extractor.create_stream()
        stream.accept_waveform(sample_rate=sample_rate, waveform=samples)
        stream.input_finished()
        if not self.extractor.is_ready(stream):
            raise ValueError("Speaker model did not receive enough usable audio")
        embedding = np.asarray(self.extractor.compute(stream), dtype=np.float32)
        if embedding.size != self.embedding_dim:
            raise RuntimeError(f"Expected embedding dim {self.embedding_dim}; got {embedding.size}")
        return _normalize(embedding)

    def enroll(self, speaker_id, display_name, wav_paths, consent, replace=False):
        if not consent:
            raise ValueError("Explicit consent is required to enroll a voice profile")
        if not wav_paths:
            raise ValueError("At least one WAV sample is required")
        speaker_id = _slug(speaker_id)
        embeddings = [self.embedding_from_wav(path).tolist() for path in wav_paths]
        speakers = self.data["speakers"]
        existing = speakers.get(speaker_id)
        if replace or not existing:
            profile = {
                "display_name": display_name.strip() or speaker_id,
                "consent": True,
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "embeddings": embeddings,
            }
        else:
            profile = dict(existing)
            profile["display_name"] = display_name.strip() or profile.get("display_name") or speaker_id
            profile["consent"] = True
            profile["updated_at"] = utc_now()
            profile.setdefault("embeddings", [])
            profile["embeddings"].extend(embeddings)
            profile["embeddings"] = profile["embeddings"][-MAX_EMBEDDINGS_PER_SPEAKER:]
        speakers[speaker_id] = profile
        self._save_profiles()
        return {"id": speaker_id, "display_name": profile["display_name"],
                "sample_count": len(profile["embeddings"])}

    def remove(self, speaker_id):
        removed = self.data["speakers"].pop(_slug(speaker_id), None)
        if removed is None:
            return False
        self._save_profiles()
        return True

    def list_speakers(self):
        return [
            {"id": speaker_id,
             "display_name": profile.get("display_name") or speaker_id,
             "sample_count": len(profile.get("embeddings") or []),
             "updated_at": profile.get("updated_at")}
            for speaker_id, profile in sorted(self.data["speakers"].items())
        ]

    def _centroid(self, profile):
        vectors = []
        for value in profile.get("embeddings") or []:
            vector = np.asarray(value, dtype=np.float32)
            if vector.size == self.embedding_dim:
                vectors.append(_normalize(vector))
        return _normalize(np.mean(vectors, axis=0)) if vectors else None

    def identify_embedding(self, embedding):
        query = _normalize(embedding)
        speakers = self.data.get("speakers") or {}
        if not speakers:
            return {"status": "unknown", "id": None, "display_name": None,
                    "reason": "no_enrolled_speakers", "threshold": self.threshold}
        scores = []
        for speaker_id, profile in speakers.items():
            centroid = self._centroid(profile)
            if centroid is not None:
                scores.append((float(np.dot(query, centroid)), speaker_id, profile))
        if not scores:
            return {"status": "unknown", "id": None, "display_name": None,
                    "reason": "no_usable_profiles", "threshold": self.threshold}
        scores.sort(reverse=True, key=lambda item: item[0])
        best_score, best_id, best_profile = scores[0]
        second_score = scores[1][0] if len(scores) > 1 else None
        separation = best_score - second_score if second_score is not None else None
        recognized = best_score >= self.threshold
        if recognized and separation is not None and separation < self.margin:
            recognized = False
        if not recognized:
            return {"status": "unknown", "id": None, "display_name": None,
                    "similarity": round(best_score, 4),
                    "margin": round(separation, 4) if separation is not None else None,
                    "threshold": self.threshold}
        return {"status": "recognized", "id": best_id,
                "display_name": best_profile.get("display_name") or best_id,
                "similarity": round(best_score, 4),
                "margin": round(separation, 4) if separation is not None else None,
                "threshold": self.threshold}

    def identify(self, wav_path):
        try:
            embedding = self.embedding_from_wav(wav_path)
        except ValueError as exc:
            return {"status": "insufficient_audio", "id": None,
                    "display_name": None, "reason": str(exc)}
        return self.identify_embedding(embedding)


def build_cli():
    parser = argparse.ArgumentParser(description="Talking Box local speaker recognition")
    parser.add_argument("--model", default=str(DEFAULT_MODEL))
    parser.add_argument("--profiles", default=str(DEFAULT_PROFILES))
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--margin", type=float, default=DEFAULT_MARGIN)
    sub = parser.add_subparsers(dest="command", required=True)
    enroll = sub.add_parser("enroll")
    enroll.add_argument("--id", required=True)
    enroll.add_argument("--name", required=True)
    enroll.add_argument("--consent", action="store_true")
    enroll.add_argument("--replace", action="store_true")
    enroll.add_argument("wav", nargs="+")
    identify = sub.add_parser("identify")
    identify.add_argument("wav")
    quality = sub.add_parser("quality")
    quality.add_argument("wav")
    sub.add_parser("list")
    remove = sub.add_parser("remove")
    remove.add_argument("--id", required=True)
    return parser


def main() -> int:
    args = build_cli().parse_args()
    if args.command == "quality":
        try:
            result = wav_quality(args.wav)
        except Exception as exc:
            result = {"valid": False, "reason": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(result, indent=2))
        return 0 if result.get("valid") else 1
    identity = SpeakerIdentity(model_path=args.model, profiles_path=args.profiles,
                               threshold=args.threshold, margin=args.margin)
    if args.command == "enroll":
        result = identity.enroll(args.id, args.name, args.wav, args.consent, args.replace)
    elif args.command == "identify":
        result = identity.identify(args.wav)
    elif args.command == "list":
        result = identity.list_speakers()
    elif args.command == "remove":
        result = {"removed": identity.remove(args.id)}
    else:
        raise RuntimeError(f"Unknown command: {args.command}")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
