#!/usr/bin/env python3
"""Ephemeral same-session clustering for unidentified voices.

Nothing in this module is written to disk. Restarting Jerry forgets all
anonymous clusters.
"""
from __future__ import annotations

import os
from collections import deque
from datetime import datetime, timezone

import numpy as np

DEFAULT_THRESHOLD = float(os.getenv("TALKING_BOX_ANON_THRESHOLD", "0.58"))
DEFAULT_MARGIN = float(os.getenv("TALKING_BOX_ANON_MARGIN", "0.06"))
MAX_CLUSTERS = int(os.getenv("TALKING_BOX_ANON_MAX_CLUSTERS", "8"))
MAX_VECTORS = int(os.getenv("TALKING_BOX_ANON_MAX_VECTORS", "8"))


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def normalize(vector):
    vector = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 1e-8:
        raise ValueError("Anonymous speaker embedding has zero/invalid norm")
    return vector / norm


class AnonymousSpeakerSession:
    def __init__(self, threshold=DEFAULT_THRESHOLD, margin=DEFAULT_MARGIN,
                 max_clusters=MAX_CLUSTERS, max_vectors=MAX_VECTORS):
        self.threshold = float(threshold)
        self.margin = float(margin)
        self.max_clusters = max(1, int(max_clusters))
        self.max_vectors = max(2, int(max_vectors))
        self.clusters = {}
        self.next_id = 1

    def _centroid(self, cluster):
        return normalize(np.mean(list(cluster["vectors"]), axis=0))

    def _new_cluster(self, embedding):
        if len(self.clusters) >= self.max_clusters:
            oldest = min(self.clusters, key=lambda cid: self.clusters[cid]["last_seen_at"])
            del self.clusters[oldest]
        anonymous_id = f"anon-{self.next_id}"
        self.next_id += 1
        now = utc_now()
        cluster = {
            "anonymous_id": anonymous_id,
            "vectors": deque([normalize(embedding)], maxlen=self.max_vectors),
            "seen_count": 1,
            "created_at": now,
            "last_seen_at": now,
        }
        self.clusters[anonymous_id] = cluster
        return cluster

    def _view(self, cluster, is_new, similarity=None, margin=None):
        return {
            "status": "anonymous",
            "id": None,
            "display_name": None,
            "anonymous_id": cluster["anonymous_id"],
            "is_new": bool(is_new),
            "seen_count": int(cluster["seen_count"]),
            "cluster_similarity": round(float(similarity), 4) if similarity is not None else None,
            "cluster_margin": round(float(margin), 4) if margin is not None else None,
            "cluster_threshold": self.threshold,
            "session_only": True,
        }

    def observe(self, embedding):
        query = normalize(embedding)
        if not self.clusters:
            return self._view(self._new_cluster(query), True)

        scores = [
            (float(np.dot(query, self._centroid(cluster))), anonymous_id)
            for anonymous_id, cluster in self.clusters.items()
        ]
        scores.sort(reverse=True)
        best_score, best_id = scores[0]
        second_score = scores[1][0] if len(scores) > 1 else None
        separation = best_score - second_score if second_score is not None else None

        matched = best_score >= self.threshold
        if matched and separation is not None and separation < self.margin:
            matched = False

        if not matched:
            return self._view(self._new_cluster(query), True, best_score, separation)

        cluster = self.clusters[best_id]
        cluster["vectors"].append(query)
        cluster["seen_count"] += 1
        cluster["last_seen_at"] = utc_now()
        return self._view(cluster, False, best_score, separation)
