"""Features.

Responsibility: turn sensed events into feature vectors.
- ``mobileclip_encoder`` provides MobileCLIP-S0 image embeddings (``embed_frame`` -> 512-d unit
  vector). Only the frozen image encoder is loaded here (no text tower).
- ``feature_assembly`` combines a sensing event, its embedding, and recent context history into a
  flat feature dict (behavioural / temporal / contextual / visual). The raw embedding is summarized
  into similarity + novelty scalars, never emitted directly.
"""
